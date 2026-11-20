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
from typing import Any, Dict, Tuple

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from backend.app.mcp.win32_job import Win32JobGuard
from backend.app.storage.workspace_models import ConnectorItem

logger = logging.getLogger("gemini_mcp.session_pool")


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

        try:
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w, message_handler=_on_msg) as session:
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
                            tool_name, arguments = args
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

    async def call_tool(self, conn: ConnectorItem, tool_name: str, arguments: dict) -> Tuple[Any, int]:
        actor = await self.get_or_create_actor(conn)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        await actor.queue.put(("call_tool", (tool_name, arguments), fut))
        try:
            return await asyncio.wait_for(fut, timeout=conn.timeout_seconds)
        except Exception:
            if actor._dirty:
                actor = await self.get_or_create_actor(conn)
                fut = loop.create_future()
                await actor.queue.put(("call_tool", (tool_name, arguments), fut))
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
