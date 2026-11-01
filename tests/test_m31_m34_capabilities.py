"""
tests/test_m31_m34_capabilities.py
Locked Agile V (P30) Verification Suite for Milestones M31, M32, M33, and M34:
- REQ-CAP-01: Cloud Python Sandbox Execution & Streaming Parser Code Execution Extraction
- REQ-CAP-02: Interactive Canvas Session Manager & Range Editor
- REQ-CAP-03: Autonomous Deep Research Engine (2-Phase Plan + Report & Citation Graph)
- REQ-CAP-04: Saved Info / Long-Term Account Memory CRUD & Local Rules File Sync
- REQ-CAP-05: Native Cloud Scheduled Actions CRUD (MaZiqc / hNvQHb)
- REQ-CAP-06: Double-Check Fact Verifier & Multi-Draft rcid Selector
- REQ-CAP-07: Response Modifier (shorter, longer, simpler, professional, concise) & Unified API/MCP Surface
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi.testclient import TestClient

from backend.app.bridge.gemini_capabilities import (
    CanvasSessionManager,
    CloudScheduleRPCManager,
    DeepResearchEngine,
    ResponseQualityEngine,
    SavedInfoManager,
    run_cloud_python_sandbox,
)
from backend.app.bridge.streaming_parser import StreamChunk, StreamingDeltaAccumulator
from backend.app.main import app
from backend.app.mcp.server import MCPServer


def _resolve(val: Any) -> Any:
    """Resolves either synchronous return values or coroutines transparently."""
    if inspect.isawaitable(val):
        return asyncio.run(val)
    return val


class MockCapabilitiesClient:
    """Deterministic mock Gemini client for M31-M34 unit verification."""

    async def generate_stream(
        self,
        prompt: str,
        model: str = "gemini-4-argon",
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        text_out = (
            "# Research & Verification Output\n\n"
            "1. Sub-question alpha\n"
            "2. Sub-question beta\n"
            "3. Sub-question gamma\n\n"
            "Python 3.12 was released in October 2023 [1].\n"
            "```python\nprint(42)\n```\n"
        )
        yield StreamChunk(type="text", delta=text_out)
        yield StreamChunk(
            type="done",
            delta="",
            metadata={
                "cid": kwargs.get("cid") or "c_cap_101",
                "rid": "r_cap_101",
                "rcid": "rc_cap_101",
                "full_text": text_out,
                "code_executions": [
                    {
                        "language": "python",
                        "code": "print(21 * 2)",
                        "stdout": "42\n",
                        "stderr": "",
                        "outcome": "OUTCOME_OK",
                    }
                ],
                "citations": [
                    {
                        "index": 1,
                        "url": "https://docs.python.org/3.12/",
                        "title": "Python 3.12 Release Schedule",
                        "domain": "docs.python.org",
                        "snippet": "Python 3.12 was released in October 2023.",
                    }
                ],
                "grounding_sources": [
                    {
                        "url": "https://docs.python.org/3.12/",
                        "title": "Python 3.12 Release Schedule",
                        "snippet": "Python 3.12 was released in October 2023.",
                    }
                ],
            },
        )


def _build_candidate_frame(
    candidates: List[List[Any]],
    cid: str = "c_frame_1",
    rid: str = "r_frame_1",
    rcid: str = "rc_frame_1",
) -> List[Any]:
    """Constructs a Stage 1 outer frame wrapping inner_json."""
    inner_json: List[Any] = [
        None,
        [cid, rid, rcid],
        None,
        None,
        candidates,
    ]
    return [["wrb.fr", None, json.dumps(inner_json)]]


def test_req_cap_01_cloud_python_sandbox_and_parser_extraction() -> None:
    """REQ-CAP-01: StreamingDeltaAccumulator extracts candidate[12] code execution & sandbox runner."""
    acc = StreamingDeltaAccumulator()
    candidate: List[Any] = [None] * 14
    candidate[0] = "rc_exec_1"
    candidate[1] = ["Sandbox execution finished: 42"]
    candidate[8] = [2]  # Turn completed
    candidate[12] = {
        "executable_code": {"language": "python", "code": "print(6 * 7)"},
        "code_execution_result": {"outcome": "OUTCOME_OK", "output": "42\n"},
    }

    frame = _build_candidate_frame([candidate], cid="c_sbx_1", rid="r_sbx_1", rcid="rc_exec_1")
    chunks = acc.process_frame(frame)
    done_chunks = [c for c in chunks if c.type == "done"]
    assert len(done_chunks) == 1
    meta = done_chunks[0].metadata
    assert "code_executions" in meta
    assert len(meta["code_executions"]) >= 1
    exec_item = meta["code_executions"][0]
    assert exec_item["code"] == "print(6 * 7)"
    assert exec_item["stdout"] == "42\n"
    assert exec_item["outcome"] == "OUTCOME_OK"

    mock_client = MockCapabilitiesClient()
    res = _resolve(run_cloud_python_sandbox(code="print(21 * 2)", client=mock_client))
    assert res["ok"] is True
    assert res["code"] == "print(21 * 2)"
    assert res["stdout"].strip() == "42"
    assert res["stderr"] == ""
    assert res["outcome"] == "OUTCOME_OK"
    assert res["cid"]
    assert res["rid"]
    assert res["rcid"]


def test_req_cap_02_canvas_session_manager_and_range_editor() -> None:
    """REQ-CAP-02: CanvasSessionManager creates versioned docs and applies line-range replacements."""
    mgr = CanvasSessionManager()
    doc = mgr.create_document(
        filename="module.py",
        content="line1\nline2\nline3\nline4\n",
        cid="c_1",
    )
    assert doc["doc_id"]
    assert doc["filename"] == "module.py"
    assert doc["version"] == 1
    assert doc["cid"] == "c_1"

    updated = mgr.apply_range_edit(
        doc["doc_id"],
        start_line=2,
        end_line=3,
        replacement_content="new_line2\nnew_line3",
    )
    assert updated["version"] == 2
    assert updated["content"] == "line1\nnew_line2\nnew_line3\nline4\n"
    assert len(updated.get("history", [])) >= 1


def test_req_cap_03_deep_research_engine_two_phase_and_auto() -> None:
    """REQ-CAP-03: DeepResearchEngine generates 2-phase research plan, executes citations, and supports auto_approve."""
    engine = DeepResearchEngine()
    mock_client = MockCapabilitiesClient()

    plan = _resolve(engine.generate_research_plan(topic="Quantum Lattice Cryptography", client=mock_client))
    assert plan["plan_id"]
    assert plan["topic"] == "Quantum Lattice Cryptography"
    assert isinstance(plan["sub_questions"], list) and len(plan["sub_questions"]) >= 1
    assert plan["status"] == "PLAN_READY"
    assert plan["cid"]

    executed = _resolve(engine.execute_research_plan(plan["plan_id"], client=mock_client))
    assert executed["status"] == "COMPLETED"
    assert executed["report_markdown"].strip() != ""
    assert isinstance(executed["citations"], list) and len(executed["citations"]) >= 1
    assert executed["citations"][0]["url"]
    assert executed["citations"][0]["title"]

    e2e = _resolve(engine.run_deep_research(topic="Zero-Knowledge Rollups", auto_approve=True, client=mock_client))
    assert e2e["status"] == "COMPLETED"
    assert e2e["report_markdown"].strip() != ""


def test_req_cap_04_saved_info_crud_and_rules_sync(tmp_path: Path) -> None:
    """REQ-CAP-04: SavedInfoManager supports CRUD and local markdown rules deduplicated sync."""
    mgr = SavedInfoManager(storage_path=tmp_path / "saved_info.json")
    entry = mgr.add_memory(
        content="Always enforce 100% English-only source files.",
        category="engineering_rule",
    )
    assert entry["memory_id"]
    assert entry["content"] == "Always enforce 100% English-only source files."
    assert entry["category"] == "engineering_rule"

    items = mgr.list_memories()
    assert len(items) == 1
    assert items[0]["memory_id"] == entry["memory_id"]

    rules_md = tmp_path / "RULES.md"
    rules_md.write_text(
        "# Project Rules\n"
        "- Always enforce 100% English-only source files.\n"
        "- Maintain strict bidirectional REQ tags.\n"
        "- Never commit personal credentials.\n",
        encoding="utf-8",
    )
    sync_res = mgr.sync_rules_file(rules_md)
    assert sync_res["ok"] is True
    assert sync_res["added_count"] == 2
    assert sync_res["total_count"] == 3

    deleted = mgr.delete_memory(entry["memory_id"])
    assert deleted is True
    assert len(mgr.list_memories()) == 2


def test_req_cap_05_cloud_schedule_rpc_manager_crud(tmp_path: Path) -> None:
    """REQ-CAP-05: CloudScheduleRPCManager supports create, pause/resume, list, and delete."""
    mgr = CloudScheduleRPCManager(storage_path=tmp_path / "cloud_schedules.json")
    sched = mgr.create_cloud_schedule(
        name="Nightly Security Audit",
        prompt="Audit repo",
        cron_expr="0 3 * * *",
    )
    sched_id = sched["schedule_id"]
    assert sched_id
    assert sched["name"] == "Nightly Security Audit"
    assert sched["paused"] is False

    paused = mgr.pause_cloud_schedule(sched_id, paused=True)
    assert paused["paused"] is True

    all_scheds = mgr.list_cloud_schedules()
    assert len(all_scheds) == 1
    assert all_scheds[0]["schedule_id"] == sched_id
    assert all_scheds[0]["paused"] is True

    deleted = mgr.delete_cloud_schedule(sched_id)
    assert deleted is True
    assert len(mgr.list_cloud_schedules()) == 0


def test_req_cap_06_and_07_multi_draft_double_check_and_modifier() -> None:
    """REQ-CAP-06, REQ-CAP-07: Multi-draft rcid extraction/selection, double-check verifier, and response modifier."""
    acc = StreamingDeltaAccumulator()
    cand1: List[Any] = [None] * 10
    cand1[0] = "rcid_1"
    cand1[1] = ["Short draft"]
    cand1[8] = [2]

    cand2: List[Any] = [None] * 10
    cand2[0] = "rcid_2"
    cand2[1] = ["Complete draft with ```python\ndef f():\n    return 1\n```"]
    cand2[8] = [2]

    frame = _build_candidate_frame([cand1, cand2], cid="c_draft_1", rid="r_draft_1", rcid="rcid_1")
    chunks = acc.process_frame(frame)
    done_chunks = [c for c in chunks if c.type == "done"]
    assert len(done_chunks) == 1
    draft_candidates = done_chunks[0].metadata.get("draft_candidates", [])
    assert len(draft_candidates) == 2

    q_engine = ResponseQualityEngine()
    best = q_engine.select_best_draft(draft_candidates)
    assert best["rcid"] == "rcid_2"

    mock_client = MockCapabilitiesClient()
    dc_res = _resolve(
        q_engine.double_check_response(
            text="Python 3.12 was released in October 2023 according to https://docs.python.org/3.12/",
            client=mock_client,
        )
    )
    assert dc_res["ok"] is True
    assert isinstance(dc_res["verified_claims"], list) and len(dc_res["verified_claims"]) >= 1
    assert float(dc_res["trust_score"]) > 0.0

    mod_res = _resolve(
        q_engine.modify_response(
            text="Long verbose text that repeats unnecessary details over and over.",
            modifier="concise",
            client=mock_client,
        )
    )
    assert mod_res["ok"] is True
    assert mod_res["modifier"] == "concise"
    assert isinstance(mod_res["modified_text"], str) and mod_res["modified_text"].strip() != ""


def test_req_cap_01_to_07_fastapi_endpoints_and_mcp_tool(tmp_path: Path) -> None:
    """REQ-CAP-01..07: FastAPI /api/v1/capabilities/* router and MCPServer gemini_capabilities tool."""
    client = TestClient(app)

    sbx = client.post("/api/v1/capabilities/sandbox/run", json={"code": "print(21 * 2)"})
    assert sbx.status_code == 200 and sbx.json()["ok"] is True

    c_create = client.post(
        "/api/v1/capabilities/canvas/create",
        json={"filename": "demo.py", "content": "a = 1\nb = 2\nc = 3\n", "cid": "c_demo"},
    )
    assert c_create.status_code == 200
    doc_id = c_create.json()["doc_id"]

    c_edit = client.post(
        "/api/v1/capabilities/canvas/edit-range",
        json={"doc_id": doc_id, "start_line": 2, "end_line": 2, "replacement_content": "b = 20"},
    )
    assert c_edit.status_code == 200 and c_edit.json()["version"] == 2

    dr = client.post("/api/v1/capabilities/deep-research", json={"topic": "ZK Rollups", "auto_approve": True})
    assert dr.status_code == 200 and dr.json()["status"] == "COMPLETED"

    si_add = client.post(
        "/api/v1/capabilities/saved-info",
        json={"content": "Use strict typing everywhere.", "category": "rule"},
    )
    assert si_add.status_code == 200 and si_add.json()["memory_id"]
    si_list = client.get("/api/v1/capabilities/saved-info")
    assert si_list.status_code == 200 and len(si_list.json()["memories"]) >= 1

    cs_add = client.post(
        "/api/v1/capabilities/cloud-schedules",
        json={"name": "Morning Check", "prompt": "Check status", "cron_expr": "0 8 * * *"},
    )
    assert cs_add.status_code == 200 and cs_add.json()["schedule_id"]
    cs_list = client.get("/api/v1/capabilities/cloud-schedules")
    assert cs_list.status_code == 200 and len(cs_list.json()["schedules"]) >= 1

    dc = client.post(
        "/api/v1/capabilities/double-check",
        json={"text": "Python 3.12 was released in October 2023 according to https://docs.python.org/3.12/"},
    )
    assert dc.status_code == 200 and dc.json()["ok"] is True

    ds = client.post(
        "/api/v1/capabilities/drafts/select",
        json={
            "draft_candidates": [
                {"rcid": "rcid_1", "text": "Brief"},
                {"rcid": "rcid_2", "text": "Detailed:\n```python\ndef x():\n    return 1\n```"},
            ]
        },
    )
    assert ds.status_code == 200 and ds.json()["rcid"] == "rcid_2"

    mr = client.post(
        "/api/v1/capabilities/modify-response",
        json={"text": "Verbose text sample.", "modifier": "concise"},
    )
    assert mr.status_code == 200 and mr.json()["ok"] is True

    mcp = MCPServer()
    actions_payloads = [
        {"action": "sandbox_run", "code": "print(6 * 7)"},
        {"action": "canvas_create", "filename": "mcp.py", "content": "x = 1\ny = 2\n"},
        {"action": "canvas_edit_range", "doc_id": doc_id, "start_line": 1, "end_line": 1, "replacement_content": "a = 10"},
        {"action": "deep_research", "topic": "Lattice Crypto", "auto_approve": True},
        {"action": "saved_info_add", "content": "Rule from MCP", "category": "mcp"},
        {"action": "saved_info_list"},
        {"action": "cloud_schedule_create", "name": "MCP Job", "prompt": "Run", "cron_expr": "0 4 * * *"},
        {"action": "double_check", "text": "Fact with https://docs.python.org/3/"},
        {
            "action": "select_draft",
            "draft_candidates": [
                {"rcid": "rcid_a", "text": "tiny"},
                {"rcid": "rcid_b", "text": "full ```python\nprint(1)\n```"},
            ],
        },
        {"action": "modify_response", "text": "Please make this shorter.", "modifier": "shorter"},
    ]
    for idx, args in enumerate(actions_payloads, start=1):
        resp = _resolve(
            mcp.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 700 + idx,
                    "method": "tools/call",
                    "params": {"name": "gemini_capabilities", "arguments": args},
                },
                client=MockCapabilitiesClient(),
            )
        )
        assert resp is not None
        assert resp["result"]["isError"] is False
