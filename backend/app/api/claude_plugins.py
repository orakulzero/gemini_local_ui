import os
from pathlib import Path
import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

from backend.app.api.gems import _LOCAL_CUSTOM_GEMS, Gem

router = APIRouter(prefix="/api/claude-plugins", tags=["claude-plugins"])

def parse_markdown_plugin(path: Path) -> dict:
    try:
        content = path.read_text(encoding="utf-8")
        parts = content.split("---")
        if len(parts) >= 3:
            frontmatter = yaml.safe_load(parts[1])
            prompt = "---".join(parts[2:]).strip()
            if isinstance(frontmatter, dict):
                frontmatter.setdefault("name", path.stem)
                frontmatter["prompt"] = prompt
                return frontmatter
    except Exception as e:
        print(f"Error parsing {path}: {e}")
    return {}

def scan_plugins(folder_name: str) -> List[dict]:
    root_dir = Path.cwd()
    target_dir = root_dir / ".claude" / folder_name
    plugins = []
    if target_dir.exists() and target_dir.is_dir():
        if folder_name == "skills":
            for md_file in sorted(target_dir.glob("*/SKILL.md")):
                parsed = parse_markdown_plugin(md_file)
                if parsed:
                    parsed.setdefault("name", md_file.parent.name)
                    plugins.append(parsed)
        for md_file in sorted(target_dir.glob("*.md")):
            parsed = parse_markdown_plugin(md_file)
            if parsed:
                plugins.append(parsed)
    return plugins


SUBAGENT_TIER_MAP: Dict[str, str] = {
    "haiku": "56fdd199312815e2",
    "flash": "56fdd199312815e2",
    "flash-lite": "8c46e95b1a07cecc",
    "sonnet": "95a4c0abcba24364",
    "opus": "95a4c0abcba24364",
    "argon": "95a4c0abcba24364",
}


def detect_subagent_profile(system_payload: Any) -> Optional[Dict[str, Any]]:
    """
    Inspects an incoming Anthropic `system` payload (str or list of text blocks)
    and matches it against `.claude/agents/*.md` prompts to resolve per-subagent
    model tier (`haiku` -> Flash, `opus` -> Argon), `gem_id`, and `feature_mode`.
    """
    if not system_payload:
        return None
    if isinstance(system_payload, str):
        sys_text = system_payload
    elif isinstance(system_payload, list):
        parts: List[str] = []
        for item in system_payload:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
            elif hasattr(item, "text") and getattr(item, "text"):
                parts.append(str(getattr(item, "text")))
        sys_text = "\n".join(parts)
    else:
        sys_text = str(system_payload)

    if not sys_text.strip():
        return None

    agents = scan_plugins("agents")
    for ag in agents:
        name = str(ag.get("name", "")).strip()
        prompt_body = str(ag.get("prompt", "")).strip()
        if not name or not prompt_body:
            continue
        first_line = next((ln.strip() for ln in prompt_body.splitlines() if ln.strip()), "")
        matched = (prompt_body in sys_text) or (len(first_line) >= 16 and first_line in sys_text)
        if matched:
            raw_tier = str(ag.get("model", "sonnet")).strip().lower()
            canonical_model = SUBAGENT_TIER_MAP.get(raw_tier, "95a4c0abcba24364")
            gem_id = f"gem_cc_{name.replace(' ', '_').lower()}"
            feature_mode = ag.get("feature_mode")
            return {
                "name": name,
                "model_alias": raw_tier,
                "canonical_model": canonical_model,
                "gem_id": gem_id,
                "feature_mode": int(feature_mode) if feature_mode is not None else None,
            }
    return None

@router.get("")
async def list_plugins():
    agents = scan_plugins("agents")
    commands = scan_plugins("commands")
    skills = scan_plugins("skills")
    return {
        "ok": True,
        "agents_count": len(agents),
        "commands_count": len(commands),
        "skills_count": len(skills),
        "agents": agents,
        "commands": commands,
        "skills": skills,
    }

@router.post("/sync-gems")
async def sync_gems():
    agents = scan_plugins("agents")
    synced = []
    for agent in agents:
        name = agent.get("name", "Unknown Agent")
        description = agent.get("description", "")
        prompt = agent.get("prompt", "")
        gem_id = f"gem_cc_{name.replace(' ', '_').lower()}"

        gem_obj = Gem(
            id=gem_id,
            name=name,
            description=description,
            prompt=prompt,
            is_builtin=False
        )

        if isinstance(_LOCAL_CUSTOM_GEMS, list):
            existing_idx = next((i for i, g in enumerate(_LOCAL_CUSTOM_GEMS) if getattr(g, "id", None) == gem_id), None)
            if existing_idx is not None:
                _LOCAL_CUSTOM_GEMS[existing_idx] = gem_obj
            else:
                _LOCAL_CUSTOM_GEMS.append(gem_obj)
        elif isinstance(_LOCAL_CUSTOM_GEMS, dict):
            _LOCAL_CUSTOM_GEMS[gem_id] = gem_obj

        gem_dict = {
            "id": gem_id,
            "name": name,
            "title": name,
            "description": description,
            "prompt": prompt,
            "system_prompt": prompt,
            "is_builtin": False,
        }
        synced.append(gem_dict)

    return {
        "ok": True,
        "synced_count": len(synced),
        "gems": synced
    }

class AgentCreateRequest(BaseModel):
    id: Optional[str] = None
    name: str
    description: str = ""
    prompt: Optional[str] = None
    system_prompt: Optional[str] = None
    tools: list = []
    model: str = "sonnet"

@router.post("/agents")
async def create_agent(req: AgentCreateRequest):
    root_dir = Path.cwd()
    target_dir = root_dir / ".claude" / "agents"
    target_dir.mkdir(parents=True, exist_ok=True)

    slug = (req.id or req.name).strip()
    file_path = target_dir / f"{slug}.md"
    body_prompt = (req.prompt if req.prompt is not None else (req.system_prompt or "")).strip()

    yaml_content = {
        "name": slug,
        "description": req.description,
        "tools": req.tools,
        "model": req.model
    }

    content = f"---\n{yaml.dump(yaml_content, default_flow_style=False, sort_keys=False)}---\n{body_prompt}\n"
    file_path.write_text(content, encoding="utf-8")

    return {"ok": True, "agent": {"id": slug, "name": req.name, "description": req.description, "prompt": body_prompt, "tools": req.tools, "model": req.model}}

@router.delete("/agents/{name}")
async def delete_agent(name: str):
    root_dir = Path.cwd()
    file_path = root_dir / ".claude" / "agents" / f"{name}.md"
    if file_path.exists():
        file_path.unlink()
        return {"ok": True, "deleted": name}
    raise HTTPException(status_code=404, detail="Agent not found")
