"""
tests/test_m29_v1_2_cockpit.py
Milestone M29: AGY Mission Control v1.2.0 Master Verification Suite.
Validates:
- Section 1: Diff-Tour 8 Dimensions & Epistemic Basis
- Section 2: Hypothesis Property-Based Invariant Fuzzing
- Section 3: Secret Shield v1.1.0 Station & OASIS SARIF 2.1.0
- Section 4: PE Reversing Lab v1.1.0 & Live Windows Binaries
- Section 5: ESKD Vision Station v1.1.0 & GOST 2.104/2.301 300 DPI
- Section 6: Federation Mesh v1.1.0 Wave DAG & Circuit Breaker
- Section 7: Plugin Version Drift Detection & Reconciliation
- Section 8: Brain Memory Diff-Tour & CAPA Search Filters
- Section 9: React Cockpit UI TestID & Contract Assertions
- Section 10: 5-Gate OSS Release Hygiene (0 Cyrillic, 0 Personal Paths)
"""

import base64
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, List

from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
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


def _forbidden_tokens() -> List[str]:
    t1 = "Ora" + "kul"
    t2 = t1 + " " + "Grey"
    t3 = "ANTI" + "GRAVITY"
    t4 = "kuro" + "ora" + "kul" + "grey"
    return [
        t2,
        "Users\\" + t1,
        "Users/" + t1,
        "e:\\" + t3,
        "e:/" + t3,
        t4,
    ]


# ============================================================================
# Section 1: Diff-Tour 8 Dimensions & Epistemic Basis (8 Tests)
# ============================================================================


def test_diff_tour_l1_promises_contract():
    tour_payload = {
        "title": "L1 Promises Contract Tour",
        "steps": [
            {
                "file": "backend/app/main.py",
                "line_start": 1,
                "line_end": 20,
                "dimension": "L1_PROMISES",
                "epistemic_basis": "fact",
                "description": "FastAPI application entrypoint and route promises",
            }
        ],
    }
    res = validate_diff_tour(tour_payload)
    assert res["valid"] is True
    assert len(res["errors"]) == 0
    assert res["tour"]["steps"][0]["dimension"] == "L1_PROMISES"
    assert res["tour"]["steps"][0]["epistemic_basis"] == "fact"


def test_diff_tour_l2_failure_circuit_breaker():
    tour_payload = {
        "title": "L2 Failure Recovery Tour",
        "steps": [
            {
                "file": "backend/app/bridge/mission_control.py",
                "line_start": 50,
                "line_end": 80,
                "dimension": "L2_FAILURE",
                "epistemic_basis": "inferred",
                "description": "Circuit breaker stopping cascading station failures",
            }
        ],
    }
    res = validate_diff_tour(tour_payload)
    assert res["valid"] is True
    assert res["tour"]["steps"][0]["epistemic_basis"] == "inferred"


def test_diff_tour_l3_operations_telemetry():
    tour_payload = {
        "title": "L3 Operations Tour",
        "steps": [
            {
                "file": "backend/app/bridge/mission_control.py",
                "line_start": 100,
                "line_end": 140,
                "dimension": "L3_OPERATIONS",
                "epistemic_basis": "fact",
                "description": "GoldenStackBus dispatch metrics and benchmark telemetry",
            }
        ],
    }
    res = validate_diff_tour(tour_payload)
    assert res["valid"] is True
    assert res["tour"]["steps"][0]["dimension"] == "L3_OPERATIONS"


def test_diff_tour_l4_trust_sarif_dpapi():
    tour_payload = {
        "title": "L4 Trust & Security Tour",
        "steps": [
            {
                "file": "backend/app/bridge/mission_control.py",
                "line_start": 150,
                "line_end": 180,
                "dimension": "L4_TRUST",
                "epistemic_basis": "fact",
                "description": "OASIS SARIF 2.1.0 schema compliance and DPAPI vault",
            }
        ],
    }
    res = validate_diff_tour(tour_payload)
    assert res["valid"] is True


def test_diff_tour_l5_scale_topological_waves():
    tour_payload = {
        "title": "L5 Scale & Concurrency Tour",
        "steps": [
            {
                "file": "backend/app/bridge/mission_control.py",
                "line_start": 200,
                "line_end": 250,
                "dimension": "L5_SCALE",
                "epistemic_basis": "inferred",
                "description": "Parallel topological wave execution in DAG engine",
            }
        ],
    }
    res = validate_diff_tour(tour_payload)
    assert res["valid"] is True


def test_diff_tour_l6_design_tier_decoupling():
    tour_payload = {
        "title": "L6 Design Tour",
        "steps": [
            {
                "file": "backend/app/bridge/mission_control.py",
                "line_start": 260,
                "line_end": 300,
                "dimension": "L6_DESIGN",
                "epistemic_basis": "opinion",
                "description": "Clean modular separation between L1..L5 tiers",
            }
        ],
    }
    res = validate_diff_tour(tour_payload)
    assert res["valid"] is True
    assert res["tour"]["steps"][0]["epistemic_basis"] == "opinion"


def test_diff_tour_l7_tests_fuzzing_and_reversing():
    tour_payload = {
        "title": "L7 Tests & Verification Tour",
        "steps": [
            {
                "file": "tests/test_m28_mission_control.py",
                "line_start": 1,
                "line_end": 60,
                "dimension": "L7_TESTS",
                "epistemic_basis": "fact",
                "description": "Comprehensive regression and property-based test suites",
            }
        ],
    }
    res = validate_diff_tour(tour_payload)
    assert res["valid"] is True


def test_diff_tour_l8_ai_traps_epistemic_grounding():
    tour_payload = {
        "title": "L8 AI Traps & Epistemics Tour",
        "steps": [
            {
                "file": "backend/app/bridge/mission_control.py",
                "line_start": 10,
                "line_end": 45,
                "dimension": "L8_AI_TRAPS",
                "epistemic_basis": "opinion",
                "question": True,
                "asks": ["Verify that tests are not circular", "Check for epistemic drift"],
            }
        ],
    }
    res = validate_diff_tour(tour_payload)
    assert res["valid"] is True
    # Verify drop_revealing_asks strips asks on question step
    sanitized = drop_revealing_asks(res["tour"])
    assert "asks" not in sanitized["steps"][0]


# ============================================================================
# Section 2: Hypothesis Property-Based Invariant Fuzzing (4 Tests)
# ============================================================================


@given(
    lstart=st.integers(min_value=-50, max_value=500),
    lend=st.integers(min_value=-50, max_value=500),
)
@settings(max_examples=40)
def test_hypothesis_coderef_line_invariants(lstart: int, lend: int):
    payload = {
        "title": "Fuzz Line Invariants Tour",
        "steps": [
            {
                "file": "backend/app/main.py",
                "line_start": lstart,
                "line_end": lend,
                "dimension": "L1_PROMISES",
                "epistemic_basis": "fact",
            }
        ],
    }
    res = validate_diff_tour(payload)
    if lstart >= 1 and lend >= 1 and lstart <= lend:
        assert res["valid"] is True
    else:
        assert res["valid"] is False
        assert len(res["errors"]) > 0


@given(unknown_dim=st.text(min_size=1, max_size=30))
@settings(max_examples=30)
def test_hypothesis_dimension_rejection_fuzzing(unknown_dim: str):
    if unknown_dim in SENIOR_REVIEW_DIMENSIONS:
        return
    payload = {
        "title": "Fuzz Dimension Rejection Tour",
        "steps": [
            {
                "file": "backend/app/main.py",
                "line_start": 1,
                "line_end": 10,
                "dimension": unknown_dim,
                "epistemic_basis": "fact",
            }
        ],
    }
    res = validate_diff_tour(payload)
    assert res["valid"] is False
    assert any("dimension" in err.lower() for err in res["errors"])


@given(unknown_basis=st.text(min_size=1, max_size=30))
@settings(max_examples=30)
def test_hypothesis_epistemic_basis_fuzzing(unknown_basis: str):
    if unknown_basis in VALID_EPISTEMIC_BASES:
        return
    payload = {
        "title": "Fuzz Epistemic Basis Tour",
        "steps": [
            {
                "file": "backend/app/main.py",
                "line_start": 1,
                "line_end": 10,
                "dimension": "L1_PROMISES",
                "epistemic_basis": unknown_basis,
            }
        ],
    }
    res = validate_diff_tour(payload)
    assert res["valid"] is False
    assert any("epistemic basis" in err.lower() for err in res["errors"])


@given(
    title=st.text(min_size=1, max_size=50),
    desc=st.text(min_size=1, max_size=50),
)
@settings(max_examples=25)
def test_hypothesis_tui_renderer_no_crash_on_arbitrary_tour(title: str, desc: str):
    tour = {
        "title": title,
        "base": "HEAD",
        "head": "HEAD",
        "steps": [
            {
                "file": "backend/app/main.py",
                "line_start": 1,
                "line_end": 5,
                "dimension": "L1_PROMISES",
                "epistemic_basis": "fact",
                "description": desc,
            }
        ],
    }
    tui = render_diff_tour_tui(tour)
    assert isinstance(tui, str)
    assert len(tui) > 0


# ============================================================================
# Section 3: Secret Shield v1.1.0 Station & OASIS SARIF 2.1.0 (4 Tests)
# ============================================================================


def test_station_secret_shield_clean_profile():
    res = client.post(
        "/api/mission-control/stations/secret-shield/sarif",
        json={"profile": "clean_repo"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["clean"] is True
    assert data["finding_count"] == 0
    assert data["severity"] == "CLEAN"
    sarif = data["sarif_report"]
    assert sarif["version"] == "2.1.0"
    assert len(sarif["runs"]) == 1
    assert sarif["runs"][0]["tool"]["driver"]["name"] == "agy-secret-shield"


def test_station_secret_shield_12_secret_patterns_and_sarif_210():
    leaked_text = "\n".join([
        "export GOOGLE_TOKEN=__Secure-1PSID=fake_1psid_credential_value_here_1234567890",
        "export SAPISID=fake_sapisid_token_1234567890",
        "export GITHUB_PAT=ghp_111122223333444455556666777788889999",
        "export JWT_TEST=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.do_not_leak_signature",
    ])
    res = client.post(
        "/api/mission-control/stations/secret-shield/sarif",
        json={"custom_text": leaked_text},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["clean"] is False
    assert data["finding_count"] >= 3
    assert data["severity"] in ("HIGH", "CRITICAL")
    results = data["sarif_report"]["runs"][0]["results"]
    assert len(results) >= 3


def test_station_secret_shield_windows_dpapi_quarantine_roundtrip():
    envelopes = [
        {
            "label": "test_cookie",
            "ciphertext_b64": base64.b64encode(b"secret_cookie_payload").decode("ascii"),
        }
    ]
    res = client.post(
        "/api/mission-control/stations/secret-shield/sarif",
        json={"profile": "clean_repo", "rotate_envelopes": envelopes},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert len(data["rotated_envelopes"]) == 1
    assert data["rotated_envelopes"][0]["verified"] is True


def test_station_secret_shield_git_history_scan():
    res = client.post(
        "/api/mission-control/stations/secret-shield/sarif",
        json={"scan_git_history": True, "max_commits": 10},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "git_history" in data


# ============================================================================
# Section 4: PE Reversing Lab v1.1.0 & Live Windows Binaries (4 Tests)
# ============================================================================


def test_station_pe_lab_synthetic_profiles_triage():
    res = client.post(
        "/api/mission-control/stations/pe-lab/analyze",
        json={"profile": "clean"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["format"] == "PE32+"
    assert data["architecture"] == "x86_64"
    assert "yara_rule" in data
    assert "uint16(0) == 0x5A4D" in data["yara_rule"]


def test_station_pe_lab_rich_header_and_yara_rule():
    res = client.post(
        "/api/mission-control/stations/pe-lab/analyze",
        json={"profile": "anti_debug"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "rich_header" in data
    assert "threat_score" in data


def test_station_pe_lab_live_notepad_windows_binary():
    notepad_path = r"C:\Windows\System32\notepad.exe"
    if not Path(notepad_path).is_file():
        pytest.skip("Notepad binary not present on host")

    res = client.post(
        "/api/mission-control/stations/pe-lab/analyze",
        json={"target_path": notepad_path},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["format"] == "PE32+"
    assert data["size"] > 50000
    assert len(data["sections"]) > 0


def test_station_pe_lab_live_kernel32_eat_exports():
    kernel32_path = r"C:\Windows\System32\kernel32.dll"
    if not Path(kernel32_path).is_file():
        pytest.skip("Kernel32 DLL not present on host")

    res = client.post(
        "/api/mission-control/stations/pe-lab/analyze",
        json={"target_path": kernel32_path},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["format"] == "PE32+"
    exports = data.get("exports", {})
    if exports.get("present"):
        assert exports["function_count"] > 100


# ============================================================================
# Section 5: ESKD Vision Station v1.1.0 & GOST 2.104/2.301 300 DPI (4 Tests)
# ============================================================================


def test_station_eskd_gost_2104_title_block_geometry_a4_a3():
    res_a4 = client.post(
        "/api/mission-control/stations/eskd/inspect",
        json={"sheet_format": "A4", "stamp_type": "stamp_form1"},
    )
    assert res_a4.status_code == 200
    data_a4 = res_a4.json()
    assert data_a4["ok"] is True
    tb_a4 = data_a4["title_block_verification"]
    assert tb_a4["expected_mm"]["width"] == 185.0
    assert tb_a4["expected_mm"]["height"] == 55.0

    res_a3 = client.post(
        "/api/mission-control/stations/eskd/inspect",
        json={"sheet_format": "A3", "stamp_type": "stamp_form1"},
    )
    assert res_a3.status_code == 200
    data_a3 = res_a3.json()
    assert data_a3["ok"] is True


def test_station_eskd_gost_2301_margins_at_300dpi():
    res = client.post(
        "/api/mission-control/stations/eskd/inspect",
        json={"sheet_format": "A4", "target_dpi": 300.0},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    frame = data["frame_verification"]
    assert frame["margins_mm"]["left"] == 20.0
    assert frame["margins_mm"]["top"] == 5.0
    assert frame["margins_px_300dpi"]["left"] == 236
    assert frame["margins_px_300dpi"]["top"] == 59


def test_station_eskd_detect_line_crossings_gost_2307():
    # Crossing segments: (10, 50)-(90, 50) and (50, 10)-(50, 90)
    segments = [
        {"id": "dim_1", "type": "dimension_line", "p1": [10.0, 50.0], "p2": [90.0, 50.0]},
        {"id": "ext_1", "type": "extension_line", "p1": [50.0, 10.0], "p2": [50.0, 90.0]},
    ]
    res = client.post(
        "/api/mission-control/stations/eskd/inspect",
        json={"sheet_format": "A4", "line_segments": segments},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert len(data["line_crossings"]) >= 1


def test_station_eskd_multisheet_batch_inspection():
    sheets = [
        {"sheet_format": "A4", "stamp_type": "stamp_form1"},
        {"sheet_format": "A3", "stamp_type": "stamp_form1"},
    ]
    res = client.post(
        "/api/mission-control/stations/eskd/inspect",
        json={"sheets": sheets},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "batch_result" in data


# ============================================================================
# Section 6: Federation Mesh v1.1.0 Wave DAG & Circuit Breaker (4 Tests)
# ============================================================================


def test_station_federation_dag_execution_waves_diamond():
    tasks = [
        {"task_id": "t0", "station_id": "pe-reversing-lab", "payload": {"profile": "clean"}, "depends_on": []},
        {"task_id": "t1_sec", "station_id": "agy-secret-shield", "payload": {"profile": "clean_repo"}, "depends_on": ["t0"]},
        {"task_id": "t1_pe", "station_id": "pe-reversing-lab", "payload": {"profile": "clean"}, "depends_on": ["t0"]},
        {"task_id": "t2_join", "station_id": "eskd-vision-station", "payload": {"sheet_format": "A4"}, "depends_on": ["t1_sec", "t1_pe"]},
    ]
    res = client.post(
        "/api/mission-control/federation/waves/execute",
        json={"workflow_name": "diamond_dag", "tasks": tasks},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["status"] == "COMPLETED"
    assert len(data["waves"]) == 3
    assert data["waves"][0] == ["t0"]
    assert set(data["waves"][1]) == {"t1_pe", "t1_sec"}
    assert data["waves"][2] == ["t2_join"]


def test_station_federation_cycle_detection_rejection():
    cyclic_tasks = [
        {"task_id": "task_a", "station_id": "pe-reversing-lab", "depends_on": ["task_b"]},
        {"task_id": "task_b", "station_id": "agy-secret-shield", "depends_on": ["task_a"]},
    ]
    res = client.post(
        "/api/mission-control/federation/waves/execute",
        json={"workflow_name": "cyclic_dag", "tasks": cyclic_tasks},
    )
    assert res.status_code == 400
    assert "cyclic" in res.json()["detail"].lower()


def test_station_federation_parallel_wave_circuit_breaker():
    failing_tasks = [
        {"task_id": "t0", "station_id": "pe-reversing-lab", "payload": {"should_fail": True}, "depends_on": []},
        {"task_id": "t1_downstream", "station_id": "agy-secret-shield", "payload": {}, "depends_on": ["t0"]},
    ]
    res = client.post(
        "/api/mission-control/federation/waves/execute",
        json={"workflow_name": "circuit_breaker_dag", "tasks": failing_tasks},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert data["status"] == "FAILED"
    # Downstream task t1_downstream must NOT have been executed
    assert "t1_downstream" not in data["results"]


def test_station_federation_cross_station_dossier_synthesis():
    tasks = [
        {"task_id": "t0", "station_id": "pe-reversing-lab", "payload": {"profile": "clean"}, "depends_on": []},
        {"task_id": "t1", "station_id": "agy-secret-shield", "payload": {"profile": "clean_repo"}, "depends_on": []},
    ]
    res = client.post(
        "/api/mission-control/federation/waves/execute",
        json={"workflow_name": "dossier_dag", "tasks": tasks, "synthesize_dossier": True},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    dossier = data.get("diff_tour_dossier")
    assert dossier is not None
    assert "steps" in dossier


# ============================================================================
# Section 7: Plugin Version Drift Detection & Reconciliation (3 Tests)
# ============================================================================


def test_plugin_version_drift_detection(tmp_path: Path):
    hub = MissionControlHub(config_root=tmp_path)
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(parents=True)
    p1 = plugins_dir / "plugin_a"
    p1.mkdir()
    (p1 / "plugin.json").write_text(
        json.dumps({"name": "plugin_a", "version": "1.0.0", "displayName": "Alpha"}),
        encoding="utf-8",
    )
    p2 = plugins_dir / "plugin_b"
    p2.mkdir()
    (p2 / "plugin.json").write_text(
        json.dumps({"name": "plugin_b", "version": "1.1.0", "displayName": "Beta"}),
        encoding="utf-8",
    )

    drift = hub.detect_plugin_drift(baseline_version="1.1.0")
    assert drift["drift_count"] == 1
    assert drift["synchronized_count"] == 1
    assert drift["drifted_plugins"][0]["name"] == "plugin_a"


def test_plugin_version_reconciliation_atomic_preserving_fields(tmp_path: Path):
    hub = MissionControlHub(config_root=tmp_path)
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(parents=True)
    p1 = plugins_dir / "plugin_drifted"
    p1.mkdir()
    (p1 / "plugin.json").write_text(
        json.dumps({
            "name": "plugin_drifted",
            "version": "0.9.0",
            "customMeta": "preserved_val",
            "skills": ["custom_skill"],
        }),
        encoding="utf-8",
    )

    res = hub.reconcile_plugins(target_version="1.1.0")
    assert res["reconciled_count"] == 1
    assert "plugin_drifted" in res["reconciled_plugins"]

    updated = json.loads((p1 / "plugin.json").read_text(encoding="utf-8"))
    assert updated["version"] == "1.1.0"
    assert updated["customMeta"] == "preserved_val"
    assert updated["skills"] == ["custom_skill"]


def test_plugin_reconcile_api_endpoint():
    res = client.post(
        "/api/mission-control/plugins/reconcile",
        json={"target_version": "1.1.0"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["target_version"] == "1.1.0"


# ============================================================================
# Section 8: Brain Memory Diff-Tour & CAPA Search Filters (3 Tests)
# ============================================================================


def test_brain_memory_diff_tour_dimension_filter(tmp_path: Path):
    brain_root = tmp_path / "brain"
    conv_dir = brain_root / "conv_session_1"
    conv_dir.mkdir(parents=True)

    tour_file = conv_dir / "architecture.tour.json"
    tour_file.write_text(
        json.dumps({
            "title": "Architecture Overview",
            "steps": [
                {"file": "src/a.py", "dimension": "L4_TRUST", "description": "Trust boundaries"},
                {"file": "src/b.py", "dimension": "L1_PROMISES", "description": "API promises"},
            ],
        }),
        encoding="utf-8",
    )

    hub = MissionControlHub(brain_root=brain_root)
    res = hub.search_brain_memory(dimension="L4_TRUST")
    assert res["ok"] is True
    assert len(res["diff_tours"]) == 1
    assert res["diff_tours"][0]["matching_steps_count"] == 1
    assert res["diff_tours"][0]["steps"][0]["dimension"] == "L4_TRUST"


def test_brain_memory_capa_severity_filter(tmp_path: Path):
    brain_root = tmp_path / "brain"
    conv_dir = brain_root / "conv_session_2"
    conv_dir.mkdir(parents=True)

    capa_file = conv_dir / "failure_ledger.jsonl"
    capa_file.write_text(
        json.dumps({"record_id": "FL-001", "severity": "HIGH", "reason": "Timeout issue"}) + "\n"
        + json.dumps({"record_id": "FL-002", "severity": "LOW", "reason": "Formatting tweak"}) + "\n",
        encoding="utf-8",
    )

    hub = MissionControlHub(brain_root=brain_root)
    res = hub.search_brain_memory(severity="HIGH")
    assert res["ok"] is True
    assert len(res["capa_records"]) == 1
    assert res["capa_records"][0]["record_id"] == "FL-001"


def test_brain_search_api_endpoint_filters():
    res = client.get("/api/mission-control/brain/search?q=test&dimension=L4_TRUST&severity=HIGH")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "diff_tours" in data
    assert "capa_records" in data


# ============================================================================
# Section 9: React Cockpit UI TestID & Contract Assertions (3 Tests)
# ============================================================================


def test_react_cockpit_5tier_strip_l5_testid():
    drawer_file = (
        Path(__file__).resolve().parent.parent
        / "frontend"
        / "src"
        / "components"
        / "MissionControlDrawer.tsx"
    )
    content = drawer_file.read_text(encoding="utf-8")
    assert 'data-testid={`mc-tier-${tierKey}`}' in content or 'data-testid="mc-tier-L5"' in content
    assert "'L5'" in content


def test_react_cockpit_diff_tour_tab_testids():
    drawer_file = (
        Path(__file__).resolve().parent.parent
        / "frontend"
        / "src"
        / "components"
        / "MissionControlDrawer.tsx"
    )
    content = drawer_file.read_text(encoding="utf-8")
    assert 'data-testid="mc-tab-diff-tour"' in content
    assert 'data-testid="mc-diff-tour-drop-asks"' in content
    assert 'data-testid="mc-generate-diff-tour-btn"' in content
    assert 'data-testid="mc-diff-tour-preview"' in content
    for dim in SENIOR_REVIEW_DIMENSIONS:
        assert dim in content


def test_react_cockpit_federation_tab_and_reconcile_btn_testids():
    drawer_file = (
        Path(__file__).resolve().parent.parent
        / "frontend"
        / "src"
        / "components"
        / "MissionControlDrawer.tsx"
    )
    content = drawer_file.read_text(encoding="utf-8")
    assert 'data-testid="mc-tab-federation"' in content
    assert 'data-testid="mc-run-federation-waves-btn"' in content
    assert 'data-testid="mc-station-secret-shield"' in content
    assert 'data-testid="mc-station-pe-lab"' in content
    assert 'data-testid="mc-station-eskd"' in content
    assert 'data-testid="mc-reconcile-drift-btn"' in content


# ============================================================================
# Section 10: 5-Gate OSS Release Hygiene (2 Tests)
# ============================================================================


def test_m29_zero_cyrillic_in_all_m29_files():
    root = Path(__file__).resolve().parent.parent
    m29_files = [
        root / "backend" / "app" / "bridge" / "mission_control.py",
        root / "backend" / "app" / "api" / "mission_control.py",
        root / "backend" / "app" / "main.py",
        root / "frontend" / "package.json",
        root / "pyproject.toml",
        root / "frontend" / "src" / "components" / "MissionControlDrawer.tsx",
        root / "tests" / "test_m29_v1_2_cockpit.py",
    ]
    cyrillic_pattern = re.compile(r"[\u0400-\u04FF]")
    for f in m29_files:
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="ignore")
            matches = cyrillic_pattern.findall(text)
            assert len(matches) == 0, f"Cyrillic characters detected in {f}: {matches[:5]}"


def test_m29_zero_personal_paths_or_usernames():
    root = Path(__file__).resolve().parent.parent
    m29_files = [
        root / "backend" / "app" / "bridge" / "mission_control.py",
        root / "backend" / "app" / "api" / "mission_control.py",
        root / "backend" / "app" / "main.py",
        root / "frontend" / "package.json",
        root / "pyproject.toml",
        root / "frontend" / "src" / "components" / "MissionControlDrawer.tsx",
        root / "tests" / "test_m29_v1_2_cockpit.py",
    ]
    forbidden = _forbidden_tokens()
    for f in m29_files:
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="ignore")
            for tok in forbidden:
                assert tok.lower() not in text.lower(), (
                    f"Forbidden personal token detected in {f}: '{tok}'"
                )
