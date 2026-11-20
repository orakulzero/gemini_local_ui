"""backend/tests/test_m8_rust_acp_unification.py - M8 Rust agy-ultra-acp Subprocess WS Bridge & Feature-Flag tests."""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
import socket
from typing import Any
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.rust_acp_client import RustAcpBridgeClient
from backend.app.bridge.session import SessionManager
from backend.app.bridge.streaming_parser import StreamChunk
from backend.app.main import app
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository


async def _maybe_await(val: Any) -> Any:
    return await val if inspect.isawaitable(val) else val


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _resolve_rust_binary() -> Path:
    base = Path(__file__).resolve().parents[3] / "agy-ultra-acp" / "target"
    for candidate in [base / "debug" / "agy-ultra-acp.exe", base / "release" / "agy-ultra-acp.exe"]:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Compiled agy-ultra-acp.exe not found under {base}")


@pytest.fixture
def m8_context(tmp_path: Path):
    import backend.app.main as main_mod

    orig = (main_mod.db, main_mod.repository, main_mod.session_manager)
    orig_st = (
        getattr(app.state, "db", None),
        getattr(app.state, "repository", None),
        getattr(app.state, "session_manager", None),
        getattr(app.state, "active_agent_engine", "python"),
        getattr(app.state, "rust_acp_client", None),
    )
    db = Database(tmp_path / "test_m8.db")
    asyncio.run(db.init_schema(Path(__file__).parent.parent / "app" / "storage" / "schema.sql"))
    repo = ConversationRepository(db)
    sm = SessionManager(base_url="http://127.0.0.1:8999")
    sm.context.cookies, sm.context.access_token = {"__Secure-1PSID": "mock_psid"}, "mock_token"
    main_mod.db, main_mod.repository, main_mod.session_manager = db, repo, sm
    app.state.db, app.state.repository, app.state.session_manager = db, repo, sm
    app.state.active_agent_engine = "python"
    try:
        yield TestClient(app, base_url="http://127.0.0.1:8000")
    finally:
        asyncio.run(db.close())
        main_mod.db, main_mod.repository, main_mod.session_manager = orig
        app.state.db, app.state.repository, app.state.session_manager = orig_st[0], orig_st[1], orig_st[2]
        app.state.active_agent_engine = orig_st[3]
        app.state.rust_acp_client = orig_st[4]


@pytest.mark.asyncio
async def test_real_rust_acp_serve_ws_subprocess_ping_and_traceparent(monkeypatch):
    bin_path = _resolve_rust_binary()
    monkeypatch.setenv("GEMINI_RUST_ACP_BIN", str(bin_path))
    port = _find_free_port()
    proc = await asyncio.create_subprocess_exec(
        str(bin_path),
        "serve-ws",
        "--bind",
        f"127.0.0.1:{port}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    client: RustAcpBridgeClient | None = None
    try:
        assert proc.stdout is not None
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=8.0)
        banner = line.decode("utf-8", errors="replace").strip()
        assert f"ws://127.0.0.1:{port}" in banner

        client = RustAcpBridgeClient(ws_url=f"ws://127.0.0.1:{port}")
        trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
        pong = await client.ping(trace_id=trace_id)
        assert pong.get("pong") is True
        assert pong.get("engine") == "agy-ultra-acp"
        assert pong.get("protocol_version") == "agy.stream.v1"
        assert pong.get("trace_id") == trace_id
    finally:
        if client is not None and hasattr(client, "shutdown"):
            await _maybe_await(client.shutdown())
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


def test_acp_runtime_endpoints_and_python_orchestrator_fallback(m8_context, monkeypatch):
    client = m8_context
    bin_path = _resolve_rust_binary()
    monkeypatch.setenv("GEMINI_RUST_ACP_BIN", str(bin_path))
    rust_cli = RustAcpBridgeClient()
    app.state.rust_acp_client = rust_cli
    try:
        r_status = client.get("/api/acp/status")
        assert r_status.status_code == 200
        st = r_status.json()
        assert st["protocol_version"] == "agy.stream.v1" and st["active_engine"] == "python"
        assert st["available"] is True

        r_sw = client.post("/api/acp/engine", json={"engine": "rust_acp"})
        assert r_sw.status_code == 200 and r_sw.json()["active_engine"] == "rust_acp"

        r_ping = client.post("/api/acp/ping")
        assert r_ping.status_code == 200 and r_ping.json().get("ok") is True

        r_back = client.post("/api/acp/engine", json={"engine": "python"})
        assert r_back.status_code == 200 and r_back.json()["active_engine"] == "python"

        # Unreachable Rust WS endpoint falls back cleanly to Python orchestrator
        unreachable_port = _find_free_port()
        app.state.rust_acp_client = RustAcpBridgeClient(ws_url=f"ws://127.0.0.1:{unreachable_port}")
        monkeypatch.setenv("GEMINI_RUST_ACP_WS_URL", f"ws://127.0.0.1:{unreachable_port}")

        async def py_fallback_stream(*a, **kw):
            yield StreamChunk(type="text", delta="Python fallback succeeded.")
            yield StreamChunk(
                type="done",
                delta="",
                metadata={"cid": "c_fb", "rid": "r_fb", "rcid": "rc_fb", "full_text": "Python fallback succeeded."},
            )

        with patch.object(GeminiWebClient, "generate_stream", side_effect=py_fallback_stream):
            resp = client.post(
                "/api/chat/generate",
                json={"prompt": "Test fallback", "model": "gemini-2.5-flash", "engine": "rust_acp"},
            )
        assert resp.status_code == 200
        events = [json.loads(ln[5:].strip()) for ln in resp.text.splitlines() if ln.strip().startswith("data:") and ln[5:].strip()]
        assert any(ev.get("type") == "text" and "Python fallback succeeded." in ev.get("delta", "") for ev in events)
    finally:
        asyncio.run(_maybe_await(rust_cli.shutdown()))
