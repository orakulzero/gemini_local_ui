"""
tests/test_m49_additive_contract_and_noop.py
Locked Agile-V verification suite for M49:
- REQ-PIPE-12: Additive-Only AST/CLI/Return-Key Contract Guard & Zero-Diff Clean No-Op Verification.
- REQ-SCOUT-07: Multi-Block JSON Extraction & Template Deduplication in EcosystemIntelligenceScout.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from backend.app.bridge.additive_contract_guard import (
    build_additive_only_prompt_block,
    extract_immutable_contract_snapshot,
    is_noop_pipeline_response,
    verify_additive_contract,
)
from backend.app.bridge.ecosystem_scout import EcosystemIntelligenceScout
from backend.app.bridge.task_pipeline import verify_five_quality_gates


def test_req_pipe_12_extract_and_verify_additive_contract() -> None:
    """REQ-PIPE-12: Verifies AST snapshot extraction and additive-only contract validation."""
    old_src = (
        "import argparse\n\n"
        "class Inspector:\n"
        "    def inspect(self, fmt: str = 'json') -> dict:\n"
        "        return {'fmt': fmt}\n\n"
        "def run_cli(as_json: bool = False) -> dict:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--json', action='store_true')\n"
        "    parser.add_argument('-f', '--format', default='text')\n"
        "    return {'ok': True, 'authenticode': {'signed': False}}\n"
    )
    snap = extract_immutable_contract_snapshot(old_src)
    assert snap["classes"] == ["Inspector"]
    assert snap["functions"]["run_cli"] == ["as_json"]
    assert snap["functions"]["Inspector.inspect"] == ["self", "fmt"]
    assert snap["cli_flags"] == ["--format", "--json"]
    assert snap["return_dict_keys"]["run_cli"] == ["authenticode", "ok"]

    prompt_block = build_additive_only_prompt_block({"cli.py": old_src})
    assert "REQ-PIPE-12" in prompt_block
    assert "run_cli" in prompt_block
    assert "--json" in prompt_block
    assert "authenticode" in prompt_block

    new_src_valid = (
        "import argparse\n\n"
        "class Inspector:\n"
        "    def inspect(self, fmt: str = 'json', strict: bool = False) -> dict:\n"
        "        return {'fmt': fmt, 'strict': strict}\n\n"
        "def helper_addon() -> int:\n"
        "    return 42\n\n"
        "def run_cli(as_json: bool = False, timeout: int = 30) -> dict:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--json', action='store_true')\n"
        "    parser.add_argument('-f', '--format', default='text')\n"
        "    parser.add_argument('--verbose', action='store_true')\n"
        "    return {'ok': True, 'authenticode': {'signed': False}, 'extra': 1}\n"
    )
    valid_res = verify_additive_contract(old_src, new_src_valid)
    assert valid_res["passed"] is True
    assert valid_res["violations"] == []

    broken_flag_src = old_src.replace("'--json'", "'--output-json'")
    res_flag = verify_additive_contract(old_src, broken_flag_src)
    assert res_flag["passed"] is False
    assert any("--json" in v for v in res_flag["violations"])

    broken_key_src = old_src.replace("'authenticode': {'signed': False}", "'sig': {}")
    res_key = verify_additive_contract(old_src, broken_key_src)
    assert res_key["passed"] is False
    assert any("authenticode" in v for v in res_key["violations"])

    broken_param_src = old_src.replace("def run_cli(as_json: bool = False)", "def run_cli(json_mode: bool = False)")
    res_param = verify_additive_contract(old_src, broken_param_src)
    assert res_param["passed"] is False
    assert any("as_json" in v for v in res_param["violations"])


def test_req_pipe_12_is_noop_and_pipeline_clean_noop_verified(tmp_path: Path) -> None:
    """REQ-PIPE-12: Verifies clean no-op response detection and quality gate handling."""
    orig = {"a.py": "def f():\n    return 1\n"}
    assert is_noop_pipeline_response("### Status: CLEAN_NOOP_VERIFIED\nAll requirements satisfied.", orig, {}, cid="c_ok") is True
    assert is_noop_pipeline_response("", orig, {}, cid="c_unauth_123") is False
    assert is_noop_pipeline_response("", orig, {}, cid="c_err_999") is False
    assert is_noop_pipeline_response("same file", orig, {"a.py": "def f():\n    return 1\n"}, cid="c_ok") is True
    assert is_noop_pipeline_response("changed file", orig, {"a.py": "def f():\n    return 2\n"}, cid="c_ok") is False

    g_noop = verify_five_quality_gates(
        workspace_dir=tmp_path,
        orig_files=orig,
        candidate_files={},
        run_pytest=False,
        allow_empty_noop=True,
    )
    assert g_noop["passed"] is True
    assert g_noop.get("noop_verified") is True

    g_strict = verify_five_quality_gates(
        workspace_dir=tmp_path,
        orig_files=orig,
        candidate_files={},
        run_pytest=False,
        allow_empty_noop=False,
    )
    assert g_strict["passed"] is False


def test_req_scout_07_multi_block_json_extraction_and_dedup(tmp_path: Path) -> None:
    """REQ-SCOUT-07: Verifies multi-block JSON extraction fallback and template deduplication."""

    class _MockClient:
        async def generate_stream(self, prompt: str, model: str = "gemini-3.8-flash") -> Any:
            raw = (
                "Thinking block with cut-off fence:\n"
                "```json\n"
                '[{"lane": "repo_solutions", "title": "Broken"\n'
                "```\n"
                "Final valid JSON block:\n"
                "```json\n"
                '[{"lane": "repo_solutions", "title": "Unique Live Finding Alpha", '
                '"target_agy_repo": "pe-reversing-lab", "summary": "Test summary", '
                '"integration_proposal": "Test proposal", "impact_score": 9.2, "feasibility_score": 9.4}]\n'
                "```\n"
            )
            yield SimpleNamespace(type="text", text_delta=raw)
            yield SimpleNamespace(type="done", text_delta="")

    scout = EcosystemIntelligenceScout(storage_path=tmp_path / "scout.json")
    sweep1 = asyncio.run(scout.run_intelligence_sweep(lanes=["repo_solutions"], client=_MockClient()))
    titles1 = [f["title"] for f in sweep1["findings"]]
    assert "Unique Live Finding Alpha" in titles1

    sweep2 = asyncio.run(scout.run_intelligence_sweep(lanes=["repo_solutions"], client=_MockClient()))
    all_stored_titles = [f["title"] for f in scout._state["findings"].values()]
    assert all_stored_titles.count("Unique Live Finding Alpha") == 1
