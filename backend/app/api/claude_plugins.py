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
from typing import Deque, Dict, Any, List, Optional

from backend.app.api.gems import _LOCAL_CUSTOM_GEMS, Gem

router = APIRouter(prefix="/api/claude-plugins", tags=["claude-plugins"])

SAFE_SLUG_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SWARM_EVENTS_PATH = _REPO_ROOT / "backend" / "data" / "swarm_events.jsonl"
_SWARM_EVENTS: Deque[Dict[str, Any]] = deque(maxlen=100)
_SWARM_EVENTS_LOADED: bool = False

MODEL_TIER_LABELS: Dict[str, str] = {
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
    subagent: Optional[str],
    gem_id: Optional[str],
    cache_hit: bool,
    matched_turns: int,
    total_turns: int,
    tool_calls: List[str],
    latency_ms: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    stream: bool = True,
) -> Dict[str, Any]:
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

def scan_plugins(folder_name: str) -> List[dict]:
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
    id: Optional[str] = None
    name: str
    description: str = ""
    prompt: Optional[str] = None
    system_prompt: Optional[str] = None
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


def _get_conversation_cache_stats() -> Dict[str, Any]:
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


def _scan_recent_claude_sessions(limit: int = 8) -> List[Dict[str, Any]]:
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return []

    jsonl_files: List[Path] = []
    for proj_dir in claude_projects.iterdir():
        if proj_dir.is_dir():
            jsonl_files.extend(proj_dir.glob("*.jsonl"))

    jsonl_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    sessions: List[Dict[str, Any]] = []

    for jf in jsonl_files[:limit]:
        try:
            stat = jf.stat()
            lines = jf.read_text(encoding="utf-8", errors="replace").splitlines()
            tool_counts: Dict[str, int] = {}
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
async def get_swarm_telemetry(limit: int = 30) -> Dict[str, Any]:
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

    tier_distribution: Dict[str, int] = {}
    subagent_distribution: Dict[str, int] = {}
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
            "agents": agents,
            "commands": commands,
            "skills": skills,
            "hooks": hooks_list,
        },
        "tier_distribution": tier_distribution,
        "subagent_distribution": subagent_distribution,
        "live_events": events_list,
        "recent_sessions": _scan_recent_claude_sessions(limit=8),
        "background_jobs": _list_persisted_background_jobs(limit=10),
    }


_BG_JOBS_FILE = _REPO_ROOT / "backend" / "data" / "background_jobs.json"
_BG_JOBS_MEM: Dict[str, Dict[str, Any]] = {}


def _load_persisted_background_jobs() -> Dict[str, Dict[str, Any]]:
    global _BG_JOBS_MEM
    if _BG_JOBS_FILE.exists():
        try:
            raw = json.loads(_BG_JOBS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k not in _BG_JOBS_MEM and isinstance(v, dict):
                        _BG_JOBS_MEM[k] = v
        except Exception:
            pass
    return _BG_JOBS_MEM


def _save_persisted_background_jobs() -> None:
    try:
        _BG_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BG_JOBS_FILE.write_text(
            json.dumps(_BG_JOBS_MEM, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _list_persisted_background_jobs(limit: int = 20) -> List[Dict[str, Any]]:
    _load_persisted_background_jobs()
    items = list(_BG_JOBS_MEM.values())
    items.sort(key=lambda x: float(x.get("created_at", 0.0)), reverse=True)
    return items[: max(1, min(limit, 100))]


class BackgroundStageSpec(BaseModel):
    name: str
    prompt: str
    subagent: str = "code-architect"
    model: str = "opus"
    output_file: Optional[str] = None


class BackgroundJobRequest(BaseModel):
    title: str
    workspace_dir: Optional[str] = None
    stages: List[BackgroundStageSpec] = []
    script_command: Optional[str] = None
    artifacts: List[str] = []
    run_async: bool = True


async def _execute_background_job(job_id: str) -> Dict[str, Any]:
    import asyncio
    from backend.app.bridge.client import GeminiWebClient

    _load_persisted_background_jobs()
    job = _BG_JOBS_MEM.get(job_id)
    if not job:
        raise ValueError(f"Background job '{job_id}' not found")

    job["status"] = "running"
    job["started_at"] = time.time()
    _save_persisted_background_jobs()

    agents = scan_plugins("agents")
    agent_map = {str(a.get("name", "")): a for a in agents}
    stage_results: List[Dict[str, Any]] = []

    try:
        for idx, st in enumerate(job.get("stages", []), 1):
            st_name = str(st.get("name", f"stage-{idx}"))
            st_subagent = str(st.get("subagent", "code-architect"))
            st_model_alias = str(st.get("model", "opus")).lower()
            st_prompt = str(st.get("prompt", ""))
            st_output_file = st.get("output_file")

            ag_info = agent_map.get(st_subagent, {})
            canonical_model = SUBAGENT_TIER_MAP.get(st_model_alias, "95a4c0abcba24364")
            feature_mode = ag_info.get("feature_mode")
            if feature_mode is None and st_model_alias in ("opus", "sonnet"):
                feature_mode = 40 if st_subagent in ("code-architect", "argon-architect") else None
            gem_id = f"gem_cc_{st_subagent.replace(' ', '_').lower()}"

            t0 = time.time()
            synthesized_text = ""
            try:
                client = GeminiWebClient()
                resp = await client.generate_content(
                    prompt=st_prompt,
                    model=canonical_model,
                    gem_id=gem_id,
                    feature_mode=int(feature_mode) if feature_mode is not None else None,
                )
                synthesized_text = getattr(resp, "text", "") or ""
            except Exception as gen_err:
                synthesized_text = (
                    f"[Background Synthesis Stage '{st_name}' via {st_subagent} ({canonical_model})]\n"
                    f"Prompt Summary: {st_prompt[:300]}\n"
                    f"Status: Synthesized locally ({gen_err})"
                )

            if st_output_file:
                out_path = Path(st_output_file).expanduser().resolve()
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(synthesized_text, encoding="utf-8")

            elapsed_ms = max(1, int(round((time.time() - t0) * 1000)))
            record_swarm_event(
                message_id=f"{job_id}_s{idx}",
                requested_model=st_model_alias,
                resolved_model=canonical_model,
                subagent=st_subagent,
                gem_id=gem_id,
                cache_hit=False,
                matched_turns=0,
                total_turns=idx,
                tool_calls=["gemini_background_batch"],
                latency_ms=elapsed_ms,
                input_tokens=max(1, len(st_prompt) // 4),
                output_tokens=max(1, len(synthesized_text) // 4),
                stream=False,
            )

            stage_results.append({
                "stage_index": idx,
                "name": st_name,
                "subagent": st_subagent,
                "resolved_model": canonical_model,
                "feature_mode": feature_mode,
                "elapsed_sec": round(time.time() - t0, 2),
                "output_chars": len(synthesized_text),
                "output_file": str(st_output_file) if st_output_file else None,
                "preview": synthesized_text[:500],
            })
            job["stage_results"] = stage_results
            _save_persisted_background_jobs()

        script_cmd = job.get("script_command")
        if script_cmd:
            cwd = job.get("workspace_dir") or str(_REPO_ROOT)
            proc = await asyncio.create_subprocess_shell(
                script_cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await proc.communicate()
            job["script_exit_code"] = proc.returncode
            job["script_stdout"] = stdout_b.decode("utf-8", errors="replace")[-4000:]
            job["script_stderr"] = stderr_b.decode("utf-8", errors="replace")[-2000:]

        job["status"] = "completed"
        job["completed_at"] = time.time()
        job["elapsed_sec"] = round(job["completed_at"] - float(job.get("started_at", job["completed_at"])), 2)
        _save_persisted_background_jobs()
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
        job["completed_at"] = time.time()
        _save_persisted_background_jobs()
    return job


@router.post("/background-jobs")
async def create_background_job(req: BackgroundJobRequest) -> Dict[str, Any]:
    import asyncio
    import uuid

    if not req.title or not req.title.strip():
        raise HTTPException(status_code=400, detail="Job title is required")

    _load_persisted_background_jobs()
    job_id = f"bg_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    job_record: Dict[str, Any] = {
        "job_id": job_id,
        "title": req.title.strip(),
        "workspace_dir": req.workspace_dir,
        "status": "queued",
        "created_at": time.time(),
        "started_at": None,
        "completed_at": None,
        "elapsed_sec": 0.0,
        "stages": [s.model_dump() for s in req.stages],
        "stage_results": [],
        "script_command": req.script_command,
        "artifacts": list(req.artifacts),
    }
    _BG_JOBS_MEM[job_id] = job_record
    _save_persisted_background_jobs()

    if req.run_async:
        asyncio.create_task(_execute_background_job(job_id))
        return {"ok": True, "job_id": job_id, "status": "queued", "job": job_record}
    else:
        completed_job = await _execute_background_job(job_id)
        return {"ok": True, "job_id": job_id, "status": completed_job.get("status", "completed"), "job": completed_job}


@router.get("/background-jobs")
async def list_background_jobs_endpoint(limit: int = 20) -> Dict[str, Any]:
    jobs = _list_persisted_background_jobs(limit=limit)
    return {"ok": True, "count": len(jobs), "jobs": jobs}


@router.get("/background-jobs/{job_id}")
async def get_background_job_endpoint(job_id: str) -> Dict[str, Any]:
    _load_persisted_background_jobs()
    job = _BG_JOBS_MEM.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Background job '{job_id}' not found")
    return {"ok": True, "job": job}



class WorkspaceInitRequest(BaseModel):
    target_dir: str
    include_mcp_config: bool = True
    overwrite: bool = True
    gateway_url: str = "http://127.0.0.1:8000"


def init_claude_workspace(
    target_dir: str,
    include_mcp_config: bool = True,
    overwrite: bool = True,
    gateway_url: str = "http://127.0.0.1:8000",
) -> Dict[str, Any]:
    """
    Exports and installs the complete `.claude/{agents,skills,commands,hooks,settings.json}`
    and `.mcp.json` pack into `target_dir` in one call.
    """
    if not target_dir or not str(target_dir).strip():
        raise ValueError("target_dir is required")

    dest_root = Path(target_dir).expanduser().resolve()
    if dest_root.exists() and not dest_root.is_dir():
        raise ValueError(f"target_dir exists and is not a directory: {dest_root}")
    dest_root.mkdir(parents=True, exist_ok=True)
    dest_claude = dest_root / ".claude"
    dest_claude.mkdir(parents=True, exist_ok=True)

    src_claude = _REPO_ROOT / ".claude"
    if not src_claude.exists():
        src_claude = Path.cwd() / ".claude"

    installed_files: List[str] = []
    counts = {"agents": 0, "skills": 0, "commands": 0, "hooks": 0, "configs": 0}

    for sub in ("agents", "commands", "hooks"):
        src_sub = src_claude / sub
        dest_sub = dest_claude / sub
        dest_sub.mkdir(parents=True, exist_ok=True)
        if src_sub.exists():
            for item in sorted(src_sub.iterdir()):
                if item.is_file():
                    dest_file = dest_sub / item.name
                    if overwrite or not dest_file.exists():
                        if dest_file.resolve() != item.resolve():
                            shutil.copy2(item, dest_file)
                        installed_files.append(str(dest_file.relative_to(dest_root)).replace("\\", "/"))
                        counts[sub] += 1

    # Copy skills/*/*
    src_skills = src_claude / "skills"
    dest_skills = dest_claude / "skills"
    dest_skills.mkdir(parents=True, exist_ok=True)
    if src_skills.exists():
        for skill_dir in sorted(src_skills.iterdir()):
            if skill_dir.is_dir():
                dest_skill_dir = dest_skills / skill_dir.name
                dest_skill_dir.mkdir(parents=True, exist_ok=True)
                for sfile in sorted(skill_dir.rglob("*")):
                    if sfile.is_file():
                        rel = sfile.relative_to(skill_dir)
                        target_sfile = dest_skill_dir / rel
                        target_sfile.parent.mkdir(parents=True, exist_ok=True)
                        if overwrite or not target_sfile.exists():
                            if target_sfile.resolve() != sfile.resolve():
                                shutil.copy2(sfile, target_sfile)
                            installed_files.append(str(target_sfile.relative_to(dest_root)).replace("\\", "/"))
                counts["skills"] += 1

    # Write adapted .claude/settings.json pointing to target_dir's hook script and gateway_url
    hook_script_path = (dest_claude / "hooks" / "notebooklm_post_tool_sync.py").resolve()
    python_exe = "C:/Python314/python.exe" if Path("C:/Python314/python.exe").exists() else "python"
    hook_cmd = f'"{python_exe}" "{str(hook_script_path).replace(chr(92), "/")}"'

    settings_payload: Dict[str, Any] = {
        "env": {
            "ANTHROPIC_BASE_URL": gateway_url.rstrip("/"),
            "ANTHROPIC_AUTH_TOKEN": "gemini-local-bridge",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Write|Edit",
                    "hooks": [
                        {
                            "type": "command",
                            "command": hook_cmd,
                            "timeout": 10,
                        }
                    ],
                }
            ]
        },
    }
    settings_path = dest_claude / "settings.json"
    existing_settings: Dict[str, Any] = {}
    if settings_path.exists():
        try:
            existing_settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            existing_settings = {}
    if not isinstance(existing_settings, dict):
        existing_settings = {}

    existing_env = existing_settings.setdefault("env", {})
    if isinstance(existing_env, dict):
        for k, v in settings_payload["env"].items():
            if overwrite or k not in existing_env:
                existing_env[k] = v

    existing_hooks = existing_settings.setdefault("hooks", {})
    if isinstance(existing_hooks, dict):
        post_tool_use = existing_hooks.setdefault("PostToolUse", [])
        if isinstance(post_tool_use, list):
            has_hook = any(
                isinstance(h, dict) and any(
                    isinstance(hk, dict) and "notebooklm_post_tool_sync" in str(hk.get("command", ""))
                    for hk in h.get("hooks", [])
                )
                for h in post_tool_use
            )
            if not has_hook:
                post_tool_use.extend(settings_payload["hooks"]["PostToolUse"])

    settings_path.write_text(json.dumps(existing_settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    installed_files.append(".claude/settings.json")
    counts["configs"] += 1

    if include_mcp_config:
        mcp_path = dest_root / ".mcp.json"
        existing_mcp: Dict[str, Any] = {}
        if mcp_path.exists():
            try:
                existing_mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
            except Exception:
                existing_mcp = {}
        if not isinstance(existing_mcp, dict):
            existing_mcp = {}
        servers = existing_mcp.setdefault("mcpServers", {})
        if isinstance(servers, dict):
            if overwrite or "gemini-local" not in servers:
                servers["gemini-local"] = {
                    "type": "http",
                    "url": f"{gateway_url.rstrip('/')}/mcp",
                }
        mcp_path.write_text(json.dumps(existing_mcp, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        installed_files.append(".mcp.json")
        counts["configs"] += 1

    return {
        "ok": True,
        "target_dir": str(dest_root),
        "gateway_url": gateway_url.rstrip("/"),
        "counts": counts,
        "installed_files": installed_files,
    }


@router.post("/init-workspace")
async def init_workspace_endpoint(req: WorkspaceInitRequest) -> Dict[str, Any]:
    try:
        return init_claude_workspace(
            target_dir=req.target_dir,
            include_mcp_config=req.include_mcp_config,
            overwrite=req.overwrite,
            gateway_url=req.gateway_url,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except (OSError, RuntimeError) as oe:
        raise HTTPException(status_code=400, detail=f"Invalid target directory: {oe}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed initializing workspace: {e}")

