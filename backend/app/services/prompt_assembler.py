"""
backend/app/services/prompt_assembler.py
Decomposes user input, workspace skills, global personas, system instructions,
and active MCP connector tools into wire prompt and clean storage representation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any

logger = logging.getLogger("gemini_backend.prompt_assembler")


@dataclass
class AssembledPrompt:
    wire_prompt: str
    clean_storage_text: str
    clean_user_prompt: str
    skill_injections: list[str]
    bridge_attachments: list[dict[str, Any]] | None = None
    active_tools: list[dict[str, Any]] = field(default_factory=list)
    rag_sources: list[dict[str, Any]] = field(default_factory=list)


def normalize_bridge_attachments(
    attachments: list[Any] | None,
    base_url: str | None = None,
) -> list[dict[str, Any]] | None:
    """Normalize input attachments into Gemini bridge payload format."""
    if not attachments:
        return None
    from backend.app.bridge.uploader import GeminiUploader

    is_live_upload = "content-push.googleapis.com" in GeminiUploader(base_url=base_url).upload_url
    results: list[dict[str, Any]] = []
    for att in attachments:
        uri = getattr(att, "uri", None) if not isinstance(att, dict) else att.get("uri")
        if not uri:
            continue
        filename = getattr(att, "filename", None) if not isinstance(att, dict) else att.get("filename")
        mime = (getattr(att, "mime_type", None) if not isinstance(att, dict) else att.get("mime_type")) or ""
        results.append({
            "uri": uri,
            "filename": filename,
            "mime_type": mime,
            "scotty": is_live_upload and mime.startswith("image/"),
        })
    return results if results else None


async def resolve_prior_context_ids(
    repo: Any,
    conv_id: str,
    parent_message_id: int | str | None,
    current_acc_id: str,
) -> tuple[str | None, str | None, str | None]:
    """Resolve prior (cid, rid, rcid) for multi-turn conversation continuation."""
    if not repo:
        return None, None, None
    try:
        candidate_msg = None
        if parent_message_id is not None and str(parent_message_id).isdigit():
            parent_msg = await repo.get_message(int(parent_message_id))
            if (
                parent_msg
                and parent_msg.role == "assistant"
                and isinstance(parent_msg.metadata, dict)
                and parent_msg.metadata.get("cid")
            ):
                candidate_msg = parent_msg

        if candidate_msg is None:
            thread_msgs = await repo.get_active_thread(conv_id)
            for msg in reversed(thread_msgs):
                if (
                    msg.role == "assistant"
                    and isinstance(msg.metadata, dict)
                    and msg.metadata.get("cid")
                ):
                    candidate_msg = msg
                    break

        if candidate_msg is None:
            all_msgs = await repo.get_all_messages(conv_id)
            for msg in reversed(all_msgs):
                if (
                    msg.role == "assistant"
                    and isinstance(msg.metadata, dict)
                    and msg.metadata.get("cid")
                ):
                    candidate_msg = msg
                    break

        if candidate_msg is not None:
            meta = candidate_msg.metadata or {}
            prev_acc = meta.get("fulfilled_by_account_id")
            if not prev_acc or prev_acc == current_acc_id:
                eff_cid = str(meta["cid"])
                eff_rid = str(meta["rid"]) if meta.get("rid") else None
                eff_rcid = str(meta["rcid"]) if meta.get("rcid") else None
                return eff_cid, eff_rid, eff_rcid
    except Exception as ctx_err:
        logger.debug("Failed resolving prior (cid, rid, rcid) for %s: %s", conv_id, ctx_err)
    return None, None, None


async def assemble_prompt(
    req: Any = None,
    *,
    prompt: str | None = None,
    skill_ids: list[str] | None = None,
    use_global_prompt: bool | None = None,
    system_instruction: str | None = None,
    gem_id: str | None = None,
    connector_ids: list[str] | None = None,
    attachments: list[Any] | None = None,
    existing_msg: Any = None,
    workspace_store: Any = None,
    mcp_manager: Any = None,
    rag_service: Any = None,
    base_url: str | None = None,
) -> AssembledPrompt:
    """Decompose prompt, skills, persona, and MCP tools with full parity with legacy chat.py."""
    if workspace_store is None:
        from backend.app.api.workspace import get_workspace_store
        store = get_workspace_store()
    else:
        store = workspace_store

    if mcp_manager is None:
        from backend.app.api.workspace import get_mcp_manager
        mcp_mgr = get_mcp_manager()
    else:
        mcp_mgr = mcp_manager

    raw_user_prompt = prompt if prompt is not None else (getattr(req, "prompt", "") if req else "")
    if existing_msg and not raw_user_prompt.strip():
        raw_user_prompt = getattr(existing_msg, "content", "") or ""

    atts = attachments if attachments is not None else (getattr(req, "attachments", None) if req else None)
    bridge_attachments = normalize_bridge_attachments(atts, base_url=base_url)
    if not bridge_attachments and existing_msg and getattr(existing_msg, "attachments", None):
        bridge_attachments = normalize_bridge_attachments(existing_msg.attachments, base_url=base_url)

    clean_user_prompt = raw_user_prompt
    skill_injections: list[str] = []

    m_slash = re.match(r"^/([a-zA-Z0-9_-]+)(?:\s+(.*))?$", clean_user_prompt, re.DOTALL)
    if m_slash:
        slug, rem = m_slash.group(1), m_slash.group(2) or ""
        for s in store.list_skills():
            triggers = getattr(s, "triggers", []) or []
            if s.id == slug or f"/{slug}" in triggers:
                clean_user_prompt = rem.strip()
                skill_injections.append(f"[Skill: {s.name}]\n{s.content}\n[End Skill]")
                break

    eff_skill_ids = skill_ids if skill_ids is not None else (getattr(req, "skill_ids", None) if req else None)
    if eff_skill_ids is not None:
        for sid in eff_skill_ids:
            for s in store.list_skills():
                if s.id == sid:
                    skill_injections.append(f"[Skill: {s.name}]\n{s.content}\n[End Skill]")
                    break
    else:
        for s in store.list_skills():
            if getattr(s, "is_active", False):
                skill_injections.append(f"[Skill: {s.name}]\n{s.content}\n[End Skill]")

    eff_use_global = use_global_prompt if use_global_prompt is not None else (getattr(req, "use_global_prompt", False) if req else False)
    global_prompt_str = ""
    if eff_use_global:
        active_p = store.get_active_prompt()
        if active_p:
            global_prompt_str = f"[Global Persona: {active_p.name}]\n{active_p.content}\n[End Global Persona]"

    eff_system_instruction = system_instruction if system_instruction is not None else (getattr(req, "system_instruction", None) if req else None)
    eff_gem_id = gem_id if gem_id is not None else (getattr(req, "gem_id", None) if req else None)

    prompt_parts: list[str] = []
    if global_prompt_str:
        prompt_parts.append(global_prompt_str)
    if skill_injections:
        prompt_parts.extend(skill_injections)
    if eff_system_instruction and not eff_gem_id:
        prompt_parts.append(
            f"[System Instructions]\n"
            f"{eff_system_instruction.strip()}\n"
            f"[End System Instructions]"
        )
    prompt_parts.append(clean_user_prompt)
    effective_prompt = "\n\n".join(p for p in prompt_parts if p)

    saved_user_prompt = clean_user_prompt
    if eff_system_instruction and not eff_gem_id:
        saved_user_prompt = (
            f"[System Instructions]\n"
            f"{eff_system_instruction.strip()}\n"
            f"[End System Instructions]\n\n"
            f"{clean_user_prompt}"
        )

    active_tools: list[dict[str, Any]] = []
    eff_conn_ids = connector_ids if connector_ids is not None else (getattr(req, "connector_ids", None) if req else None)
    if eff_conn_ids is None:
        enabled_conns = [c.id for c in store.list_connectors(mask=False) if getattr(c, "enabled", False)]
        if enabled_conns:
            eff_conn_ids = enabled_conns
    if eff_conn_ids and mcp_mgr:
        try:
            active_tools = await mcp_mgr.list_active_tools(connector_ids=eff_conn_ids)
            if active_tools:
                from backend.app.mcp.client_manager import MCPConnectorManager
                tools_prompt = MCPConnectorManager.build_connector_tools_prompt(active_tools)
                effective_prompt = f"{effective_prompt}\n\n{tools_prompt}"
        except Exception as e:
            logger.debug("Failed resolving active MCP tools: %s", e)

    rag_sources: list[dict[str, Any]] = []
    if req and getattr(req, "use_rag", False) and clean_user_prompt.strip():
        try:
            svc = rag_service
            if svc is None:
                from backend.app.api.rag import get_rag_service
                svc = get_rag_service()
            rag_k = int(getattr(req, "rag_top_k", 5) or 5)
            rag_res = await svc.search(clean_user_prompt, top_k=rag_k, mode="hybrid")
            xml_ctx = rag_res.get("formatted_xml_context") or ""
            if xml_ctx:
                effective_prompt = f"{xml_ctx}\n\n{effective_prompt}"
                rag_sources = [
                    {
                        "chunk_id": h.get("chunk_id"),
                        "source_type": h.get("source_type"),
                        "source_id": h.get("source_id"),
                        "source_title": h.get("source_title"),
                        "score": h.get("score"),
                    }
                    for h in rag_res.get("results", [])
                ]
        except Exception as rag_err:
            logger.debug("RAG augmentation skipped: %s", rag_err)

    return AssembledPrompt(
        wire_prompt=effective_prompt,
        clean_storage_text=saved_user_prompt,
        clean_user_prompt=clean_user_prompt,
        skill_injections=skill_injections,
        bridge_attachments=bridge_attachments,
        active_tools=active_tools,
        rag_sources=rag_sources,
    )


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def assemble_turn_prompts(
    *,
    raw_prompt: str = "",
    system_instruction: str | None = None,
    use_global_prompt: bool | None = False,
    gem_id: str | None = None,
    skill_ids: list[str] | None = None,
    workspace_config: Any = None,
    active_tools: list[dict[str, Any]] | None = None,
    attachments: list[Any] | None = None,
    base_url: str | None = None,
) -> AssembledPrompt:
    """Synchronously assemble wire_prompt and clean_storage_text from pre-resolved tools and config."""
    if workspace_config is None:
        from backend.app.api.workspace import get_workspace_store
        store = get_workspace_store()
    else:
        store = workspace_config

    clean_user_prompt = raw_prompt
    skill_injections: list[str] = []
    skills = list(store.list_skills()) if hasattr(store, "list_skills") else []

    m_slash = re.match(r"^/([a-zA-Z0-9_-]+)(?:\s+(.*))?$", clean_user_prompt, re.DOTALL)
    if m_slash:
        slug, rem = m_slash.group(1), m_slash.group(2) or ""
        for s in skills:
            s_id = _get_attr(s, "id", "")
            triggers = _get_attr(s, "triggers", []) or []
            if s_id == slug or f"/{slug}" in triggers:
                clean_user_prompt = rem.strip()
                s_name = _get_attr(s, "name") or s_id
                s_content = _get_attr(s, "content", "")
                skill_injections.append(f"[Skill: {s_name}]\n{s_content}\n[End Skill]")
                break

    if skill_ids is not None:
        for sid in skill_ids:
            for s in skills:
                if _get_attr(s, "id") == sid:
                    s_name = _get_attr(s, "name") or sid
                    skill_injections.append(f"[Skill: {s_name}]\n{_get_attr(s, 'content', '')}\n[End Skill]")
                    break
    else:
        for s in skills:
            if _get_attr(s, "is_active", False):
                s_name = _get_attr(s, "name") or _get_attr(s, "id", "")
                skill_injections.append(f"[Skill: {s_name}]\n{_get_attr(s, 'content', '')}\n[End Skill]")

    global_prompt_str = ""
    if use_global_prompt and hasattr(store, "get_active_prompt"):
        active_p = store.get_active_prompt()
        if active_p:
            p_name = _get_attr(active_p, "name") or "Default"
            p_content = _get_attr(active_p, "content", "")
            if p_content:
                global_prompt_str = f"[Global Persona: {p_name}]\n{p_content}\n[End Global Persona]"

    parts: list[str] = []
    if global_prompt_str:
        parts.append(global_prompt_str)
    if skill_injections:
        parts.extend(skill_injections)
    if system_instruction and not gem_id:
        parts.append(f"[System Instructions]\n{system_instruction.strip()}\n[End System Instructions]")
    parts.append(clean_user_prompt)
    wire_prompt = "\n\n".join(p for p in parts if p)

    clean_storage_text = clean_user_prompt
    if system_instruction and not gem_id:
        clean_storage_text = (
            f"[System Instructions]\n{system_instruction.strip()}\n[End System Instructions]\n\n{clean_user_prompt}"
        )

    tools_list = list(active_tools or [])
    if tools_list:
        from backend.app.mcp.client_manager import MCPConnectorManager
        tools_prompt = MCPConnectorManager.build_connector_tools_prompt(tools_list)
        wire_prompt = f"{wire_prompt}\n\n{tools_prompt}"

    return AssembledPrompt(
        wire_prompt=wire_prompt,
        clean_storage_text=clean_storage_text,
        clean_user_prompt=clean_user_prompt,
        skill_injections=skill_injections,
        bridge_attachments=normalize_bridge_attachments(attachments, base_url=base_url),
        active_tools=tools_list,
    )
