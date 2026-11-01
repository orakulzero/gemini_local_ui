"""Cross-IDE Swarm AST & Operational Transformation Conflict Resolver (REQ-SWARM-CR-01).

Resolves concurrent code mutation proposals across multiple IDE agents (Cursor,
Claude Code, Windsurf, Antigravity) using line-range operational transformation,
idempotent edit deduplication, AST symbol collision detection, and syntax verification.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentPatchProposal:
    """Represents a line-range patch proposed by an autonomous IDE agent (REQ-SWARM-CR-01)."""

    agent_id: str
    source_ide: str
    file_path: str
    start_line: int
    end_line: int
    replacement_text: str


class SwarmConflictResolver:
    """Resolves concurrent multi-agent patch proposals with AST and line-range checks (REQ-SWARM-CR-01)."""

    def detect_ast_symbol_collisions(
        self,
        base_source: str,
        proposals: list[AgentPatchProposal],
    ) -> dict[str, Any]:
        """Map patch proposals to top-level AST symbols and identify collisions (REQ-SWARM-CR-01)."""
        symbol_spans: list[tuple[str, int, int]] = []
        try:
            tree = ast.parse(base_source)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    s_line = int(getattr(node, "lineno", 1))
                    e_line = int(getattr(node, "end_lineno", s_line))
                    symbol_spans.append((node.name, s_line, e_line))
        except SyntaxError:
            pass

        symbol_map: dict[str, list[str]] = {}
        for sym_name, s_line, e_line in symbol_spans:
            touching_agents: list[str] = []
            for prop in proposals:
                if max(s_line, prop.start_line) <= min(e_line, prop.end_line):
                    if prop.agent_id not in touching_agents:
                        touching_agents.append(prop.agent_id)
            if touching_agents:
                symbol_map[sym_name] = touching_agents

        colliding_symbols = [
            sym_name for sym_name, agents in symbol_map.items() if len(agents) > 1
        ]
        return {
            "symbol_map": symbol_map,
            "colliding_symbols": colliding_symbols,
        }

    def resolve_concurrent_mutations(
        self,
        base_source: str,
        proposals: list[AgentPatchProposal],
    ) -> dict[str, Any]:
        """Merge non-overlapping or idempotent patches and reject conflicts or syntax errors (REQ-SWARM-CR-01)."""
        deduped: list[AgentPatchProposal] = []
        seen_signatures: set[tuple[str, int, int, str]] = set()
        for prop in proposals:
            sig = (
                prop.file_path,
                int(prop.start_line),
                int(prop.end_line),
                prop.replacement_text.strip("\r\n"),
            )
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                deduped.append(prop)

        conflicts: list[dict[str, Any]] = []
        num_deduped = len(deduped)
        for i in range(num_deduped):
            for j in range(i + 1, num_deduped):
                p1 = deduped[i]
                p2 = deduped[j]
                if p1.file_path == p2.file_path and max(p1.start_line, p2.start_line) <= min(
                    p1.end_line, p2.end_line
                ):
                    conflicts.append(
                        {
                            "file_path": p1.file_path,
                            "agent_ids": [p1.agent_id, p2.agent_id],
                            "source_ides": [p1.source_ide, p2.source_ide],
                            "overlap_range": [
                                max(p1.start_line, p2.start_line),
                                min(p1.end_line, p2.end_line),
                            ],
                        }
                    )

        if conflicts:
            return {
                "ok": False,
                "merged_text": base_source,
                "conflicts": conflicts,
                "syntax_valid": False,
            }

        lines = base_source.splitlines()
        trailing_newline = base_source.endswith("\n")

        ordered = sorted(deduped, key=lambda p: (p.start_line, p.end_line), reverse=True)
        for prop in ordered:
            start_idx = max(0, int(prop.start_line) - 1)
            end_idx = max(start_idx, int(prop.end_line))
            replacement_lines = prop.replacement_text.splitlines()
            lines[start_idx:end_idx] = replacement_lines

        merged_text = "\n".join(lines)
        if trailing_newline and not merged_text.endswith("\n"):
            merged_text += "\n"

        try:
            ast.parse(merged_text)
            syntax_valid = True
        except SyntaxError:
            syntax_valid = False

        return {
            "ok": syntax_valid,
            "merged_text": merged_text,
            "conflicts": [],
            "syntax_valid": syntax_valid,
        }
