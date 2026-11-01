"""
tests/test_m41_ecosystem_scout.py
Locked Agile V (P30) Verification Suite for Milestone M41:
Autonomous Ecosystem Intelligence, Repo/Skill/Forum Scout & Cross-IDE Skill Transpiler.
- REQ-SCOUT-01: Multi-Lane Intelligence Sweep (repo_solutions, cross_ide_skills, forum_pain_points, emerging_tech)
- REQ-SCOUT-02: Cross-IDE Skill & Rule Transpiler to Native Antigravity SKILL.md
- REQ-SCOUT-03: Promote Finding to Stage-Only Task Pipeline & NotebookLM Export
- REQ-SCOUT-04: Consolidated Markdown Intelligence Digest
- REQ-SCOUT-05: FastAPI /api/v1/scout/* Endpoints & MCP gemini_ecosystem_scout Tool
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

from fastapi.testclient import TestClient

from backend.app.bridge.ecosystem_scout import (
    EcosystemIntelligenceScout,
    get_ecosystem_scout,
)
from backend.app.bridge.streaming_parser import StreamChunk
from backend.app.bridge.task_pipeline import TaskPipelineOrchestrator
from backend.app.main import app
from backend.app.mcp.server import MCPServer


def _resolve(val: Any) -> Any:
    """Resolves either synchronous return values or coroutines transparently."""
    if inspect.isawaitable(val):
        return asyncio.run(val)
    return val


class MockScoutClient:
    """Deterministic mock Gemini Web client for M41 Ecosystem Scout unit verification."""

    async def generate_stream(
        self,
        prompt: str,
        model: str = "gemini-4-argon",
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        text_out = (
            "### Ecosystem Intelligence Sweep Synthesis\n"
            "- Lane repo_solutions: AST-aware incremental context compression in FastMCP.\n"
            "- Lane cross_ide_skills: Cursor/Windsurf AST refactoring rule adaptation.\n"
            "- Lane forum_pain_points: Developers report SSE stream timeout over 240s on large edits.\n"
            "- Lane emerging_tech: Speculative decoding with local SQLite WAL telemetry caches.\n"
        )
        yield StreamChunk(type="thinking", delta="Scanning multi-lane ecosystem intelligence sources")
        yield StreamChunk(type="text", delta=text_out)
        yield StreamChunk(
            type="done",
            delta="",
            metadata={
                "cid": kwargs.get("cid") or "c_scout_41",
                "rid": "r_scout_41",
                "rcid": "rc_scout_41",
                "full_text": text_out,
                "model": model,
            },
        )


def test_req_scout_01_multi_lane_intelligence_sweep(tmp_path: Path) -> None:
    """REQ-SCOUT-01: Multi-Lane Intelligence Sweep discovers, scores, and filters findings across all 4 lanes."""
    scout = EcosystemIntelligenceScout(storage_path=tmp_path / "scout_state.json")
    mock_client = MockScoutClient()
    lanes = ["repo_solutions", "cross_ide_skills", "forum_pain_points", "emerging_tech"]

    res = _resolve(
        scout.run_intelligence_sweep(
            lanes=lanes,
            focus_query="MCP context compression and IDE skills",
            client=mock_client,
        )
    )
    assert res["ok"] is True
    assert isinstance(res["sweep_id"], str) and res["sweep_id"].strip() != ""
    findings: List[Dict[str, Any]] = res["findings"]
    assert isinstance(findings, list) and len(findings) >= 4

    lane_counts: Dict[str, int] = res["lane_counts"]
    for lane in lanes:
        assert lane_counts.get(lane, 0) >= 1

    required_keys = {
        "finding_id",
        "lane",
        "title",
        "source_url",
        "target_agy_repo",
        "summary",
        "integration_proposal",
        "impact_score",
        "feasibility_score",
        "priority_score",
        "status",
    }
    for item in findings:
        assert required_keys.issubset(item.keys())
        assert item["lane"] in lanes
        assert isinstance(item["impact_score"], float)
        assert isinstance(item["feasibility_score"], float)
        assert isinstance(item["priority_score"], float)
        assert item["status"] == "DISCOVERED"

    pain_points = scout.list_findings(lane="forum_pain_points")
    assert len(pain_points) >= 1
    assert all(f["lane"] == "forum_pain_points" for f in pain_points)
    priorities = [float(f["priority_score"]) for f in pain_points]
    assert priorities == sorted(priorities, reverse=True)

    all_sorted = scout.list_findings()
    all_priorities = [float(f["priority_score"]) for f in all_sorted]
    assert all_priorities == sorted(all_priorities, reverse=True)


def test_req_scout_02_cross_ide_skill_transpiler(tmp_path: Path) -> None:
    """REQ-SCOUT-02: Cross-IDE Skill & Rule Transpiler converts Cursor/Claude/Windsurf rules to Antigravity SKILL.md."""
    scout = EcosystemIntelligenceScout(storage_path=tmp_path / "scout_state.json")
    raw_rule = "Always parse AST before modifying functions. Never truncate imports."

    adapted = _resolve(
        scout.adapt_skill_to_antigravity(
            source_ide="cursor",
            skill_name="cursor-ast-refactor",
            raw_content=raw_rule,
            target_plugin="agy-golden-stack",
        )
    )
    assert adapted["ok"] is True
    assert isinstance(adapted["adaptation_id"], str) and adapted["adaptation_id"].strip() != ""
    assert adapted["skill_name"] == "cursor-ast-refactor"
    assert adapted["source_ide"] == "cursor"
    assert adapted["target_plugin"] == "agy-golden-stack"
    assert adapted["status"] == "STAGED_FOR_REVIEW"

    skill_md = adapted["skill_markdown"]
    assert isinstance(skill_md, str)
    assert skill_md.startswith("---\nname: cursor-ast-refactor\ndescription:")
    assert "Always parse AST before modifying functions." in skill_md
    assert not any("\u0400" <= ch <= "\u04ff" for ch in skill_md)


def test_req_scout_03_promote_finding_to_stage_only_pipeline(tmp_path: Path) -> None:
    """REQ-SCOUT-03: Promote Finding to Stage-Only Task Pipeline creates schedule and updates finding status."""
    scout = EcosystemIntelligenceScout(storage_path=tmp_path / "scout_state.json")
    task_orchestrator = TaskPipelineOrchestrator(storage_path=tmp_path / "pipeline_state.json")

    sweep_res = _resolve(
        scout.run_intelligence_sweep(
            lanes=["repo_solutions", "forum_pain_points"],
            focus_query="AST context compression",
            client=MockScoutClient(),
        )
    )
    target_finding = sweep_res["findings"][0]
    finding_id = target_finding["finding_id"]
    expected_repo = target_finding["target_agy_repo"]

    promo_res = _resolve(
        scout.promote_finding_to_pipeline(
            finding_id=finding_id,
            orchestrator=task_orchestrator,
        )
    )
    assert promo_res["ok"] is True
    assert promo_res["finding_id"] == finding_id
    assert isinstance(promo_res["schedule_id"], str) and promo_res["schedule_id"].strip() != ""
    assert promo_res["target_agy_repo"] == expected_repo

    schedules = task_orchestrator.list_schedules()
    assert any(s["schedule_id"] == promo_res["schedule_id"] for s in schedules)

    updated_findings = [f for f in scout.list_findings() if f["finding_id"] == finding_id]
    assert len(updated_findings) == 1
    assert updated_findings[0]["status"] == "PROMOTED_TO_PIPELINE"


def test_req_scout_04_consolidated_markdown_intelligence_digest(tmp_path: Path) -> None:
    """REQ-SCOUT-04: Consolidated Markdown Intelligence Digest renders all 4 intelligence lanes and counts."""
    scout = EcosystemIntelligenceScout(storage_path=tmp_path / "scout_state.json")
    _resolve(
        scout.run_intelligence_sweep(
            lanes=["repo_solutions", "cross_ide_skills", "forum_pain_points", "emerging_tech"],
            focus_query="Autonomous IDE orchestration",
            client=MockScoutClient(),
        )
    )
    _resolve(
        scout.adapt_skill_to_antigravity(
            source_ide="windsurf",
            skill_name="windsurf-context-guard",
            raw_content="Enforce context window chunking.",
            target_plugin="agy-golden-stack",
        )
    )

    digest = _resolve(scout.generate_digest_markdown())
    assert digest["ok"] is True
    assert int(digest["total_findings"]) >= 4
    assert int(digest["staged_skills_count"]) >= 1

    md = digest["markdown"]
    assert isinstance(md, str)
    for section_title in ("Repository Solutions", "Cross-IDE Skills", "Forum Pain Points", "Emerging Tech"):
        assert section_title in md


def test_req_scout_05_fastapi_endpoints_and_mcp_tool(tmp_path: Path) -> None:
    """REQ-SCOUT-05: FastAPI /api/v1/scout/* endpoints and MCPServer gemini_ecosystem_scout tool actions."""
    client = TestClient(app)

    r_sweep = client.post(
        "/api/v1/scout/sweep",
        json={
            "lanes": ["repo_solutions", "cross_ide_skills", "forum_pain_points", "emerging_tech"],
            "focus_query": "MCP context compression and IDE skills",
        },
    )
    assert r_sweep.status_code == 200
    sweep_data = r_sweep.json()
    assert sweep_data["ok"] is True
    assert len(sweep_data["findings"]) >= 4
    first_finding_id = sweep_data["findings"][0]["finding_id"]

    r_list = client.get("/api/v1/scout/findings", params={"lane": "forum_pain_points"})
    assert r_list.status_code == 200
    list_data = r_list.json()
    assert list_data["ok"] is True
    assert len(list_data["findings"]) >= 1
    assert all(f["lane"] == "forum_pain_points" for f in list_data["findings"])

    r_adapt = client.post(
        "/api/v1/scout/adapt-skill",
        json={
            "source_ide": "cursor",
            "skill_name": "cursor-ast-refactor",
            "raw_content": "Always parse AST before modifying functions. Never truncate imports.",
            "target_plugin": "agy-golden-stack",
        },
    )
    assert r_adapt.status_code == 200
    adapt_data = r_adapt.json()
    assert adapt_data["ok"] is True
    assert adapt_data["status"] == "STAGED_FOR_REVIEW"
    assert adapt_data["skill_markdown"].startswith("---\nname: cursor-ast-refactor\ndescription:")

    r_promote = client.post(f"/api/v1/scout/promote/{first_finding_id}")
    assert r_promote.status_code == 200
    promote_data = r_promote.json()
    assert promote_data["ok"] is True
    assert promote_data["finding_id"] == first_finding_id
    assert promote_data["schedule_id"]

    r_digest = client.get("/api/v1/scout/digest")
    assert r_digest.status_code == 200
    digest_data = r_digest.json()
    assert digest_data["ok"] is True
    assert "Repository Solutions" in digest_data["markdown"]

    mcp = MCPServer()
    mcp_sweep_resp = _resolve(
        mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 4101,
                "method": "tools/call",
                "params": {
                    "name": "gemini_ecosystem_scout",
                    "arguments": {
                        "action": "sweep",
                        "lanes": ["repo_solutions", "cross_ide_skills", "forum_pain_points", "emerging_tech"],
                        "focus_query": "MCP ecosystem intelligence",
                    },
                },
            },
            client=MockScoutClient(),
        )
    )
    assert mcp_sweep_resp is not None and mcp_sweep_resp["result"]["isError"] is False
    mcp_sweep_payload = json.loads(mcp_sweep_resp["result"]["content"][0]["text"])
    assert mcp_sweep_payload["ok"] is True
    mcp_finding_id = mcp_sweep_payload["findings"][0]["finding_id"]

    mcp_actions = [
        {"action": "list_findings", "lane": "forum_pain_points"},
        {
            "action": "adapt_skill",
            "source_ide": "claude",
            "skill_name": "claude-tdd-gate",
            "raw_content": "Write verification tests before implementation.",
            "target_plugin": "agy-golden-stack",
        },
        {"action": "promote_finding", "finding_id": mcp_finding_id},
        {"action": "digest"},
    ]
    for idx, args in enumerate(mcp_actions, start=2):
        resp = _resolve(
            mcp.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 4100 + idx,
                    "method": "tools/call",
                    "params": {"name": "gemini_ecosystem_scout", "arguments": args},
                },
                client=MockScoutClient(),
            )
        )
        assert resp is not None
        assert resp["result"]["isError"] is False
        payload = json.loads(resp["result"]["content"][0]["text"])
        assert payload["ok"] is True
