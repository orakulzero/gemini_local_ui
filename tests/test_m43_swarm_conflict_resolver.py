"""Locked Agile V (P30) Verification Suite for Milestone M43 Cross-IDE Swarm Conflict Resolver.

Requirement Traceability:
- REQ-SWARM-CR-01: Cross-IDE Swarm AST & Operational Transformation Conflict Resolver (`swarm_conflict_resolver.py`)

Hygiene: 100% English-only (0 Cyrillic characters).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.bridge.swarm_conflict_resolver import (  # noqa: E402
    AgentPatchProposal,
    SwarmConflictResolver,
)

BASE_TWO_FUNCS = (
    "def func_a(x: int) -> int:\n"
    "    val = x + 1\n"
    "    return val\n"
    "\n"
    "\n"
    "def func_b(y: int) -> int:\n"
    "    res = y * 2\n"
    "    return res\n"
    "\n"
)


def test_non_overlapping_line_range_patches_merge_cleanly() -> None:
    """REQ-SWARM-CR-01: Verify non-overlapping line-range patches merge cleanly into valid AST."""
    resolver = SwarmConflictResolver()
    p1 = AgentPatchProposal(
        agent_id="agent_cursor",
        source_ide="cursor",
        file_path="service.py",
        start_line=1,
        end_line=3,
        replacement_text="def func_a(x: int) -> int:\n    val = x + 100\n    return val",
    )
    p2 = AgentPatchProposal(
        agent_id="agent_claude",
        source_ide="claude",
        file_path="service.py",
        start_line=6,
        end_line=8,
        replacement_text="def func_b(y: int) -> int:\n    res = y * 50\n    return res",
    )
    res = resolver.resolve_concurrent_mutations(BASE_TWO_FUNCS, [p1, p2])
    assert res["ok"] is True
    assert res["conflicts"] == []
    assert res.get("syntax_valid", True) is True
    merged = res["merged_text"]
    assert "x + 100" in merged
    assert "y * 50" in merged
    ast.parse(merged)


def test_overlapping_ast_symbol_or_line_range_flagged_as_conflict() -> None:
    """REQ-SWARM-CR-01: Verify overlapping line ranges across two agents are flagged as a conflict."""
    resolver = SwarmConflictResolver()
    p1 = AgentPatchProposal(
        agent_id="agent_cursor",
        source_ide="cursor",
        file_path="service.py",
        start_line=1,
        end_line=5,
        replacement_text="def func_a(x: int) -> int:\n    return x + 10\n",
    )
    p2 = AgentPatchProposal(
        agent_id="agent_windsurf",
        source_ide="windsurf",
        file_path="service.py",
        start_line=3,
        end_line=7,
        replacement_text="    return x - 99\ndef func_b(y: int) -> int:\n",
    )
    res = resolver.resolve_concurrent_mutations(BASE_TWO_FUNCS, [p1, p2])
    assert res["ok"] is False
    assert len(res["conflicts"]) == 1
    conflict = res["conflicts"][0]
    assert conflict["agent_ids"] == ["agent_cursor", "agent_windsurf"]


def test_identical_idempotent_concurrent_edits_deduplicated() -> None:
    """REQ-SWARM-CR-01: Verify identical concurrent edits on the same range are deduplicated cleanly."""
    resolver = SwarmConflictResolver()
    same_replacement = "def func_a(x: int) -> int:\n    val = x + 42\n    return val"
    p1 = AgentPatchProposal(
        agent_id="agent_cursor",
        source_ide="cursor",
        file_path="service.py",
        start_line=1,
        end_line=3,
        replacement_text=same_replacement,
    )
    p2 = AgentPatchProposal(
        agent_id="agent_claude",
        source_ide="claude",
        file_path="service.py",
        start_line=1,
        end_line=3,
        replacement_text=same_replacement,
    )
    res = resolver.resolve_concurrent_mutations(BASE_TWO_FUNCS, [p1, p2])
    assert res["ok"] is True
    assert res["conflicts"] == []
    assert "x + 42" in res["merged_text"]
    assert res["merged_text"].count("def func_a") == 1


def test_syntax_invalid_merged_output_rejected() -> None:
    """REQ-SWARM-CR-01: Verify patch introducing a Python syntax error is rejected."""
    resolver = SwarmConflictResolver()
    bad_patch = AgentPatchProposal(
        agent_id="agent_windsurf",
        source_ide="windsurf",
        file_path="service.py",
        start_line=1,
        end_line=3,
        replacement_text="def func_a(x: int -> :\n    return ???",
    )
    res = resolver.resolve_concurrent_mutations(BASE_TWO_FUNCS, [bad_patch])
    assert res["ok"] is False
    assert res["syntax_valid"] is False


def test_detect_ast_symbol_collisions() -> None:
    """REQ-SWARM-CR-01: Verify detect_ast_symbol_collisions maps proposals to AST symbols."""
    resolver = SwarmConflictResolver()
    p1 = AgentPatchProposal(
        agent_id="agent_cursor",
        source_ide="cursor",
        file_path="service.py",
        start_line=1,
        end_line=3,
        replacement_text="def func_a(x: int) -> int:\n    return x + 1",
    )
    p2 = AgentPatchProposal(
        agent_id="agent_windsurf",
        source_ide="windsurf",
        file_path="service.py",
        start_line=2,
        end_line=3,
        replacement_text="    val = x + 999\n    return val",
    )
    report = resolver.detect_ast_symbol_collisions(BASE_TWO_FUNCS, [p1, p2])
    assert "func_a" in report["symbol_map"]
    assert "func_a" in report["colliding_symbols"]
    assert set(report["symbol_map"]["func_a"]) == {"agent_cursor", "agent_windsurf"}


def test_req_swarm_cr_01_traceability_and_english_hygiene() -> None:
    """REQ-SWARM-CR-01: Verify swarm_conflict_resolver.py contains REQ-SWARM-CR-01 and 0 Cyrillic chars."""
    target_file = REPO_ROOT / "backend" / "app" / "bridge" / "swarm_conflict_resolver.py"
    assert target_file.is_file(), f"Missing target module: {target_file}"
    content = target_file.read_text(encoding="utf-8")
    assert "REQ-SWARM-CR-01" in content
    for ch in content:
        code = ord(ch)
        assert not (0x0400 <= code <= 0x04FF), f"Cyrillic character found: {ch!r}"
