"""
tests/test_m22_architect_plugin.py
Verification tests for Milestone M22 — Installed & Adapted Claude Code Architect Plugin Suite:
- Subagents: `code-architect` (Argon Task Mode), `code-explorer` (Flash), `code-reviewer` (Argon)
- Native Skill: `.claude/skills/architect/SKILL.md`
- Slash Commands: `/architect`, `/feature-dev`, `/architect-audit`
- Blueprint auto-sync via `.claude/hooks/notebooklm_post_tool_sync.py`
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.api.claude_plugins import detect_subagent_profile


def test_architect_plugin_suite_discovered():
    client = TestClient(app)
    res = client.get("/api/claude-plugins")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True

    agent_names = {a["name"] for a in data["agents"]}
    assert {"code-architect", "code-explorer", "code-reviewer"}.issubset(agent_names)

    skill_names = {s["name"] for s in data["skills"]}
    assert "architect" in skill_names

    cmd_names = {c["name"] for c in data["commands"]}
    assert {"architect", "feature-dev", "architect-audit"}.issubset(cmd_names)


def test_architect_subagent_auto_routing_tiers():
    # 1. code-explorer -> Gemini 3 Flash (56fdd199312815e2)
    prof_exp = detect_subagent_profile(
        "You are **code-explorer** — a high-speed code analyst adapted from Anthropic's `feature-dev` plugin"
    )
    assert prof_exp is not None
    assert prof_exp["name"] == "code-explorer"
    assert prof_exp["canonical_model"] == "56fdd199312815e2"
    assert prof_exp["gem_id"] == "gem_cc_code-explorer"

    # 2. code-architect -> Gemini 4 Argon (95a4c0abcba24364) + feature_mode=40
    prof_arch = detect_subagent_profile(
        "You are **code-architect** — a senior software architect adapted from Anthropic's `feature-dev` and `the-architect` plugins"
    )
    assert prof_arch is not None
    assert prof_arch["name"] == "code-architect"
    assert prof_arch["canonical_model"] == "95a4c0abcba24364"
    assert prof_arch["feature_mode"] == 40
    assert prof_arch["gem_id"] == "gem_cc_code-architect"

    # 3. code-reviewer -> Gemini 4 Argon (95a4c0abcba24364)
    prof_rev = detect_subagent_profile(
        "You are **code-reviewer** — a precision code and blueprint auditor adapted from Anthropic's `feature-dev`"
    )
    assert prof_rev is not None
    assert prof_rev["name"] == "code-reviewer"
    assert prof_rev["canonical_model"] == "95a4c0abcba24364"
    assert prof_rev["gem_id"] == "gem_cc_code-reviewer"


def test_architect_gems_sync_and_blueprint_hook():
    client = TestClient(app)
    res = client.post("/api/claude-plugins/sync-gems")
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is True
    gem_ids = {g["id"] for g in payload["gems"]}
    assert {
        "gem_cc_code-architect",
        "gem_cc_code-explorer",
        "gem_cc_code-reviewer",
    }.issubset(gem_ids)

    from importlib.machinery import SourceFileLoader
    from pathlib import Path

    hook_path = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "notebooklm_post_tool_sync.py"
    hook_mod = SourceFileLoader("notebooklm_post_tool_sync", str(hook_path)).load_module()
    assert hook_mod.should_sync_path("blueprints/swarm-router-blueprint.md") is True
