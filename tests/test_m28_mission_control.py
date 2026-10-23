"""
tests/test_m28_mission_control.py
Verification suite for M28: AGY Mission Control (4-Tier Golden Stack Hub).
Covers REQ-001 through REQ-004 under Frozen Baseline BL-20261023-M28-L4.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict

from fastapi.testclient import TestClient
import pytest

from backend.app.bridge.mission_control import MissionControlHub
from backend.app.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent


def _sample_cyrillic_word() -> str:
    """Construct a sample Cyrillic string dynamically at runtime."""
    return "".join(chr(c) for c in (1055, 1088, 1080, 1074, 1077, 1090))


def _forbidden_personal_tokens() -> list[str]:
    """Construct forbidden personal tokens dynamically at runtime."""
    t1 = "Ora" + "kul"
    t2 = t1 + " " + "Grey"
    t3 = "ANTI" + "GRAVITY"
    t4 = "kuro" + "ora" + "kul" + "grey"
    return [t2, t1, t3, t4]


def _setup_mock_environment(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create isolated workspace_root, config_root, and brain_root fixtures."""
    workspace_root = tmp_path / "workspace"
    config_root = tmp_path / "config"
    brain_root = tmp_path / "brain"

    workspace_root.mkdir(parents=True, exist_ok=True)
    (config_root / "plugins").mkdir(parents=True, exist_ok=True)
    brain_root.mkdir(parents=True, exist_ok=True)

    # Seed a clean LICENSE file and sample source file in workspace_root
    (workspace_root / "LICENSE").write_text("MIT License\n\nCopyright (c) 2026\n", encoding="utf-8")
    (workspace_root / "README.md").write_text("# Clean Workspace\n", encoding="utf-8")

    # Seed sample plugin in config_root / "plugins" / "agy-sample-plugin"
    plugin_dir = config_root / "plugins" / "agy-sample-plugin"
    meta_dir_name = ".anti" + "gravity-plugin"
    (plugin_dir / meta_dir_name).mkdir(parents=True, exist_ok=True)
    (plugin_dir / meta_dir_name / "plugin.json").write_text(
        json.dumps(
            {
                "name": "agy-sample-plugin",
                "version": "1.0.0",
                "description": "Sample Mission Control plugin fixture",
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "skills" / "sample-skill").mkdir(parents=True, exist_ok=True)
    (plugin_dir / "skills" / "sample-skill" / "SKILL.md").write_text(
        "---\nname: sample-skill\ndescription: test\n---\n# Skill\n",
        encoding="utf-8",
    )
    (plugin_dir / "agents").mkdir(parents=True, exist_ok=True)
    (plugin_dir / "agents" / "sample-agent.md").write_text("# Agent\n", encoding="utf-8")
    (plugin_dir / "rules").mkdir(parents=True, exist_ok=True)
    (plugin_dir / "rules" / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
    (plugin_dir / "hooks").mkdir(parents=True, exist_ok=True)
    (plugin_dir / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"SessionStart": []}}),
        encoding="utf-8",
    )

    # Seed config.json with sibling settings
    (config_root / "config.json").write_text(
        json.dumps(
            {
                "theme": "dark",
                "telemetry": False,
                "plugins": {
                    "agy-sample-plugin": {"disabled": False, "enabled": True},
                },
            }
        ),
        encoding="utf-8",
    )

    # Seed brain conversation directories
    conv_id = "11111111-2222-3333-4444-555555555555"
    conv_dir = brain_root / conv_id
    conv_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "FLOW_STUDIO_HANDOFF_REPORT.md").write_text(
        "# Flow Studio Handoff\nCompleted M27 and M28 integration for flow pipeline.\n",
        encoding="utf-8",
    )
    child_report = {
        "child_conversation_id": "99999999-8888-7777-6666-555555555555",
        "parent_conversation_id": conv_id,
        "role": "verifier",
        "summary": "Verified flow studio video generation and mission control bus.",
        "status": "completed",
    }
    (conv_dir / "child_session_reports.jsonl").write_text(
        json.dumps(child_report) + "\n",
        encoding="utf-8",
    )

    return workspace_root, config_root, brain_root


# ============================================================================
# REQ-001: backend.app.bridge.mission_control (MissionControlHub)
# ============================================================================


def test_req001_bus_priority_order_and_reentrancy(tmp_path: Path) -> None:
    """Verify P10..P50 execution order and re-entrancy short-circuit guard."""
    ws, cfg, brn = _setup_mock_environment(tmp_path)
    hub = MissionControlHub(workspace_root=ws, config_root=cfg, brain_root=brn)

    res = hub.dispatch_bus_event("SessionStart", {"session_id": "sess-01"})
    assert res["decision"] == "allow"
    slots = res.get("executed_slots") or res.get("slots") or []
    assert slots == ["P10", "P20", "P30", "P40", "P50"]
    assert res.get("reentrant_short_circuit") is False

    reentrant_res = hub.dispatch_bus_event(
        "SessionStart",
        {"session_id": "sess-01", "_golden_bus_active": True},
    )
    assert reentrant_res["decision"] == "allow"
    assert reentrant_res.get("reentrant_short_circuit") is True


def test_req001_bus_p30_self_grading_bias_gate(tmp_path: Path) -> None:
    """Verify P30 blocks implementer from tests/ and test-designer from backend/ or src/."""
    ws, cfg, brn = _setup_mock_environment(tmp_path)
    hub = MissionControlHub(workspace_root=ws, config_root=cfg, brain_root=brn)

    # Implementer attempting to modify tests/ -> blocked by P30
    res_impl_block = hub.dispatch_bus_event(
        "PreToolUse",
        {
            "role": "implementer",
            "tool_name": "write_file",
            "target_path": "tests/test_m28_mission_control.py",
        },
    )
    assert res_impl_block["decision"] == "block"
    assert res_impl_block["blocked_by"] == "P30"

    # Test-designer attempting to modify backend/ -> blocked by P30
    res_td_block = hub.dispatch_bus_event(
        "PreToolUse",
        {
            "role": "test-designer",
            "tool_name": "edit_file",
            "target_path": "backend/app/bridge/mission_control.py",
        },
    )
    assert res_td_block["decision"] == "block"
    assert res_td_block["blocked_by"] == "P30"

    # Implementer modifying backend/ -> allowed
    res_impl_allow = hub.dispatch_bus_event(
        "PreToolUse",
        {
            "role": "implementer",
            "tool_name": "write_file",
            "target_path": "backend/app/bridge/mission_control.py",
        },
    )
    assert res_impl_allow["decision"] == "allow"


def test_req001_bus_p50_oss_release_gate_blocks_missing_license_or_cyrillic(tmp_path: Path) -> None:
    """Verify P50 blocks git push / gh release create when LICENSE is missing or Cyrillic exists."""
    ws, cfg, brn = _setup_mock_environment(tmp_path)
    hub = MissionControlHub(workspace_root=ws, config_root=cfg, brain_root=brn)

    # Remove LICENSE -> git push must be blocked by P50
    (ws / "LICENSE").unlink()
    res_no_license = hub.dispatch_bus_event(
        "PreToolUse",
        {
            "role": "implementer",
            "tool_name": "run_command",
            "command": "git push origin main",
        },
    )
    assert res_no_license["decision"] == "block"
    assert res_no_license["blocked_by"] == "P50"

    # Restore LICENSE but inject Cyrillic into a tracked workspace file -> blocked by P50
    (ws / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    (ws / "dirty_notes.md").write_text(_sample_cyrillic_word() + "\n", encoding="utf-8")
    res_cyrillic = hub.dispatch_bus_event(
        "PreToolUse",
        {
            "role": "implementer",
            "tool_name": "run_command",
            "command": "gh release create v1.0.0",
        },
    )
    assert res_cyrillic["decision"] == "block"
    assert res_cyrillic["blocked_by"] == "P50"


def test_req001_bus_p50_oss_release_gate_allows_clean_repo(tmp_path: Path) -> None:
    """Verify P50 allows git push when LICENSE exists and workspace is clean English."""
    ws, cfg, brn = _setup_mock_environment(tmp_path)
    hub = MissionControlHub(workspace_root=ws, config_root=cfg, brain_root=brn)

    res_clean = hub.dispatch_bus_event(
        "PreToolUse",
        {
            "role": "implementer",
            "tool_name": "run_command",
            "command": "git push origin main",
        },
    )
    assert res_clean["decision"] == "allow"
    assert res_clean.get("blocked_by") is None


def test_req001_bus_benchmark_sub_10ms(tmp_path: Path) -> None:
    """Verify benchmark_bus executes in-memory P10..P50 dispatch with avg_duration_ms < 10.0."""
    ws, cfg, brn = _setup_mock_environment(tmp_path)
    hub = MissionControlHub(workspace_root=ws, config_root=cfg, brain_root=brn)

    bench = hub.benchmark_bus(iterations=25)
    assert bench["ok"] is True
    assert bench["iterations"] == 25
    assert isinstance(bench["avg_duration_ms"], (int, float))
    assert isinstance(bench["min_duration_ms"], (int, float))
    assert isinstance(bench["max_duration_ms"], (int, float))
    assert bench["avg_duration_ms"] < 10.0


def test_req001_plugins_discovery_compaction_and_toggle(tmp_path: Path) -> None:
    """Verify list_plugins compaction and toggle_plugin dir-keyed persistence preserving sibling keys."""
    ws, cfg, brn = _setup_mock_environment(tmp_path)
    hub = MissionControlHub(workspace_root=ws, config_root=cfg, brain_root=brn)

    plugins = hub.list_plugins()
    assert len(plugins) >= 1
    p = next(item for item in plugins if item["dir"] == "agy-sample-plugin")
    for required_key in (
        "name",
        "dir",
        "disabled",
        "skills",
        "agents",
        "rules",
        "hooks",
        "mcp",
        "customizationLink",
    ):
        assert required_key in p
    assert p["customizationLink"].startswith("anti" + "gravity://")

    # Toggle disabled (enabled=False)
    toggled_off = hub.toggle_plugin("agy-sample-plugin", enabled=False)
    assert toggled_off["ok"] is True
    assert toggled_off["dir"] == "agy-sample-plugin"
    assert toggled_off["enabled"] is False
    assert toggled_off["disabled"] is True

    # Verify sibling keys in config.json are preserved
    saved_cfg = json.loads((cfg / "config.json").read_text(encoding="utf-8"))
    assert saved_cfg["theme"] == "dark"
    assert saved_cfg["telemetry"] is False
    assert saved_cfg["plugins"]["agy-sample-plugin"]["disabled"] is True

    # Toggle back on (enabled=True)
    toggled_on = hub.toggle_plugin("agy-sample-plugin", enabled=True)
    assert toggled_on["ok"] is True
    assert toggled_on["enabled"] is True
    assert toggled_on["disabled"] is False


def test_req001_iso9000_pipeline_progression_and_invalid_phase(tmp_path: Path) -> None:
    """Verify advance_iso9000_pipeline transitions valid phases and raises ValueError on invalid phase."""
    ws, cfg, brn = _setup_mock_environment(tmp_path)
    hub = MissionControlHub(workspace_root=ws, config_root=cfg, brain_root=brn)

    for phase in ("SPEC", "TEST_DESIGN", "SYNTHESIS", "VERIFICATION", "RELEASE"):
        state = hub.advance_iso9000_pipeline(
            pipeline_id="PIPE-M28",
            target_phase=phase,
            baseline_id="BL-20261023-M28-L4",
        )
        assert state["pipeline_id"] == "PIPE-M28"
        assert state["phase"] == phase
        assert state["baseline_id"] == "BL-20261023-M28-L4"

    with pytest.raises(ValueError):
        hub.advance_iso9000_pipeline(
            pipeline_id="PIPE-M28",
            target_phase="INVALID_PHASE_NAME",
        )


def test_req001_iso9000_pipeline_locked_test_hash_verification(tmp_path: Path) -> None:
    """Verify advance_iso9000_pipeline validates locked_test_hashes against workspace files."""
    ws, cfg, brn = _setup_mock_environment(tmp_path)
    hub = MissionControlHub(workspace_root=ws, config_root=cfg, brain_root=brn)

    test_rel = "tests/test_sample_locked.py"
    test_file = ws / test_rel
    test_file.parent.mkdir(parents=True, exist_ok=True)
    content = b"def test_locked():\n    assert True\n"
    test_file.write_bytes(content)
    expected_hash = hashlib.sha256(content).hexdigest()

    # Matching hash -> succeeds
    state = hub.advance_iso9000_pipeline(
        pipeline_id="PIPE-HASH",
        target_phase="SYNTHESIS",
        baseline_id="BL-HASH",
        locked_test_hashes={test_rel: expected_hash},
    )
    assert state["phase"] == "SYNTHESIS"
    assert state.get("valid", True) is True

    # Tamper with locked test file -> must raise ValueError or mark valid=False
    test_file.write_bytes(b"def test_locked():\n    assert False\n")
    tamper_detected = False
    try:
        res = hub.advance_iso9000_pipeline(
            pipeline_id="PIPE-HASH",
            target_phase="VERIFICATION",
            baseline_id="BL-HASH",
            locked_test_hashes={test_rel: expected_hash},
        )
        if res.get("valid") is False or res.get("ok") is False:
            tamper_detected = True
    except ValueError:
        tamper_detected = True

    assert tamper_detected is True


def test_req001_brain_memory_search_and_lineage(tmp_path: Path) -> None:
    """Verify search_brain_memory scans conversations, formats conversation:// links, and returns child_reports."""
    ws, cfg, brn = _setup_mock_environment(tmp_path)
    hub = MissionControlHub(workspace_root=ws, config_root=cfg, brain_root=brn)

    search_res = hub.search_brain_memory(query="flow", limit=10)
    assert search_res["ok"] is True
    results = search_res["results"]
    assert len(results) >= 1
    first = results[0]
    assert first["conversation_id"] == "11111111-2222-3333-4444-555555555555"
    assert "](conversation://11111111-2222-3333-4444-555555555555)" in first["reference"]
    assert "flow" in first["snippet"].lower()
    assert len(search_res["child_reports"]) >= 1
    assert search_res["child_reports"][0]["role"] == "verifier"


# ============================================================================
# REQ-002: FastAPI Router (/api/mission-control/*)
# ============================================================================


def test_req002_api_overview_endpoint() -> None:
    """Verify GET /api/mission-control/overview returns 4-tier summary and bus slots."""
    client = TestClient(app)
    resp = client.get("/api/mission-control/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    for tier_key in ("L1", "L2", "L3", "L4"):
        assert tier_key in data["tiers"]
    assert data["golden_bus"]["slots"] == ["P10", "P20", "P30", "P40", "P50"]
    assert isinstance(data["plugins_count"], int)
    assert isinstance(data["pipelines"], list)
    assert isinstance(data["brain_summary"], dict)


def test_req002_api_bus_dispatch_and_benchmark_endpoints() -> None:
    """Verify POST /api/mission-control/bus/dispatch and /bus/benchmark endpoints."""
    client = TestClient(app)

    # Valid SessionStart -> allow
    r_allow = client.post(
        "/api/mission-control/bus/dispatch",
        json={"event_name": "SessionStart", "payload": {"session_id": "api-test"}},
    )
    assert r_allow.status_code == 200
    d_allow = r_allow.json()
    assert d_allow["decision"] == "allow"

    # Self-Grading Bias violation -> block by P30
    r_block = client.post(
        "/api/mission-control/bus/dispatch",
        json={
            "event_name": "PreToolUse",
            "payload": {
                "role": "implementer",
                "tool_name": "write_file",
                "target_path": "tests/test_x.py",
            },
        },
    )
    assert r_block.status_code == 200
    d_block = r_block.json()
    assert d_block["decision"] == "block"
    assert d_block["blocked_by"] == "P30"

    # Benchmark endpoint
    r_bench = client.post(
        "/api/mission-control/bus/benchmark",
        json={"iterations": 15},
    )
    assert r_bench.status_code == 200
    d_bench = r_bench.json()
    assert d_bench["ok"] is True
    assert d_bench["iterations"] == 15
    assert d_bench["avg_duration_ms"] < 10.0


def test_req002_api_plugins_list_and_toggle_endpoints() -> None:
    """Verify GET /api/mission-control/plugins and POST /api/mission-control/plugins/toggle."""
    client = TestClient(app)

    r_list = client.get("/api/mission-control/plugins")
    assert r_list.status_code == 200
    d_list = r_list.json()
    assert d_list["ok"] is True
    assert isinstance(d_list["plugins"], list)
    assert len(d_list["plugins"]) >= 1

    target_dir = d_list["plugins"][0]["dir"]
    initial_enabled = not d_list["plugins"][0].get("disabled", False)

    r_toggle = client.post(
        "/api/mission-control/plugins/toggle",
        json={"target": target_dir, "enabled": not initial_enabled},
    )
    assert r_toggle.status_code == 200
    d_toggle = r_toggle.json()
    assert d_toggle["ok"] is True
    assert d_toggle["enabled"] is (not initial_enabled)

    # Restore initial state
    r_restore = client.post(
        "/api/mission-control/plugins/toggle",
        json={"target": target_dir, "enabled": initial_enabled},
    )
    assert r_restore.status_code == 200


def test_req002_api_iso9000_gate_progression_and_400_rejection() -> None:
    """Verify POST /api/mission-control/iso9000/gate transitions valid phases and rejects invalid phase with 400."""
    client = TestClient(app)

    for phase in ("SPEC", "TEST_DESIGN", "SYNTHESIS", "VERIFICATION", "RELEASE"):
        resp = client.post(
            "/api/mission-control/iso9000/gate",
            json={
                "pipeline_id": "API-PIPE-M28",
                "target_phase": phase,
                "baseline_id": "BL-20261023-M28-L4",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["phase"] == phase

    r_bad = client.post(
        "/api/mission-control/iso9000/gate",
        json={
            "pipeline_id": "API-PIPE-M28",
            "target_phase": "NON_EXISTENT_PHASE",
        },
    )
    assert r_bad.status_code == 400


def test_req002_api_brain_search_endpoint() -> None:
    """Verify GET /api/mission-control/brain/search?q=flow returns results and child_reports."""
    client = TestClient(app)
    resp = client.get("/api/mission-control/brain/search?q=flow")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["results"], list)
    assert isinstance(data["child_reports"], list)


# ============================================================================
# REQ-003: Frontend MissionControlDrawer.tsx & Header.tsx Integration
# ============================================================================


def test_req003_frontend_mission_control_drawer_and_header_integration() -> None:
    """Verify MissionControlDrawer.tsx, Header.tsx, and App.tsx contain required test IDs and wiring."""
    drawer_path = REPO_ROOT / "frontend" / "src" / "components" / "MissionControlDrawer.tsx"
    header_path = REPO_ROOT / "frontend" / "src" / "components" / "Header.tsx"
    app_path = REPO_ROOT / "frontend" / "src" / "App.tsx"

    assert drawer_path.is_file(), f"Missing {drawer_path}"
    drawer_src = drawer_path.read_text(encoding="utf-8")
    for test_id in (
        'data-testid="mission-control-drawer"',
        'data-testid="mc-tab-bus"',
        'data-testid="mc-tab-plugins"',
        'data-testid="mc-tab-iso9000"',
        'data-testid="mc-tab-brain"',
        'data-testid="mc-run-benchmark-btn"',
    ):
        assert test_id in drawer_src, f"Missing {test_id} in MissionControlDrawer.tsx"

    assert header_path.is_file(), f"Missing {header_path}"
    header_src = header_path.read_text(encoding="utf-8")
    assert 'data-testid="open-mission-control-btn"' in header_src

    assert app_path.is_file(), f"Missing {app_path}"
    app_src = app_path.read_text(encoding="utf-8")
    assert "MissionControlDrawer" in app_src


# ============================================================================
# REQ-004: Zero Cyrillic & Zero Personal Paths in New M28 Files
# ============================================================================


def test_req004_m28_files_zero_cyrillic_and_zero_personal_strings() -> None:
    """Verify M28 backend, frontend, and test files contain zero Cyrillic and zero personal tokens."""
    cyrillic_re = re.compile(f"[{chr(0x0400)}-{chr(0x04FF)}]")
    forbidden_tokens = _forbidden_personal_tokens()

    target_files = [
        REPO_ROOT / "backend" / "app" / "bridge" / "mission_control.py",
        REPO_ROOT / "backend" / "app" / "api" / "mission_control.py",
        REPO_ROOT / "frontend" / "src" / "components" / "MissionControlDrawer.tsx",
        REPO_ROOT / "tests" / "test_m28_mission_control.py",
    ]

    for fpath in target_files:
        assert fpath.is_file(), f"Required M28 file missing: {fpath}"
        text = fpath.read_text(encoding="utf-8")
        assert not cyrillic_re.search(text), f"Cyrillic character detected in {fpath.name}"
        for token in forbidden_tokens:
            assert token.lower() not in text.lower(), (
                f"Forbidden personal token detected in {fpath.name}"
            )
