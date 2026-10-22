from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")

# Construct forbidden tokens dynamically at runtime so this test file itself
# never contains any raw personal path, drive, or email substrings.
TOKEN_USER_NAME = "Ora" + "kul"
TOKEN_FULL_USER = TOKEN_USER_NAME + " " + "Grey"
TOKEN_USERS_WIN = "Users\\" + TOKEN_USER_NAME
TOKEN_USERS_POSIX = "Users/" + TOKEN_USER_NAME
TOKEN_EXT_FOLDER = "ANTI" + "GRA" + "VITY"
TOKEN_EXT_WIN = "e:\\" + TOKEN_EXT_FOLDER
TOKEN_EXT_POSIX = "e:/" + TOKEN_EXT_FOLDER
TOKEN_PERSONAL_EMAIL = "kuro" + "ora" + "kul" + "grey" + "@gmail.com"
EXPECTED_ANON_EMAIL = "129319184+" + TOKEN_USER_NAME.lower() + "zero@users.noreply.github.com"


def _git_output(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout


def _git_ls_files() -> list[str]:
    out = _git_output("ls-files")
    return [line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()]


def _iter_existing_tracked_text_files() -> list[ tuple[str, str] ]:
    results: list[tuple[str, str]] = []
    for rel_path in _git_ls_files():
        full_path = REPO_ROOT / rel_path
        if not full_path.is_file():
            continue
        try:
            raw = full_path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            continue
        text = raw.decode("utf-8", errors="replace")
        results.append((rel_path, text))
    return results


# ============================================================================
# REQ-001: Portable Relative Paths & Self-Contained Skill Dependencies
# ============================================================================


def test_req001_no_personal_user_paths_in_tracked_files() -> None:
    forbidden = (
        TOKEN_FULL_USER.lower(),
        TOKEN_USERS_WIN.lower(),
        TOKEN_USERS_POSIX.lower(),
    )
    offenders: list[str] = []
    for rel_path, content in _iter_existing_tracked_text_files():
        lowered = content.lower()
        for pat in forbidden:
            if pat in lowered:
                offenders.append(f"{rel_path} (matched: {pat!r})")
                break
    assert not offenders, f"Tracked files contain personal user paths: {offenders}"


def test_req001_no_hardcoded_external_drive_paths_in_tracked_files() -> None:
    forbidden = (
        TOKEN_EXT_WIN.lower(),
        TOKEN_EXT_POSIX.lower(),
    )
    offenders: list[str] = []
    for rel_path, content in _iter_existing_tracked_text_files():
        lowered = content.lower()
        for pat in forbidden:
            if pat in lowered:
                offenders.append(f"{rel_path} (matched: {pat!r})")
                break
    assert not offenders, f"Tracked files contain hardcoded external drive paths: {offenders}"


def test_req001_portable_gemini_task_skill_bundled() -> None:
    skill_script = REPO_ROOT / ".agents" / "skills" / "gemini-task" / "scripts" / "gemini_task.py"
    skill_doc = REPO_ROOT / ".agents" / "skills" / "gemini-task" / "SKILL.md"
    assert skill_script.is_file(), f"Missing bundled skill script: {skill_script}"
    assert skill_doc.is_file(), f"Missing bundled skill doc: {skill_doc}"


def test_req001_scripts_use_dynamic_relative_resolution() -> None:
    overnight = REPO_ROOT / "scripts" / "overnight_task_loop.py"
    ps1_script = REPO_ROOT / "run_claude_live.ps1"
    cmd_script = REPO_ROOT / "run_claude_live.cmd"

    assert overnight.is_file(), "scripts/overnight_task_loop.py must exist"
    overnight_text = overnight.read_text(encoding="utf-8", errors="replace")
    assert "Path(__file__).resolve()" in overnight_text, (
        "scripts/overnight_task_loop.py must resolve paths dynamically via Path(__file__).resolve()"
    )
    assert TOKEN_USERS_WIN.lower() not in overnight_text.lower()
    assert TOKEN_EXT_WIN.lower() not in overnight_text.lower()

    assert ps1_script.is_file(), "run_claude_live.ps1 must exist"
    ps1_text = ps1_script.read_text(encoding="utf-8", errors="replace")
    assert "$PSScriptRoot" in ps1_text, "run_claude_live.ps1 must use $PSScriptRoot"
    assert TOKEN_USERS_WIN.lower() not in ps1_text.lower()

    assert cmd_script.is_file(), "run_claude_live.cmd must exist"
    cmd_text = cmd_script.read_text(encoding="utf-8", errors="replace")
    assert "%~dp0" in cmd_text, "run_claude_live.cmd must use %~dp0"
    assert TOKEN_USERS_WIN.lower() not in cmd_text.lower()


# ============================================================================
# REQ-002: Clean Git Index & .gitignore Boundary Enforcement
# ============================================================================


def test_req002_no_scratch_or_audit_dumps_in_git_index() -> None:
    banned_files = {
        "OVERNIGHT_REPORT.md",
        "QA_AUDIT_REPORT_M21.md",
        "argon_audit_report.md",
        "argon_task_audit_prompt.md",
        "argon_task_audit_sanitized.md",
        "gemini_argon_deep_audit_report.md",
        "test_file_prompt.md",
        "test_out.md",
        "test_resp.md",
        "test_resp_chat.md",
    }
    tracked = set(_git_ls_files())
    present = sorted(banned_files.intersection(tracked))
    assert not present, f"Scratch or audit dumps still tracked in git index: {present}"


def test_req002_no_ephemeral_agent_state_dirs_in_git_index() -> None:
    banned_prefixes = (
        ".supergraph/",
        ".agents-learning/",
        ".monitor/",
        ".agents/teamwork/",
    )
    offenders = [
        path for path in _git_ls_files() if path.startswith(banned_prefixes)
    ]
    assert not offenders, f"Ephemeral agent state files tracked in git index: {offenders}"


def test_req002_gitignore_blocks_ephemeral_and_sensitive_paths() -> None:
    probe_paths = [
        "OVERNIGHT_REPORT.md",
        "argon_audit_report.md",
        ".supergraph/state.json",
        ".agents-learning/expel_rules.json",
        ".monitor/prov_graph.jsonl",
        "gemini_local.db",
        "master.key",
        ".env",
    ]
    not_ignored: list[str] = []
    for probe in probe_paths:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", probe],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            not_ignored.append(probe)
    assert not not_ignored, f"Paths not ignored by .gitignore (or still tracked): {not_ignored}"


def test_req002_claude_and_agent_skills_preserved_in_git_index() -> None:
    tracked = _git_ls_files()
    assert ".claude/settings.json" in tracked, ".claude/settings.json must remain tracked in git"
    skill_docs = [
        p for p in tracked if p.startswith(".agents/skills/") and p.endswith("/SKILL.md")
    ]
    assert skill_docs, "At least one .agents/skills/*/SKILL.md must be tracked in git"


# ============================================================================
# REQ-003: Zero Secrets/DBs/Keys & Valid Open-Source MIT LICENSE
# ============================================================================


def test_req003_no_databases_keys_or_env_files_tracked() -> None:
    banned_suffixes = (
        ".db",
        ".sqlite",
        ".sqlite3",
        ".db-wal",
        ".db-shm",
        ".key",
        ".pem",
    )
    offenders: list[str] = []
    for path in _git_ls_files():
        lower_path = path.lower()
        name = Path(lower_path).name
        if lower_path.endswith(banned_suffixes):
            offenders.append(path)
        elif name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            offenders.append(path)
    assert not offenders, f"Sensitive DB, key, or env files tracked in git: {offenders}"


def test_req003_no_personal_email_or_live_credentials_in_tracked_files() -> None:
    target_email = TOKEN_PERSONAL_EMAIL.lower()
    offenders: list[str] = []
    for rel_path, content in _iter_existing_tracked_text_files():
        if target_email in content.lower():
            offenders.append(rel_path)
    assert not offenders, f"Personal email address leaked in tracked files: {offenders}"


def test_req003_mit_license_present_and_tracked() -> None:
    license_file = REPO_ROOT / "LICENSE"
    assert license_file.is_file(), "LICENSE file must exist at repository root"
    tracked = set(_git_ls_files())
    assert "LICENSE" in tracked, "LICENSE must be tracked in git index"
    text = license_file.read_text(encoding="utf-8", errors="replace")
    assert "MIT License" in text, "LICENSE must contain 'MIT License'"
    assert "Permission is hereby granted, free of charge" in text, (
        "LICENSE must contain standard MIT grant clause"
    )


# ============================================================================
# REQ-004: Sanitized Git Commit History & Anonymized Author Metadata
# ============================================================================


def test_req004_git_history_free_of_teamwork_and_scratch_dumps() -> None:
    history_names = _git_output("log", "--all", "--pretty=format:", "--name-only")
    offenders: set[str] = set()
    for line in history_names.splitlines():
        clean = line.strip().replace("\\", "/")
        if not clean:
            continue
        if clean.startswith(".agents/teamwork/") or clean in {
            "argon_audit_report.md",
            "OVERNIGHT_REPORT.md",
        }:
            offenders.add(clean)
    assert not offenders, f"Historical commits still contain banned paths: {sorted(offenders)}"


def test_req004_git_history_diffs_free_of_personal_paths() -> None:
    history_diffs = _git_output("log", "-p", "--all").lower()
    has_personal_user = TOKEN_FULL_USER.lower() in history_diffs
    has_personal_email = TOKEN_PERSONAL_EMAIL.lower() in history_diffs
    assert not has_personal_user, (
        "Git commit history diffs still contain personal user name/path"
    )
    assert not has_personal_email, (
        "Git commit history diffs still contain personal email address"
    )


def test_req004_git_commit_authors_anonymized() -> None:
    emails_out = _git_output("log", "--all", "--format=%ae")
    emails = [line.strip() for line in emails_out.splitlines() if line.strip()]
    assert emails, "Git history must contain at least one commit"
    invalid_emails = [e for e in emails if e != EXPECTED_ANON_EMAIL]
    assert not invalid_emails, (
        f"Non-anonymized commit author emails found in git history: {set(invalid_emails)}"
    )


# ============================================================================
# REQ-005: 100% English-Only Standardization Across Tracked Files & Git History
# ============================================================================


def test_req005_zero_cyrillic_in_all_tracked_files() -> None:
    offenders: list[str] = []
    for rel_path, content in _iter_existing_tracked_text_files():
        matches = CYRILLIC_RE.findall(content)
        if matches:
            offenders.append(f"{rel_path} ({len(matches)} Cyrillic chars)")
    assert not offenders, (
        f"Tracked files contain non-English Cyrillic characters: {offenders}"
    )


def test_req005_english_ui_and_extension_strings() -> None:
    targets = [
        "frontend/src/components/PromptInput.tsx",
        "frontend/src/components/Sidebar.tsx",
        "extensions/gemini-cookie-sync-extension/popup.html",
        "docs/argon_auth_persistence_plan.md",
    ]
    english_word_re = re.compile(r"[A-Za-z]{4,}")
    offenders: list[str] = []
    for rel_path in targets:
        full_path = REPO_ROOT / rel_path
        assert full_path.is_file(), f"Expected target file missing: {rel_path}"
        text = full_path.read_text(encoding="utf-8", errors="replace")
        assert english_word_re.search(text), (
            f"Target file {rel_path} must contain English UI/header text"
        )
        matches = CYRILLIC_RE.findall(text)
        if matches:
            offenders.append(f"{rel_path} ({len(matches)} Cyrillic chars)")
    assert not offenders, (
        f"UI/Extension/Doc target files still contain Cyrillic text: {offenders}"
    )


def test_req005_zero_cyrillic_in_git_commit_history() -> None:
    history_diffs = _git_output("log", "-p", "--all")
    has_cyrillic = bool(CYRILLIC_RE.search(history_diffs))
    assert not has_cyrillic, (
        "Git commit history (git log -p --all) still contains Cyrillic characters"
    )

