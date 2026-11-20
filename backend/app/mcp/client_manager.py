"""
backend/app/mcp/client_manager.py
MCP Client Manager supporting stdio, sse, and streamable-http transports.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import os
import sys
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from backend.app.mcp.session_pool import MCPSessionPool
from backend.app.storage.workspace_config import WorkspaceConfigStore
from backend.app.storage.workspace_models import ConnectorItem

logger = logging.getLogger("gemini_workspace.mcp_client")


class MCPConnectorManager:
    def __init__(self, config_store: WorkspaceConfigStore):
        self.config_store = config_store
        self.session_pool = MCPSessionPool(str(self.config_store.project_root))

    async def close_all(self) -> None:
        await self.session_pool.close_all()

    @asynccontextmanager
    async def _connect(self, conn: ConnectorItem) -> AsyncIterator[ClientSession]:
        timeout = conn.timeout_seconds or 15
        if conn.transport == "stdio":
            env = dict(os.environ)
            if conn.env:
                env.update(conn.env)
            proj_dir = str(self.config_store.project_root)
            env["PYTHONPATH"] = proj_dir + os.pathsep + env.get("PYTHONPATH", "")
            params = StdioServerParameters(
                command=conn.command or sys.executable,
                args=conn.args,
                env=env,
                cwd=proj_dir,
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await asyncio.wait_for(session.initialize(), timeout=timeout)
                    yield session
        elif conn.transport == "sse":
            async with sse_client(conn.url or "", headers=conn.headers or {}) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await asyncio.wait_for(session.initialize(), timeout=timeout)
                    yield session
        elif conn.transport in ("streamable-http", "streamable_http"):
            async with streamable_http_client(conn.url or "", headers=conn.headers or {}) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await asyncio.wait_for(session.initialize(), timeout=timeout)
                    yield session
        else:
            raise ValueError(f"Unsupported MCP transport: {conn.transport}")

    async def test_connector(self, connector_id: str) -> Dict[str, Any]:
        conn = self.config_store.get_connector(connector_id, mask=False)
        if not conn:
            raise ValueError(f"Connector '{connector_id}' not found")
        t0 = time.perf_counter()
        if conn.transport == "stdio":
            tools_res = await self.session_pool.list_tools(conn)
        else:
            async with self._connect(conn) as session:
                tools_res = await asyncio.wait_for(session.list_tools(), timeout=conn.timeout_seconds)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        tools = []
        for t in tools_res.tools:
            ann = getattr(t, "annotations", None)
            ann_dict = {
                "read_only_hint": getattr(ann, "read_only_hint", None) if ann else None,
                "destructive_hint": getattr(ann, "destructive_hint", None) if ann else None,
                "idempotent_hint": getattr(ann, "idempotent_hint", None) if ann else None,
                "open_world_hint": getattr(ann, "open_world_hint", None) if ann else None,
            }
            tools.append({
                "name": t.name,
                "namespaced_name": f"{conn.id}__{t.name}",
                "description": t.description or "",
                "input_schema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {},
                "output_schema": getattr(t, "output_schema", None) or getattr(t, "outputSchema", None) or {},
                "read_only_hint": getattr(ann, "read_only_hint", None) if ann else None,
                "destructive_hint": getattr(ann, "destructive_hint", None) if ann else None,
                "annotations": ann_dict,
            })
        from backend.app.security.mcp_pinning import get_schema_pin_store
        pin_status, pin_sha, changed_tools = get_schema_pin_store().verify_or_pin(conn.id, tools)
        return {
            "ok": True,
            "status": "ok",
            "connector_id": conn.id,
            "latency_ms": elapsed_ms,
            "tools": tools,
            "pin_status": pin_status,
            "pin_sha256": pin_sha,
            "pin_changed_tools": changed_tools,
            "error": None,
        }

    async def list_active_tools(self, connector_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        all_conns = self.config_store.list_connectors(mask=False)
        target_conns = [
            c for c in all_conns
            if c.enabled and (connector_ids is None or c.id in connector_ids)
        ]
        tools: List[Dict[str, Any]] = []
        for conn in target_conns:
            try:
                if conn.transport == "stdio":
                    tools_res = await self.session_pool.list_tools(conn)
                else:
                    async with self._connect(conn) as session:
                        tools_res = await asyncio.wait_for(session.list_tools(), timeout=conn.timeout_seconds)
                for t in tools_res.tools:
                    if "*" in conn.allowed_tools or t.name in conn.allowed_tools:
                        ann = getattr(t, "annotations", None)
                        tools.append({
                            "name": t.name,
                            "namespaced_name": f"{conn.id}__{t.name}",
                            "description": t.description or "",
                            "input_schema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {},
                            "connector_id": conn.id,
                            "read_only_hint": getattr(ann, "read_only_hint", None) if ann else None,
                            "destructive_hint": getattr(ann, "destructive_hint", None) if ann else None,
                        })
            except Exception as e:
                logger.warning("Failed listing tools for connector %s: %s", conn.id, e)
        return tools

    async def call_tool(self, namespaced_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if "__" not in namespaced_name:
            raise ValueError(f"Invalid namespaced tool name: {namespaced_name}")
        connector_id, tool_name = namespaced_name.split("__", 1)
        conn = self.config_store.get_connector(connector_id, mask=False)
        if not conn:
            raise ValueError(f"Connector '{connector_id}' not found")

        from backend.app.security.mcp_pinning import get_schema_pin_store
        pin_store = get_schema_pin_store()
        if connector_id in pin_store._pins:
            if conn.transport == "stdio":
                cur_tools_res = await self.session_pool.list_tools(conn)
            else:
                async with self._connect(conn) as session:
                    cur_tools_res = await asyncio.wait_for(session.list_tools(), timeout=conn.timeout_seconds)
            raw_tools = []
            for t in cur_tools_res.tools:
                ann = getattr(t, "annotations", None)
                raw_tools.append({
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {},
                    "output_schema": getattr(t, "output_schema", None) or getattr(t, "outputSchema", None) or {},
                    "annotations": {
                        "read_only_hint": getattr(ann, "read_only_hint", None) if ann else None,
                        "destructive_hint": getattr(ann, "destructive_hint", None) if ann else None,
                        "idempotent_hint": getattr(ann, "idempotent_hint", None) if ann else None,
                        "open_world_hint": getattr(ann, "open_world_hint", None) if ann else None,
                    },
                })
            pin_store.assert_unmodified(connector_id, raw_tools)
        t0 = time.perf_counter()
        reuse_count = 0
        if conn.transport == "stdio":
            res, reuse_count = await self.session_pool.call_tool(conn, tool_name, arguments)
        else:
            async with self._connect(conn) as session:
                res = await asyncio.wait_for(session.call_tool(tool_name, arguments), timeout=conn.timeout_seconds)
        duration_ms = int((time.perf_counter() - t0) * 1000)
        text_out = []
        for item in (res.content or []):
            if hasattr(item, "text"):
                text_out.append(item.text)
            elif isinstance(item, dict) and "text" in item:
                text_out.append(item["text"])
            else:
                text_out.append(str(item))
        result_str = "\n".join(text_out)
        is_err = bool(getattr(res, "isError", False))
        return {
            "ok": not is_err,
            "result": result_str,
            "is_error": is_err,
            "duration_ms": duration_ms,
            "reuse_count": reuse_count,
        }

    @staticmethod
    def build_connector_tools_prompt(tools: List[Dict[str, Any]]) -> str:
        if not tools:
            return ""
        lines = [
            "You have access to the following MCP tools:",
        ]
        for t in tools:
            lines.append(f"- Tool: `{t['namespaced_name']}`")
            if t.get("description"):
                lines.append(f"  Description: {t['description']}")
            if t.get("input_schema"):
                lines.append(f"  Schema: {t['input_schema']}")
        lines.append(
            "\nTo call a tool, you MUST emit a tool_call markdown block formatted exactly like this:\n"
            "```tool_call\n"
            '{"name": "<namespaced_name>", "arguments": {<args>}}\n'
            "```\n"
            "You will receive the tool output in the next turn and can then complete your answer."
        )
        return "\n".join(lines)
