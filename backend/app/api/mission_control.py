"""
backend/app/api/mission_control.py
FastAPI router for M29: AGY Mission Control v1.2.0 (`/api/mission-control/*`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.bridge.mission_control import MissionControlHub

router = APIRouter(prefix="/api/mission-control", tags=["mission-control"])

_hub = MissionControlHub()


class BusDispatchRequest(BaseModel):
    event_name: str = "SessionStart"
    payload: dict[str, Any] = Field(default_factory=dict)


class BusBenchmarkRequest(BaseModel):
    iterations: int = Field(default=25, ge=1, le=500)


class PluginToggleRequest(BaseModel):
    target: str
    enabled: bool


class Iso9000GateRequest(BaseModel):
    pipeline_id: str
    target_phase: str
    baseline_id: str = "BL-20261023-M28-L4"
    locked_test_hashes: dict[str, str] | None = None


class DiffTourGenerateRequest(BaseModel):
    title: str = "Senior Engineer Architecture Tour"
    target_file: str | None = None
    dimensions: list[str] | None = None
    drop_revealing_asks: bool = False
    base: str = "HEAD"
    head: str = "HEAD"


class DiffTourValidateRequest(BaseModel):
    tour: dict[str, Any]
    drop_revealing_asks: bool = False


class PluginReconcileRequest(BaseModel):
    target_version: str = "1.1.0"
    plugins: list[str] | None = None


class SecretShieldApiRequest(BaseModel):
    profile: str = "clean_repo"
    custom_text: str | None = None
    repo_path: str | None = None
    scan_git_history: bool = False
    max_commits: int = 50
    extra_pii_tokens: list[str] | None = None
    rotate_envelopes: list[dict[str, Any]] | None = None


class PeLabApiRequest(BaseModel):
    profile: str = "clean"
    target_path: str | None = None
    raw_base64: str | None = None


class EskdInspectApiRequest(BaseModel):
    sheet_format: str = "A4"
    stamp_type: str = "stamp_form1"
    annotations: list[dict[str, Any]] | None = None
    line_segments: list[dict[str, Any]] | None = None
    title_block_bbox: list[float] | None = None
    frame_bbox: list[float] | None = None
    target_dpi: float = 300.0
    tolerance_mm: float = 1.0
    sheets: list[dict[str, Any]] | None = None


class WorkflowTaskItem(BaseModel):
    task_id: str
    station_id: str
    action: str = "execute"
    payload: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class FederationWaveApiRequest(BaseModel):
    workflow_name: str = "cross_station_audit_dag"
    tasks: list[WorkflowTaskItem]
    max_workers: int = 4
    synthesize_dossier: bool = True
    dossier_title: str | None = None
    repo_path: str | None = None


@router.get("/overview")
async def get_mission_control_overview() -> dict[str, Any]:
    """Return 5-Tier Architecture status, GoldenStackBus P10..P50 slots, plugins, pipelines, and brain summary."""
    return _hub.get_overview()


@router.post("/bus/dispatch")
async def dispatch_golden_bus_event(req: BusDispatchRequest) -> dict[str, Any]:
    """Dispatch a lifecycle event (`SessionStart`, `PreToolUse`, `PostToolUse`) through P10..P50."""
    return _hub.dispatch_bus_event(req.event_name, req.payload)


@router.post("/bus/benchmark")
async def benchmark_golden_bus(req: BusBenchmarkRequest) -> dict[str, Any]:
    """Run an in-memory 5-slot latency benchmark on the P10..P50 GoldenStackBus."""
    return _hub.benchmark_bus(iterations=req.iterations)


@router.get("/plugins")
async def list_mission_control_plugins() -> dict[str, Any]:
    """List installed plugins with context-compacted metadata."""
    plugins = _hub.list_plugins()
    return {
        "ok": True,
        "count": len(plugins),
        "plugins": plugins,
    }


@router.post("/plugins/toggle")
async def toggle_mission_control_plugin(req: PluginToggleRequest) -> dict[str, Any]:
    """Toggle a plugin's enabled state keyed by its true install directory."""
    try:
        return _hub.toggle_plugin(target=req.target, enabled=req.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/plugins/drift")
async def detect_plugins_drift(
    baseline_version: str = Query(default="1.1.0", description="Target baseline plugin version")
) -> dict[str, Any]:
    """Detect plugin version drift across installed manifests against target baseline."""
    return _hub.detect_plugin_drift(baseline_version=baseline_version)


@router.post("/plugins/reconcile")
async def reconcile_mission_control_plugins(req: PluginReconcileRequest) -> dict[str, Any]:
    """Atomically reconcile drifted plugin manifests to target version preserving custom fields."""
    try:
        return _hub.reconcile_plugins(target_version=req.target_version, plugins=req.plugins)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/iso9000/gate")
async def advance_iso9000_gate(req: Iso9000GateRequest) -> dict[str, Any]:
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
    q: str = Query(default="", description="Search query across brain sessions, handoff reports, diff-tours, and CAPA"),
    limit: int = Query(default=20, ge=1, le=100),
    dimension: str | None = Query(default=None, description="Optional Senior Review Dimension filter (L1..L8)"),
    severity: str | None = Query(default=None, description="Optional CAPA severity filter"),
) -> dict[str, Any]:
    """Search across brain conversation sessions, child handoff reports, diff-tours, and CAPA records."""
    return _hub.search_brain_memory(query=q, limit=limit, dimension=dimension, severity=severity)


@router.post("/diff-tour/generate")
async def generate_diff_tour_endpoint(req: DiffTourGenerateRequest) -> dict[str, Any]:
    """Generate a Senior Engineer Diff-Tour v1 payload anchored in workspace files."""
    return _hub.generate_diff_tour(
        title=req.title,
        target_file=req.target_file,
        dimensions=req.dimensions,
        drop_revealing_asks_flag=req.drop_revealing_asks,
        base=req.base,
        head=req.head,
    )


@router.post("/diff-tour/validate")
async def validate_diff_tour_endpoint(req: DiffTourValidateRequest) -> dict[str, Any]:
    """Validate a Diff-Tour v1 payload and attach a deterministic TUI box preview."""
    return _hub.validate_diff_tour_payload(
        tour=req.tour,
        drop_revealing_asks_flag=req.drop_revealing_asks,
    )


@router.post("/stations/secret-shield/sarif")
async def run_secret_shield_station(req: SecretShieldApiRequest) -> dict[str, Any]:
    """L4 Station: Secret Shield v1.1.0 scan, OASIS SARIF 2.1.0 export, and DPAPI rotation."""
    return _hub.run_secret_shield(req.model_dump())


@router.post("/stations/pe-lab/analyze")
async def run_pe_lab_station(req: PeLabApiRequest) -> dict[str, Any]:
    """L4 Station: PE Reversing Lab v1.1.0 PE32+ static triage and YARA rule emission."""
    return _hub.run_pe_lab(req.model_dump())


@router.post("/stations/eskd/inspect")
async def run_eskd_inspect_station(req: EskdInspectApiRequest) -> dict[str, Any]:
    """L4 Station: ESKD Vision Station v1.1.0 GOST 2.104/2.301 and 300 DPI geometry inspection."""
    return _hub.run_eskd_inspect(req.model_dump())


@router.post("/federation/waves/execute")
async def execute_federation_waves(req: FederationWaveApiRequest) -> dict[str, Any]:
    """L5 Station: Execute topological wave DAG with circuit breaker and dossier synthesis."""
    try:
        return _hub.run_federation_waves(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
