"""
backend/app/api/gemini_capabilities.py
FastAPI router for M31-M34 Native Gemini Capabilities (`/api/v1/capabilities/*`).
Implements REQ-CAP-01 through REQ-CAP-07.
"""

from __future__ import annotations

import base64
from typing import Any

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
from backend.app.bridge.ultra_capabilities import (
    analyze_ultra_media_with_timecodes,
    get_notebooklm_ultra_engine,
    get_ultra_subscription_manager,
    get_veo_ultra_engine,
    pack_ultra_2m_repository_context,
    run_deep_think_reasoning,
)

router = APIRouter(prefix="/api/v1/capabilities", tags=["capabilities"])


class SandboxRunRequest(BaseModel):
    code: str
    prompt: str | None = None
    cid: str | None = None
    rid: str | None = None
    rcid: str | None = None


class CanvasCreateRequest(BaseModel):
    filename: str
    content: str
    cid: str | None = None
    rid: str | None = None
    rcid: str | None = None
    language: str = "python"


class CanvasEditRangeRequest(BaseModel):
    doc_id: str
    start_line: int
    end_line: int
    replacement_content: str
    instruction: str | None = None


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
    cid: str | None = None
    rid: str | None = None
    rcid: str | None = None


class DraftSelectRequest(BaseModel):
    draft_candidates: list[dict[str, Any]]
    criterion: str = "completeness"


class ModifyResponseRequest(BaseModel):
    text: str
    modifier: str = "concise"
    cid: str | None = None
    rid: str | None = None
    rcid: str | None = None


@router.post("/sandbox/run")
async def sandbox_run_endpoint(req: SandboxRunRequest) -> dict[str, Any]:
    """REQ-CAP-01: Executes code in Cloud Python Sandbox."""
    return await run_cloud_python_sandbox(
        code=req.code,
        prompt=req.prompt,
        cid=req.cid,
        rid=req.rid,
        rcid=req.rcid,
    )


@router.post("/canvas/create")
async def canvas_create_endpoint(req: CanvasCreateRequest) -> dict[str, Any]:
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
async def canvas_edit_range_endpoint(req: CanvasEditRangeRequest) -> dict[str, Any]:
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
async def canvas_list_endpoint() -> dict[str, Any]:
    """REQ-CAP-02: Lists all Canvas documents."""
    docs = get_canvas_manager().list_documents()
    return {"ok": True, "documents": docs}


@router.post("/deep-research")
async def deep_research_endpoint(req: DeepResearchRequest) -> dict[str, Any]:
    """REQ-CAP-03: Runs 2-phase Deep Research plan and execution."""
    return await get_deep_research_engine().run_deep_research(
        topic=req.topic,
        auto_approve=req.auto_approve,
    )


@router.get("/deep-research/{plan_id}")
async def deep_research_get_endpoint(plan_id: str) -> dict[str, Any]:
    """REQ-CAP-03: Retrieves a Deep Research plan or report by ID."""
    plan = get_deep_research_engine().get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found")
    return plan


@router.post("/saved-info")
async def saved_info_add_endpoint(req: SavedInfoAddRequest) -> dict[str, Any]:
    """REQ-CAP-04: Adds a Saved Info memory entry."""
    return get_saved_info_manager().add_memory(content=req.content, category=req.category)


@router.get("/saved-info")
async def saved_info_list_endpoint() -> dict[str, Any]:
    """REQ-CAP-04: Lists Saved Info memory entries."""
    items = get_saved_info_manager().list_memories()
    return {"ok": True, "memories": items}


@router.delete("/saved-info/{memory_id}")
async def saved_info_delete_endpoint(memory_id: str) -> dict[str, Any]:
    """REQ-CAP-04: Deletes a Saved Info memory entry."""
    deleted = get_saved_info_manager().delete_memory(memory_id)
    return {"ok": True, "deleted": deleted, "memory_id": memory_id}


@router.post("/saved-info/sync-file")
async def saved_info_sync_file_endpoint(req: SavedInfoSyncFileRequest) -> dict[str, Any]:
    """REQ-CAP-04: Syncs markdown rules file into Saved Info."""
    return get_saved_info_manager().sync_rules_file(req.file_path, category=req.category)


@router.post("/cloud-schedules")
async def cloud_schedule_create_endpoint(req: CloudScheduleCreateRequest) -> dict[str, Any]:
    """REQ-CAP-05: Creates a Cloud Scheduled Action."""
    return get_cloud_schedule_manager().create_cloud_schedule(
        name=req.name,
        prompt=req.prompt,
        cron_expr=req.cron_expr,
        timezone=req.timezone,
    )


@router.get("/cloud-schedules")
async def cloud_schedule_list_endpoint() -> dict[str, Any]:
    """REQ-CAP-05: Lists Cloud Scheduled Actions."""
    items = get_cloud_schedule_manager().list_cloud_schedules()
    return {"ok": True, "schedules": items}


@router.post("/cloud-schedules/{schedule_id}/pause")
async def cloud_schedule_pause_endpoint(schedule_id: str, req: CloudSchedulePauseRequest) -> dict[str, Any]:
    """REQ-CAP-05: Pauses or resumes a Cloud Scheduled Action."""
    return get_cloud_schedule_manager().pause_cloud_schedule(schedule_id, paused=req.paused)


@router.delete("/cloud-schedules/{schedule_id}")
async def cloud_schedule_delete_endpoint(schedule_id: str) -> dict[str, Any]:
    """REQ-CAP-05: Deletes a Cloud Scheduled Action."""
    deleted = get_cloud_schedule_manager().delete_cloud_schedule(schedule_id)
    return {"ok": True, "deleted": deleted, "schedule_id": schedule_id}


@router.post("/double-check")
async def double_check_endpoint(req: DoubleCheckRequest) -> dict[str, Any]:
    """REQ-CAP-06: Verifies factual claims in a response."""
    return await get_response_quality_engine().double_check_response(
        text=req.text, cid=req.cid, rid=req.rid, rcid=req.rcid
    )


@router.post("/drafts/select")
async def select_draft_endpoint(req: DraftSelectRequest) -> dict[str, Any]:
    """REQ-CAP-06: Selects the highest-quality draft candidate."""
    return get_response_quality_engine().select_best_draft(
        draft_candidates=req.draft_candidates, criterion=req.criterion
    )


@router.post("/modify-response")
async def modify_response_endpoint(req: ModifyResponseRequest) -> dict[str, Any]:
    """REQ-CAP-07: Rewrites a response with the specified tone/length modifier."""
    return await get_response_quality_engine().modify_response(
        text=req.text, modifier=req.modifier, cid=req.cid, rid=req.rid, rcid=req.rcid
    )



class UltraDeepThinkRequest(BaseModel):
    prompt: str
    branch_count: int = 3
    cid: str | None = None


class UltraPack2MRequest(BaseModel):
    workspace_dir: str
    max_total_bytes: int = 7_500_000
    max_file_bytes: int = 1_000_000


class UltraMediaAnalyzeRequest(BaseModel):
    media_b64: str
    filename: str = "video.mp4"
    prompt: str = "Extract key timestamps"


class UltraVeoRenderRequest(BaseModel):
    prompt: str
    model_key: str = "veo_3_1_t2v_hq_1080p_audio"
    resolution: str = "1080p"
    first_frame_b64: str | None = None
    last_frame_b64: str | None = None
    extend_from_scene_id: str | None = None


class UltraNotebookLMAudioRequest(BaseModel):
    notebook_id: str
    mode: str = "deep_dive"
    instructions: str = ""
    language: str = "ru"


@router.get("/ultra/profile")
async def ultra_profile_endpoint(account_id: str = "acc_21aac20f") -> dict[str, Any]:
    """REQ-ULTRA-06: Returns Ultra 5x subscription profile and thresholds."""
    return get_ultra_subscription_manager().detect_subscription_profile(account_id=account_id)


@router.get("/ultra/credits")
async def ultra_credits_endpoint(account_id: str = "acc_21aac20f") -> dict[str, Any]:
    """REQ-ULTRA-06: Returns shared family AI credits pool telemetry."""
    return get_ultra_subscription_manager().get_family_credits_telemetry(account_id=account_id)


@router.post("/ultra/deep-think")
async def ultra_deep_think_endpoint(req: UltraDeepThinkRequest) -> dict[str, Any]:
    """REQ-ULTRA-06: Executes Gemini 4 Argon Deep Think parallel reasoning."""
    return await run_deep_think_reasoning(
        prompt=req.prompt,
        branch_count=req.branch_count,
        cid=req.cid,
    )


@router.post("/ultra/pack-2m")
async def ultra_pack_2m_endpoint(req: UltraPack2MRequest) -> dict[str, Any]:
    """REQ-ULTRA-06: Packs repository context up to the 2M-token Ultra budget."""
    return pack_ultra_2m_repository_context(
        workspace_dir=req.workspace_dir,
        max_total_bytes=req.max_total_bytes,
        max_file_bytes=req.max_file_bytes,
    )


@router.post("/ultra/media-analyze")
async def ultra_media_analyze_endpoint(req: UltraMediaAnalyzeRequest) -> dict[str, Any]:
    """REQ-ULTRA-06: Analyzes long-form media payload and extracts timecodes."""
    raw_bytes = base64.b64decode(req.media_b64) if req.media_b64 else b""
    return await analyze_ultra_media_with_timecodes(
        media_bytes=raw_bytes,
        filename=req.filename,
        prompt=req.prompt,
    )


@router.post("/ultra/veo-render")
async def ultra_veo_render_endpoint(req: UltraVeoRenderRequest) -> dict[str, Any]:
    """REQ-ULTRA-06: Renders a Veo 3.1 Ultra scene."""
    return await get_veo_ultra_engine().render_ultra_scene(
        prompt=req.prompt,
        model_key=req.model_key,
        resolution=req.resolution,
        first_frame_b64=req.first_frame_b64,
        last_frame_b64=req.last_frame_b64,
        extend_from_scene_id=req.extend_from_scene_id,
    )


@router.post("/ultra/notebooklm-audio")
async def ultra_notebooklm_audio_endpoint(req: UltraNotebookLMAudioRequest) -> dict[str, Any]:
    """REQ-ULTRA-06: Generates a NotebookLM Ultra Audio Overview in critique/debate/deep_dive/brief mode."""
    return await get_notebooklm_ultra_engine().generate_ultra_audio_overview(
        notebook_id=req.notebook_id,
        mode=req.mode,
        instructions=req.instructions,
        language=req.language,
    )
