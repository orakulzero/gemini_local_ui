"""
backend/app/mcp/routes.py
Stateless Streamable HTTP (`2026-07-28`) router for MCP JSON-RPC 2.0 communication.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.notebooklm import NotebookLMClient
from backend.app.mcp.server import MCPServer, PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS
from backend.app.observability.telemetry import get_tracer

logger = logging.getLogger("gemini_mcp.routes")

router = APIRouter(tags=["MCP"])

_mcp_server: Optional[MCPServer] = None


def get_mcp_server(request: Request) -> MCPServer:
    """Dependency injection resolving MCPServer instance."""
    global _mcp_server
    img_dir = getattr(request.app.state, "image_data_dir", None)
    sm = getattr(request.app.state, "session_manager", None)
    server = getattr(request.app.state, "mcp_server", None)
    if server:
        if img_dir:
            server.image_data_dir = img_dir
        if sm and not server.session_manager:
            server.session_manager = sm
        return server
    if _mcp_server is None:
        _mcp_server = MCPServer(session_manager=sm, image_data_dir=img_dir)
    else:
        if img_dir:
            _mcp_server.image_data_dir = img_dir
        if sm and not _mcp_server.session_manager:
            _mcp_server.session_manager = sm
    return _mcp_server


def get_gemini_client(request: Request) -> Optional[GeminiWebClient]:
    """Dependency injection resolving active GeminiWebClient."""
    sm = getattr(request.app.state, "session_manager", None)
    if sm and sm.active_account_id:
        ctx = sm.get_session_context(sm.active_account_id)
        if ctx and ctx.cookies:
            return GeminiWebClient(session_context=ctx, base_url=sm.base_url)
    from backend.app.openai.routes import get_gemini_client as resolve_client
    try:
        return resolve_client(request)
    except Exception as e:
        logger.debug("Optional GeminiWebClient not available: %s", e)
        return None


async def get_notebooklm_client(request: Request) -> Optional[NotebookLMClient]:
    """Dependency injection resolving active NotebookLMClient."""
    from backend.app.api.notebooklm import get_notebooklm_client as resolve_nb_client
    try:
        res = resolve_nb_client(request)
        if hasattr(res, "__await__"):
            return await res
        return res
    except Exception as e:
        logger.debug("Optional NotebookLMClient not available: %s", e)
        return None


@router.get("/mcp", response_model=None)
@router.get("", response_model=None, include_in_schema=False)
@router.get("/", response_model=None, include_in_schema=False)
async def handle_mcp_get() -> Response:
    """Stateless Streamable HTTP: no persistent SSE listener on GET."""
    return Response(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, headers={"Allow": "POST, DELETE"})


@router.delete("/mcp", response_model=None)
@router.delete("", response_model=None, include_in_schema=False)
@router.delete("/", response_model=None, include_in_schema=False)
async def handle_mcp_delete() -> Response:
    """Stateless Streamable HTTP: idempotent session termination no-op."""
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/mcp", response_model=None)
@router.post("", response_model=None, include_in_schema=False)
@router.post("/", response_model=None, include_in_schema=False)
async def handle_mcp_endpoint(
    request: Request,
    server: MCPServer = Depends(get_mcp_server),
    client: Optional[GeminiWebClient] = Depends(get_gemini_client),
    notebooklm_client: Optional[NotebookLMClient] = Depends(get_notebooklm_client),
) -> Any:
    """MCP `2026-07-28` Stateless Streamable HTTP JSON-RPC 2.0 endpoint."""
    hdr_ver = request.headers.get("MCP-Protocol-Version", "").strip()
    if hdr_ver and hdr_ver not in SUPPORTED_PROTOCOL_VERSIONS:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": f"Unsupported MCP-Protocol-Version: {hdr_ver}"},
            },
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error: Invalid JSON"},
            },
        )

    rpc_method = request.headers.get("Mcp-Method", "").strip()
    rpc_tool = request.headers.get("Mcp-Name", "").strip()
    if isinstance(body, dict):
        rpc_method = rpc_method or str(body.get("method") or "")
        params = body.get("params")
        if not rpc_tool and isinstance(params, dict):
            rpc_tool = str(params.get("name") or "")

    tracer = get_tracer("gemini_local_ui.mcp")
    with tracer.start_as_current_span("mcp.http.rpc") as span:
        if rpc_method:
            span.set_attribute("mcp.method", rpc_method)
        if rpc_tool:
            span.set_attribute("mcp.tool_name", rpc_tool)
        response = await server.handle_message(body, client=client, notebooklm_client=notebooklm_client)

    negotiated_ver = hdr_ver or PROTOCOL_VERSION
    if isinstance(response, dict) and isinstance(response.get("result"), dict):
        negotiated_ver = str(response["result"].get("protocolVersion") or negotiated_ver)

    resp_headers = {"MCP-Protocol-Version": negotiated_ver}
    if response is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers=resp_headers)
    return JSONResponse(status_code=status.HTTP_200_OK, content=response, headers=resp_headers)
