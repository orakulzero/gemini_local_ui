"""
.claude/hooks/notebooklm_post_tool_sync.py
Claude Code PostToolUse hook for Write|Edit:
Automatically syncs modified documentation, .claude specs, and router files
into the project's NotebookLM RAG notebook via POST /api/notebooklm/sync-repo.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

SYNCABLE_SUFFIXES = (".md", ".mdc")
SYNCABLE_SUBPATHS = (".claude/", "docs/", "backend/app/api/", "backend/app/anthropic/")
DEFAULT_NOTEBOOK_ID = os.environ.get("GEMINI_RAG_NOTEBOOK_ID", "nb_2f3e4d5c-6b7a-8c9d-0e1f-2a3b4c5d6e7f")
GATEWAY_SYNC_URL = os.environ.get("GEMINI_LOCAL_SYNC_URL", "http://127.0.0.1:8000/api/notebooklm/sync-repo")


def should_sync_path(rel_posix: str) -> bool:
    low = rel_posix.lower()
    if low in ("claude.md", "readme.md", "project.md"):
        return True
    if low.endswith(SYNCABLE_SUFFIXES) and any(low.startswith(p) for p in (".claude/", "docs/", "blueprints/")):
        return True
    if any(low.startswith(p) for p in SYNCABLE_SUBPATHS) and low.endswith(("routes.py", "claude_plugins.py", "notebooklm.py")):
        return True
    return False


def run_hook(payload: dict) -> dict:
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    if tool_name not in ("Write", "Edit", "MultiEdit") or not isinstance(tool_input, dict):
        return {}

    raw_path = str(tool_input.get("file_path") or tool_input.get("path") or "").strip()
    if not raw_path:
        return {}

    cwd = Path.cwd().resolve()
    try:
        fpath = Path(raw_path).resolve()
        rel_posix = fpath.relative_to(cwd).as_posix()
    except Exception:
        rel_posix = raw_path.replace("\\", "/")

    if not should_sync_path(rel_posix):
        return {}

    body = json.dumps(
        {
            "notebook_id": DEFAULT_NOTEBOOK_ID,
            "paths": [rel_posix],
            "generate_briefing": False,
            "force": False,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        GATEWAY_SYNC_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            synced = data.get("synced_files", [])
            skipped = data.get("skipped_files", [])
            status_msg = f"synced={synced}" if synced else f"unchanged={skipped}"
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": f"[NotebookLM RAG Auto-Sync] {rel_posix} -> {DEFAULT_NOTEBOOK_ID} ({status_msg})",
                }
            }
    except Exception:
        return {}


def main() -> int:
    try:
        raw_stdin = sys.stdin.read().strip()
        if not raw_stdin:
            return 0
        payload = json.loads(raw_stdin)
        out = run_hook(payload)
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
