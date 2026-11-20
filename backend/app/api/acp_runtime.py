"""
backend/app/api/acp_runtime.py
Feature-flag & runtime health router (`/api/acp/*`) for switching Python vs Rust `agy-ultra-acp`.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.app.bridge.rust_acp_client import RustAcpBridgeClient
from backend.app.contracts.agent_stream_v1 import AGENT_PROTOCOL_VERSION
from backend.app.observability.telemetry import current_trace_id

logger = logging.getLogger("gemini_api.acp_runtime")
router = APIRouter(prefix="/api/acp", tags=["ACP Runtime"])


class AcpEngineSwitchRequest(BaseModel):
    engine: Literal["python", "rust_acp"]


class AcpPingRequest(BaseModel):
    trace_id: str | None = None
    ws_url: str | None = None


def get_rust_acp_client(request: Request | None = None) -> RustAcpBridgeClient:
    if request is not None:
        cli = getattr(request.app.state, "rust_acp_client", None)
        if cli is not None:
            return cli
    import backend.app.main as main_mod

    cli = getattr(main_mod.app.state, "rust_acp_client", None)
    if cli is None:
        cli = RustAcpBridgeClient()
        main_mod.app.state.rust_acp_client = cli
    return cli


def _build_status_dict(request: Request) -> dict[str, Any]:
    cli = get_rust_acp_client(request)
    active_eng = getattr(request.app.state, "active_agent_engine", "python")
    if active_eng not in ("python", "rust_acp"):
        active_eng = "python"
    bin_path = cli.binary_path
    ws_url = cli.ws_url
    return {
        "available": bool(bin_path or ws_url),
        "binary_path": bin_path,
        "active_engine": active_eng,
        "ws_url": ws_url,
        "protocol_version": AGENT_PROTOCOL_VERSION,
        "win32_job_bound": bool(cli.win32_job_bound),
    }


@router.get("/status")
async def get_acp_status(request: Request) -> dict[str, Any]:
    return _build_status_dict(request)


@router.post("/engine")
async def set_acp_engine(req: AcpEngineSwitchRequest, request: Request) -> dict[str, Any]:
    request.app.state.active_agent_engine = req.engine
    return _build_status_dict(request)


@router.post("/ping")
async def ping_acp_runtime(request: Request, body: AcpPingRequest | None = None) -> dict[str, Any]:
    trace_id = (body.trace_id if body and body.trace_id else None) or getattr(request.state, "trace_id", "") or current_trace_id()
    cli = RustAcpBridgeClient(ws_url=body.ws_url) if (body and body.ws_url) else get_rust_acp_client(request)
    try:
        pong = await cli.ping(trace_id=trace_id)
        return {"ok": True, **pong}
    except Exception as exc:
        logger.debug("Rust ACP ping unavailable: %s", exc)
        return {
            "ok": False,
            "pong": False,
            "protocol_version": AGENT_PROTOCOL_VERSION,
            "error": str(exc),
        }
