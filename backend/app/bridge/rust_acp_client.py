"""backend/app/bridge/rust_acp_client.py - Loopback WS client & Win32 Job sidecar for `agy-ultra-acp`."""
from __future__ import annotations

import asyncio, contextlib, json, logging, os, socket, sys, uuid  # noqa: E401
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
import websockets

from backend.app.contracts.agent_stream_v1 import AGENT_PROTOCOL_VERSION
from backend.app.mcp.win32_job import Win32JobGuard
from backend.app.services.stream_events import (
    ChatStreamEvent, CitationEvent, DoneEvent, ElicitationRequiredEvent, ErrorEvent, FailoverEvent,
    GroundingEvent, KeepaliveEvent, MediaEvent, SavedEvent, StartEvent, TextEvent, ThoughtEvent,
    ToolApprovalRequiredEvent, ToolCallEvent,
)

logger = logging.getLogger("gemini_bridge.rust_acp")
_EXE = "agy-ultra-acp.exe" if sys.platform == "win32" else "agy-ultra-acp"
_META_MAP: dict[str, Any] = {
    "citation": CitationEvent, "grounding": GroundingEvent, "media": MediaEvent, "done": DoneEvent,
    "tool_approval_required": ToolApprovalRequiredEvent, "elicitation_required": ElicitationRequiredEvent, "tool_call": ToolCallEvent,
}


def resolve_rust_acp_binary(explicit_path: str | Path | None = None) -> Path | None:
    sib = Path(__file__).resolve().parents[3].parent / "agy-ultra-acp" / "target"
    env_p = os.environ.get("GEMINI_RUST_ACP_BIN", "").strip()
    cands = ([Path(explicit_path)] if explicit_path else []) + ([Path(env_p)] if env_p else []) + [sib / "debug" / _EXE, sib / "release" / _EXE]
    return next((p.resolve() for p in cands if p.is_file()), None)


def _dict_to_stream_event(ev: dict[str, Any], conv_id: str = "", trace_id: str = "") -> ChatStreamEvent:
    et, meta = str(ev.get("type") or ""), (ev["metadata"] if isinstance(ev.get("metadata"), dict) else {})
    if et in _META_MAP:
        return _META_MAP[et](metadata=meta)
    if et == "start":
        return StartEvent(str(ev.get("conversation_id") or conv_id), ev.get("user_message_id", 0), str(ev.get("trace_id") or trace_id))
    if et in ("thought", "text"):
        return ThoughtEvent(delta=str(ev.get("delta") or "")) if et == "thought" else TextEvent(delta=str(ev.get("delta") or ""))
    if et == "failover":
        return FailoverEvent(payload={k: v for k, v in ev.items() if k != "type"})
    if et == "saved":
        return SavedEvent(assistant_message_id=ev.get("assistant_message_id", 0))
    return ErrorEvent(error=str(ev.get("error") or "Unknown Rust ACP error")) if et == "error" else KeepaliveEvent()


class RustAcpBridgeClient:
    """Client & sidecar supervisor speaking `agy.stream.v1` JSON-RPC 2.0 over loopback WS."""

    def __init__(self, *, ws_url: str | None = None, binary_path: str | Path | None = None,
                 token: str | None = None, auto_spawn: bool = True, startup_timeout: float = 8.0) -> None:
        self._explicit_ws_url = ws_url.strip() if isinstance(ws_url, str) and ws_url.strip() else None
        self._explicit_bin, self._token, self.auto_spawn, self.startup_timeout = binary_path, token, auto_spawn, float(startup_timeout)
        self._proc: asyncio.subprocess.Process | None = None
        self._drain_tasks: list[asyncio.Task[None]] = []
        self._spawned_ws_url: str | None = None
        self._job_guard: Win32JobGuard | None = None
        self.win32_job_bound: bool = False
        self._lock = asyncio.Lock()

    @property
    def binary_path(self) -> str | None:
        p = resolve_rust_acp_binary(self._explicit_bin)
        return str(p) if p is not None else None

    @property
    def ws_url(self) -> str | None:
        env_ws = os.environ.get("GEMINI_RUST_ACP_WS_URL", "").strip()
        return (self._explicit_ws_url or env_ws) or (self._spawned_ws_url if (self._proc and self._proc.returncode is None) else None)

    async def ensure_running(self) -> str:
        if self.ws_url:
            return self.ws_url
        async with self._lock:
            if self.ws_url:
                return self.ws_url
            if not self.auto_spawn:
                raise ConnectionError("Rust ACP WS URL not configured and auto_spawn=False")
            bin_p = resolve_rust_acp_binary(self._explicit_bin)
            if bin_p is None:
                raise FileNotFoundError("Compiled agy-ultra-acp binary not found")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                port = int(s.getsockname()[1])
            env = {**os.environ, **({"GEMINI_LOCAL_API_TOKEN": self._token} if self._token else {})}
            proc = await asyncio.create_subprocess_exec(str(bin_p), "serve-ws", "--bind", f"127.0.0.1:{port}", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env)
            self._proc, self._job_guard = proc, Win32JobGuard()
            self.win32_job_bound = bool(self._job_guard.assign_pid(proc.pid))

            async def _read_banner() -> str:
                assert proc.stdout is not None
                while raw_ln := await proc.stdout.readline():
                    ln = raw_ln.decode("utf-8", errors="replace").strip()
                    if "AGY_WS_LISTENING=" in ln:
                        return ln.split("AGY_WS_LISTENING=", 1)[1].strip()
                raise RuntimeError("agy-ultra-acp exited before AGY_WS_LISTENING")

            self._spawned_ws_url = await asyncio.wait_for(_read_banner(), timeout=self.startup_timeout)
            with contextlib.suppress(Exception):
                pt = getattr(proc, "_transport", None)
                if pt is not None and pt.get_pipe_transport(1) is not None:
                    pt.get_pipe_transport(1).close()
            return self._spawned_ws_url

    def _headers_for(self, trace_id: str = "") -> tuple[str, dict[str, str]]:
        c = trace_id.strip().lower()
        tid = c if (len(c) == 32 and all(ch in "0123456789abcdef" for ch in c)) else uuid.uuid4().hex
        hdrs = {"X-Trace-Id": tid, "traceparent": f"00-{tid}-0123456789abcdef-01"}
        tok = (self._token if self._token is not None else os.environ.get("GEMINI_LOCAL_API_TOKEN", "")).strip()
        if tok:
            hdrs.update({"X-Gemini-Local-Token": tok, "Authorization": f"Bearer {tok}"})
        return tid, hdrs

    async def _rpc_call(self, method: str, params: dict[str, Any], *, trace_id: str = "", require_spawn: bool = True) -> dict[str, Any]:
        url = await self.ensure_running() if require_spawn else (self.ws_url or "")
        if not url:
            raise ConnectionError("No active Rust ACP WS URL")
        tid, hdrs = self._headers_for(trace_id)
        req_id = f"rpc_{uuid.uuid4().hex[:8]}"
        async with websockets.connect(url, origin="http://127.0.0.1:5173", additional_headers=hdrs, proxy=None, open_timeout=4.0) as ws:  # type: ignore[arg-type]
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}))
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                if msg.get("id") == req_id:
                    if "error" in msg:
                        raise RuntimeError(str(msg["error"]))
                    return {"protocol_version": AGENT_PROTOCOL_VERSION, "trace_id": tid, **(msg.get("result") or {})}

    async def ping(self, trace_id: str = "") -> dict[str, Any]:
        return await self._rpc_call("ping", {}, trace_id=trace_id, require_spawn=True)

    async def forward_permission(self, approval_id: str, decision: str) -> bool:
        with contextlib.suppress(Exception):
            return bool(self.ws_url and (await self._rpc_call("session/respond_permission", {"approval_id": approval_id, "decision": decision}, require_spawn=False)).get("ok"))
        return False

    async def forward_elicitation(self, elicitation_id: str, action: str, content: dict[str, Any] | None = None) -> bool:
        with contextlib.suppress(Exception):
            return bool(self.ws_url and (await self._rpc_call("elicitation/respond", {"elicitation_id": elicitation_id, "action": action, "content": content}, require_spawn=False)).get("ok"))
        return False

    async def stream_prompt(self, req_dict: dict[str, Any], *, cancel_event: asyncio.Event, trace_id: str = "") -> AsyncIterator[ChatStreamEvent]:
        url, (tid, hdrs) = await self.ensure_running(), self._headers_for(trace_id)
        req_id, conv_id = f"turn_{tid[:8]}", str(req_dict.get("conversation_id") or "")
        async with websockets.connect(url, origin="http://127.0.0.1:5173", additional_headers=hdrs, proxy=None, open_timeout=4.0) as ws:  # type: ignore[arg-type]
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": "session/prompt", "params": {**req_dict, "trace_id": tid}}))
            recv_t: asyncio.Task[Any] | None = None
            try:
                while True:
                    if cancel_event.is_set():
                        with contextlib.suppress(Exception):
                            await ws.send(json.dumps({"jsonrpc": "2.0", "id": "cancel_1", "method": "session/cancel", "params": {}}))
                        break
                    recv_t = recv_t or asyncio.create_task(ws.recv())
                    done, _ = await asyncio.wait({recv_t}, timeout=0.25)
                    if not done:
                        continue
                    frame, recv_t = json.loads(recv_t.result()), None
                    if frame.get("method") == "session/update":
                        ev_obj = _dict_to_stream_event((frame.get("params") or {}).get("event") or {}, conv_id, tid)
                        yield ev_obj
                        if isinstance(ev_obj, (SavedEvent, ErrorEvent)):
                            break
                    elif frame.get("id") == req_id:
                        if "error" in frame:
                            yield ErrorEvent(error=str(frame["error"]))
                        break
            finally:
                if recv_t and not recv_t.done():
                    recv_t.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await recv_t

    async def shutdown(self) -> None:
        for t in self._drain_tasks:
            t.cancel()
        if self._drain_tasks:
            with contextlib.suppress(Exception):
                await asyncio.gather(*self._drain_tasks, return_exceptions=True)
        self._drain_tasks.clear()
        if self._proc:
            if self._proc.returncode is None:
                with contextlib.suppress(Exception):
                    self._proc.terminate()
                    await asyncio.wait_for(self._proc.wait(), timeout=2.0)
                if self._proc.returncode is None:
                    with contextlib.suppress(Exception):
                        self._proc.kill()
            tr = getattr(self._proc, "_transport", None)
            if tr is not None:
                with contextlib.suppress(Exception):
                    tr.close()
        self._proc, self._spawned_ws_url, self.win32_job_bound = None, None, False
        if self._job_guard:
            self._job_guard.close()
            self._job_guard = None
