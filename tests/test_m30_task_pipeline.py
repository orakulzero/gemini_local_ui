"""
tests/test_m30_task_pipeline.py
Locked Agile V (P30) Verification Suite for M30:
Autonomous Gemini 4 Argon Task Mode Pipeline, Scheduled Orchestrator & Self-Healing Verifier.
Covers REQ-PIPE-01 through REQ-PIPE-06.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any, Dict, List, Optional
import zipfile

from fastapi.testclient import TestClient

from backend.app.bridge.task_pipeline import (
    TaskPipelineOrchestrator,
    pack_workspace_context,
    verify_five_quality_gates,
)
from backend.app.main import app


def _build_sample_calc_source(multiply_expr: str = "a * b", extra_comment: str = "# REQ-CALC-01") -> str:
    """Builds a 26-line Python module with explicit requirement tags and type annotations."""
    lines = [
        '"""Sample calculator module for M30 task pipeline verification."""',
        extra_comment,
        "",
        "def add(a: int, b: int) -> int:",
        '    """REQ-CALC-01: Adds two integers."""',
        "    result = a + b",
        "    return result",
        "",
        "def subtract(a: int, b: int) -> int:",
        '    """REQ-CALC-01: Subtracts b from a."""',
        "    result = a - b",
        "    return result",
        "",
        "def multiply(a: int, b: int) -> int:",
        '    """REQ-CALC-01: Multiplies two integers."""',
        f"    result = {multiply_expr}",
        "    return result",
        "",
        "def clamp(val: int, low: int, high: int) -> int:",
        '    """REQ-CALC-01: Clamps integer between low and high bounds."""',
        "    if val < low:",
        "        return low",
        "    if val > high:",
        "        return high",
        "    return val",
        "",
        "DEFAULT_SCALE = 1",
        "MAX_BOUND = 1000",
        "MIN_BOUND = -1000",
    ]
    return "\n".join(lines) + "\n"


def _create_mini_project(root: Path, initial_multiply: str = "0") -> Path:
    """Creates a realistic mini-project in root with src/, tests/, and noise directories."""
    src_dir = root / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "__init__.py").write_text('"""Package init."""\n', encoding="utf-8")
    (src_dir / "calc.py").write_text(
        _build_sample_calc_source(multiply_expr=initial_multiply),
        encoding="utf-8",
    )

    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    test_code = (
        '"""Unit tests for mini calculator."""\n'
        "from src.calc import add, multiply\n\n"
        "def test_add_and_multiply() -> None:\n"
        "    assert add(2, 3) == 5\n"
        "    assert multiply(3, 4) == 12\n"
    )
    (tests_dir / "test_calc.py").write_text(test_code, encoding="utf-8")

    # Noise directories that must be pruned by pack_workspace_context
    for noise in (".git", "__pycache__", ".ruff_cache", "node_modules"):
        ndir = root / noise
        ndir.mkdir(parents=True, exist_ok=True)
        (ndir / "junk.txt").write_text("noise data", encoding="utf-8")

    return root


def test_req_pipe_01_context_packer_and_ast_contract(tmp_path: Path) -> None:
    """REQ-PIPE-01: Context archive bundler excludes noise dirs and extracts AST/REQ contracts."""
    _create_mini_project(tmp_path, initial_multiply="a * b")

    bundle = pack_workspace_context(tmp_path)
    assert "zip_bytes" in bundle and isinstance(bundle["zip_bytes"], bytes)
    assert bundle["zip_filename"].endswith(".zip")
    assert bundle["file_count"] >= 3
    assert "REQ-CALC-01" in bundle["req_tags"]
    assert "src/calc.py" in bundle["ast_contracts"]
    assert "def add(a: int, b: int) -> int:" in bundle["ast_contracts"]["src/calc.py"]

    with zipfile.ZipFile(io.BytesIO(bundle["zip_bytes"]), "r") as zf:
        names = zf.namelist()
        assert "src/calc.py" in names
        assert "tests/test_calc.py" in names
        assert not any(".git" in n or "__pycache__" in n or ".ruff_cache" in n for n in names)


def test_req_pipe_04_five_quality_gates_enforcement(tmp_path: Path) -> None:
    """REQ-PIPE-04: 5-Gate Quality Verifier enforces pytest, secrets, AST/REQ, English-only, and >=95% lines."""
    _create_mini_project(tmp_path, initial_multiply="a * b")
    orig_code = (tmp_path / "src" / "calc.py").read_text(encoding="utf-8")
    orig_files = {"src/calc.py": orig_code}

    # 1. Clean candidate passes all 5 gates
    clean_res = verify_five_quality_gates(
        workspace_dir=tmp_path,
        orig_files=orig_files,
        candidate_files={"src/calc.py": orig_code},
        run_pytest=False,
    )
    assert clean_res["passed"] is True
    for gate_key in ("gate1_pytest", "gate2_secrets", "gate3_ast_req", "gate4_english", "gate5_line_cap"):
        assert clean_res["gates"][gate_key] is True

    # 2. Candidate dropping REQ-CALC-01 fails gate3_ast_req
    no_req_code = orig_code.replace("REQ-CALC-01", "REMOVED-TAG")
    res_gate3 = verify_five_quality_gates(tmp_path, orig_files, {"src/calc.py": no_req_code}, run_pytest=False)
    assert res_gate3["passed"] is False
    assert res_gate3["gates"]["gate3_ast_req"] is False

    # 3. Candidate truncating > 5% of non-empty lines fails gate5_line_cap
    truncated_lines = orig_code.splitlines()[:15]
    res_gate5 = verify_five_quality_gates(
        tmp_path, orig_files, {"src/calc.py": "\n".join(truncated_lines) + "\n"}, run_pytest=False
    )
    assert res_gate5["passed"] is False
    assert res_gate5["gates"]["gate5_line_cap"] is False

    # 4. Candidate containing Cyrillic characters fails gate4_english
    cyrillic_code = orig_code + "\n# \u0422\u0435\u0441\u0442\n"
    res_gate4 = verify_five_quality_gates(tmp_path, orig_files, {"src/calc.py": cyrillic_code}, run_pytest=False)
    assert res_gate4["passed"] is False
    assert res_gate4["gates"]["gate4_english"] is False

    # 5. Candidate containing a secret token fails gate2_secrets
    fake_token = "ghp_" + ("A" * 36)
    secret_code = orig_code + f'\nAPI_TOKEN = "{fake_token}"\n'
    res_gate2 = verify_five_quality_gates(tmp_path, orig_files, {"src/calc.py": secret_code}, run_pytest=False)
    assert res_gate2["passed"] is False
    assert res_gate2["gates"]["gate2_secrets"] is False


def test_req_pipe_02_schedule_crud_and_due_trigger(tmp_path: Path) -> None:
    """REQ-PIPE-02: TaskPipelineOrchestrator supports schedule CRUD, pause/resume, and due checks."""
    storage_file = tmp_path / "pipeline.json"
    orchestrator = TaskPipelineOrchestrator(storage_path=storage_file)

    sched = orchestrator.create_schedule(
        name="Nightly Core Refactor",
        workspace_dir=str(tmp_path),
        prompt="Optimize calc.py",
        schedule_type="interval",
        interval_seconds=60,
        target_files=["src/calc.py"],
    )
    sched_id = sched["schedule_id"]
    assert sched["name"] == "Nightly Core Refactor"
    assert sched["enabled"] is True

    schedules = orchestrator.list_schedules()
    assert len(schedules) == 1
    assert schedules[0]["schedule_id"] == sched_id

    paused = orchestrator.update_schedule(sched_id, enabled=False)
    assert paused["enabled"] is False

    resumed = orchestrator.update_schedule(sched_id, enabled=True)
    assert resumed["enabled"] is True

    deleted = orchestrator.delete_schedule(sched_id)
    assert deleted is True
    assert len(orchestrator.list_schedules()) == 0


def test_req_pipe_03_and_05_stateful_cid_self_healing_and_stage_only_approval(tmp_path: Path) -> None:
    """REQ-PIPE-03, REQ-PIPE-05: Stateful CID/RID/RCID self-healing loop and Stage-Only approval gate."""
    _create_mini_project(tmp_path, initial_multiply="0")
    calc_path = tmp_path / "src" / "calc.py"
    initial_disk_content = calc_path.read_text(encoding="utf-8")

    orchestrator = TaskPipelineOrchestrator(storage_path=tmp_path / "pipeline.json")
    turn_calls: List[Dict[str, Any]] = []

    async def mock_task_runner(
        prompt: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
        cid: Optional[str] = None,
        rid: Optional[str] = None,
        rcid: Optional[str] = None,
    ) -> Dict[str, Any]:
        turn_calls.append({"prompt": prompt, "attachments": attachments, "cid": cid, "rid": rid, "rcid": rcid})
        if cid is None:
            # Turn 1: intentional bug in multiply (a + b instead of a * b) so pytest Gate 1 fails
            buggy_code = _build_sample_calc_source(multiply_expr="a + b")
            return {
                "cid": "c_task_101",
                "rid": "r_turn_1",
                "rcid": "rc_turn_1",
                "text": f"### File: src/calc.py\n```python\n{buggy_code}```\n",
            }
        # Turn 2: self-healing turn must preserve stateful session IDs and include failure diagnostic
        assert cid == "c_task_101"
        assert rid == "r_turn_1"
        assert rcid == "rc_turn_1"
        assert "FAILED" in prompt or "AssertionError" in prompt or "gate1_pytest" in prompt
        fixed_code = _build_sample_calc_source(multiply_expr="a * b")
        return {
            "cid": "c_task_101",
            "rid": "r_turn_2",
            "rcid": "rc_turn_2",
            "text": f"### File: src/calc.py\n```python\n{fixed_code}```\n",
        }

    run = asyncio.run(
        orchestrator.execute_pipeline_run(
            workspace_dir=str(tmp_path),
            prompt="Implement multiply(a, b) in src/calc.py",
            target_files=["src/calc.py"],
            task_runner=mock_task_runner,
        )
    )

    assert len(turn_calls) == 2
    assert run["status"] == "STAGED_AWAITING_APPROVAL"
    assert run["heal_attempts"] == 1
    assert run["cid"] == "c_task_101"
    assert run["rid"] == "r_turn_2"
    assert run["rcid"] == "rc_turn_2"
    assert run["gates"]["passed"] is True
    assert "+    result = a * b" in run["unified_diff"]

    # CRITICAL Stage-Only check: disk file MUST NOT be modified before explicit approval
    assert calc_path.read_text(encoding="utf-8") == initial_disk_content

    approved_run = orchestrator.approve_run(run["run_id"])
    assert approved_run["status"] == "APPROVED_APPLIED"
    assert "result = a * b" in calc_path.read_text(encoding="utf-8")

    # Verify reject_run leaves disk untouched on a second run
    calc_path.write_text(initial_disk_content, encoding="utf-8")
    run2 = asyncio.run(
        orchestrator.execute_pipeline_run(
            workspace_dir=str(tmp_path),
            prompt="Second run to test rejection",
            target_files=["src/calc.py"],
            task_runner=lambda prompt, attachments=None, cid=None, rid=None, rcid=None: {
                "cid": "c_task_202",
                "rid": "r_202",
                "rcid": "rc_202",
                "text": f"### File: src/calc.py\n```python\n{_build_sample_calc_source('a * b')}```\n",
            },
        )
    )
    assert run2["status"] == "STAGED_AWAITING_APPROVAL"
    rejected_run = orchestrator.reject_run(run2["run_id"], reason="Manual review declined")
    assert rejected_run["status"] == "REJECTED"
    assert calc_path.read_text(encoding="utf-8") == initial_disk_content


def test_req_pipe_06_fastapi_endpoints_and_mcp_tool(tmp_path: Path) -> None:
    """REQ-PIPE-06: FastAPI /api/task-pipeline/* endpoints and MCP gemini_task_pipeline tool."""
    _create_mini_project(tmp_path, initial_multiply="a * b")
    client = TestClient(app)

    # 1. Create and list schedules
    create_resp = client.post(
        "/api/task-pipeline/schedules",
        json={
            "name": "API Schedule Test",
            "workspace_dir": str(tmp_path),
            "prompt": "Audit src/calc.py",
            "schedule_type": "interval",
            "interval_seconds": 120,
            "target_files": ["src/calc.py"],
        },
    )
    assert create_resp.status_code == 200
    sched_data = create_resp.json()
    sched_id = sched_data["schedule_id"]

    list_resp = client.get("/api/task-pipeline/schedules")
    assert list_resp.status_code == 200
    assert any(s["schedule_id"] == sched_id for s in list_resp.json()["schedules"])

    # 2. Execute a staged pipeline run via API (dry/mocked candidate)
    exec_resp = client.post(
        "/api/task-pipeline/runs/execute",
        json={
            "workspace_dir": str(tmp_path),
            "prompt": "Add harmless comment to src/calc.py",
            "target_files": ["src/calc.py"],
            "mock_candidate_files": {
                "src/calc.py": _build_sample_calc_source(multiply_expr="a * b", extra_comment="# REQ-CALC-01 Verified")
            },
            "run_pytest": False,
        },
    )
    assert exec_resp.status_code == 200
    run_data = exec_resp.json()
    run_id = run_data["run_id"]
    assert run_data["status"] == "STAGED_AWAITING_APPROVAL"

    # 3. Get runs list, single run, and approve run
    runs_resp = client.get("/api/task-pipeline/runs")
    assert runs_resp.status_code == 200
    assert any(r["run_id"] == run_id for r in runs_resp.json()["runs"])

    single_resp = client.get(f"/api/task-pipeline/runs/{run_id}")
    assert single_resp.status_code == 200
    assert single_resp.json()["run_id"] == run_id

    approve_resp = client.post(f"/api/task-pipeline/runs/{run_id}/approve")
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "APPROVED_APPLIED"

    # 4. Cloud Scheduled Actions sync endpoint
    cloud_resp = client.post("/api/task-pipeline/cloud-scheduled/sync")
    assert cloud_resp.status_code == 200
    assert "cloud_actions" in cloud_resp.json()

    # 5. MCP tool gemini_task_pipeline via POST /mcp
    mcp_sched = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 901,
            "method": "tools/call",
            "params": {"name": "gemini_task_pipeline", "arguments": {"action": "list_schedules"}},
        },
    )
    assert mcp_sched.status_code == 200
    assert mcp_sched.json()["result"]["isError"] is False

    mcp_runs = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 902,
            "method": "tools/call",
            "params": {"name": "gemini_task_pipeline", "arguments": {"action": "list_runs"}},
        },
    )
    assert mcp_runs.status_code == 200
    assert mcp_runs.json()["result"]["isError"] is False

    # Cleanup schedule
    del_resp = client.delete(f"/api/task-pipeline/schedules/{sched_id}")
    assert del_resp.status_code == 200


def test_req_pipe_07_ultra_5x_pipeline_integration(tmp_path: Path) -> None:
    """REQ-PIPE-07: Ultra 5x profile, Canvas RangeEdit patching, and multi-draft quality selection."""
    from backend.app.bridge.task_pipeline import extract_code_files_from_response

    # 1. Verify Canvas targeted RangeEdit line replacement preserves surrounding lines intact
    orig_mod = {"src/mod.py": "line1\nline2\nline3\nline4\n"}
    range_response = (
        "### RangeEdit: src/mod.py:2-3\n"
        "```python\n"
        "updated_line2\n"
        "updated_line3\n"
        "```\n"
    )
    patched_files = extract_code_files_from_response(range_response, orig_files=orig_mod)
    assert patched_files == {"src/mod.py": "line1\nupdated_line2\nupdated_line3\nline4\n"}

    # 2. Verify schedule creation stores Ultra 5x Deep-Think & 2M parameters
    _create_mini_project(tmp_path, initial_multiply="a * b")
    orchestrator = TaskPipelineOrchestrator(storage_path=tmp_path / "state.json")
    sched = orchestrator.create_schedule(
        name="Ultra 5x Deep Think Schedule",
        workspace_dir=str(tmp_path),
        prompt="Verify and annotate src/calc.py",
        model="gemini-4-argon-deep-think",
        thinking=3,
        use_ultra_2m=True,
        target_files=["src/calc.py"],
    )
    assert sched["model"] == "gemini-4-argon-deep-think"
    assert sched["thinking"] == 3
    assert sched["use_ultra_2m"] is True

    # 3. Verify execute_pipeline_run selects best draft candidate, applies RangeEdit, and computes trust
    async def mock_ultra_runner(
        prompt: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
        cid: Optional[str] = None,
        rid: Optional[str] = None,
        rcid: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "cid": "c_ultra_5x",
            "rid": "r_ultra_5x",
            "rcid": "rc_draft_truncated",
            "text": "### File: src/calc.py\n```python\ndef add(a: int, b: int) -> int:\n    return 0\n```\n",
            "draft_candidates": [
                {
                    "rcid": "rc_draft_truncated",
                    "text": "### File: src/calc.py\n```python\ndef add(a: int, b: int) -> int:\n    return 0\n```\n",
                    "finish_reason": "MAX_TOKENS",
                    "citations": [],
                },
                {
                    "rcid": "rc_draft_best",
                    "text": (
                        "Verified REQ-CALC-01 implementation against [1](https://docs.python.org/3/).\n"
                        "### RangeEdit: src/calc.py:2-2\n"
                        "```python\n"
                        "# REQ-CALC-01: Ultra 5x RangeEdit verified\n"
                        "```\n"
                    ),
                    "finish_reason": "STOP",
                    "citations": [{"url": "https://docs.python.org/3/", "title": "Python Docs"}],
                },
            ],
        }

    run_rec = asyncio.run(
        orchestrator.execute_pipeline_run(
            workspace_dir=str(tmp_path),
            prompt="Apply targeted header refinement to src/calc.py",
            target_files=["src/calc.py"],
            model="gemini-4-argon-deep-think",
            thinking=3,
            use_ultra_2m=True,
            task_runner=mock_ultra_runner,
            schedule_id=sched["schedule_id"],
        )
    )

    assert run_rec["status"] == "STAGED_AWAITING_APPROVAL"
    assert run_rec["gates"]["passed"] is True
    assert run_rec["ultra_profile"]["tier"] == "ultra_5x"
    assert run_rec["selected_rcid"] == "rc_draft_best"
    assert run_rec["double_check_trust"] > 0.0
    assert "# REQ-CALC-01: Ultra 5x RangeEdit verified" in run_rec["candidate_files"]["src/calc.py"]
    assert "def multiply(a: int, b: int) -> int:" in run_rec["candidate_files"]["src/calc.py"]


def test_req_pipe_08_mcp_skills_rules_and_gem_pipeline_integration(tmp_path: Path) -> None:
    """REQ-PIPE-08: Workspace skills/rules discovery, Gem persona routing, and in-process MCP tool loop."""
    from backend.app.bridge.task_pipeline import discover_workspace_skills_and_rules

    _create_mini_project(tmp_path, initial_multiply="a * b")

    # 1. Create workspace skills and rules and verify discovery
    skill_dir = tmp_path / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Demo skill\n---\nAlways validate bounds.\n",
        encoding="utf-8",
    )
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "AGENTS.md").write_text("- Enforce strict bounds check.\n", encoding="utf-8")

    discovered = discover_workspace_skills_and_rules(tmp_path)
    assert any(s.get("name") == "demo-skill" for s in discovered["skills"])
    assert len(discovered["rules"]) >= 1
    assert "demo-skill" in discovered["prompt_block"]
    assert "Enforce strict bounds check." in discovered["prompt_block"]

    # 2. Execute pipeline run with MCP tool-call loop, active skills/rules, and gem_id
    orchestrator = TaskPipelineOrchestrator(storage_path=tmp_path / "state_pipe08.json")
    prompts_seen: List[str] = []

    async def mock_mcp_runner(
        prompt: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
        cid: Optional[str] = None,
        rid: Optional[str] = None,
        rcid: Optional[str] = None,
    ) -> Dict[str, Any]:
        prompts_seen.append(prompt)
        if len(prompts_seen) == 1:
            assert "# Active Skills & Rules" in prompt
            assert "demo-skill" in prompt
            assert "# Available MCP Tools" in prompt
            assert "gemini_capabilities" in prompt
            return {
                "cid": "c_mcp_08",
                "rid": "r_mcp_08_t1",
                "rcid": "rc_mcp_08_t1",
                "text": (
                    "Inspecting capabilities via MCP tool first.\n"
                    "```tool_call\n"
                    '{"name": "gemini_capabilities", "arguments": {"action": "ultra_profile", "account_id": "acc_21aac20f"}}\n'
                    "```\n"
                ),
            }
        assert cid == "c_mcp_08"
        assert "# MCP Tool Result (gemini_capabilities)" in prompt
        assert "ultra_5x" in prompt
        return {
            "cid": "c_mcp_08",
            "rid": "r_mcp_08_t2",
            "rcid": "rc_mcp_08_t2",
            "text": (
                "### RangeEdit: src/calc.py:2-2\n"
                "```python\n"
                "# REQ-CALC-01: MCP + Skill + Gem verified\n"
                "```\n"
            ),
        }

    run_rec = asyncio.run(
        orchestrator.execute_pipeline_run(
            workspace_dir=str(tmp_path),
            prompt="Use MCP tool and workspace skills to verify src/calc.py",
            target_files=["src/calc.py"],
            gem_id="gem_ultra_pipeline",
            enable_mcp_tools=True,
            task_runner=mock_mcp_runner,
        )
    )

    assert run_rec["status"] == "STAGED_AWAITING_APPROVAL"
    assert run_rec["gem_id"] == "gem_ultra_pipeline"
    assert len(run_rec["mcp_tool_calls"]) == 1
    assert run_rec["mcp_tool_calls"][0]["name"] == "gemini_capabilities"
    assert len(run_rec["active_skills"]) >= 1
    assert "demo-skill" in run_rec["active_skills"]
    assert run_rec["gates"]["passed"] is True
    assert "# REQ-CALC-01: MCP + Skill + Gem verified" in run_rec["candidate_files"]["src/calc.py"]

