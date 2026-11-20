"""backend/tests/test_stage55_hardening.py - Stage 5.5 Pre-M6 Hardening regression suite."""
from __future__ import annotations
import asyncio
import time
import warnings
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import httpx
import pytest
from starlette.testclient import TestClient

from backend.app.bridge.native_messaging_host import handle_native_message
from backend.app.bridge.wreq_transport import WreqTransportClient
from backend.app.main import app
from backend.app.mcp.approval_gate import MAX_PENDING_APPROVALS, ToolApprovalGate
from backend.app.security.mcp_pinning import MCPSchemaDriftError, MCPSchemaPinStore
from backend.app.security.path_sandbox import validate_safe_path, validate_safe_slug


def test_mcp_schema_pinning_tamper_and_invariants(tmp_path: Path):
    pin_file = tmp_path / "pins.json"
    store = MCPSchemaPinStore(pin_file_path=pin_file)
    tool = {"name": "calc", "description": "Math", "input_schema": {"type": "object"}, "annotations": {"readOnlyHint": False}, "output_schema": {"type": "string"}}

    store.verify_or_pin("conn_1", [tool])
    assert pin_file.read_text("utf-8").startswith("dpapi_v2:")

    # Changing annotations or output_schema causes drift
    t_mod1 = {**tool, "annotations": {"readOnlyHint": True}}
    with pytest.raises(MCPSchemaDriftError):
        store.assert_unmodified("conn_1", [t_mod1])

    t_mod2 = {**tool, "output_schema": {"type": "number"}}
    with pytest.raises(MCPSchemaDriftError):
        store.assert_unmodified("conn_1", [t_mod2])

    # Corrupt ciphertext on disk -> fails closed
    pin_file.write_text("dpapi_v2:corrupted_ciphertext_data", "utf-8")
    tampered_store = MCPSchemaPinStore(pin_file_path=pin_file)
    with pytest.raises(MCPSchemaDriftError):
        tampered_store.assert_unmodified("conn_1", [tool])

    # Explicit repin heals
    tampered_store.repin("conn_1", [tool])
    assert tampered_store.verify_or_pin("conn_1", [tool])[0] == "verified"


def test_path_sandbox_dos_and_symlinks(tmp_path: Path):
    root = tmp_path / "sandbox"
    root.mkdir()

    # Reject DOS devices, trailing dot/space, UNC/device prefixes
    for bad in ["NUL.png", "CON", "aux.json", "COM1", "file.txt.", "file.txt ", "//server/share", "//?/C:/Windows"]:
        with pytest.raises(ValueError):
            validate_safe_path(root / bad if not bad.startswith("//") else bad, allowed_roots=[root])

    # Reject bad slugs
    for bad_slug in ["CON", "a.", "bad slug", "AUX"]:
        with pytest.raises(ValueError):
            validate_safe_slug(bad_slug)

    # Symlink escape check
    try:
        out_target = tmp_path / "outside.txt"
        out_target.write_text("outside")
        link = root / "symlink_escape"
        link.symlink_to(out_target)
        with pytest.raises(ValueError):
            validate_safe_path(link, allowed_roots=[root])
    except (OSError, NotImplementedError):
        pass


def test_origin_null_and_sec_fetch_site_guard():
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/health", headers={"Origin": "null"}).status_code == 403
    assert client.get("/health", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/health", headers={"Origin": "tauri://localhost"}).status_code == 200


@pytest.mark.asyncio
async def test_hitl_approval_gate_lifecycle_and_cancellation():
    gate = ToolApprovalGate()

    # Zero DeprecationWarning when called without running loop
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always", DeprecationWarning)
        gate.create_pending("aid_0", "c1", "t1", "c1__t1", {})
    assert not any(issubclass(item.category, DeprecationWarning) for item in w)

    # Evicts oldest when exceeding MAX_PENDING_APPROVALS
    for i in range(1, MAX_PENDING_APPROVALS + 2):
        gate.create_pending(f"aid_{i}", "c1", "t1", "c1__t1", {})
    assert "aid_0" not in gate._pending
    assert f"aid_{MAX_PENDING_APPROVALS + 1}" in gate._pending

    # Cancel event aborts in <0.5s returning "deny"
    ev = asyncio.Event()
    aid = "aid_cancel"
    gate.create_pending(aid, "c1", "t1", "c1__t1", {})
    ev.set()
    t0 = time.monotonic()
    decision = await gate.wait_for_decision(aid, timeout=120.0, cancel_event=ev)
    assert decision == "deny" and (time.monotonic() - t0) < 0.5
    assert aid not in gate._pending


def test_nmh_single_writer_priority(tmp_path: Path, monkeypatch):
    vault_file = tmp_path / "v.enc"
    cookie_payload = {"type": "sync_session", "cookie": "SAPISID=x; __Secure-1PSID=y"}

    # 1. Gateway 200 OK -> Uvicorn sole writer, file NOT written directly
    class MockSuccessClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, *a, **k): return MagicMock(status_code=200)

    monkeypatch.setattr(httpx, "Client", MockSuccessClient)
    res1 = handle_native_message(cookie_payload, vault_path=vault_file, notify_gateway=True)
    assert res1.get("ok") is True and res1.get("gateway_notified") is True and res1.get("saved_to_vault") is True
    assert vault_file.exists() is False

    # 2. Gateway fails -> fallback directly saves to vault
    class MockFailingClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, *a, **k): raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "Client", MockFailingClient)
    res2 = handle_native_message(cookie_payload, vault_path=vault_file, notify_gateway=True)
    assert res2.get("ok") is True and res2.get("gateway_notified") is False and res2.get("saved_to_vault") is True
    assert vault_file.exists() is True


@pytest.mark.asyncio
async def test_wreq_transport_idle_recycling_and_extensions():
    cli = WreqTransportClient(timeout=15.0)
    raw1 = cli._get_raw_client()
    assert raw1 is not None

    # Advance idle time past 35s threshold -> allocates new client
    cli._last_used_monotonic = time.monotonic() - 40.0
    raw2 = cli._get_raw_client()
    assert raw2 is not raw1

    # Mock raw client verifying content & extensions
    mock_raw = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status.as_int.return_value = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_raw.post = AsyncMock(return_value=mock_resp)
    cli._client = mock_raw

    adapter = await cli._execute_request(
        "POST", "https://api.test/v1", content=b'{"query": 1}', extensions={"account_id": "a1"}
    )
    assert adapter.request.extensions.get("account_id") == "a1"
    assert adapter.status_code == 200
