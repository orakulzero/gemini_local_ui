"""
backend/app/bridge/task_pipeline.py
M30: Autonomous Gemini 4 Argon Task Mode Pipeline, Scheduled Orchestrator & Self-Healing Verifier.
Implements REQ-PIPE-01, REQ-PIPE-02, REQ-PIPE-03, REQ-PIPE-04, REQ-PIPE-05.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
import difflib
import inspect
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable
import uuid
import zipfile

logger = logging.getLogger("gemini_backend.task_pipeline")

PRUNED_DIRS: set[str] = {
    ".git",
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".supergraph",
    ".agents-learning",
    ".monitor",
}

REQ_TAG_PATTERN = re.compile(r"\bREQ-[A-Z0-9]+(?:-[A-Z0-9]+)*\b")
CYRILLIC_PATTERN = re.compile(r"[\u0400-\u04FF]")
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("github_pat", re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("local_user_path", re.compile(r"(?:[A-Za-z]:\\Users\\[A-Za-z0-9_ .-]+|/home/[A-Za-z0-9_.-]+)")),
]


def _extract_python_ast_contract(source: str) -> str:
    """REQ-PIPE-01: Extracts top-level class and function signature contracts from Python source."""
    tree = ast.parse(source)
    lines: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            args_str = ast.unparse(node.args)
            ret_str = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
            lines.append(f"{prefix} {node.name}({args_str}){ret_str}:")
        elif isinstance(node, ast.ClassDef):
            bases = [ast.unparse(b) for b in node.bases]
            base_str = f"({', '.join(bases)})" if bases else ""
            lines.append(f"class {node.name}{base_str}:")
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sprefix = "async def" if isinstance(sub, ast.AsyncFunctionDef) else "def"
                    sargs = ast.unparse(sub.args)
                    sret = f" -> {ast.unparse(sub.returns)}" if sub.returns is not None else ""
                    lines.append(f"    {sprefix} {sub.name}({sargs}){sret}:")
    return "\n".join(lines)


def pack_workspace_context(
    workspace_dir: str | Path,
    target_files: list[str] | None = None,
) -> dict[str, Any]:
    """
    REQ-PIPE-01: Bundles workspace files into an in-memory .zip archive, excluding noise
    directories, and extracts AST signature contracts and REQ-* traceability tags.
    """
    root = Path(workspace_dir).resolve()
    buf = io.BytesIO()
    ast_contracts: dict[str, str] = {}
    req_tags_set: set[str] = set()
    included_files: list[str] = []
    target_norm = {p.replace("\\", "/").lstrip("/") for p in (target_files or [])}

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in sorted(dirnames)
                if d not in PRUNED_DIRS and not d.endswith(".egg-info")
            ]
            for fname in sorted(filenames):
                if fname.endswith((".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe")):
                    continue
                fpath = Path(dirpath) / fname
                rel_path = fpath.relative_to(root).as_posix()
                if any(part in PRUNED_DIRS for part in Path(rel_path).parts):
                    continue
                try:
                    raw_bytes = fpath.read_bytes()
                except OSError:
                    continue
                zf.writestr(rel_path, raw_bytes)
                included_files.append(rel_path)

                try:
                    text = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    continue

                for match in REQ_TAG_PATTERN.findall(text):
                    req_tags_set.add(match)

                if rel_path.endswith(".py") and (not target_norm or rel_path in target_norm or rel_path.startswith("src/")):
                    try:
                        contract = _extract_python_ast_contract(text)
                        if contract:
                            ast_contracts[rel_path] = contract
                    except SyntaxError:
                        pass

    zip_bytes = buf.getvalue()
    return {
        "zip_filename": f"{root.name or 'workspace'}_context.zip",
        "zip_bytes": zip_bytes,
        "size_bytes": len(zip_bytes),
        "file_count": len(included_files),
        "files": included_files,
        "ast_contracts": ast_contracts,
        "req_tags": sorted(req_tags_set),
    }


def extract_code_files_from_response(
    text: str,
    orig_files: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    REQ-PIPE-03, REQ-PIPE-07: Extracts multi-file code blocks and targeted Canvas RangeEdit blocks
    from Gemini Task Mode Markdown responses.
    Supports '### File: <path>', '**File:** <path>', '```lang:<path>', '<file path="...">',
    and '### RangeEdit: <path>:<start_line>-<end_line>'.
    """
    extracted: dict[str, str] = {}
    if not text:
        return extracted

    header_Fence = re.compile(
        r"(?:^|\n)(?:#{1,4}\s*File:\s*|\*\*File:\*\*\s*)`?([^\n`*]+?)`?\s*\n```[^\n]*\n(.*?)```",
        re.DOTALL,
    )
    for match in header_Fence.finditer(text):
        rel = match.group(1).strip().replace("\\", "/").lstrip("/")
        code = match.group(2)
        if rel:
            extracted[rel] = code if code.endswith("\n") else code + "\n"

    inline_fence = re.compile(r"```[a-zA-Z0-9_+-]+:([^\n`]+)\n(.*?)```", re.DOTALL)
    for match in inline_fence.finditer(text):
        rel = match.group(1).strip().replace("\\", "/").lstrip("/")
        code = match.group(2)
        if rel and rel not in extracted:
            extracted[rel] = code if code.endswith("\n") else code + "\n"

    xml_file = re.compile(r'<file\s+path=["\']([^"\']+)["\']>\s*\n?(.*?)</file>', re.DOTALL)
    for match in xml_file.finditer(text):
        rel = match.group(1).strip().replace("\\", "/").lstrip("/")
        code = match.group(2)
        if rel and rel not in extracted:
            extracted[rel] = code if code.endswith("\n") else code + "\n"

    range_fence = re.compile(
        r"(?:^|\n)(?:#{1,4}\s*RangeEdit:\s*|\*\*RangeEdit:\*\*\s*)`?([^\n:`*]+):(\d+)-(\d+)`?\s*\n```[^\n]*\n(.*?)```",
        re.DOTALL,
    )
    if range_fence.search(text):
        from backend.app.bridge.gemini_capabilities import CanvasSessionManager

        canvas_mgr = CanvasSessionManager()
        base_map = dict(orig_files or {})
        for match in range_fence.finditer(text):
            rel = match.group(1).strip().replace("\\", "/").lstrip("/")
            start_line = int(match.group(2))
            end_line = int(match.group(3))
            repl_code = match.group(4)
            base_content = extracted.get(rel) or base_map.get(rel)
            if rel and base_content is not None:
                doc = canvas_mgr.create_document(filename=rel, content=base_content)
                updated_doc = canvas_mgr.apply_range_edit(
                    doc["doc_id"],
                    start_line=start_line,
                    end_line=end_line,
                    replacement_content=repl_code,
                )
                extracted[rel] = str(updated_doc["content"])

    return extracted


def discover_workspace_skills_and_rules(workspace_dir: str | Path) -> dict[str, Any]:
    """
    REQ-PIPE-08: Scans workspace_dir for local skills (skills/*/SKILL.md, .claude/skills/*/SKILL.md)
    and engineering rules (rules/*.md, .claude/rules/*.md, AGENTS.md, CLAUDE.md), plus saved memories.
    """
    root = Path(workspace_dir).resolve()
    skills: list[dict[str, str]] = []
    rules: list[str] = []

    skill_bases = [root / "skills", root / ".claude" / "skills", root / ".agents" / "skills"]
    for sbase in skill_bases:
        if not sbase.is_dir():
            continue
        for skill_md in sorted(sbase.glob("*/SKILL.md")):
            try:
                raw = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name = skill_md.parent.name
            desc = ""
            body = raw
            if raw.startswith("---"):
                parts = raw.split("---", 2)
                if len(parts) >= 3:
                    front = parts[1]
                    body = parts[2]
                    for line in front.splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            if k.strip() == "name" and v.strip():
                                name = v.strip()
                            elif k.strip() == "description" and v.strip():
                                desc = v.strip()
            skills.append(
                {
                    "name": name,
                    "description": desc,
                    "instructions": body.strip()[:600],
                    "path": skill_md.relative_to(root).as_posix(),
                }
            )

    rule_files: list[Path] = [root / "AGENTS.md", root / "CLAUDE.md"]
    for rbase in (root / "rules", root / ".claude" / "rules", root / ".agents" / "rules"):
        if rbase.is_dir():
            rule_files.extend(sorted(rbase.glob("*.md")))

    for rfile in rule_files:
        if rfile.is_file():
            try:
                txt = rfile.read_text(encoding="utf-8", errors="replace").strip()
                if txt:
                    rules.append(txt[:800])
            except OSError:
                pass

    try:
        from backend.app.bridge.gemini_capabilities import get_saved_info_manager

        for mem in get_saved_info_manager().list_memories():
            c = str(mem.get("content", "")).strip()
            if c:
                rules.append(c)
    except Exception:
        pass

    prompt_lines = ["# Active Skills & Rules"]
    for s in skills:
        prompt_lines.append(f"- Skill [{s['name']}]: {s['description']} — {s['instructions']}")
    for r in rules:
        prompt_lines.append(f"- Rule: {r}")

    return {
        "skills": skills,
        "rules": rules,
        "prompt_block": "\n".join(prompt_lines),
    }


def extract_mcp_tool_calls(text: str) -> list[dict[str, Any]]:
    """REQ-PIPE-08: Extracts JSON tool call payloads from ```tool_call fenced blocks."""
    calls: list[dict[str, Any]] = []
    if not text:
        return calls
    pattern = re.compile(r"```tool_call\s*\n(.*?)```", re.DOTALL)
    for match in pattern.finditer(text):
        raw_json = match.group(1).strip()
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict) and parsed.get("name"):
                calls.append(
                    {
                        "name": str(parsed["name"]),
                        "arguments": dict(parsed.get("arguments") or {}),
                    }
                )
        except Exception:
            continue
    return calls


async def verify_candidate_drafts_in_sandbox(
    drafts: list[str],
    client: Any | None = None,
) -> dict[str, Any]:
    """
    REQ-PIPE-10: Evaluates multiple candidate drafts using AST syntax validation and
    Cloud Python Sandbox execution, returning the highest-scoring draft and verification metrics.
    """
    from backend.app.bridge.gemini_capabilities import run_cloud_python_sandbox

    fenced_py_re = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)
    evaluations: list[dict[str, Any]] = []

    for idx, draft in enumerate(drafts):
        text = str(draft or "")
        extracted = extract_code_files_from_response(text)
        py_snippets = [code for rel, code in extracted.items() if rel.endswith(".py")]
        if not py_snippets:
            py_snippets = [m.group(1) for m in fenced_py_re.finditer(text) if m.group(1).strip()]

        syntax_ok = False
        sandbox_ok = False
        score = 0.0

        if py_snippets:
            all_valid_ast = True
            for snip in py_snippets:
                try:
                    ast.parse(snip)
                except SyntaxError:
                    all_valid_ast = False
                    break
            if all_valid_ast:
                syntax_ok = True
                combined_code = "\n\n".join(py_snippets)
                sbx_res = await run_cloud_python_sandbox(code=combined_code, client=client)
                sandbox_ok = bool(sbx_res.get("ok", False))
                score = 1.0 if sandbox_ok else 0.5
            else:
                score = 0.0
        else:
            score = 0.25 if text.strip() else 0.0

        evaluations.append(
            {
                "index": idx,
                "syntax_ok": syntax_ok,
                "sandbox_ok": sandbox_ok,
                "score": score,
            }
        )

    verified_count = sum(1 for ev in evaluations if ev["syntax_ok"] and ev["sandbox_ok"])
    best_index = 0
    if evaluations:
        best_ev = max(
            evaluations,
            key=lambda ev: (float(ev["score"]), len(str(drafts[int(ev["index"])] or ""))),
        )
        best_index = int(best_ev["index"])

    best_draft = str(drafts[best_index]) if drafts and 0 <= best_index < len(drafts) else ""
    return {
        "ok": True,
        "best_draft": best_draft,
        "best_index": best_index,
        "verified_count": verified_count,
        "evaluations": evaluations,
    }


def _extract_top_level_symbol_map(source: str) -> dict[str, tuple[str, int]]:
    """Extracts top-level function and class names with parameter counts from Python AST."""
    tree = ast.parse(source)
    symbols: dict[str, tuple[str, int]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arg_count = len(node.args.posonlyargs) + len(node.args.args) + len(node.args.kwonlyargs)
            symbols[node.name] = ("func", arg_count)
        elif isinstance(node, ast.ClassDef):
            symbols[node.name] = ("class", 0)
    return symbols


def verify_five_quality_gates(
    workspace_dir: str | Path,
    orig_files: dict[str, str],
    candidate_files: dict[str, str],
    run_pytest: bool = True,
    pytest_args: list[str] | None = None,
) -> dict[str, Any]:
    """
    REQ-PIPE-04: Evaluates the 5 Quality Gates in an isolated sandbox without modifying workspace_dir:
      1. gate1_pytest: pytest passes in an isolated temporary copy of workspace_dir.
      2. gate2_secrets: zero secrets (PATs, API keys, private keys) or hardcoded user paths.
      3. gate3_ast_req: valid Python AST, 100% REQ-* tag preservation, and top-level signature preservation.
      4. gate4_english: zero Cyrillic characters (\\u0400-\\u04FF).
      5. gate5_line_cap: >= 95% non-empty line retention on existing files with >= 20 non-empty lines.
    """
    diagnostics: list[str] = []
    gate2_secrets = True
    gate3_ast_req = True
    gate4_english = True
    gate5_line_cap = True

    if not candidate_files:
        diagnostics.append("gate3_ast_req: candidate_files is empty")
        gate3_ast_req = False

    for rel_path, new_code in candidate_files.items():
        # Gate 2: Secret & path hygiene
        for label, pat in SECRET_PATTERNS:
            if pat.search(new_code):
                gate2_secrets = False
                diagnostics.append(f"gate2_secrets: detected {label} in {rel_path}")

        # Gate 4: 100% English-only check
        if CYRILLIC_PATTERN.search(new_code):
            gate4_english = False
            diagnostics.append(f"gate4_english: detected Cyrillic characters in {rel_path}")

        # Gate 3 (part A): Python AST syntax check
        if rel_path.endswith(".py"):
            try:
                ast.parse(new_code)
            except SyntaxError as exc:
                gate3_ast_req = False
                diagnostics.append(f"gate3_ast_req: SyntaxError in {rel_path}: {exc}")

    for rel_path, old_code in orig_files.items():
        if rel_path not in candidate_files:
            continue
        new_code = candidate_files[rel_path]

        # Gate 3 (part B): REQ-* tag preservation
        old_reqs = set(REQ_TAG_PATTERN.findall(old_code))
        new_reqs = set(REQ_TAG_PATTERN.findall(new_code))
        missing_reqs = sorted(old_reqs - new_reqs)
        if missing_reqs:
            gate3_ast_req = False
            diagnostics.append(
                f"gate3_ast_req: missing requirement tags in {rel_path}: {', '.join(missing_reqs)}"
            )

        # Gate 3 (part C): Top-level function/class symbol preservation
        if rel_path.endswith(".py") and gate3_ast_req:
            try:
                old_syms = _extract_top_level_symbol_map(old_code)
                new_syms = _extract_top_level_symbol_map(new_code)
                for sym_name, (sym_kind, old_argc) in old_syms.items():
                    if sym_name not in new_syms:
                        gate3_ast_req = False
                        diagnostics.append(
                            f"gate3_ast_req: missing top-level {sym_kind} '{sym_name}' in {rel_path}"
                        )
                    elif sym_kind == "func" and new_syms[sym_name][1] < old_argc:
                        gate3_ast_req = False
                        diagnostics.append(
                            f"gate3_ast_req: signature regression on '{sym_name}' in {rel_path}"
                        )
            except SyntaxError:
                gate3_ast_req = False

        # Gate 5: Non-empty line count retention (>= 95% for files with >= 20 non-empty lines)
        old_nonempty = [ln for ln in old_code.splitlines() if ln.strip()]
        new_nonempty = [ln for ln in new_code.splitlines() if ln.strip()]
        if len(old_nonempty) >= 20:
            min_required = int(len(old_nonempty) * 0.95)
            if len(new_nonempty) < min_required:
                gate5_line_cap = False
                diagnostics.append(
                    f"gate5_line_cap: {rel_path} non-empty lines dropped from "
                    f"{len(old_nonempty)} to {len(new_nonempty)} (min required: {min_required})"
                )

    # Gate 1: Isolated sandbox pytest execution
    gate1_pytest = True
    pytest_output = ""
    if run_pytest:
        root = Path(workspace_dir).resolve()
        with tempfile.TemporaryDirectory(prefix="m30_Verify_") as tmp_sandbox:
            sandbox_root = Path(tmp_sandbox) / "workspace"
            shutil.copytree(
                root,
                sandbox_root,
                ignore=shutil.ignore_patterns(*PRUNED_DIRS, "*.pyc", "*.pyo"),
                dirs_exist_ok=True,
            )
            for rel_path, new_code in candidate_files.items():
                dest = sandbox_root / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(new_code, encoding="utf-8")

            env = os.environ.copy()
            env["PYTHONPATH"] = str(sandbox_root)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            cmd = [sys.executable, "-m", "pytest", "-q", *(pytest_args or ["tests"])]
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(sandbox_root),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                pytest_output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
                if proc.returncode != 0:
                    gate1_pytest = False
                    diagnostics.append(f"gate1_pytest: pytest failed (exit {proc.returncode}):\n{pytest_output}")
            except Exception as exc:
                gate1_pytest = False
                pytest_output = str(exc)
                diagnostics.append(f"gate1_pytest: pytest execution error: {exc}")

    gates = {
        "gate1_pytest": gate1_pytest,
        "gate2_secrets": gate2_secrets,
        "gate3_ast_req": gate3_ast_req,
        "gate4_english": gate4_english,
        "gate5_line_cap": gate5_line_cap,
    }
    return {
        "passed": all(gates.values()),
        "gates": gates,
        "diagnostics": diagnostics,
        "pytest_output": pytest_output,
    }


def _compute_unified_diff(orig_files: dict[str, str], candidate_files: dict[str, str]) -> str:
    """REQ-PIPE-05: Computes a unified diff across all candidate files against original files."""
    diff_chunks: list[str] = []
    for rel_path, new_code in sorted(candidate_files.items()):
        old_code = orig_files.get(rel_path, "")
        old_lines = old_code.splitlines(keepends=True)
        new_lines = new_code.splitlines(keepends=True)
        udiff = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
            )
        )
        if udiff:
            diff_chunks.append("".join(udiff))
    return "\n".join(diff_chunks)


class TaskPipelineOrchestrator:
    """
    REQ-PIPE-02, REQ-PIPE-03, REQ-PIPE-05:
    Scheduled Orchestrator, Stateful CID/RID/RCID Self-Healing Task-Mode Runner,
    and Stage-Only Human-in-the-Loop Approval Manager.
    """

    def __init__(self, storage_path: str | Path | None = None) -> None:
        if storage_path is None:
            base_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "gemini_local_ui"
            storage_path = base_dir / "task_pipeline_state.json"
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._state: dict[str, Any] = {
            "schedules": {},
            "runs": {},
            "cloud_actions": [],
        }
        self._load_state()

    def _load_state(self) -> None:
        if self.storage_path.exists():
            try:
                raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._state["schedules"] = raw.get("schedules", {})
                    self._state["runs"] = raw.get("runs", {})
                    self._state["cloud_actions"] = raw.get("cloud_actions", [])
            except Exception as exc:
                logger.warning("Failed loading task pipeline state from %s: %s", self.storage_path, exc)

    def _save_state(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.storage_path.write_text(
                json.dumps(self._state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed saving task pipeline state to %s: %s", self.storage_path, exc)

    # ------------------------------------------------------------------
    # REQ-PIPE-02: Schedule CRUD & Due Trigger Checks
    # ------------------------------------------------------------------
    def create_schedule(
        self,
        name: str,
        workspace_dir: str,
        prompt: str,
        schedule_type: str = "interval",
        interval_seconds: int = 3600,
        cron_expr: str | None = None,
        target_files: list[str] | None = None,
        model: str = "gemini-4-argon",
        thinking: int = 2,
        use_ultra_2m: bool = True,
        gem_id: str | None = "gem_ultra_pipeline",
        enable_mcp_tools: bool = True,
        max_heal_attempts: int = 2,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """REQ-PIPE-02, REQ-PIPE-07, REQ-PIPE-08: Creates a recurring Task-Mode schedule (cron or interval)."""
        now_ts = time.time()
        schedule_id = f"sched_{uuid.uuid4().hex[:10]}"
        record: dict[str, Any] = {
            "schedule_id": schedule_id,
            "name": name,
            "workspace_dir": str(workspace_dir),
            "prompt": prompt,
            "schedule_type": schedule_type,
            "interval_seconds": max(10, int(interval_seconds)),
            "cron_expr": cron_expr or "",
            "target_files": list(target_files or []),
            "model": model,
            "thinking": int(thinking),
            "use_ultra_2m": bool(use_ultra_2m),
            "gem_id": gem_id,
            "enable_mcp_tools": bool(enable_mcp_tools),
            "max_heal_attempts": int(max_heal_attempts),
            "enabled": bool(enabled),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_run_ts": 0.0,
            "next_run_ts": now_ts + max(10, int(interval_seconds)),
            "last_run_id": None,
        }
        self._state["schedules"][schedule_id] = record
        self._save_state()
        return dict(record)

    def list_schedules(self) -> list[dict[str, Any]]:
        """REQ-PIPE-02: Lists all configured Task-Mode schedules."""
        return [dict(v) for v in self._state["schedules"].values()]

    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        rec = self._state["schedules"].get(schedule_id)
        return dict(rec) if rec else None

    def update_schedule(self, schedule_id: str, **updates: Any) -> dict[str, Any]:
        """REQ-PIPE-02: Updates or pauses/resumes an existing schedule."""
        if schedule_id not in self._state["schedules"]:
            raise KeyError(f"Schedule '{schedule_id}' not found")
        rec = self._state["schedules"][schedule_id]
        for k, v in updates.items():
            if v is not None and k in rec:
                rec[k] = v
        self._save_state()
        return dict(rec)

    def delete_schedule(self, schedule_id: str) -> bool:
        """REQ-PIPE-02: Deletes a schedule by ID."""
        removed = self._state["schedules"].pop(schedule_id, None)
        if removed is not None:
            self._save_state()
            return True
        return False

    def get_due_schedules(self, now_ts: float | None = None) -> list[dict[str, Any]]:
        """REQ-PIPE-02: Returns enabled schedules whose next_run_ts is due."""
        ts = now_ts if now_ts is not None else time.time()
        due: list[dict[str, Any]] = []
        for rec in self._state["schedules"].values():
            if rec.get("enabled", True) and ts >= float(rec.get("next_run_ts", 0.0)):
                due.append(dict(rec))
        return due


    async def _default_live_task_runner(
        self,
        prompt: str,
        bundle: dict[str, Any],
        model: str,
        thinking: int,
        cid: str | None,
        rid: str | None,
        rcid: str | None,
        is_first_turn: bool,
        use_ultra_2m: bool = True,
        gem_id: str | None = None,
    ) -> dict[str, Any]:
        """
        REQ-PIPE-03, REQ-PIPE-07: Executes live Gemini 4 Argon Task Mode (feature_mode=40) via
        CredentialVault + SessionManager + GeminiUploader + GeminiWebClient.
        """
        from backend.app.bridge.client import GeminiWebClient
        from backend.app.bridge.gemini_capabilities import get_saved_info_manager
        from backend.app.bridge.session import SessionManager
        from backend.app.bridge.uploader import GeminiUploader
        from backend.app.storage.vault import CredentialVault

        vault = CredentialVault()
        sm = SessionManager(vault=vault)
        vdata = vault.load_vault_data()
        if vdata and vdata.accounts:
            await sm.initialize_all()
        else:
            stored = vault.load_credentials()
            if stored and isinstance(stored, dict):
                await sm.init_session(stored)

        if not sm.get_health().is_authenticated:
            await sm.close()
            return {
                "cid": cid or f"c_unauth_{uuid.uuid4().hex[:8]}",
                "rid": "r_unauth",
                "rcid": "rc_unauth",
                "text": "",
                "draft_candidates": [],
                "code_executions": [],
            }

        client = GeminiWebClient(
            session_context=sm.context,
            base_url=sm.base_url,
            vault=vault,
            account_id=sm.active_account_id,
            timeout=360.0,
        )
        try:
            att_list: list[dict[str, Any]] | None = None
            full_prompt = prompt
            if is_first_turn and bundle.get("zip_bytes"):
                try:
                    uploader = GeminiUploader(session_context=sm.context, base_url=sm.base_url)
                    uri = await uploader.upload_file(
                        data=bundle["zip_bytes"],
                        filename=bundle["zip_filename"],
                        mime_type="application/zip",
                    )
                    att_list = [{"uri": uri, "filename": bundle["zip_filename"], "scotty": True}]
                except Exception as up_err:
                    logger.debug("Scotty zip upload skipped (using inline Ultra 2M AST context): %s", up_err)
                    att_list = None
                contracts_summary = "\n".join(
                    f"# {k}\n{v}" for k, v in list(bundle.get("ast_contracts", {}).items())[:15]
                )
                req_summary = ", ".join(bundle.get("req_tags", [])) or "None"
                saved_memories = get_saved_info_manager().list_memories()
                rules_block = "\n".join(f"- {m.get('content', '')}" for m in saved_memories if m.get("content"))
                full_prompt = (
                    f"[Task Mode Context Archive Attached: {bundle['zip_filename']} | Ultra 2M={use_ultra_2m}]\n"
                    f"Required REQ-* Tags to Preserve: {req_summary}\n"
                    f"Saved Account Engineering Rules:\n{rules_block or 'None'}\n"
                    f"AST Signature Contracts:\n```python\n{contracts_summary[:6000]}\n```\n\n"
                    f"Task Instruction:\n{prompt}\n\n"
                    "Output modified/created files using '### File: <rel_path>' or targeted "
                    "'### RangeEdit: <rel_path>:<start_line>-<end_line>' followed by a ```python code block."
                )

            text_parts: list[str] = []
            out_cid = cid
            out_rid = rid
            out_rcid = rcid
            draft_candidates: list[dict[str, Any]] = []
            code_executions: list[dict[str, Any]] = []
            eff_model = f"{model}@thinking={thinking}@mode=task"
            async for chunk in client.generate_stream(
                prompt=full_prompt,
                model=eff_model,
                cid=cid,
                rid=rid,
                rcid=rcid,
                attachments=att_list,
                gem_id=gem_id,
                feature_mode=40,
            ):
                if getattr(chunk, "text_delta", ""):
                    text_parts.append(chunk.text_delta)
                if getattr(chunk, "cid", None):
                    out_cid = chunk.cid
                if getattr(chunk, "rid", None):
                    out_rid = chunk.rid
                if getattr(chunk, "rcid", None):
                    out_rcid = chunk.rcid
                if getattr(chunk, "type", "") == "done" and isinstance(getattr(chunk, "metadata", None), dict):
                    meta = chunk.metadata
                    draft_candidates = list(meta.get("draft_candidates") or [])
                    code_executions = list(meta.get("code_executions") or [])
            return {
                "cid": out_cid or cid or f"c_task_{uuid.uuid4().hex[:8]}",
                "rid": out_rid or rid or "r_live",
                "rcid": out_rcid or rcid or "rc_live",
                "text": "".join(text_parts),
                "draft_candidates": draft_candidates,
                "code_executions": code_executions,
            }
        except Exception as exc:
            logger.warning("Live Task-Mode runner error: %s", exc)
            return {
                "cid": cid or f"c_err_{uuid.uuid4().hex[:8]}",
                "rid": rid or "r_err",
                "rcid": rcid or "rc_err",
                "text": "",
                "draft_candidates": [],
                "code_executions": [],
            }
        finally:
            await client.close()
            await sm.close()

    async def run_schedule_now(
        self,
        schedule_id: str,
        run_pytest: bool = True,
        task_runner: Callable[..., Any] | None = None,
        mock_candidate_files: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """REQ-PIPE-02, REQ-PIPE-09, REQ-PIPE-10: Immediately executes a specific schedule by schedule_id."""
        sched = self.get_schedule(schedule_id)
        if sched is None:
            raise KeyError(f"Schedule '{schedule_id}' not found")
        return await self.execute_pipeline_run(
            workspace_dir=sched["workspace_dir"],
            prompt=sched["prompt"],
            target_files=sched.get("target_files") or None,
            model=sched.get("model", "gemini-4-argon"),
            thinking=int(sched.get("thinking", 2)),
            use_ultra_2m=bool(sched.get("use_ultra_2m", True)),
            gem_id=sched.get("gem_id"),
            enable_mcp_tools=bool(sched.get("enable_mcp_tools", True)),
            max_heal_attempts=int(sched.get("max_heal_attempts", 2)),
            run_pytest=run_pytest,
            task_runner=task_runner,
            mock_candidate_files=mock_candidate_files,
            schedule_id=schedule_id,
            ast_compress_unchanged=bool(sched.get("ast_compress_unchanged", True)),
        )

    async def trigger_due_schedules(
        self,
        now_ts: float | None = None,
        task_runner: Callable[..., Any] | None = None,
    ) -> list[dict[str, Any]]:
        """REQ-PIPE-02: Executes all currently due enabled schedules and returns their run records."""
        due_list = self.get_due_schedules(now_ts=now_ts)
        executed_runs: list[dict[str, Any]] = []
        for sched in due_list:
            run_rec = await self.run_schedule_now(
                schedule_id=sched["schedule_id"],
                task_runner=task_runner,
            )
            executed_runs.append(run_rec)
        return executed_runs
    def _read_original_files(self, workspace_dir: Path, target_files: list[str] | None) -> dict[str, str]:
        orig: dict[str, str] = {}
        if target_files:
            for rel in target_files:
                norm = rel.replace("\\", "/").lstrip("/")
                fpath = workspace_dir / norm
                if fpath.is_file():
                    orig[norm] = fpath.read_text(encoding="utf-8", errors="replace")
        else:
            for dirpath, dirnames, filenames in os.walk(workspace_dir):
                dirnames[:] = [d for d in dirnames if d not in PRUNED_DIRS]
                for fn in filenames:
                    if fn.endswith(".py"):
                        fpath = Path(dirpath) / fn
                        norm = fpath.relative_to(workspace_dir).as_posix()
                        orig[norm] = fpath.read_text(encoding="utf-8", errors="replace")
        return orig

    async def _invoke_runner(
        self,
        runner: Callable[..., Any],
        prompt: str,
        attachments: list[dict[str, Any]] | None,
        cid: str | None,
        rid: str | None,
        rcid: str | None,
    ) -> dict[str, Any]:
        res = runner(prompt=prompt, attachments=attachments, cid=cid, rid=rid, rcid=rcid)
        if inspect.isawaitable(res):
            res = await res
        return dict(res)

    async def execute_pipeline_run(
        self,
        workspace_dir: str | Path,
        prompt: str,
        target_files: list[str] | None = None,
        model: str = "gemini-4-argon",
        thinking: int = 2,
        use_ultra_2m: bool = True,
        gem_id: str | None = None,
        enable_mcp_tools: bool = True,
        max_heal_attempts: int = 2,
        run_pytest: bool = True,
        task_runner: Callable[..., Any] | None = None,
        mock_candidate_files: dict[str, str] | None = None,
        schedule_id: str | None = None,
        ast_compress_unchanged: bool = True,
    ) -> dict[str, Any]:
        """
        REQ-PIPE-03, REQ-PIPE-05, REQ-PIPE-07, REQ-PIPE-08, REQ-PIPE-09, REQ-PIPE-10:
        Executes an isolated Gemini 4 Argon Task Mode run with stateful CID/RID/RCID self-healing,
        Ultra 5x profile + 2M AST-compressed context metadata, speculative sandbox draft verification,
        Canvas RangeEdit patching, workspace skills/rules, Gem persona routing, and in-process MCP tool loop.
        """
        from backend.app.bridge.gemini_capabilities import get_response_quality_engine
        from backend.app.bridge.ultra_capabilities import (
            UltraSubscriptionManager,
            pack_ultra_2m_repository_context,
        )

        root = Path(workspace_dir).resolve()
        orig_files = self._read_original_files(root, target_files)
        bundle = pack_workspace_context(root, target_files=target_files)
        ultra_profile = UltraSubscriptionManager().detect_subscription_profile()
        ultra_2m_meta = (
            pack_ultra_2m_repository_context(
                root,
                ast_compress_unchanged=ast_compress_unchanged,
                full_detail_files=target_files,
            )
            if use_ultra_2m
            else {"ast_compressed_files": 0}
        )
        discovered = discover_workspace_skills_and_rules(root)
        active_skills = [str(s.get("name", "")) for s in discovered["skills"] if s.get("name")]
        mcp_tool_calls_log: list[dict[str, Any]] = []

        cid: str | None = None
        rid: str | None = None
        rcid: str | None = None
        selected_rcid: str | None = None
        double_check_trust: float = 0.95
        heal_attempts = 0
        candidate_files: dict[str, str] = {}
        gates_result: dict[str, Any] = {}
        speculative_sandbox_verified = False

        if mock_candidate_files is not None:
            candidate_files = {
                k.replace("\\", "/").lstrip("/"): v for k, v in mock_candidate_files.items()
            }
            mock_draft_str = "\n".join(
                f"### File: {k}\n```python\n{v}\n```" for k, v in candidate_files.items()
            )
            sbx_check = await verify_candidate_drafts_in_sandbox([mock_draft_str])
            speculative_sandbox_verified = bool(sbx_check.get("ok", True))
            gates_result = verify_five_quality_gates(
                workspace_dir=root,
                orig_files=orig_files,
                candidate_files=candidate_files,
                run_pytest=run_pytest,
            )
            cid = "c_mock_pipeline"
            rid = "r_mock_1"
            rcid = "rc_mock_1"
            selected_rcid = rcid
        else:
            attachments = [
                {
                    "filename": bundle["zip_filename"],
                    "size_bytes": bundle["size_bytes"],
                    "ast_contracts": bundle["ast_contracts"],
                    "req_tags": bundle["req_tags"],
                    "ultra_2m_max_tokens": ultra_2m_meta.get("max_context_tokens", 2_000_000),
                    "ultra_2m_estimated_tokens": ultra_2m_meta.get("estimated_tokens", 0),
                }
            ]
            current_prompt = prompt
            if discovered["skills"] or discovered["rules"] or enable_mcp_tools:
                mcp_tools_section = (
                    "# Available MCP Tools\n"
                    "- gemini_capabilities\n"
                    "- gemini_task_pipeline\n"
                    "- notebooklm_query\n"
                    'Emit ```tool_call\n{"name": "...", "arguments": {...}}\n``` to invoke an MCP tool before outputting code.'
                )
                current_prompt = f"{prompt}\n\n{discovered['prompt_block']}\n\n{mcp_tools_section}"
            q_engine = get_response_quality_engine()
            for attempt in range(max_heal_attempts + 1):
                if task_runner is not None:
                    resp = await self._invoke_runner(
                        task_runner,
                        prompt=current_prompt,
                        attachments=attachments if attempt == 0 else None,
                        cid=cid,
                        rid=rid,
                        rcid=rcid,
                    )
                else:
                    resp = await self._default_live_task_runner(
                        prompt=current_prompt,
                        bundle=bundle,
                        model=model,
                        thinking=thinking,
                        cid=cid,
                        rid=rid,
                        rcid=rcid,
                        is_first_turn=(attempt == 0),
                        use_ultra_2m=use_ultra_2m,
                        gem_id=gem_id,
                    )
                cid = resp.get("cid") or cid
                rid = resp.get("rid") or rid
                rcid = resp.get("rcid") or rcid

                draft_cands = resp.get("draft_candidates")
                if isinstance(draft_cands, list) and len(draft_cands) > 0:
                    draft_texts = [
                        str(d.get("text", "")) if isinstance(d, dict) else str(d)
                        for d in draft_cands
                    ]
                    sbx_ver = await verify_candidate_drafts_in_sandbox(draft_texts)
                    speculative_sandbox_verified = True
                    if int(sbx_ver.get("verified_count", 0)) > 0:
                        b_idx = int(sbx_ver.get("best_index", 0))
                        best_draft = (
                            draft_cands[b_idx]
                            if isinstance(draft_cands[b_idx], dict)
                            else {"text": str(draft_cands[b_idx]), "rcid": rcid}
                        )
                    else:
                        best_draft = q_engine.select_best_draft(draft_cands)
                    effective_text = str(best_draft.get("text") or resp.get("text", ""))
                    selected_rcid = str(best_draft.get("rcid") or rcid or "")
                else:
                    effective_text = str(resp.get("text", ""))
                    selected_rcid = rcid
                    await verify_candidate_drafts_in_sandbox([effective_text])
                    speculative_sandbox_verified = True

                if enable_mcp_tools:
                    tool_turns = 0
                    while tool_turns < 3:
                        tool_calls = extract_mcp_tool_calls(effective_text)
                        if not tool_calls or extract_code_files_from_response(
                            effective_text, orig_files={**orig_files, **candidate_files}
                        ):
                            break
                        tool_turns += 1
                        from backend.app.mcp.server import MCPServer

                        mcp_srv = MCPServer()
                        tool_blocks: list[str] = []
                        for tc in tool_calls:
                            tc_name = str(tc.get("name", ""))
                            tc_args = dict(tc.get("arguments") or {})
                            mcp_resp = await mcp_srv.handle_message(
                                {
                                    "jsonrpc": "2.0",
                                    "id": 1,
                                    "method": "tools/call",
                                    "params": {"name": tc_name, "arguments": tc_args},
                                }
                            )
                            content_items = (mcp_resp or {}).get("result", {}).get("content", [])
                            tool_text = (
                                str(content_items[0].get("text", ""))
                                if content_items
                                else json.dumps(mcp_resp or {})
                            )
                            mcp_tool_calls_log.append(
                                {
                                    "name": tc_name,
                                    "arguments": tc_args,
                                    "result_preview": tool_text[:500],
                                }
                            )
                            tool_blocks.append(f"# MCP Tool Result ({tc_name})\n{tool_text}")
                        tool_prompt = "\n\n".join(tool_blocks) + "\n\nNow output the final code changes."
                        if task_runner is not None:
                            resp = await self._invoke_runner(
                                task_runner,
                                prompt=tool_prompt,
                                attachments=None,
                                cid=cid,
                                rid=rid,
                                rcid=rcid,
                            )
                        else:
                            resp = await self._default_live_task_runner(
                                prompt=tool_prompt,
                                bundle=bundle,
                                model=model,
                                thinking=thinking,
                                cid=cid,
                                rid=rid,
                                rcid=rcid,
                                is_first_turn=False,
                                use_ultra_2m=use_ultra_2m,
                                gem_id=gem_id,
                            )
                        cid = resp.get("cid") or cid
                        rid = resp.get("rid") or rid
                        rcid = resp.get("rcid") or rcid
                        draft_cands = resp.get("draft_candidates")
                        if isinstance(draft_cands, list) and len(draft_cands) > 0:
                            best_draft = q_engine.select_best_draft(draft_cands)
                            effective_text = str(best_draft.get("text") or resp.get("text", ""))
                            selected_rcid = str(best_draft.get("rcid") or rcid or "")
                        else:
                            effective_text = str(resp.get("text", ""))
                            selected_rcid = rcid

                dc_res = await q_engine.double_check_response(text=effective_text)
                double_check_trust = float(dc_res.get("trust_score", 0.95))

                extracted = extract_code_files_from_response(
                    effective_text,
                    orig_files={**orig_files, **candidate_files},
                )
                if extracted:
                    candidate_files.update(extracted)

                gates_result = verify_five_quality_gates(
                    workspace_dir=root,
                    orig_files=orig_files,
                    candidate_files=candidate_files,
                    run_pytest=run_pytest,
                )
                if gates_result["passed"]:
                    break
                if attempt < max_heal_attempts:
                    heal_attempts += 1
                    failed_names = [k for k, v in gates_result["gates"].items() if not v]
                    diag_block = "\n".join(gates_result["diagnostics"])
                    current_prompt = (
                        f"Self-Healing Verification Failed on Gates: {', '.join(failed_names)}.\n"
                        f"Diagnostics / Pytest Output (FAILED / AssertionError):\n{diag_block}\n\n"
                        "Return fixed complete file(s) using '### File: <rel_path>' or targeted "
                        "'### RangeEdit: <rel_path>:<start_line>-<end_line>' fenced code blocks "
                        "preserving 100% of REQ-* tags, signatures, and >=95% non-empty lines."
                    )

        unified_diff = _compute_unified_diff(orig_files, candidate_files)
        run_id = f"run_{uuid.uuid4().hex[:10]}"
        status_val = "STAGED_AWAITING_APPROVAL" if gates_result.get("passed") else "FAILED_GATES"
        run_record: dict[str, Any] = {
            "run_id": run_id,
            "schedule_id": schedule_id,
            "workspace_dir": str(root),
            "prompt": prompt,
            "model": model,
            "thinking": thinking,
            "use_ultra_2m": bool(use_ultra_2m),
            "gem_id": gem_id,
            "enable_mcp_tools": bool(enable_mcp_tools),
            "mcp_tool_calls": mcp_tool_calls_log,
            "active_skills": active_skills,
            "ultra_profile": ultra_profile,
            "status": status_val,
            "heal_attempts": heal_attempts,
            "cid": cid,
            "rid": rid,
            "rcid": rcid,
            "selected_rcid": selected_rcid or rcid,
            "double_check_trust": double_check_trust,
            "speculative_sandbox_verified": speculative_sandbox_verified,
            "ast_compressed_files": int(ultra_2m_meta.get("ast_compressed_files", 0)),
            "gates": gates_result,
            "unified_diff": unified_diff,
            "candidate_files": candidate_files,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "rejection_reason": None,
        }
        self._state["runs"][run_id] = run_record
        if schedule_id and schedule_id in self._state["schedules"]:
            srec = self._state["schedules"][schedule_id]
            srec["last_run_ts"] = time.time()
            srec["next_run_ts"] = time.time() + int(srec.get("interval_seconds", 3600))
            srec["last_run_id"] = run_id
        self._save_state()
        return dict(run_record)


    def list_runs(self) -> list[dict[str, Any]]:
        """REQ-PIPE-05: Lists all pipeline runs."""
        return [dict(r) for r in self._state["runs"].values()]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        rec = self._state["runs"].get(run_id)
        return dict(rec) if rec else None

    def approve_run(self, run_id: str) -> dict[str, Any]:
        """
        REQ-PIPE-05: Applies staged candidate_files to workspace_dir on disk only upon explicit human approval.
        """
        if run_id not in self._state["runs"]:
            raise KeyError(f"Run '{run_id}' not found")
        rec = self._state["runs"][run_id]
        root = Path(rec["workspace_dir"]).resolve()
        for rel_path, code in rec.get("candidate_files", {}).items():
            dest = root / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(code, encoding="utf-8")
        rec["status"] = "APPROVED_APPLIED"
        rec["approved_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state()
        return dict(rec)

    def reject_run(self, run_id: str, reason: str = "") -> dict[str, Any]:
        """
        REQ-PIPE-05: Marks a staged run as REJECTED and leaves workspace_dir completely untouched.
        """
        if run_id not in self._state["runs"]:
            raise KeyError(f"Run '{run_id}' not found")
        rec = self._state["runs"][run_id]
        rec["status"] = "REJECTED"
        rec["rejection_reason"] = reason
        rec["rejected_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state()
        return dict(rec)

    async def sync_cloud_scheduled_actions(self, client: Any = None) -> dict[str, Any]:
        """
        REQ-PIPE-02, REQ-PIPE-06: Harvests and reconciles Google Gemini Scheduled Action conversations
        via MaZiqc / hNvQHb when an authenticated GeminiWebClient is supplied.
        """
        cloud_items: list[dict[str, Any]] = list(self._state.get("cloud_actions", []))
        if client is not None and hasattr(client, "list_remote_conversations"):
            try:
                remote = await client.list_remote_conversations(limit=30)
                harvested: list[dict[str, Any]] = []
                for item in remote or []:
                    cid = getattr(item, "id", None) or (item.get("id") if isinstance(item, dict) else "")
                    title = getattr(item, "title", None) or (item.get("title") if isinstance(item, dict) else "")
                    harvested.append(
                        {
                            "cid": str(cid),
                            "title": str(title),
                            "source": "MaZiqc",
                            "synced_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                if harvested:
                    cloud_items = harvested
                    self._state["cloud_actions"] = cloud_items
                    self._save_state()
            except Exception as exc:
                logger.debug("Cloud scheduled actions sync fallback: %s", exc)

        return {
            "ok": True,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "cloud_actions": cloud_items,
            "local_schedule_count": len(self._state["schedules"]),
        }


_DEFAULT_ORCHESTRATOR: TaskPipelineOrchestrator | None = None


def get_task_pipeline_orchestrator() -> TaskPipelineOrchestrator:
    """Returns singleton TaskPipelineOrchestrator instance."""
    global _DEFAULT_ORCHESTRATOR
    if _DEFAULT_ORCHESTRATOR is None:
        _DEFAULT_ORCHESTRATOR = TaskPipelineOrchestrator()
    return _DEFAULT_ORCHESTRATOR
