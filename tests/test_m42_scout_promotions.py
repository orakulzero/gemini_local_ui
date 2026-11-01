"""
tests/test_m42_scout_promotions.py
Locked Agile V (P30) Verification Suite for Milestone M42:
- REQ-SCOUT-06: Live Staged Skill Installer (`install_staged_skill`, `install_all_staged_skills`, `/api/v1/scout/install-skill`) & Repo Path Resolution
- REQ-PIPE-09: AST-Aware Incremental Context Compression (`compress_python_ast_contract` & `ast_compress_unchanged`)
- REQ-PIPE-10: Speculative Multi-Branch Sandbox Verification (`verify_candidate_drafts_in_sandbox` & `run_schedule_now`)
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.app.bridge.ecosystem_scout import EcosystemIntelligenceScout, get_ecosystem_scout
from backend.app.bridge.task_pipeline import (
    TaskPipelineOrchestrator,
    verify_candidate_drafts_in_sandbox,
)
from backend.app.bridge.ultra_capabilities import (
    compress_python_ast_contract,
    pack_ultra_2m_repository_context,
)
from backend.app.main import app


def _resolve(val: Any) -> Any:
    """Resolves either synchronous return values or coroutines transparently."""
    return asyncio.run(val) if inspect.isawaitable(val) else val


def test_req_scout_06_install_staged_skills_and_repo_resolution(tmp_path: Path) -> None:
    """REQ-SCOUT-06: Installs staged skills to disk, exposes POST /api/v1/scout/install-skill, and resolves target_agy_repo paths."""
    scout = EcosystemIntelligenceScout(storage_path=tmp_path / "scout_state.json")
    skills_root = tmp_path / "skills"

    s1 = scout.adapt_skill_to_antigravity(
        source_ide="cursor",
        skill_name="cursor-ast-refactor",
        raw_content="Always parse AST before modifying functions.",
        target_plugin="agy-golden-stack",
    )
    assert s1["status"] == "STAGED_FOR_REVIEW"

    installed_one = _resolve(scout.install_staged_skill("cursor-ast-refactor", skills_root=skills_root))
    assert installed_one["ok"] is True
    assert installed_one["status"] == "INSTALLED"
    skill_md_path = skills_root / "cursor-ast-refactor" / "SKILL.md"
    assert skill_md_path.is_file()
    assert Path(installed_one["installed_path"]) == skill_md_path
    assert "name: cursor-ast-refactor" in skill_md_path.read_text(encoding="utf-8")

    scout.adapt_skill_to_antigravity(
        source_ide="claude",
        skill_name="claude-tdd-gate",
        raw_content="Isolate test authoring from production code.",
        target_plugin="iso9000-agentops",
    )
    batch_res = _resolve(scout.install_all_staged_skills(skills_root=skills_root))
    assert batch_res["ok"] is True
    assert batch_res["installed_count"] >= 1
    assert any(item["skill_name"] == "claude-tdd-gate" for item in batch_res["installed"])
    assert (skills_root / "claude-tdd-gate" / "SKILL.md").is_file()

    global_scout = get_ecosystem_scout()
    global_scout.adapt_skill_to_antigravity(
        source_ide="windsurf",
        skill_name="windsurf-chunk-guard",
        raw_content="Keep file writes under 180 lines.",
    )
    client = TestClient(app)
    api_skills_root = tmp_path / "api_skills"
    r_single = client.post(
        "/api/v1/scout/install-skill",
        json={"skill_name": "windsurf-chunk-guard", "skills_root": str(api_skills_root)},
    )
    assert r_single.status_code == 200 and r_single.json()["ok"] is True
    assert (api_skills_root / "windsurf-chunk-guard" / "SKILL.md").is_file()

    r_all = client.post(
        "/api/v1/scout/install-skill",
        json={"install_all": True, "skills_root": str(api_skills_root)},
    )
    assert r_all.status_code == 200 and r_all.json()["ok"] is True

    orch = TaskPipelineOrchestrator(storage_path=tmp_path / "pipeline_state.json")
    sweep = _resolve(scout.run_intelligence_sweep(lanes=["repo_solutions"]))
    gemini_finding = next(f for f in sweep["findings"] if f["target_agy_repo"] == "gemini_local_ui")
    promo = _resolve(scout.promote_finding_to_pipeline(gemini_finding["finding_id"], orchestrator=orch))
    sched = orch.get_schedule(promo["schedule_id"])
    assert sched is not None
    assert Path(sched["workspace_dir"]).exists()
    assert Path(sched["workspace_dir"]).name == "gemini_local_ui"


def test_req_pipe_09_ast_aware_context_compression(tmp_path: Path) -> None:
    """REQ-PIPE-09: Compresses unchanged Python modules into AST signature contracts while keeping target files intact."""
    verbose_source = '''import os
from typing import List

class DataProcessor:
    """Processes batch records."""
    def compute_metrics(self, values: List[int]) -> float:
        """Computes weighted average."""
        total = 0
        for idx, val in enumerate(values):
            total += (idx + 1) * val
        result = float(total) / max(1, len(values))
        return round(result, 4)

async def fetch_remote_status(url: str, timeout: int = 10) -> dict:
    """Fetches status payload."""
    payload = {"url": url, "timeout": timeout, "ok": True}
    for step in range(15):
        payload[f"step_{step}"] = step * 2
    return payload
'''
    compressed = compress_python_ast_contract(verbose_source)
    assert "import os" in compressed
    assert "class DataProcessor:" in compressed
    assert "def compute_metrics(self, values: List[int]) -> float:" in compressed
    assert "async def fetch_remote_status(url: str, timeout: int = 10) -> dict:" in compressed
    assert "..." in compressed
    assert len(compressed) < len(verbose_source)

    ws = tmp_path / "repo"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "unchanged_helper.py").write_text(verbose_source, encoding="utf-8")
    (ws / "target.py").write_text("def active_target() -> int:\n    x = 40 + 2\n    return x\n", encoding="utf-8")

    packed = pack_ultra_2m_repository_context(ws, ast_compress_unchanged=True, full_detail_files=["target.py"])
    assert packed["ok"] is True
    assert int(packed["ast_compressed_files"]) >= 1
    assert "x = 40 + 2" in packed["bundle_text"]
    assert "payload[f\"step_{step}\"]" not in packed["bundle_text"]


def test_req_pipe_10_speculative_sandbox_draft_verification(tmp_path: Path) -> None:
    """REQ-PIPE-10: Evaluates candidate drafts in sandbox and records speculative_sandbox_verified in schedule runs."""
    bad_draft = "### File: mod.py\n```python\ndef broken(:\n    return 1\n```\n"
    good_draft = "### File: mod.py\n```python\ndef compute_answer() -> int:\n    return 42\nassert compute_answer() == 42\n```\n"
    ver = _resolve(verify_candidate_drafts_in_sandbox([bad_draft, good_draft]))
    assert ver["best_index"] == 1
    assert ver["best_draft"] == good_draft
    assert int(ver["verified_count"]) >= 1
    assert ver["evaluations"][1]["score"] > ver["evaluations"][0]["score"]

    ws = tmp_path / "sched_ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "mod.py").write_text("# REQ-PIPE-10\ndef compute_answer() -> int:\n    return 0\n", encoding="utf-8")

    orch = TaskPipelineOrchestrator(storage_path=tmp_path / "orch.json")
    sched = orch.create_schedule(
        name="M42-Speculative-Verify",
        workspace_dir=str(ws),
        prompt="Update compute_answer to return 42",
        target_files=["mod.py"],
    )
    run_rec = _resolve(
        orch.run_schedule_now(
            sched["schedule_id"],
            run_pytest=False,
            mock_candidate_files={"mod.py": "# REQ-PIPE-10\ndef compute_answer() -> int:\n    return 42\n"},
        )
    )
    assert run_rec["speculative_sandbox_verified"] is True
    assert "ast_compressed_files" in run_rec
