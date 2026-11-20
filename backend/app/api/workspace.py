"""
backend/app/api/workspace.py
Workspace Customization REST API (Prompts, Skills, MCP Connectors).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.mcp.client_manager import MCPConnectorManager
from backend.app.storage.workspace_config import WorkspaceConfigStore

logger = logging.getLogger("gemini_workspace.api")
router = APIRouter(prefix="/api/workspace", tags=["workspace"])

_store_instance: Optional[WorkspaceConfigStore] = None
_mcp_manager_instance: Optional[MCPConnectorManager] = None


def get_workspace_store() -> WorkspaceConfigStore:
    global _store_instance
    if _store_instance is None:
        _store_instance = WorkspaceConfigStore()
    return _store_instance


def get_mcp_manager() -> MCPConnectorManager:
    global _mcp_manager_instance
    if _mcp_manager_instance is None:
        _mcp_manager_instance = MCPConnectorManager(get_workspace_store())
    return _mcp_manager_instance


class PromptCreateRequest(BaseModel):
    name: str
    description: str = ""
    content: str
    apply_mode: str = "turn0"


class PromptActivateRequest(BaseModel):
    prompt_id: Optional[str] = None
    enabled: bool = True


class SkillCreateRequest(BaseModel):
    name: str
    description: str = ""
    content: Optional[str] = None
    body: Optional[str] = None
    id: Optional[str] = None
    slug: Optional[str] = None
    compatibility: str = "native"
    triggers: List[str] = Field(default_factory=list)


class SkillsActiveRequest(BaseModel):
    enabled_skill_ids: List[str] = Field(default_factory=list)


class ToolCallRequest(BaseModel):
    namespaced_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


@router.get("/prompts")
async def list_prompts():
    return {"prompts": [p.model_dump() for p in get_workspace_store().list_prompts()]}


@router.post("/prompts")
async def create_prompt(req: PromptCreateRequest):
    item = get_workspace_store().create_prompt(req.model_dump())
    return item.model_dump()


@router.put("/prompts/{prompt_id}")
async def update_prompt(prompt_id: str, data: Dict[str, Any]):
    item = get_workspace_store().update_prompt(prompt_id, data)
    if not item:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return item.model_dump()


@router.delete("/prompts/{prompt_id}")
async def delete_prompt(prompt_id: str):
    if not get_workspace_store().delete_prompt(prompt_id):
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"status": "deleted"}


@router.post("/prompts/activate")
async def activate_prompt(req: PromptActivateRequest):
    pid = req.prompt_id if req.enabled else None
    get_workspace_store().activate_prompt(pid, req.enabled)
    return {"ok": True, "status": "ok", "active_prompt_id": pid}


@router.get("/skills")
async def list_skills():
    return {"skills": [s.model_dump() for s in get_workspace_store().list_skills()]}


@router.post("/skills/active")
async def set_active_skills(req: SkillsActiveRequest):
    updated = get_workspace_store().set_enabled_skills(req.enabled_skill_ids)
    return {"ok": True, "enabled_skill_ids": updated}


@router.post("/skills")
async def create_skill(req: SkillCreateRequest):
    data = req.model_dump()
    data["content"] = req.content or req.body or ""
    if req.slug and not req.id:
        data["id"] = req.slug
    item = get_workspace_store().create_custom_skill(data)
    return item.model_dump()


@router.put("/skills/{skill_slug}")
async def update_skill(skill_slug: str, data: Dict[str, Any]):
    item = get_workspace_store().update_custom_skill(skill_slug, data)
    if not item:
        raise HTTPException(status_code=404, detail="Skill not found")
    return item.model_dump()


@router.delete("/skills/{skill_slug}")
async def delete_skill(skill_slug: str):
    if not get_workspace_store().delete_custom_skill(skill_slug):
        raise HTTPException(status_code=404, detail="Skill not found")
    return {"status": "deleted"}


def _enrich_connector_with_pin(conn_dict: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from backend.app.security.mcp_pinning import get_schema_pin_store
        pin_store = get_schema_pin_store()
        cid = conn_dict.get("id", "")
        pinned = pin_store._pins.get(cid)
        if pinned:
            conn_dict["pin_status"] = "pinned"
            conn_dict["pin_sha256"] = pinned.get("sha256")
            conn_dict["pin_changed_tools"] = []
        else:
            conn_dict["pin_status"] = "unpinned"
            conn_dict["pin_sha256"] = None
            conn_dict["pin_changed_tools"] = []
    except Exception:
        pass
    return conn_dict


@router.get("/connectors")
async def list_connectors():
    return {"connectors": [_enrich_connector_with_pin(c.model_dump()) for c in get_workspace_store().list_connectors(mask=True)]}


@router.get("/connectors/templates")
async def list_connector_templates():
    return {"templates": get_workspace_store().get_templates()}


@router.post("/connectors")
async def create_connector(data: Dict[str, Any]):
    item = get_workspace_store().create_connector(data)
    return item.model_dump()


@router.get("/connectors/{connector_id}")
async def get_connector(connector_id: str):
    conn = get_workspace_store().get_connector(connector_id, mask=True)
    if not conn:
        raise HTTPException(status_code=404, detail="Connector not found")
    return _enrich_connector_with_pin(conn.model_dump())


@router.put("/connectors/{connector_id}")
async def update_connector(connector_id: str, data: Dict[str, Any]):
    conn = get_workspace_store().update_connector(connector_id, data)
    if not conn:
        raise HTTPException(status_code=404, detail="Connector not found")
    return _enrich_connector_with_pin(conn.model_dump())


@router.delete("/connectors/{connector_id}")
async def delete_connector(connector_id: str):
    if not get_workspace_store().delete_connector(connector_id):
        raise HTTPException(status_code=404, detail="Connector not found")
    return {"status": "deleted"}


@router.post("/connectors/{connector_id}/test")
async def test_connector(connector_id: str):
    try:
        return await get_mcp_manager().test_connector(connector_id)
    except Exception as e:
        logger.error("Connector test failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/connectors/{connector_id}/repin")
async def repin_connector(connector_id: str):
    try:
        from backend.app.security.mcp_pinning import get_schema_pin_store
        test_res = await get_mcp_manager().test_connector(connector_id)
        tools = test_res.get("tools", [])
        new_sha = get_schema_pin_store().repin(connector_id, tools)
        return {
            "ok": True,
            "connector_id": connector_id,
            "pin_status": "verified",
            "pin_sha256": new_sha,
            "pin_changed_tools": [],
        }
    except Exception as e:
        logger.error("Connector repin failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/connectors/{connector_id}/call")
async def call_connector_tool(connector_id: str, req: ToolCallRequest):
    try:
        return await get_mcp_manager().call_tool(req.namespaced_name, req.arguments)
    except Exception as e:
        logger.error("Tool execution failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/config")
async def get_full_workspace_config():
    store = get_workspace_store()
    return {
        "prompts": [p.model_dump() for p in store.list_prompts()],
        "skills": [s.model_dump() for s in store.list_skills()],
        "connectors": [_enrich_connector_with_pin(c.model_dump()) for c in store.list_connectors(mask=True)],
        "templates": store.get_templates(),
    }
