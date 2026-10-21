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
