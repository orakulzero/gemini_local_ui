"""
tests/test_adversarial_m21_challenger.py
Adversarial empirical stress tests authored by m21_challenger_2.
Verifies filesystem security, path traversal defenses, error status codes,
idempotency, and configuration preservation across Gemini Local UI endpoints.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.api.claude_plugins import init_claude_workspace
from backend.app.api.notebooklm import sync_repo_to_notebooklm


# ============================================================================
# 1. Path Traversal & Filesystem Security: POST /api/notebooklm/sync-repo
# ============================================================================

@pytest.mark.parametrize(
    "traversal_path",
    [
        "../../Windows/win.ini",
        "../../../../../../../../etc/passwd",
        r"..\..\Windows\System32\calc.exe",
        "sub/../../escaped.txt",
        "..",
        "docs/../../../outside.txt",
    ],
)
def test_sync_repo_relative_traversal_returns_400(traversal_path: str) -> None:
    """Verifies that all forms of relative directory traversal return HTTP 400."""
    with TestClient(app) as client:
        resp = client.post(
            "/api/notebooklm/sync-repo",
            json={"notebook_id": "nb_adv_test", "paths": [traversal_path]},
        )
        assert resp.status_code == 400, f"Expected 400 for {traversal_path}, got {resp.status_code}: {resp.text}"
        assert "traversal" in resp.json()["detail"].lower()


@pytest.mark.parametrize(
    "absolute_path",
    [
        "C:/Windows/System32/drivers/etc/hosts",
        r"C:\Windows\System32\cmd.exe",
        "D:/outside_drive/secret.txt",
        r"\\?\C:\Windows\System32\drivers\etc\hosts",
    ],
)
def test_sync_repo_absolute_path_escape_returns_400(absolute_path: str) -> None:
    """Verifies that absolute drive paths escaping the workspace return HTTP 400."""
    with TestClient(app) as client:
        resp = client.post(
            "/api/notebooklm/sync-repo",
            json={"notebook_id": "nb_adv_test", "paths": [absolute_path]},
        )
        assert resp.status_code == 400, f"Expected 400 for {absolute_path}, got {resp.status_code}: {resp.text}"
        assert "escapes workspace root" in resp.json()["detail"].lower()


def test_sync_repo_empty_path_pattern_returns_400() -> None:
    """Verifies that an empty path string pattern returns HTTP 400 cleanly."""
    with TestClient(app) as client:
        resp = client.post(
            "/api/notebooklm/sync-repo",
            json={"notebook_id": "nb_adv_test", "paths": [""]},
        )
        assert resp.status_code == 400
        assert "unacceptable pattern" in resp.json()["detail"].lower() or "error" in resp.json()["detail"].lower()


@pytest.mark.parametrize("bad_nb_id", ["", "   ", "\t\n"])
def test_sync_repo_blank_notebook_id_returns_400(bad_nb_id: str) -> None:
    """Verifies empty or whitespace notebook_id returns HTTP 400."""
    with TestClient(app) as client:
        resp = client.post(
            "/api/notebooklm/sync-repo",
            json={"notebook_id": bad_nb_id, "paths": ["README.md"]},
        )
        assert resp.status_code == 400
        assert "notebook_id cannot be empty" in resp.json()["detail"].lower()


# ============================================================================
# 2. Filesystem Guards: POST /api/claude-plugins/init-workspace
# ============================================================================

@pytest.mark.parametrize(
    "invalid_target",
    [
        "invalid:path",
        "invalid:<>|*?path",
        r"C:\invalid\path\with|pipe",
        r"C:\invalid\path\with<angle>",
        r"C:\invalid\path\with?question",
        "",
        "   ",
    ],
)
def test_init_workspace_invalid_syntax_returns_400(invalid_target: str) -> None:
    """Verifies that invalid Windows directory paths return HTTP 400, never unhandled 500."""
    with TestClient(app) as client:
        resp = client.post(
            "/api/claude-plugins/init-workspace",
            json={"target_dir": invalid_target},
        )
        assert resp.status_code == 400, f"Expected 400 for {invalid_target}, got {resp.status_code}: {resp.text}"
        detail = resp.json().get("detail", "").lower()
        assert "target_dir is required" in detail or "directory" in detail or "error" in detail


def test_init_workspace_target_is_existing_file_returns_400(tmp_path: Path) -> None:
    """Verifies pointing target_dir to an existing regular file returns HTTP 400."""
    blocking_file = tmp_path / "already_a_file.txt"
    blocking_file.write_text("file content", encoding="utf-8")

    with TestClient(app) as client:
        resp = client.post(
            "/api/claude-plugins/init-workspace",
            json={"target_dir": str(blocking_file)},
        )
        assert resp.status_code == 400
        assert "not a directory" in resp.json()["detail"].lower()


# ============================================================================
# 3. Agent Slug Traversal & Sanitization: /api/claude-plugins/agents
# ============================================================================

@pytest.mark.parametrize(
    "bad_slug",
    [
        "../../traversal",
        r"..\..\traversal",
        "/etc/passwd",
        "C:/Windows/System32",
        r"C:\Windows\System32",
        "agent.md",
        "agent..name",
        "agent name with space",
        "agent\x00null",
        "agent:stream",
        "agent|pipe",
        "agent<bracket>",
        "agent;cmd",
        "agent`cmd`",
        "agent$var",
        "",
        "   ",
        ".",
        "..",
    ],
)
def test_agent_slug_validation_rejects_all_adversarial_inputs(bad_slug: str) -> None:
    """Verifies that invalid or traversal agent slugs are strictly rejected with 400/404/422."""
    with TestClient(app) as client:
        # POST /api/claude-plugins/agents
        resp_post = client.post(
            "/api/claude-plugins/agents",
            json={"name": bad_slug, "prompt": "malicious prompt"},
        )
        assert resp_post.status_code in (400, 422), (
            f"POST expected 400/422 for slug {bad_slug!r}, got {resp_post.status_code}: {resp_post.text}"
        )

        # DELETE /api/claude-plugins/agents/{slug}
        import urllib.parse
        enc_slug = urllib.parse.quote(bad_slug, safe="")
        resp_del = client.delete(f"/api/claude-plugins/agents/{enc_slug}")
        assert resp_del.status_code in (400, 404, 405, 422), (
            f"DELETE expected 400/404/405/422 for slug {bad_slug!r}, got {resp_del.status_code}: {resp_del.text}"
        )
        assert resp_del.status_code != 500


def test_agent_crud_lifecycle_clean_behavior(tmp_path: Path) -> None:
    """Verifies that a valid slug creates cleanly, deletes cleanly, and repeat delete returns 404."""
    valid_slug = "m21-test-agent-alpha_1"
    with TestClient(app) as client:
        # Create
        resp_create = client.post(
            "/api/claude-plugins/agents",
            json={"name": valid_slug, "description": "QA Challenger agent", "prompt": "You are a test agent."},
        )
        assert resp_create.status_code == 200
        assert resp_create.json()["ok"] is True
        assert resp_create.json()["agent"]["id"] == valid_slug

        # Delete
        resp_del = client.delete(f"/api/claude-plugins/agents/{valid_slug}")
        assert resp_del.status_code == 200
        assert resp_del.json()["ok"] is True
        assert resp_del.json()["deleted"] == valid_slug

        # Repeat delete -> 404
        resp_del_repeat = client.delete(f"/api/claude-plugins/agents/{valid_slug}")
        assert resp_del_repeat.status_code == 404


# ============================================================================
# 4. Idempotency & Configuration Preservation
# ============================================================================

@pytest.mark.asyncio
async def test_sync_repo_idempotency_and_force_flag(tmp_path: Path) -> None:
    """
    Verifies multi-turn sync idempotency:
    - Turn 1: 2 new files -> 2 synced, 0 skipped
    - Turn 2: unchanged files -> 0 synced, 2 skipped
    - Turn 3: 1 file modified -> 1 synced, 1 skipped
    - Turn 4: force=True -> 2 synced, 0 skipped
    """
    f1 = tmp_path / "README.md"
    f1.write_text("# Project Alpha\nInitial overview", encoding="utf-8")
    f2 = tmp_path / "CLAUDE.md"
    f2.write_text("# Claude Guidance\nGuidance notes", encoding="utf-8")

    nb_id = f"nb_idem_adv_{tmp_path.name}"

    # Turn 1: Fresh sync
    res1 = await sync_repo_to_notebooklm(
        notebook_id=nb_id,
        paths=["README.md", "CLAUDE.md"],
        root_dir=tmp_path,
        force=False,
    )
    assert res1["synced_count"] == 2
    assert res1["skipped_count"] == 0

    # Turn 2: Unchanged sync
    res2 = await sync_repo_to_notebooklm(
        notebook_id=nb_id,
        paths=["README.md", "CLAUDE.md"],
        root_dir=tmp_path,
        force=False,
    )
    assert res2["synced_count"] == 0
    assert res2["skipped_count"] == 2

    # Turn 3: Modify one file
    f1.write_text("# Project Alpha\nUpdated content v2", encoding="utf-8")
    res3 = await sync_repo_to_notebooklm(
        notebook_id=nb_id,
        paths=["README.md", "CLAUDE.md"],
        root_dir=tmp_path,
        force=False,
    )
    assert res3["synced_count"] == 1
    assert res3["skipped_count"] == 1
    assert "README.md" in res3["synced_files"]
    assert "CLAUDE.md" in res3["skipped_files"]

    # Turn 4: Force resync
    res4 = await sync_repo_to_notebooklm(
        notebook_id=nb_id,
        paths=["README.md", "CLAUDE.md"],
        root_dir=tmp_path,
        force=True,
    )
    assert res4["synced_count"] == 2
    assert res4["skipped_count"] == 0


def test_init_claude_workspace_preserves_complex_user_configurations(tmp_path: Path) -> None:
    """
    Verifies that init_claude_workspace deeply preserves:
    1. Third-party MCP servers in .mcp.json
    2. Custom user environment variables in .claude/settings.json
    3. Custom pre-existing hooks in .claude/settings.json
    4. Does not duplicate notebooklm_post_tool_sync on repeated calls
    """
    target_dir = tmp_path / "custom_workspace"
    target_dir.mkdir()

    # Pre-populate custom .mcp.json
    mcp_path = target_dir / ".mcp.json"
    initial_mcp = {
        "mcpServers": {
            "postgres-db": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres"]},
            "filesystem-extra": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]},
        }
    }
    mcp_path.write_text(json.dumps(initial_mcp, indent=2), encoding="utf-8")

    # Pre-populate custom .claude/settings.json
    claude_dir = target_dir / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    initial_settings = {
        "env": {
            "MY_CUSTOM_API_KEY": "sk_test_12345",
            "DEBUG_LEVEL": "trace",
        },
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "python security_precheck.py"}],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "CustomLog",
                    "hooks": [{"type": "command", "command": "echo done"}],
                }
            ],
        },
    }
    settings_path.write_text(json.dumps(initial_settings, indent=2), encoding="utf-8")

    # First run of init_claude_workspace
    res1 = init_claude_workspace(str(target_dir), include_mcp_config=True, overwrite=True)
    assert res1["ok"] is True

    # Check .mcp.json merged
    mcp_after1 = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert "postgres-db" in mcp_after1["mcpServers"]
    assert "filesystem-extra" in mcp_after1["mcpServers"]
    assert "gemini-local" in mcp_after1["mcpServers"]

    # Check .claude/settings.json merged
    settings_after1 = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings_after1["env"]["MY_CUSTOM_API_KEY"] == "sk_test_12345"
    assert settings_after1["env"]["DEBUG_LEVEL"] == "trace"
    assert "ANTHROPIC_BASE_URL" in settings_after1["env"]
    assert "PreToolUse" in settings_after1["hooks"]

    post_hooks1 = settings_after1["hooks"]["PostToolUse"]
    assert any(h.get("matcher") == "CustomLog" for h in post_hooks1)
    sync_hooks1 = [
        h for h in post_hooks1
        if any("notebooklm_post_tool_sync" in str(hk.get("command", "")) for hk in h.get("hooks", []))
    ]
    assert len(sync_hooks1) == 1

    # Second run of init_claude_workspace (Idempotency test)
    res2 = init_claude_workspace(str(target_dir), include_mcp_config=True, overwrite=True)
    assert res2["ok"] is True

    settings_after2 = json.loads(settings_path.read_text(encoding="utf-8"))
    post_hooks2 = settings_after2["hooks"]["PostToolUse"]
    sync_hooks2 = [
        h for h in post_hooks2
        if any("notebooklm_post_tool_sync" in str(hk.get("command", "")) for hk in h.get("hooks", []))
    ]
    # Hook must NOT be duplicated!
    assert len(sync_hooks2) == 1, f"Expected 1 sync hook, got {len(sync_hooks2)} (duplicated!)"


def test_init_claude_workspace_corrupted_config_recovery(tmp_path: Path) -> None:
    """Verifies that non-dict or syntax-corrupted existing configs are safely replaced without 500."""
    target_dir = tmp_path / "corrupted_workspace"
    target_dir.mkdir()

    mcp_path = target_dir / ".mcp.json"
    mcp_path.write_text("{CORRUPTED_JSON_MALFORMED", encoding="utf-8")

    claude_dir = target_dir / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    settings_path.write_text("[\"array_not_dict\"]", encoding="utf-8")

    res = init_claude_workspace(str(target_dir), include_mcp_config=True, overwrite=True)
    assert res["ok"] is True

    # Both configs should now be valid dicts
    mcp_fixed = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert isinstance(mcp_fixed, dict)
    assert "gemini-local" in mcp_fixed.get("mcpServers", {})

    settings_fixed = json.loads(settings_path.read_text(encoding="utf-8"))
    assert isinstance(settings_fixed, dict)
    assert "env" in settings_fixed
