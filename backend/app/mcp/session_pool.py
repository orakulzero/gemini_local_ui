"""
backend/app/mcp/session_pool.py
Actor-based stdio session pool maintaining warm MCP processes without cross-task cancellation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sys
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple
import uuid

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
import mcp.types as mcp_types

from backend.app.mcp.elicitation_gate import get_elicitation_gate
from backend.app.mcp.win32_job import Win32JobGuard
from backend.app.storage.workspace_models import ConnectorItem

logger = logging.getLogger("gemini_mcp.session_pool")


async def handle_mcp_elicitation_request(
    *,
    connector_id: str,
    params: Any,
    timeout_seconds: float = 120.0,
    tool_name: Optional[str] = None,
    on_elicitation: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> mcp_types.ElicitResult:
    """Bridge server-initiated MCP `elicitation/create` onto ElicitationGate + live stream callback."""
    raw_url = getattr(params, "url", None)
    raw_mode = getattr(params, "mode", None) or ("url" if raw_url else "form")
    mode = "url" if raw_mode == "url" else "form"
    message = str(getattr(params, "message", "") or "")
    raw_schema = getattr(params, "requested_schema", None) or getattr(params, "requestedSchema", None)
    if hasattr(raw_schema, "model_dump"):
        req_schema = raw_schema.model_dump(by_alias=True, exclude_none=True)
    elif isinstance(raw_schema, dict):
        req_schema = raw_schema
    else:
        req_schema = None
    elicitation_id = str(getattr(params, "elicitation_id", None) or f"elicit_{uuid.uuid4().hex[:10]}")

    gate = get_elicitation_gate()
    try:
        pending = gate.create_pending(
            elicitation_id,
            connector_id=connector_id,
            mode=mode,  # type: ignore[arg-type]
            message=message,
            requested_schema=req_schema,
            url=str(raw_url) if raw_url is not None else None,
            tool_name=tool_name,
        )
    except ValueError as err:
        logger.warning("Blocked unsafe MCP elicitation URL from %s: %s", connector_id, err)
        return mcp_types.ElicitResult(action="cancel", content=None)

    if on_elicitation is not None:
        await on_elicitation(pending.to_metadata())

    action, content = await gate.wait_for_response(
        elicitation_id, timeout=float(timeout_seconds or 120.0), cancel_event=cancel_event
    )
    return mcp_types.ElicitResult(action=action, content=content if action == "accept" else None)


def _compute_config_hash(conn: ConnectorItem) -> str:
    key_data = {
        "command": conn.command,
        "args": conn.args,
        "env": conn.env,
        "transport": conn.transport,
    }
    return hashlib.sha256(json.dumps(key_data, sort_keys=True).encode("utf-8")).hexdigest()[:16]


class StdioActor:
    def __init__(self, conn: ConnectorItem, project_root: str, job_guard: Win32JobGuard) -> None:
        self.conn = conn
        self.project_root = project_root
        self.job_guard = job_guard
        self.queue: asyncio.Queue[Tuple[str, Tuple[Any, ...], asyncio.Future[Any]]] = asyncio.Queue()
        self.reuse_count: int = 0
        self._dirty: bool = False
        self._active_tool_name: Optional[str] = None
        self._active_on_elicitation: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self._active_cancel_event: Optional[asyncio.Event] = None
        self.task = asyncio.create_task(self._run_actor())

    async def _run_actor(self) -> None:
        cmd = self.conn.command or sys.executable
        resolved_cmd = shutil.which(cmd) or cmd
        env = dict(os.environ)
        if self.conn.env:
            env.update(self.conn.env)
        env["PYTHONPATH"] = self.project_root + os.pathsep + env.get("PYTHONPATH", "")

        params = StdioServerParameters(
            command=resolved_cmd,
            args=self.conn.args,
            env=env,
            cwd=self.project_root,
        )

        async def _on_msg(message: Any) -> None:
            m_type = type(message).__name__
            m_str = str(message)
            if (
                m_type == "ToolListChangedNotification"
                or getattr(message, "method", "") == "notifications/tools/list_changed"
                or "tools/list_changed" in m_str
            ):
                logger.info("ToolListChangedNotification received for connector '%s'; marking dirty", self.conn.id)
                self._dirty = True

        async def _on_elicit(_ctx: Any, params: Any) -> mcp_types.ElicitResult:
            return await handle_mcp_elicitation_request(
                connector_id=self.conn.id,
                params=params,
                timeout_seconds=float(self.conn.timeout_seconds or 120.0),
                tool_name=self._active_tool_name,
                on_elicitation=self._active_on_elicitation,
                cancel_event=self._active_cancel_event,
            )

        try:
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w, message_handler=_on_msg, elicitation_callback=_on_elicit) as session:
                    await asyncio.wait_for(session.initialize(), timeout=self.conn.timeout_seconds)
                    while True:
                        action, args, fut = await self.queue.get()
                        if action == "close" or self._dirty:
                            if not fut.done():
                                if self._dirty and action != "close":
                                    fut.set_exception(RuntimeError("Actor marked dirty due to tool schema change"))
                                else:
                                    fut.set_result(None)
                            self.queue.task_done()
                            break
                        elif action == "list_tools":
                            try:
                                tools_res = await asyncio.wait_for(session.list_tools(), timeout=self.conn.timeout_seconds)
                                if not fut.done():
                                    fut.set_result(tools_res)
                            except Exception as ex:
                                if not fut.done():
                                    fut.set_exception(ex)
                        elif action == "call_tool":
                            tool_name, arguments, on_elicit_cb, cancel_ev = args
                            self._active_tool_name = tool_name
                            self._active_on_elicitation = on_elicit_cb
                            self._active_cancel_event = cancel_ev
                            try:
                                call_res = await asyncio.wait_for(
                                    session.call_tool(tool_name, arguments), timeout=self.conn.timeout_seconds
                                )
                                if not fut.done():
                                    fut.set_result((call_res, self.reuse_count))
                                self.reuse_count += 1
                            except Exception as ex:
                                if not fut.done():
                                    fut.set_exception(ex)
                            finally:
                                self._active_tool_name = None
                                self._active_on_elicitation = None
                                self._active_cancel_event = None
                        self.queue.task_done()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("StdioActor loop encountered error: %s", e)
            while not self.queue.empty():
                _, _, f = self.queue.get_nowait()
                if not f.done():
                    f.set_exception(e)


class MCPSessionPool:
    def __init__(self, project_root: str) -> None:
        self.project_root = str(project_root)
        self.job_guard = Win32JobGuard()
        self._actors: Dict[Tuple[str, str, int], StdioActor] = {}
        self._lock = asyncio.Lock()

    async def get_or_create_actor(self, conn: ConnectorItem) -> StdioActor:
        loop = asyncio.get_running_loop()
        chash = _compute_config_hash(conn)
        key = (conn.id, chash, id(loop))
        async with self._lock:
            actor = self._actors.get(key)
            if actor is not None and (actor.task.done() or actor._dirty):
                if not actor.task.done():
                    actor.task.cancel()
                self._actors.pop(key, None)
                actor = None
            if actor is None or actor.task.done():
                actor = StdioActor(conn, self.project_root, self.job_guard)
                self._actors[key] = actor
            return actor

    async def invalidate_connector(self, connector_id: str) -> None:
        async with self._lock:
            for key, actor in list(self._actors.items()):
                if key[0] == connector_id:
                    actor._dirty = True
                    if not actor.task.done():
                        actor.task.cancel()
                    self._actors.pop(key, None)

    async def list_tools(self, conn: ConnectorItem) -> Any:
        actor = await self.get_or_create_actor(conn)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        await actor.queue.put(("list_tools", (), fut))
        try:
            return await asyncio.wait_for(fut, timeout=conn.timeout_seconds)
        except Exception:
            if actor._dirty:
                actor = await self.get_or_create_actor(conn)
                fut = loop.create_future()
                await actor.queue.put(("list_tools", (), fut))
                return await asyncio.wait_for(fut, timeout=conn.timeout_seconds)
            raise

    async def call_tool(
        self,
        conn: ConnectorItem,
        tool_name: str,
        arguments: dict,
        *,
        on_elicitation: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> Tuple[Any, int]:
        actor = await self.get_or_create_actor(conn)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        payload = (tool_name, arguments, on_elicitation, cancel_event)
        await actor.queue.put(("call_tool", payload, fut))
        try:
            return await asyncio.wait_for(fut, timeout=conn.timeout_seconds)
        except Exception:
            if actor._dirty:
                actor = await self.get_or_create_actor(conn)
                fut = loop.create_future()
                await actor.queue.put(("call_tool", payload, fut))
                return await asyncio.wait_for(fut, timeout=conn.timeout_seconds)
            raise

    async def close_all(self) -> None:
        async with self._lock:
            for actor in list(self._actors.values()):
                if not actor.task.done():
                    loop = asyncio.get_running_loop()
                    fut = loop.create_future()
                    try:
                        await actor.queue.put(("close", (), fut))
                        await asyncio.wait_for(fut, timeout=2.0)
                    except Exception:
                        actor.task.cancel()
            self._actors.clear()
            self.job_guard.close()
