"""
backend/app/api/mission_control.py
FastAPI router for M28: AGY Mission Control (`/api/mission-control/*`).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.bridge.mission_control import MissionControlHub

router = APIRouter(prefix="/api/mission-control", tags=["mission-control"])

_hub = MissionControlHub()


class BusDispatchRequest(BaseModel):
    event_name: str = "SessionStart"
    payload: Dict[str, Any] = Field(default_factory=dict)


class BusBenchmarkRequest(BaseModel):
    iterations: int = Field(default=25, ge=1, le=500)


class PluginToggleRequest(BaseModel):
    target: str
    enabled: bool


class Iso9000GateRequest(BaseModel):
    pipeline_id: str
    target_phase: str
    baseline_id: str = "BL-20261023-M28-L4"
    locked_test_hashes: Optional[Dict[str, str]] = None


@router.get("/overview")
async def get_mission_control_overview() -> Dict[str, Any]:
    """Return 4-Tier Architecture status, GoldenStackBus P10..P50 slots, plugins, pipelines, and brain summary."""
    return _hub.get_overview()


@router.post("/bus/dispatch")
async def dispatch_golden_bus_event(req: BusDispatchRequest) -> Dict[str, Any]:
    """Dispatch a lifecycle event (`SessionStart`, `PreToolUse`, `PostToolUse`) through P10..P50."""
    return _hub.dispatch_bus_event(req.event_name, req.payload)


@router.post("/bus/benchmark")
async def benchmark_golden_bus(req: BusBenchmarkRequest) -> Dict[str, Any]:
    """Run an in-memory 5-slot latency benchmark on the P10..P50 GoldenStackBus."""
    return _hub.benchmark_bus(iterations=req.iterations)


@router.get("/plugins")
async def list_mission_control_plugins() -> Dict[str, Any]:
    """List installed plugins with context-compacted metadata."""
    plugins = _hub.list_plugins()
    return {
        "ok": True,
        "count": len(plugins),
        "plugins": plugins,
    }


@router.post("/plugins/toggle")
async def toggle_mission_control_plugin(req: PluginToggleRequest) -> Dict[str, Any]:
    """Toggle a plugin's enabled state keyed by its true install directory."""
    try:
        return _hub.toggle_plugin(target=req.target, enabled=req.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/iso9000/gate")
async def advance_iso9000_gate(req: Iso9000GateRequest) -> Dict[str, Any]:
    """Advance an ISO 9001:2015 Agile V pipeline across SPEC..RELEASE with locked test verification."""
    try:
        return _hub.advance_iso9000_pipeline(
            pipeline_id=req.pipeline_id,
            target_phase=req.target_phase,
            baseline_id=req.baseline_id,
            locked_test_hashes=req.locked_test_hashes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/brain/search")
async def search_brain_sessions(
    q: str = Query(default="", description="Search query across brain sessions and handoff reports"),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    """Search across brain conversation sessions and child handoff reports."""
    return _hub.search_brain_memory(query=q, limit=limit)
