"""
backend/app/api/gemini_capabilities.py
FastAPI router for M31-M34 Native Gemini Capabilities (`/api/v1/capabilities/*`).
Implements REQ-CAP-01 through REQ-CAP-07.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.bridge.gemini_capabilities import (
    get_canvas_manager,
    get_cloud_schedule_manager,
    get_deep_research_engine,
    get_response_quality_engine,
    get_saved_info_manager,
    run_cloud_python_sandbox,
)

router = APIRouter(prefix="/api/v1/capabilities", tags=["capabilities"])


class SandboxRunRequest(BaseModel):
    code: str
    prompt: Optional[str] = None
    cid: Optional[str] = None
    rid: Optional[str] = None
    rcid: Optional[str] = None


class CanvasCreateRequest(BaseModel):
    filename: str
    content: str
    cid: Optional[str] = None
    rid: Optional[str] = None
    rcid: Optional[str] = None
    language: str = "python"


class CanvasEditRangeRequest(BaseModel):
    doc_id: str
    start_line: int
    end_line: int
    replacement_content: str
    instruction: Optional[str] = None


class DeepResearchRequest(BaseModel):
    topic: str
    auto_approve: bool = True


class SavedInfoAddRequest(BaseModel):
    content: str
    category: str = "engineering_rule"


class SavedInfoSyncFileRequest(BaseModel):
    file_path: str
    category: str = "engineering_rule"


class CloudScheduleCreateRequest(BaseModel):
    name: str
    prompt: str
    cron_expr: str
    timezone: str = "UTC"


class CloudSchedulePauseRequest(BaseModel):
    paused: bool = True


class DoubleCheckRequest(BaseModel):
    text: str
    cid: Optional[str] = None
    rid: Optional[str] = None
    rcid: Optional[str] = None


class DraftSelectRequest(BaseModel):
    draft_candidates: List[Dict[str, Any]]
    criterion: str = "completeness"


class ModifyResponseRequest(BaseModel):
    text: str
    modifier: str = "concise"
    cid: Optional[str] = None
    rid: Optional[str] = None
    rcid: Optional[str] = None


@router.post("/sandbox/run")
async def sandbox_run_endpoint(req: SandboxRunRequest) -> Dict[str, Any]:
    """REQ-CAP-01: Executes code in Cloud Python Sandbox."""
    return await run_cloud_python_sandbox(
        code=req.code,
        prompt=req.prompt,
        cid=req.cid,
        rid=req.rid,
        rcid=req.rcid,
    )


@router.post("/canvas/create")
async def canvas_create_endpoint(req: CanvasCreateRequest) -> Dict[str, Any]:
    """REQ-CAP-02: Creates a new Canvas document."""
    return get_canvas_manager().create_document(
        filename=req.filename,
        content=req.content,
        cid=req.cid,
        rid=req.rid,
        rcid=req.rcid,
        language=req.language,
    )


@router.post("/canvas/edit-range")
async def canvas_edit_range_endpoint(req: CanvasEditRangeRequest) -> Dict[str, Any]:
    """REQ-CAP-02: Applies a line-range edit to a Canvas document."""
    try:
        return get_canvas_manager().apply_range_edit(
            doc_id=req.doc_id,
            start_line=req.start_line,
            end_line=req.end_line,
            replacement_content=req.replacement_content,
            instruction=req.instruction,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/canvas")
async def canvas_list_endpoint() -> Dict[str, Any]:
    """REQ-CAP-02: Lists all Canvas documents."""
    docs = get_canvas_manager().list_documents()
    return {"ok": True, "documents": docs}


@router.post("/deep-research")
async def deep_research_endpoint(req: DeepResearchRequest) -> Dict[str, Any]:
    """REQ-CAP-03: Runs 2-phase Deep Research plan and execution."""
    return await get_deep_research_engine().run_deep_research(
        topic=req.topic,
        auto_approve=req.auto_approve,
    )


@router.get("/deep-research/{plan_id}")
async def deep_research_get_endpoint(plan_id: str) -> Dict[str, Any]:
    """REQ-CAP-03: Retrieves a Deep Research plan or report by ID."""
    plan = get_deep_research_engine().get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found")
    return plan


@router.post("/saved-info")
async def saved_info_add_endpoint(req: SavedInfoAddRequest) -> Dict[str, Any]:
    """REQ-CAP-04: Adds a Saved Info memory entry."""
    return get_saved_info_manager().add_memory(content=req.content, category=req.category)


@router.get("/saved-info")
async def saved_info_list_endpoint() -> Dict[str, Any]:
    """REQ-CAP-04: Lists Saved Info memory entries."""
    items = get_saved_info_manager().list_memories()
    return {"ok": True, "memories": items}


@router.delete("/saved-info/{memory_id}")
async def saved_info_delete_endpoint(memory_id: str) -> Dict[str, Any]:
    """REQ-CAP-04: Deletes a Saved Info memory entry."""
    deleted = get_saved_info_manager().delete_memory(memory_id)
    return {"ok": True, "deleted": deleted, "memory_id": memory_id}


@router.post("/saved-info/sync-file")
async def saved_info_sync_file_endpoint(req: SavedInfoSyncFileRequest) -> Dict[str, Any]:
    """REQ-CAP-04: Syncs markdown rules file into Saved Info."""
    return get_saved_info_manager().sync_rules_file(req.file_path, category=req.category)


@router.post("/cloud-schedules")
async def cloud_schedule_create_endpoint(req: CloudScheduleCreateRequest) -> Dict[str, Any]:
    """REQ-CAP-05: Creates a Cloud Scheduled Action."""
    return get_cloud_schedule_manager().create_cloud_schedule(
        name=req.name,
        prompt=req.prompt,
        cron_expr=req.cron_expr,
        timezone=req.timezone,
    )


@router.get("/cloud-schedules")
async def cloud_schedule_list_endpoint() -> Dict[str, Any]:
    """REQ-CAP-05: Lists Cloud Scheduled Actions."""
    items = get_cloud_schedule_manager().list_cloud_schedules()
    return {"ok": True, "schedules": items}


@router.post("/cloud-schedules/{schedule_id}/pause")
async def cloud_schedule_pause_endpoint(schedule_id: str, req: CloudSchedulePauseRequest) -> Dict[str, Any]:
    """REQ-CAP-05: Pauses or resumes a Cloud Scheduled Action."""
    return get_cloud_schedule_manager().pause_cloud_schedule(schedule_id, paused=req.paused)


@router.delete("/cloud-schedules/{schedule_id}")
async def cloud_schedule_delete_endpoint(schedule_id: str) -> Dict[str, Any]:
    """REQ-CAP-05: Deletes a Cloud Scheduled Action."""
    deleted = get_cloud_schedule_manager().delete_cloud_schedule(schedule_id)
    return {"ok": True, "deleted": deleted, "schedule_id": schedule_id}


@router.post("/double-check")
async def double_check_endpoint(req: DoubleCheckRequest) -> Dict[str, Any]:
    """REQ-CAP-06: Verifies factual claims in a response."""
    return await get_response_quality_engine().double_check_response(
        text=req.text, cid=req.cid, rid=req.rid, rcid=req.rcid
    )


@router.post("/drafts/select")
async def select_draft_endpoint(req: DraftSelectRequest) -> Dict[str, Any]:
    """REQ-CAP-06: Selects the highest-quality draft candidate."""
    return get_response_quality_engine().select_best_draft(
        draft_candidates=req.draft_candidates, criterion=req.criterion
    )


@router.post("/modify-response")
async def modify_response_endpoint(req: ModifyResponseRequest) -> Dict[str, Any]:
    """REQ-CAP-07: Rewrites a response with the specified tone/length modifier."""
    return await get_response_quality_engine().modify_response(
        text=req.text, modifier=req.modifier, cid=req.cid, rid=req.rid, rcid=req.rcid
    )
