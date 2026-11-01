from collections import deque
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any

from backend.app.api.gems import _LOCAL_CUSTOM_GEMS, Gem
import contextlib

router = APIRouter(prefix="/api/claude-plugins", tags=["claude-plugins"])

SAFE_SLUG_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SWARM_EVENTS_PATH = _REPO_ROOT / "backend" / "data" / "swarm_events.jsonl"
_SWARM_EVENTS: deque[dict[str, Any]] = deque(maxlen=100)
_SWARM_EVENTS_LOADED: bool = False

MODEL_TIER_LABELS: dict[str, str] = {
    "95a4c0abcba24364": "Gemini 4 Argon",
    "56fdd199312815e2": "Gemini 3 Flash",
    "e6fa609c3fa255c0": "Gemini 3.1 Pro",
    "8c46e95b1a07cecc": "Gemini 2.5 Flash Lite",
}


def _load_persisted_swarm_events() -> None:
    global _SWARM_EVENTS_LOADED
    if _SWARM_EVENTS_LOADED:
        return
    _SWARM_EVENTS_LOADED = True
    if not _SWARM_EVENTS_PATH.exists():
        return
    try:
        lines = _SWARM_EVENTS_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-100:]:
            line = line.strip()
            if line:
                try:
                    _SWARM_EVENTS.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass


def record_swarm_event(
    *,
    message_id: str,
    requested_model: str,
    resolved_model: str,
    subagent: str | None,
    gem_id: str | None,
    cache_hit: bool,
    matched_turns: int,
    total_turns: int,
    tool_calls: list[str],
    latency_ms: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    stream: bool = True,
) -> dict[str, Any]:
    """Records a live gateway call from Claude Code for the Swarm Monitor UI."""
    _load_persisted_swarm_events()
    event = {
        "id": message_id,
        "timestamp": round(time.time(), 3),
        "requested_model": requested_model,
        "resolved_model": resolved_model,
        "model_tier_label": MODEL_TIER_LABELS.get(resolved_model, resolved_model),
        "subagent": subagent or "lead-orchestrator",
        "gem_id": gem_id,
        "cache_hit": bool(cache_hit),
        "matched_turns": int(matched_turns),
        "total_turns": int(total_turns),
        "tool_calls": list(tool_calls),
        "latency_ms": int(latency_ms),
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "stream": bool(stream),
    }
    _SWARM_EVENTS.append(event)
    try:
        _SWARM_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _SWARM_EVENTS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return event

def parse_markdown_plugin(path: Path) -> dict:
    try:
        content = path.read_text(encoding="utf-8")
        parts = content.split("---")
        if len(parts) >= 3:
            frontmatter = yaml.safe_load(parts[1])
            prompt = "---".join(parts[2:]).strip()
            if isinstance(frontmatter, dict):
                frontmatter.setdefault("name", path.stem)
                raw_tools = frontmatter.get("tools")
                if isinstance(raw_tools, str):
                    frontmatter["tools"] = [t.strip() for t in raw_tools.split(",") if t.strip()]
                elif isinstance(raw_tools, list):
                    frontmatter["tools"] = [str(t).strip() for t in raw_tools if str(t).strip()]
                elif raw_tools is None and "tools" in frontmatter:
                    frontmatter["tools"] = []
                frontmatter["prompt"] = prompt
                return frontmatter
    except Exception as e:
        print(f"Error parsing {path}: {e}")
    return {}

def scan_plugins(folder_name: str) -> list[dict]:
    target_dir = _REPO_ROOT / ".claude" / folder_name
    if not target_dir.exists():
        target_dir = Path.cwd() / ".claude" / folder_name
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


SUBAGENT_TIER_MAP: dict[str, str] = {
    "haiku": "56fdd199312815e2",
    "flash": "56fdd199312815e2",
    "flash-lite": "8c46e95b1a07cecc",
    "sonnet": "95a4c0abcba24364",
    "opus": "95a4c0abcba24364",
    "argon": "95a4c0abcba24364",
}


def detect_subagent_profile(system_payload: Any) -> dict[str, Any] | None:
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
        parts: list[str] = []
        for item in system_payload:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
            elif hasattr(item, "text") and item.text:
                parts.append(str(item.text))
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
        first_prefix = first_line[:64] if len(first_line) >= 32 else first_line
        matched = (
            (prompt_body in sys_text)
            or (len(first_line) >= 16 and first_line in sys_text)
            or (len(first_prefix) >= 32 and first_prefix in sys_text)
            or (f"**{name}**" in sys_text)
        )
        if matched:
            raw_tier = str(ag.get("model", "sonnet")).strip().lower()
            canonical_model = SUBAGENT_TIER_MAP.get(raw_tier, "95a4c0abcba24364")
            gem_id = f"gem_cc_{name.replace(' ', '_').lower()}"
            feature_mode = ag.get("feature_mode")
            return {
                "name": name,
                "subagent": name,
                "model": canonical_model,
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
    id: str | None = None
    name: str
    description: str = ""
    prompt: str | None = None
    system_prompt: str | None = None
    tools: list = []
    model: str = "sonnet"

@router.post("/agents")
async def create_agent(req: AgentCreateRequest):
    slug = (req.id or req.name).strip()
    if not SAFE_SLUG_RE.match(slug):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid agent slug/name '{slug}': must be alphanumeric with dashes or underscores",
        )

    target_dir = _REPO_ROOT / ".claude" / "agents"
    if not target_dir.exists():
        target_dir = Path.cwd() / ".claude" / "agents"
    target_dir.mkdir(parents=True, exist_ok=True)

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
    slug = name.strip()
    if not SAFE_SLUG_RE.match(slug):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid agent slug/name '{slug}': must be alphanumeric with dashes or underscores",
        )
    target_dir = _REPO_ROOT / ".claude" / "agents"
    if not target_dir.exists():
        target_dir = Path.cwd() / ".claude" / "agents"
    file_path = target_dir / f"{slug}.md"
    if file_path.exists():
        file_path.unlink()
        return {"ok": True, "deleted": slug}
    raise HTTPException(status_code=404, detail="Agent not found")


def _get_conversation_cache_stats() -> dict[str, Any]:
    from backend.app.openai import conversation_cache

    cache = conversation_cache.default_cache
    db_path = getattr(cache, "db_path", None)
    ram_entries = len(getattr(cache, "_cache", {}))
    sqlite_rows = 0
    if db_path and Path(db_path).exists():
        try:
            with sqlite3.connect(str(db_path)) as conn:
                row = conn.execute("SELECT COUNT(*) FROM conversation_prefix_cache").fetchone()
                if row:
                    sqlite_rows = int(row[0])
        except Exception:
            pass
    return {
        "ram_entries": ram_entries,
        "sqlite_rows": sqlite_rows,
        "db_path": str(db_path) if db_path else None,
    }


def _scan_recent_claude_sessions(limit: int = 8) -> list[dict[str, Any]]:
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return []

    jsonl_files: list[Path] = []
    for proj_dir in claude_projects.iterdir():
        if proj_dir.is_dir():
            jsonl_files.extend(proj_dir.glob("*.jsonl"))

    jsonl_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    sessions: list[dict[str, Any]] = []

    for jf in jsonl_files[:limit]:
        try:
            stat = jf.stat()
            lines = jf.read_text(encoding="utf-8", errors="replace").splitlines()
            tool_counts: dict[str, int] = {}
            turn_count = 0
            last_prompt_preview = ""
            for raw_ln in lines:
                if not raw_ln.strip():
                    continue
                try:
                    entry = json.loads(raw_ln)
                except Exception:
                    continue
                if not isinstance(entry, dict):
                    continue
                etype = entry.get("type")
                if etype in ("user", "assistant"):
                    turn_count += 1
                msg = entry.get("message")
                if not isinstance(msg, dict):
                    msg = {}
                content = msg.get("content")
                if etype == "user" and isinstance(content, str) and content.strip():
                    last_prompt_preview = content.strip()[:140]
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tname = str(block.get("name", "unknown"))
                            tool_counts[tname] = tool_counts.get(tname, 0) + 1

            subagent_dir = jf.parent / jf.stem / "subagents"
            subagents_count = len(list(subagent_dir.glob("agent-*.jsonl"))) if subagent_dir.exists() else 0

            sessions.append(
                {
                    "session_id": jf.stem,
                    "project": jf.parent.name,
                    "updated_at": round(stat.st_mtime, 2),
                    "size_bytes": stat.st_size,
                    "turn_count": turn_count,
                    "subagents_count": subagents_count,
                    "tool_counts": tool_counts,
                    "last_prompt_preview": last_prompt_preview,
                }
            )
        except Exception:
            continue
    return sessions


@router.get("/swarm-telemetry")
async def get_swarm_telemetry(limit: int = 30) -> dict[str, Any]:
    """
    Returns live Claude Code Swarm Monitor telemetry:
    - Recent gateway events (resolved model tier Flash/Argon, subagent profile, cache hit, tool calls)
    - SQLite + RAM ConversationCache statistics
    - Registered .claude plugins (agents, commands, skills, hooks)
    - Recent Claude Code JSONL sessions from ~/.claude/projects
    """
    _load_persisted_swarm_events()
    agents = scan_plugins("agents")
    commands = scan_plugins("commands")
    skills = scan_plugins("skills")

    hooks_dir = _REPO_ROOT / ".claude" / "hooks"
    if not hooks_dir.exists():
        hooks_dir = Path.cwd() / ".claude" / "hooks"
    hooks_list = [p.name for p in sorted(hooks_dir.glob("*.py"))] if hooks_dir.exists() else []

    events_list = list(_SWARM_EVENTS)[-max(1, min(limit, 100)):]
    events_list.reverse()

    total_events = len(_SWARM_EVENTS)
    cache_hits = sum(1 for e in _SWARM_EVENTS if e.get("cache_hit"))
    cache_hit_rate = round((cache_hits / total_events) * 100.0, 1) if total_events > 0 else 100.0

    tier_distribution: dict[str, int] = {}
    subagent_distribution: dict[str, int] = {}
    for ev in _SWARM_EVENTS:
        lbl = str(ev.get("model_tier_label", "Gemini 4 Argon"))
        tier_distribution[lbl] = tier_distribution.get(lbl, 0) + 1
        sub = str(ev.get("subagent", "lead-orchestrator"))
        subagent_distribution[sub] = subagent_distribution.get(sub, 0) + 1

    return {
        "ok": True,
        "cache_stats": {
            **_get_conversation_cache_stats(),
            "cache_hit_rate_pct": cache_hit_rate,
            "total_recorded_turns": total_events,
        },
        "plugins_summary": {
            "agents_count": len(agents),
            "commands_count": len(commands),
            "skills_count": len(skills),
            "hooks_count": len(hooks_list),
            "hooks": hooks_list,
        },
        "live_events": events_list,
        "recent_sessions": _scan_recent_claude_sessions(),
        "tier_distribution": tier_distribution,
        "subagent_distribution": subagent_distribution,
    }

_BG_JOBS_FILE = _REPO_ROOT / "backend" / "data" / "background_jobs.json"
_BG_JOBS_MEM: dict[str, dict[str, Any]] = {}

def _load_persisted_background_jobs() -> dict[str, dict[str, Any]]:
    if not _BG_JOBS_FILE.exists():
        return {}
    try:
        data = json.loads(_BG_JOBS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _BG_JOBS_MEM.update(data)
    except Exception:
        pass
    return _BG_JOBS_MEM

def _save_persisted_background_jobs() -> None:
    try:
        _BG_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BG_JOBS_FILE.write_text(json.dumps(_BG_JOBS_MEM, indent=2), encoding="utf-8")
    except Exception:
        pass

def _list_persisted_background_jobs(limit: int = 20) -> list[dict[str, Any]]:
    _load_persisted_background_jobs()
    jobs = list(_BG_JOBS_MEM.values())
    jobs.sort(key=lambda j: j.get("created_at", 0), reverse=True)
    return jobs[:limit]

class BackgroundStageSpec(BaseModel):
    name: str
    prompt: str
    subagent: str
    model: str
    output_file: str | None = None

class BackgroundJobRequest(BaseModel):
    title: str
    workspace_dir: str | None = None
    stages: list[BackgroundStageSpec]
    script_command: str | None = None
    artifacts: list[str] = []
    run_async: bool = True

async def _execute_background_job(job_id: str) -> dict[str, Any]:
    job = _BG_JOBS_MEM.get(job_id)
    if not job:
        return {}
    job["status"] = "running"
    job["started_at"] = time.time()
    _save_persisted_background_jobs()

    import subprocess
    import sys
    
    stages = job.get("stages", [])
    workspace = job.get("workspace_dir") or str(Path.cwd())
    results = []

    try:
        for i, stage in enumerate(stages):
            job["current_stage"] = i
            _save_persisted_background_jobs()

            cmd = [
                sys.executable, "-m", "claude",
                "--workspace", workspace,
                "--prompt", stage.get("prompt", ""),
            ]
            
            if stage.get("subagent"):
                cmd.extend(["--agent", stage.get("subagent")])
                
            env = os.environ.copy()
            if stage.get("model"):
                env["CLAUDE_MODEL"] = stage.get("model")

            proc = subprocess.run(
                cmd,
                cwd=workspace,
                capture_output=True,
                text=True,
                env=env,
                check=False
            )
            
            stage_result = {
                "stage_name": stage.get("name"),
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
            results.append(stage_result)

            if proc.returncode != 0:
                job["status"] = "failed"
                job["error"] = f"Stage {i} '{stage.get('name')}' failed."
                break

        if job["status"] == "running":
            script_cmd = job.get("script_command")
            if script_cmd:
                job["current_stage"] = "script"
                _save_persisted_background_jobs()
                proc = subprocess.run(
                    script_cmd,
                    shell=True,
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    check=False
                )
                results.append({
                    "stage_name": "script_command",
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                })
                if proc.returncode != 0:
                    job["status"] = "failed"
                    job["error"] = "Post-script command failed."

        if job["status"] == "running":
            job["status"] = "completed"

    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
    finally:
        job["completed_at"] = time.time()
        job["results"] = results
        _save_persisted_background_jobs()

    return job


@router.post("/background-jobs")
async def create_background_job(req: BackgroundJobRequest) -> dict[str, Any]:
    import uuid
    import asyncio
    
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    _load_persisted_background_jobs()
    
    _BG_JOBS_MEM[job_id] = {
        "id": job_id,
        "title": req.title,
        "workspace_dir": req.workspace_dir,
        "stages": [s.dict() for s in req.stages],
        "script_command": req.script_command,
        "artifacts": req.artifacts,
        "status": "pending",
        "created_at": time.time(),
        "started_at": None,
        "completed_at": None,
        "current_stage": None,
        "results": [],
        "error": None,
    }
    _save_persisted_background_jobs()
    
    if req.run_async:
        asyncio.create_task(_execute_background_job(job_id))
        return {"ok": True, "job_id": job_id, "status": "pending"}
    result = await _execute_background_job(job_id)
    return {"ok": True, "job": result}

@router.get("/background-jobs")
async def list_background_jobs_endpoint(limit: int = 20) -> dict[str, Any]:
    jobs = _list_persisted_background_jobs(limit)
    return {"ok": True, "jobs": jobs}

@router.get("/background-jobs/{job_id}")
async def get_background_job_endpoint(job_id: str) -> dict[str, Any]:
    _load_persisted_background_jobs()
    if job_id not in _BG_JOBS_MEM:
        raise HTTPException(status_code=404, detail="Background job not found")
    return {"ok": True, "job": _BG_JOBS_MEM[job_id]}

class WorkspaceInitRequest(BaseModel):
    target_dir: str
    include_mcp_config: bool = True
    overwrite: bool = False
    gateway_url: str = "http://127.0.0.1:8000"

def init_claude_workspace(
    target_dir: str,
    include_mcp_config: bool = True,
    overwrite: bool = True,
    gateway_url: str = "http://127.0.0.1:8000"
) -> dict[str, Any]:
    """
    Exports and installs the complete `.claude/{agents,skills,commands,hooks,settings.json}`
    and `.mcp.json` pack into `target_dir` in one call.
    """
    tgt = Path(target_dir).resolve()
    if not tgt.exists():
        tgt.mkdir(parents=True, exist_ok=True)
        
    claude_dir = tgt / ".claude"
    if claude_dir.exists():
        if not overwrite:
            return {"ok": False, "error": f".claude already exists in {target_dir}"}
        shutil.rmtree(claude_dir, ignore_errors=True)
        
    source_claude = _REPO_ROOT / ".claude"
    if source_claude.exists():
        shutil.copytree(source_claude, claude_dir)
    else:
        claude_dir.mkdir()
        
    settings_path = claude_dir / "settings.json"
    settings = {}
    if settings_path.exists():
        with contextlib.suppress(BaseException):
            settings = json.loads(settings_path.read_text())
    settings["custom_gateway_url"] = gateway_url
    settings["theme"] = "dark"
    settings["verbose"] = True
    settings_path.write_text(json.dumps(settings, indent=2))
    
    mcp_exported = False
    if include_mcp_config:
        source_mcp = _REPO_ROOT / "backend" / "mcp_servers" / "mcp_config.json"
        if source_mcp.exists():
            tgt_mcp = tgt / "mcp.json"
            if overwrite or not tgt_mcp.exists():
                shutil.copy2(source_mcp, tgt_mcp)
                mcp_exported = True
                
    return {
        "ok": True,
        "target_dir": str(tgt),
        "exported_plugins": True,
        "exported_mcp": mcp_exported,
        "gateway_url": gateway_url
    }

@router.post("/init-workspace")
async def init_workspace_endpoint(req: WorkspaceInitRequest) -> dict[str, Any]:
    res = init_claude_workspace(
        req.target_dir,
        req.include_mcp_config,
        req.overwrite,
        req.gateway_url
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res


import hashlib
from urllib.parse import parse_qs, urlparse

_BARE_SHARE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{8,32}$")
_REQ_ID_RE = re.compile(r"^REQ-\d+$")

def _safe_nested_get(obj: Any, *path: Any) -> Any:
    for key in path:
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list) and isinstance(key, int) and 0 <= key < len(obj):
            obj = obj[key]
        else:
            return None
    return obj

def _collect_html_widgets(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        if "html" in node and isinstance(node["html"], str):
            out.append(node["html"])
        for v in node.values():
            _collect_html_widgets(v, out)
    elif isinstance(node, list):
        for item in node:
            _collect_html_widgets(item, out)

def parse_shared_conversation_payload(raw_data: Any) -> dict[str, Any]:
    """REQ-001: Parse ujx1Bf shared conversation payload into normalized document."""
    try:
        if isinstance(raw_data, str):
            raw_data = json.loads(raw_data)
        
        envelope = _safe_nested_get(raw_data, "default", 0) or _safe_nested_get(raw_data, 0)
        inner_payload = _safe_nested_get(envelope, 2)
        if isinstance(inner_payload, str):
            inner_payload = json.loads(inner_payload)
            
        convo_data = _safe_nested_get(inner_payload, 0, 0)
        if not convo_data:
            return {"error": "Invalid ujx1Bf payload structure"}
            
        title = _safe_nested_get(convo_data, 1)
        created_at = _safe_nested_get(convo_data, 3)
        author = _safe_nested_get(convo_data, 4, 1)
        author_avatar = _safe_nested_get(convo_data, 4, 2)
        
        messages = []
        raw_msgs = _safe_nested_get(convo_data, 0) or []
        for rm in raw_msgs:
            role = "user" if _safe_nested_get(rm, 1) == 1 else "model"
            parts = _safe_nested_get(rm, 2) or []
            text_parts = []
            html_widgets = []
            
            for pt in parts:
                if isinstance(pt, str):
                    text_parts.append(pt)
                else:
                    _collect_html_widgets(pt, html_widgets)
                    
            messages.append({
                "role": role,
                "text": "\n".join(text_parts),
                "has_widgets": len(html_widgets) > 0,
                "widgets": html_widgets
            })
            
        return {
            "title": title,
            "created_at": created_at,
            "author": {"name": author, "avatar": author_avatar},
            "turn_count": len(messages),
            "messages": messages
        }
    except Exception as e:
        return {"error": str(e)}

def resolve_share_url_ids(url_or_id: str) -> dict[str, Any]:
    """REQ-002: Resolve share URL or bare ID into share_id, skid, and is_short_link."""
    val = url_or_id.strip()
    if _BARE_SHARE_ID_RE.match(val):
        return {"share_id": val, "skid": None, "is_short_link": False}
        
    parsed = urlparse(val)
    if parsed.netloc == "g.co" and parsed.path.startswith("/gemini/share/"):
        return {"share_id": parsed.path.split("/")[-1], "skid": None, "is_short_link": True}
        
    if "gemini.google.com/share/" in val:
        share_id = parsed.path.split("/")[-1]
        qs = parse_qs(parsed.query)
        skid = qs.get("skid", [None])[0]
        return {"share_id": share_id, "skid": skid, "is_short_link": False}
        
    return {"error": "Unrecognized share URL format"}

class ShareIngestRequest(BaseModel):
    url: str
    account_id: str | None = None
    rpc_payload: Any | None = None

class ISO9000BaselineRequest(BaseModel):
    baseline_yaml: str

@router.post("/share-ingest")
async def share_ingest_endpoint(req: ShareIngestRequest) -> dict[str, Any]:
    """REQ-003: Ingest a shared Gemini conversation link or pre-captured ujx1Bf payload."""
    ids = resolve_share_url_ids(req.url)
    if "error" in ids:
        raise HTTPException(status_code=400, detail=ids["error"])
        
    if req.rpc_payload:
        doc = parse_shared_conversation_payload(req.rpc_payload)
        if "error" in doc:
            raise HTTPException(status_code=400, detail=doc["error"])
            
        # In a real app we'd save to DB here
        doc_hash = hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:12]
        return {
            "ok": True,
            "share_id": ids["share_id"],
            "ingest_id": f"ing_{doc_hash}",
            "document": doc
        }
        
    return {
        "ok": True,
        "share_id": ids["share_id"],
        "status": "pending_fetch",
        "message": "Send rpc_payload to complete ingestion"
    }

@router.post("/iso9000-validate")
async def iso9000_validate_endpoint(req: ISO9000BaselineRequest) -> dict[str, Any]:
    """REQ-004: Validate an ISO 9001:2015 / Agile V Frozen Baseline YAML/JSON specification."""
    try:
        spec = yaml.safe_load(req.baseline_yaml)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}") from e
        
    if not isinstance(spec, dict):
        raise HTTPException(status_code=400, detail="Root must be a mapping")
        
    errors = []
    
    metadata = spec.get("metadata", {})
    if not metadata.get("version"):
        errors.append("Missing metadata.version")
    if not metadata.get("status") in ("FROZEN", "DRAFT", "OBSOLETE"):
        errors.append("metadata.status must be FROZEN, DRAFT, or OBSOLETE")
        
    requirements = spec.get("requirements", [])
    if not isinstance(requirements, list):
        errors.append("requirements must be a list")
    else:
        req_ids = set()
        for i, req_item in enumerate(requirements):
            rid = req_item.get("id", "")
            if not _REQ_ID_RE.match(rid):
                errors.append(f"Requirement at index {i} has invalid ID format: {rid}")
            if rid in req_ids:
                errors.append(f"Duplicate requirement ID: {rid}")
            req_ids.add(rid)
            
            if not req_item.get("description"):
                errors.append(f"Requirement {rid} missing description")
                
            trace = req_item.get("traceability", {})
            if not trace.get("source"):
                errors.append(f"Requirement {rid} missing traceability.source")
                
    if errors:
        return {"ok": False, "is_valid": False, "errors": errors}
        
    return {
        "ok": True,
        "is_valid": True,
        "baseline_version": metadata.get("version"),
        "req_count": len(requirements)
    }
