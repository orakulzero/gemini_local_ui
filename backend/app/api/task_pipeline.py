"""
backend/app/api/task_pipeline.py
FastAPI router for M30: Autonomous Gemini 4 Argon Task Mode Pipeline & Scheduled Orchestrator.
Implements REQ-PIPE-06 (`/api/task-pipeline/*`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.app.bridge.task_pipeline import get_task_pipeline_orchestrator

router = APIRouter(prefix="/api/task-pipeline", tags=["task-pipeline"])


class ScheduleCreateRequest(BaseModel):
    name: str
    workspace_dir: str
    prompt: str
    schedule_type: str = "interval"
    interval_seconds: int = Field(default=3600, ge=10)
    cron_expr: str | None = None
    target_files: list[str] | None = None
    model: str = "gemini-4-argon"
    thinking: int = 2
    max_heal_attempts: int = 2
    enabled: bool = True


class ScheduleUpdateRequest(BaseModel):
    name: str | None = None
    prompt: str | None = None
    interval_seconds: int | None = None
    cron_expr: str | None = None
    enabled: bool | None = None


class PipelineExecuteRequest(BaseModel):
    workspace_dir: str
    prompt: str
    target_files: list[str] | None = None
    model: str = "gemini-4-argon"
    thinking: int = 2
    max_heal_attempts: int = 2
    run_pytest: bool = True
    mock_candidate_files: dict[str, str] | None = None
    schedule_id: str | None = None


class RejectRunRequest(BaseModel):
    reason: str = "Manual rejection"


@router.post("/schedules")
async def create_pipeline_schedule(req: ScheduleCreateRequest) -> dict[str, Any]:
    """REQ-PIPE-02, REQ-PIPE-06: Creates a new Task-Mode schedule."""
    orch = get_task_pipeline_orchestrator()
    return orch.create_schedule(
        name=req.name,
        workspace_dir=req.workspace_dir,
        prompt=req.prompt,
        schedule_type=req.schedule_type,
        interval_seconds=req.interval_seconds,
        cron_expr=req.cron_expr,
        target_files=req.target_files,
        model=req.model,
        thinking=req.thinking,
        max_heal_attempts=req.max_heal_attempts,
        enabled=req.enabled,
    )


@router.get("/schedules")
async def list_pipeline_schedules() -> dict[str, Any]:
    """REQ-PIPE-02, REQ-PIPE-06: Lists all Task-Mode schedules."""
    orch = get_task_pipeline_orchestrator()
    schedules = orch.list_schedules()
    return {"ok": True, "count": len(schedules), "schedules": schedules}


@router.patch("/schedules/{schedule_id}")
@router.post("/schedules/{schedule_id}/toggle")
async def update_pipeline_schedule(
    schedule_id: str,
    req: ScheduleUpdateRequest | None = None,
) -> dict[str, Any]:
    """REQ-PIPE-02, REQ-PIPE-06: Updates or toggles a Task-Mode schedule."""
    orch = get_task_pipeline_orchestrator()
    current = orch.get_schedule(schedule_id)
    if not current:
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found")
    if req is None:
        return orch.update_schedule(schedule_id, enabled=not bool(current.get("enabled", True)))
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    return orch.update_schedule(schedule_id, **updates)


@router.delete("/schedules/{schedule_id}")
async def delete_pipeline_schedule(schedule_id: str) -> dict[str, Any]:
    """REQ-PIPE-02, REQ-PIPE-06: Deletes a Task-Mode schedule."""
    orch = get_task_pipeline_orchestrator()
    deleted = orch.delete_schedule(schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found")
    return {"ok": True, "deleted": True, "schedule_id": schedule_id}


@router.post("/schedules/trigger-due")
async def trigger_due_schedules_endpoint() -> dict[str, Any]:
    """REQ-PIPE-02, REQ-PIPE-06: Immediately triggers all due enabled schedules."""
    orch = get_task_pipeline_orchestrator()
    runs = await orch.trigger_due_schedules()
    return {"ok": True, "triggered_count": len(runs), "runs": runs}


@router.post("/runs/execute")
async def execute_pipeline_run_endpoint(req: PipelineExecuteRequest) -> dict[str, Any]:
    """REQ-PIPE-03, REQ-PIPE-05, REQ-PIPE-06: Executes a staged Task-Mode pipeline run."""
    orch = get_task_pipeline_orchestrator()
    return await orch.execute_pipeline_run(
        workspace_dir=req.workspace_dir,
        prompt=req.prompt,
        target_files=req.target_files,
        model=req.model,
        thinking=req.thinking,
        max_heal_attempts=req.max_heal_attempts,
        run_pytest=req.run_pytest,
        mock_candidate_files=req.mock_candidate_files,
        schedule_id=req.schedule_id,
    )


@router.get("/runs")
async def list_pipeline_runs() -> dict[str, Any]:
    """REQ-PIPE-05, REQ-PIPE-06: Lists all staged/approved/rejected Task-Mode pipeline runs."""
    orch = get_task_pipeline_orchestrator()
    runs = orch.list_runs()
    return {"ok": True, "count": len(runs), "runs": runs}


@router.get("/runs/{run_id}")
async def get_pipeline_run(run_id: str) -> dict[str, Any]:
    """REQ-PIPE-05, REQ-PIPE-06: Returns details and unified diff for a pipeline run."""
    orch = get_task_pipeline_orchestrator()
    run = orch.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return run


@router.post("/runs/{run_id}/approve")
async def approve_pipeline_run(run_id: str) -> dict[str, Any]:
    """REQ-PIPE-05, REQ-PIPE-06: Approves a staged run and writes verified files to workspace_dir."""
    orch = get_task_pipeline_orchestrator()
    try:
        return orch.approve_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/reject")
async def reject_pipeline_run(
    run_id: str,
    req: RejectRunRequest | None = None,
) -> dict[str, Any]:
    """REQ-PIPE-05, REQ-PIPE-06: Rejects a staged run without modifying workspace_dir."""
    orch = get_task_pipeline_orchestrator()
    try:
        return orch.reject_run(run_id, reason=(req.reason if req else "Manual rejection"))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/cloud-scheduled/sync")
async def sync_cloud_scheduled_actions_endpoint(request: Request) -> dict[str, Any]:
    """REQ-PIPE-02, REQ-PIPE-06: Reconciles Google Gemini Cloud Scheduled Actions via MaZiqc / hNvQHb."""
    orch = get_task_pipeline_orchestrator()
    session_mgr = getattr(request.app.state, "session_manager", None)
    client = None
    if session_mgr and session_mgr.get_health().is_authenticated:
        from backend.app.bridge.client import GeminiWebClient
        client = GeminiWebClient(session_context=session_mgr.context, base_url=session_mgr.base_url, timeout=15.0)
    try:
        return await orch.sync_cloud_scheduled_actions(client=client)
    finally:
        if client is not None:
            await client.close()
