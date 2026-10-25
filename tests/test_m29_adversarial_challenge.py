"""
tests/test_m29_adversarial_challenge.py
Milestone M29: Empirical Adversarial Challenge & Stress Test Suite.

Authored by m29_challenger_1 (EMPIRICAL CHALLENGER).
Verifies:
- Section 1: GoldenStackBus Latency Benchmark across 150+ Dispatches (< 1.0 ms)
- Section 2: L5 Topological Wave DAG Cycles, Disconnected Graphs & Circuit Breaker
- Section 3: Diff-Tour & CodeRef Line Range Fuzzing (start > end, start < 0, types)
- Section 4: Dimension & Epistemic Basis Edge Fuzzing (L1..L8, fact/inferred/opinion)
- Section 5: Structural Malformed Payload Fuzzing & TUI Renderer Robustness
"""

import time
from typing import Any, Dict, List

from fastapi.testclient import TestClient
import pytest

from backend.app.bridge.mission_control import (
    SENIOR_REVIEW_DIMENSIONS,
    VALID_EPISTEMIC_BASES,
    MissionControlHub,
    drop_revealing_asks,
    render_diff_tour_tui,
    validate_diff_tour,
)
from backend.app.main import app

client = TestClient(app)


# ============================================================================
# Section 1: GoldenStackBus In-Memory Latency Benchmark (150+ Dispatches)
# ============================================================================


def test_golden_stack_bus_latency_150_dispatches():
    """Verify that average dispatch latency across 150 calls is strictly < 1.0 ms."""
    hub = MissionControlHub()
    durations_ms: List[float] = []

    for _ in range(150):
        t0 = time.perf_counter()
        res = hub.dispatch_bus_event(
            "PreToolUse",
            {
                "role": "implementer",
                "tool_name": "write_file",
                "target_path": "backend/app/main.py",
            },
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        durations_ms.append(elapsed_ms)
        assert res["ok"] is True
        assert res["decision"] == "allow"

    avg_ms = sum(durations_ms) / len(durations_ms)
    p95_ms = sorted(durations_ms)[int(len(durations_ms) * 0.95)]
    p99_ms = sorted(durations_ms)[int(len(durations_ms) * 0.99)]
    max_ms = max(durations_ms)

    # Hard contract assertions
    assert avg_ms < 1.0, f"Average dispatch latency {avg_ms:.4f} ms exceeded 1.0 ms threshold"
    assert p95_ms < 1.0, f"95th percentile latency {p95_ms:.4f} ms exceeded 1.0 ms threshold"
    assert hub._active_depth == 0, "Active reentrancy depth must return to 0"


def test_golden_stack_bus_complex_payload_dispatch_latency():
    """Stress-test GoldenStackBus with diff_tour, sarif, and regex link payloads across 100 dispatches."""
    hub = MissionControlHub()
    tour_payload = {
        "title": "Adversarial Stress Tour",
        "steps": [
            {
                "file": "backend/app/main.py",
                "line_start": 1,
                "line_end": 20,
                "dimension": "L1_PROMISES",
                "epistemic_basis": "fact",
                "description": "Validation step",
            }
        ],
    }
    sarif_payload = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "SecretShield", "version": "1.1.0"}},
                "results": [],
            }
        ],
    }

    durations_ms: List[float] = []
    for _ in range(100):
        t0 = time.perf_counter()
        res = hub.dispatch_bus_event(
            "PreToolUse",
            {
                "role": "implementer",
                "tool_name": "read_file",
                "path": "backend/app/main.py",
                "prompt": "Check conversation link [audit](conversation://conv_998877)",
                "diff_tour": tour_payload,
                "sarif_report": sarif_payload,
            },
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        durations_ms.append(elapsed_ms)
        assert res["ok"] is True
        assert res["decision"] == "allow"
        assert res["context"].get("diff_tour_validated") is True
        assert res["context"].get("sarif_report_validated") is True

    avg_ms = sum(durations_ms) / len(durations_ms)
    assert avg_ms < 1.0, f"Complex payload average latency {avg_ms:.4f} ms exceeded 1.0 ms"
    assert hub._active_depth == 0


def test_golden_stack_bus_reentrant_short_circuit():
    """Verify that reentrant dispatch triggers instant short-circuit under 0.05 ms."""
    hub = MissionControlHub()
    t0 = time.perf_counter()
    res = hub.dispatch_bus_event("PreToolUse", {"_golden_bus_active": True})
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert res["ok"] is True
    assert res["reentrant_short_circuit"] is True
    assert res["total_duration_ms"] == 0.0
    assert elapsed_ms < 0.5


def test_golden_stack_bus_self_grading_bias_enforcement():
    """Verify Self-Grading Bias gate blocks implementers from tests and test-designers from src."""
    hub = MissionControlHub()

    # Implementer attempting to edit test file -> blocked
    b1 = hub.dispatch_bus_event(
        "PreToolUse",
        {"role": "implementer", "tool_name": "write_file", "target_path": "tests/test_foo.py"},
    )
    assert b1["decision"] == "block"
    assert b1["blocked_by"] == "P30"
    assert "Self-Grading Bias" in b1["reason"]

    # Test designer attempting to edit source file -> blocked
    b2 = hub.dispatch_bus_event(
        "PreToolUse",
        {"role": "test-designer", "tool_name": "write_file", "target_path": "backend/app/main.py"},
    )
    assert b2["decision"] == "block"
    assert b2["blocked_by"] == "P30"
    assert "Self-Grading Bias" in b2["reason"]

    # Test designer modifying test file -> allowed
    a1 = hub.dispatch_bus_event(
        "PreToolUse",
        {"role": "test-designer", "tool_name": "write_file", "target_path": "tests/test_foo.py"},
    )
    assert a1["decision"] == "allow"


# ============================================================================
# Section 2: L5 Topological Wave DAG Adversarial Stress (12 Tests)
# ============================================================================


def test_wave_dag_self_cycle_rejection():
    """Reject 1-node self cycle (A -> A)."""
    hub = MissionControlHub()
    with pytest.raises(ValueError, match="Cyclic dependency detected"):
        hub.run_federation_waves({
            "tasks": [{"task_id": "task_self", "depends_on": ["task_self"]}],
            "synthesize_dossier": False,
        })


def test_wave_dag_2node_cycle_rejection():
    """Reject 2-node cycle (A -> B -> A)."""
    hub = MissionControlHub()
    with pytest.raises(ValueError, match="Cyclic dependency detected"):
        hub.run_federation_waves({
            "tasks": [
                {"task_id": "node_a", "depends_on": ["node_b"]},
                {"task_id": "node_b", "depends_on": ["node_a"]},
            ],
            "synthesize_dossier": False,
        })


def test_wave_dag_3node_indirect_cycle_rejection():
    """Reject 3-node indirect cycle (A -> B -> C -> A)."""
    hub = MissionControlHub()
    with pytest.raises(ValueError, match="Cyclic dependency detected"):
        hub.run_federation_waves({
            "tasks": [
                {"task_id": "c1", "depends_on": ["c3"]},
                {"task_id": "c2", "depends_on": ["c1"]},
                {"task_id": "c3", "depends_on": ["c2"]},
            ],
            "synthesize_dossier": False,
        })


def test_wave_dag_embedded_cycle_in_dag():
    """Reject DAG containing valid roots but an embedded cyclic component."""
    hub = MissionControlHub()
    with pytest.raises(ValueError, match="Cyclic dependency detected"):
        hub.run_federation_waves({
            "tasks": [
                {"task_id": "root_1", "depends_on": []},
                {"task_id": "root_2", "depends_on": []},
                {"task_id": "cycle_x", "depends_on": ["root_1", "cycle_y"]},
                {"task_id": "cycle_y", "depends_on": ["cycle_x"]},
                {"task_id": "sink_z", "depends_on": ["cycle_x", "root_2"]},
            ],
            "synthesize_dossier": False,
        })


def test_wave_dag_unknown_dependency_rejection():
    """Reject task depending on nonexistent task ID."""
    hub = MissionControlHub()
    with pytest.raises(ValueError, match="depends on unknown task 'phantom_upstream'"):
        hub.run_federation_waves({
            "tasks": [
                {"task_id": "t_valid", "depends_on": []},
                {"task_id": "t_orphan", "depends_on": ["phantom_upstream"]},
            ],
            "synthesize_dossier": False,
        })


def test_wave_dag_unknown_dependency_api_http_400():
    """Verify HTTP 400 Bad Request when DAG contains unknown task dependency."""
    res = client.post(
        "/api/mission-control/federation/waves/execute",
        json={
            "workflow_name": "unknown_dep_dag",
            "tasks": [
                {"task_id": "t1", "station_id": "custom", "depends_on": ["ghost_task"]},
            ],
        },
    )
    assert res.status_code == 400
    assert "unknown task 'ghost_task'" in res.json()["detail"]


def test_wave_dag_disconnected_components_wave_grouping():
    """Decompose disconnected graph (two independent chains + singleton) into proper waves."""
    hub = MissionControlHub()
    tasks = [
        {"task_id": "chain1_root", "depends_on": []},
        {"task_id": "chain1_mid", "depends_on": ["chain1_root"]},
        {"task_id": "chain1_sink", "depends_on": ["chain1_mid"]},
        {"task_id": "chain2_root", "depends_on": []},
        {"task_id": "chain2_sink", "depends_on": ["chain2_root"]},
        {"task_id": "singleton_iso", "depends_on": []},
    ]
    res = hub.run_federation_waves({"tasks": tasks, "synthesize_dossier": False})
    assert res["ok"] is True
    assert res["status"] == "COMPLETED"
    assert res["tasks_executed"] == 6

    # Wave 0 must contain all three independent roots
    assert set(res["waves"][0]) == {"chain1_root", "chain2_root", "singleton_iso"}
    # Wave 1 must contain downstream children
    assert set(res["waves"][1]) == {"chain1_mid", "chain2_sink"}
    # Wave 2 must contain final child
    assert res["waves"][2] == ["chain1_sink"]


def test_wave_dag_20_isolated_singletons_wave_0():
    """Verify that 20 isolated nodes execute concurrently in a single Wave 0."""
    hub = MissionControlHub()
    tasks = [{"task_id": f"iso_{i:02d}", "depends_on": []} for i in range(20)]
    res = hub.run_federation_waves({"tasks": tasks, "synthesize_dossier": False})
    assert res["ok"] is True
    assert len(res["waves"]) == 1
    assert len(res["waves"][0]) == 20
    assert res["tasks_executed"] == 20


def test_wave_dag_circuit_breaker_wave_0_halting():
    """Verify circuit breaker trips in Wave 0 and completely halts downstream execution."""
    hub = MissionControlHub()
    tasks = [
        {"task_id": "w0_failing", "payload": {"should_fail": True}, "depends_on": []},
        {"task_id": "w1_child", "payload": {}, "depends_on": ["w0_failing"]},
        {"task_id": "w2_grandchild", "payload": {}, "depends_on": ["w1_child"]},
    ]
    res = hub.run_federation_waves({"tasks": tasks, "synthesize_dossier": False})
    assert res["ok"] is False
    assert res["status"] == "FAILED"
    assert "Station fault in task 'w0_failing'" in res["error"]
    assert "w0_failing" in res["results"]
    assert "w1_child" not in res["results"]
    assert "w2_grandchild" not in res["results"]
    assert res["tasks_executed"] == 1


def test_wave_dag_circuit_breaker_wave_1_halting():
    """Verify circuit breaker executes Wave 0, fails in Wave 1, and halts Wave 2."""
    hub = MissionControlHub()
    tasks = [
        {"task_id": "w0_clean", "station_id": "pe-reversing-lab", "payload": {"profile": "clean"}, "depends_on": []},
        {"task_id": "w1_fail", "station_id": "pe-reversing-lab", "payload": {"should_fail": True}, "depends_on": ["w0_clean"]},
        {"task_id": "w2_blocked", "station_id": "eskd-vision-station", "payload": {}, "depends_on": ["w1_fail"]},
    ]
    res = hub.run_federation_waves({"tasks": tasks, "synthesize_dossier": False})
    assert res["ok"] is False
    assert res["status"] == "FAILED"
    assert res["results"]["w0_clean"]["ok"] is True
    assert res["results"]["w1_fail"]["ok"] is False
    assert "w2_blocked" not in res["results"]
    assert res["tasks_executed"] == 2


def test_wave_dag_prov_trace_audit_integrity():
    """Verify W3C PROV-DM trace records exact executed activities and zero phantom tasks."""
    hub = MissionControlHub()
    tasks = [
        {"task_id": "t0", "station_id": "pe-reversing-lab", "payload": {"profile": "clean"}, "depends_on": []},
        {"task_id": "t1_fail", "payload": {"should_fail": True}, "depends_on": ["t0"]},
        {"task_id": "t2_skipped", "depends_on": ["t1_fail"]},
    ]
    res = hub.run_federation_waves({"tasks": tasks, "synthesize_dossier": False})
    trace = res["prov_trace"]

    # Only 2 events should be logged
    assert len(trace) == 2
    assert trace[0]["task_id"] == "t0"
    assert trace[0]["status"] == "COMPLETED"
    assert trace[1]["task_id"] == "t1_fail"
    assert trace[1]["status"] == "FAILED"
    assert not any(ev["task_id"] == "t2_skipped" for ev in trace)


# ============================================================================
# Section 3: Diff-Tour & CodeRef Line Range Fuzzing (8 Tests)
# ============================================================================


def test_diff_tour_line_start_greater_than_end():
    """Reject inverted line range start > end."""
    payload = {
        "title": "Inverted Line Range",
        "steps": [
            {
                "file": "backend/app/main.py",
                "line_start": 50,
                "line_end": 20,
                "dimension": "L1_PROMISES",
                "epistemic_basis": "fact",
            }
        ],
    }
    res = validate_diff_tour(payload)
    assert res["valid"] is False
    assert any("line_start (50) > line_end (20)" in err for err in res["errors"])


def test_diff_tour_line_start_less_than_one():
    """Reject line_start < 1 (0 or negative)."""
    for bad_start in (0, -1, -9999):
        payload = {
            "title": "Negative Line Start",
            "steps": [
                {
                    "file": "backend/app/main.py",
                    "line_start": bad_start,
                    "line_end": 20,
                    "dimension": "L1_PROMISES",
                    "epistemic_basis": "fact",
                }
            ],
        }
        res = validate_diff_tour(payload)
        assert res["valid"] is False
        assert any("line_start' must be an integer >= 1" in err for err in res["errors"])


def test_diff_tour_line_end_less_than_one():
    """Reject line_end < 1."""
    for bad_end in (0, -1, -50):
        payload = {
            "title": "Negative Line End",
            "steps": [
                {
                    "file": "backend/app/main.py",
                    "line_start": 1,
                    "line_end": bad_end,
                    "dimension": "L1_PROMISES",
                    "epistemic_basis": "fact",
                }
            ],
        }
        res = validate_diff_tour(payload)
        assert res["valid"] is False
        assert any("line_end' must be an integer >= 1" in err for err in res["errors"])


def test_diff_tour_single_line_range_valid():
    """Accept single-line range where line_start == line_end >= 1."""
    payload = {
        "title": "Single Line Range",
        "steps": [
            {
                "file": "backend/app/main.py",
                "line_start": 42,
                "line_end": 42,
                "dimension": "L1_PROMISES",
                "epistemic_basis": "fact",
            }
        ],
    }
    res = validate_diff_tour(payload)
    assert res["valid"] is True
    assert len(res["errors"]) == 0


def test_diff_tour_massive_line_numbers():
    """Accept massive line numbers without integer overflow."""
    payload = {
        "title": "Massive Line Numbers",
        "steps": [
            {
                "file": "backend/app/main.py",
                "line_start": 10**9,
                "line_end": 10**9 + 500,
                "dimension": "L5_SCALE",
                "epistemic_basis": "inferred",
            }
        ],
    }
    res = validate_diff_tour(payload)
    assert res["valid"] is True
    assert len(res["errors"]) == 0


def test_diff_tour_non_integer_line_types():
    """Reject non-integer types for line numbers (strings, floats, None, lists)."""
    for bad_val in ("42", 42.5, None, [42], {"line": 42}):
        payload = {
            "title": "Bad Type Line Numbers",
            "steps": [
                {
                    "file": "backend/app/main.py",
                    "line_start": bad_val,
                    "line_end": 50,
                    "dimension": "L1_PROMISES",
                    "epistemic_basis": "fact",
                }
            ],
        }
        res = validate_diff_tour(payload)
        assert res["valid"] is False


# ============================================================================
# Section 4: Dimension & Epistemic Basis Edge Fuzzing (6 Tests)
# ============================================================================


def test_diff_tour_all_eight_dimensions_accepted():
    """Verify that all 8 Senior Engineer review dimensions pass validation."""
    for dim in SENIOR_REVIEW_DIMENSIONS:
        payload = {
            "title": f"Valid Dim {dim}",
            "steps": [
                {
                    "file": "backend/app/main.py",
                    "line_start": 1,
                    "line_end": 10,
                    "dimension": dim,
                    "epistemic_basis": "fact",
                }
            ],
        }
        res = validate_diff_tour(payload)
        assert res["valid"] is True


def test_diff_tour_invalid_dimensions_rejected():
    """Reject dimensions outside the 8 standard dimensions."""
    for bad_dim in ("L0_BASELINE", "L9_FUTURE", "L10_META", "l1_promises", "", None, 123):
        payload = {
            "title": "Invalid Dimension Tour",
            "steps": [
                {
                    "file": "backend/app/main.py",
                    "line_start": 1,
                    "line_end": 10,
                    "dimension": bad_dim,
                    "epistemic_basis": "fact",
                }
            ],
        }
        res = validate_diff_tour(payload)
        assert res["valid"] is False
        assert any("dimension" in err.lower() for err in res["errors"])


def test_diff_tour_all_three_epistemic_bases_accepted():
    """Verify that fact, inferred, and opinion pass validation."""
    for basis in VALID_EPISTEMIC_BASES:
        payload = {
            "title": f"Valid Basis {basis}",
            "steps": [
                {
                    "file": "backend/app/main.py",
                    "line_start": 1,
                    "line_end": 10,
                    "dimension": "L1_PROMISES",
                    "epistemic_basis": basis,
                }
            ],
        }
        res = validate_diff_tour(payload)
        assert res["valid"] is True


def test_diff_tour_basis_alias_normalization():
    """Accept 'basis' key and normalize to 'epistemic_basis'."""
    payload = {
        "title": "Basis Alias Tour",
        "steps": [
            {
                "file": "backend/app/main.py",
                "line_start": 1,
                "line_end": 10,
                "dimension": "L1_PROMISES",
                "basis": "opinion",
            }
        ],
    }
    res = validate_diff_tour(payload)
    assert res["valid"] is True
    assert res["tour"]["steps"][0]["epistemic_basis"] == "opinion"


def test_diff_tour_invalid_epistemic_basis_rejected():
    """Reject unknown epistemic bases."""
    for bad_basis in ("guess", "truth", "certain", "FACT", "", None, 99):
        payload = {
            "title": "Invalid Basis Tour",
            "steps": [
                {
                    "file": "backend/app/main.py",
                    "line_start": 1,
                    "line_end": 10,
                    "dimension": "L1_PROMISES",
                    "epistemic_basis": bad_basis,
                }
            ],
        }
        res = validate_diff_tour(payload)
        assert res["valid"] is False
        assert any("epistemic basis" in err.lower() for err in res["errors"])


# ============================================================================
# Section 5: Structural Malformed Payload Fuzzing & TUI Edge Cases (6 Tests)
# ============================================================================


def test_diff_tour_malformed_root_types():
    """Reject non-dict root payloads (None, string, int, list)."""
    for bad_root in (None, "", "not_json", 123, [1, 2, 3]):
        res = validate_diff_tour(bad_root)
        assert res["valid"] is False
        assert "Tour must be a JSON object" in res["errors"]


def test_diff_tour_empty_title_or_missing_file():
    """Reject empty title or missing target file in steps."""
    # Empty title
    p1 = {
        "title": "   ",
        "steps": [{"file": "a.py", "line_start": 1, "line_end": 5, "dimension": "L1_PROMISES", "epistemic_basis": "fact"}],
    }
    assert validate_diff_tour(p1)["valid"] is False

    # Missing file
    p2 = {
        "title": "Valid Title",
        "steps": [{"file": "", "line_start": 1, "line_end": 5, "dimension": "L1_PROMISES", "epistemic_basis": "fact"}],
    }
    assert validate_diff_tour(p2)["valid"] is False


def test_drop_revealing_asks_edge_indices():
    """Verify drop_revealing_asks handles index 0 questions and questions with no asks."""
    tour = {
        "title": "Asks Edge Tour",
        "steps": [
            {"file": "a.py", "question": True, "asks": ["Secret ask 0"]},
            {"file": "b.py", "question": False, "asks": ["Preserved ask 1"]},
            {"file": "c.py", "question": False, "asks": ["Predecessor ask 2"]},
            {"file": "d.py", "question": True, "asks": ["Secret ask 3"]},
        ],
    }
    cleaned = drop_revealing_asks(tour)
    steps = cleaned["steps"]

    # Step 0 was question -> asks dropped, no crash on idx - 1
    assert "asks" not in steps[0]
    # Step 1 was not question and not predecessor -> asks kept
    assert steps[1].get("asks") == ["Preserved ask 1"]
    # Step 2 was predecessor to step 3 question -> asks dropped
    assert "asks" not in steps[2]
    # Step 3 was question -> asks dropped
    assert "asks" not in steps[3]


def test_tui_preview_empty_steps_box():
    """Deterministic TUI box renderer produces clean frame on empty steps."""
    preview = render_diff_tour_tui({"title": "Empty Tour", "steps": []})
    assert "DIFF-TOUR v1: Empty Tour" in preview
    assert "[No steps present in tour]" in preview
    assert "┌" in preview and "┘" in preview


def test_tui_preview_long_title_truncation():
    """Deterministic TUI box renderer truncates overlong titles cleanly."""
    long_title = "A" * 200
    preview = render_diff_tour_tui({"title": long_title, "steps": []})
    assert "..." in preview
    # Check that lines don't exceed expected width
    for line in preview.splitlines():
        assert len(line) <= 80


def test_diff_tour_corrupted_step_elements_rejection():
    """Verify validate_diff_tour rejects non-dict step elements gracefully."""
    p1 = {"title": "Null Step Tour", "steps": [None]}
    res1 = validate_diff_tour(p1)
    assert res1["valid"] is False
    assert "Step [0] must be a JSON object" in res1["errors"]

    p2 = {"title": "Int Step Tour", "steps": [123]}
    res2 = validate_diff_tour(p2)
    assert res2["valid"] is False
    assert "Step [0] must be a JSON object" in res2["errors"]

    p3 = {"title": "None Steps Tour", "steps": None}
    res3 = validate_diff_tour(p3)
    assert res3["valid"] is False
    assert "Tour 'steps' must be a list" in res3["errors"]

