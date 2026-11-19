"""
backend/app/storage/workspace_skill_parser.py
Parses and discovers Gemini skills across native pack, custom workspace, claude-repo, and user roots.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import re
from typing import List, Optional

from backend.app.storage.workspace_models import SkillItem

logger = logging.getLogger("gemini_workspace.skill_parser")


def parse_skill_markdown(path: Path, default_source: str = "custom") -> Optional[SkillItem]:
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.debug("Failed reading skill file %s: %s", path, e)
        return None

    slug = path.parent.name
    name = slug.replace("-", " ").title()
    desc = ""
    compat = "native"
    triggers: List[str] = [f"/{slug}"]
    content = raw

    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.DOTALL)
    if m:
        fm_text, content = m.group(1), m.group(2)
        for line in fm_text.splitlines():
            line = line.strip()
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip().strip('"\'')
            elif line.startswith("description:"):
                desc = line.split(":", 1)[1].strip().strip('"\'')
            elif line.startswith("compatibility:"):
                compat_val = line.split(":", 1)[1].strip().strip('"\'').lower()
                if compat_val in ("native", "ide-only", "mcp-bridged"):
                    compat = compat_val
            elif line.startswith("- ") and "triggers:" in fm_text:
                trig = line[2:].strip().strip('"\'')
                if trig and trig not in triggers:
                    triggers.append(trig)

    # Heuristic fallback if compatibility was not explicitly declared
    if compat == "native":
        search_target = f"{raw} {desc}"
        if any(k in search_target for k in ("run_command", "read_file", "agy-lsrpc", "swarm", "computer-use")):
            compat = "ide-only"
        elif "mcp__" in search_target:
            compat = "mcp-bridged"

    return SkillItem(
        id=slug,
        name=name,
        description=desc,
        content=content.strip(),
        compatibility=compat,
        triggers=triggers,
        source=default_source,  # type: ignore[arg-type]
        is_builtin=(default_source == "gemini-pack"),
    )


def discover_all_skills(custom_skills_dir: Path, project_root: Path) -> List[SkillItem]:
    """Scans and merges skills from gemini-pack, custom workspace, claude-repo, and user-gemini roots."""
    discovered: List[SkillItem] = []
    seen_ids: set[str] = set()

    sources = [
        (custom_skills_dir, "custom"),
        (project_root / "backend" / "data" / "gemini_skills", "gemini-pack"),
        (project_root / ".claude" / "skills", "claude-repo"),
        (Path.home() / ".gemini" / "config" / "skills", "user-gemini"),
    ]

    for root_dir, src_label in sources:
        if not root_dir.is_dir():
            continue
        try:
            for item in sorted(root_dir.iterdir()):
                if not item.is_dir():
                    continue
                skill_file = item / "SKILL.md"
                if skill_file.is_file():
                    skill = parse_skill_markdown(skill_file, default_source=src_label)
                    if skill and skill.id not in seen_ids:
                        seen_ids.add(skill.id)
                        discovered.append(skill)
        except Exception as e:
            logger.debug("Failed scanning skills root %s: %s", root_dir, e)

    return discovered
