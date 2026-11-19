"""Tests for Gemini 4 Hydrogen routing, aliases, admin session models, and Flash4p0 fallback."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.exceptions import AuthenticationError
from backend.app.bridge.models import (
    DEFAULT_MODELS,
    HeaderBuilder,
    KNOWN_MODEL_LABELS,
    ModelTier,
    OPENAI_MODEL_ALIASES,
    UI_PRIMARY_MODEL_IDS,
    extract_answered_by_label,
    resolve_model,
)
from backend.app.bridge.session import SessionContext
from backend.app.main import app


def _encode_wire_frame(payload: Any) -> bytes:
    """Encodes a JSON frame with Google's UTF-16 code-unit length prefix."""
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    utf16_units = len(body.encode("utf-16-le")) // 2
    return f"{utf16_units}\n{body}\n".encode("utf-8")


def _make_stream_body(text: str, is_final: bool = True, answered_by: str | None = None) -> bytes:
    """Builds a minimal anti-XSSI prefixed StreamGenerate response body."""
    candidate: list[Any] = [
        "rc_test_1",
        [text] if text else [],
        None,
        None,
        None,
        None,
        None,
        None,
        [2] if is_final else [1],
    ]
    if answered_by:
        candidate.extend([None, ["meta", answered_by]])
    inner = [None, ["c_test_1", "r_test_1"], None, None, [candidate]]
    frame = [[["wrb.fr", None, json.dumps(inner, ensure_ascii=False)]]]
    return b")]}'\n" + _encode_wire_frame(frame)


def test_hydrogen_model_registry_and_aliases() -> None:
    """Verifies DEFAULT_MODELS, UI_PRIMARY_MODEL_IDS, KNOWN_MODEL_LABELS, and Hydrogen aliases."""
    h_model = DEFAULT_MODELS["gemini-4-hydrogen"]
    hex_model = DEFAULT_MODELS["56fdd199312815e2"]

    assert h_model == hex_model
    assert h_model.id == "56fdd199312815e2"
    assert h_model.display_name == "4 Hydrogen"
    assert h_model.description == "Everyday help · Fast execution"
    assert h_model.model_number == 1
    assert h_model.thinking_flag == 1
    assert h_model.capacity_tail == 12
    assert h_model.tier == ModelTier.FREE
    assert h_model.mode_tag == "Flash4p0"
    assert h_model.is_default is True

    assert UI_PRIMARY_MODEL_IDS == [
        "gemini-4-hydrogen",
        "8c46e95b1a07cecc",
        "e6fa609c3fa255c0",
        "95a4c0abcba24364",
        "gemini-4-carbon",
    ]

    assert "4 Hydrogen" in KNOWN_MODEL_LABELS
    assert "4 Carbon" in KNOWN_MODEL_LABELS

    for alias in (
        "gemini-4-hydrogen",
        "hydrogen",
        "4-hydrogen",
        "4 hydrogen",
        "4 Hydrogen",
        "gemini-3.8-flash",
        "3.8-flash",
        "3.8 flash",
        "3.8 Flash",
    ):
        assert OPENAI_MODEL_ALIASES[alias] == "gemini-4-hydrogen"
        info = resolve_model(alias)
        assert info.id == "56fdd199312815e2"
        assert info.display_name == "4 Hydrogen"
        assert info.mode_tag == "Flash4p0"

    assert resolve_model("gemini-4-hydrogen-low").thinking_flag == 1
    assert resolve_model("gemini-4-hydrogen-medium").thinking_flag == 4
    assert resolve_model("gemini-4-hydrogen-high").thinking_flag == 2
    assert resolve_model("gemini-4-hydrogen-xhigh").thinking_flag == 3


def test_hydrogen_jspb_header_and_answered_by_label() -> None:
    """Verifies 25-element Flash4p0 JSPB header and label extraction for 4 Hydrogen / 4 Carbon."""
    parsed = json.loads(HeaderBuilder.build_model_jspb_header("gemini-4-hydrogen", "SESS-H4"))
    assert len(parsed) == 25
    assert parsed[4] == "56fdd199312815e2"
    assert parsed[11] == 12
    assert parsed[14] == 1
    assert parsed[15] == 1
    assert parsed[24] == "Flash4p0"

    for label in ("4 Hydrogen", "4 Carbon"):
        inner = [None, ["c1", "r1"], None, None, [["rc1", ["body"], None, None, None, None, None, None, [2], None, [label]]]]
        assert extract_answered_by_label(inner) == label


def test_api_models_and_admin_session_advertise_hydrogen() -> None:
    """Verifies /api/models deduplicates 56fdd199312815e2 to 4 Hydrogen and /admin/session advertises gemini-4-hydrogen."""
    with TestClient(app, base_url="http://testserver") as client:
        with patch.object(
            GeminiWebClient,
            "list_models",
            new=AsyncMock(return_value=[resolve_model("56fdd199312815e2")]),
        ):
            res_models = client.get("/api/models")
            assert res_models.status_code == 200
            models = res_models.json()["models"]
            h_entries = [m for m in models if m["id"] == "56fdd199312815e2"]
            assert len(h_entries) == 1
            assert h_entries[0]["display_name"] == "4 Hydrogen"
            assert h_entries[0]["is_default"] is True
            assert all("3.8 Flash" not in m["display_name"] for m in models)

        res_admin_get = client.get("/admin/session")
        assert res_admin_get.status_code == 200
        admin_models = res_admin_get.json()["models"]
        assert "gemini-4-hydrogen" in admin_models
        assert "gemini-3.8-flash" not in admin_models

        res_admin_post = client.post(
            "/admin/session",
            json={"cookie": "__Secure-1PSID=test_psid; __Secure-1PSIDTS=test_psidts"},
        )
        assert res_admin_post.status_code == 200
        post_models = res_admin_post.json()["models"]
        assert "gemini-4-hydrogen" in post_models
        assert "gemini-3.8-flash" not in post_models


def _build_mock_client(handler: Any) -> GeminiWebClient:
    ctx = SessionContext(
        cookies={"__Secure-1PSID": "mock_psid", "__Secure-1PSIDTS": "mock_psidts"},
        access_token="mock_at",
    )
    client = GeminiWebClient(session_context=ctx, base_url="https://gemini.google.com")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal_text",
    [
        "Я просто языковая модель",
        "Я вас не понимаю. Я просто языковая модель.",
        "Sorry, something went wrong.",
    ],
)
async def test_flash4p0_falls_back_once_on_canned_refusal(refusal_text: str) -> None:
    """Verifies Flash4p0 holds back canned refusals and retries once on 8c46e95b1a07cecc."""
    recorded_headers: list[list[Any]] = []
    recorded_inners: list[list[Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        jspb = json.loads(request.headers["x-goog-ext-525001261-jspb"])
        recorded_headers.append(jspb)
        from urllib.parse import parse_qs

        form = parse_qs(request.content.decode("utf-8"))
        f_req = json.loads(form["f.req"][0])
        inner = json.loads(f_req[1])
        recorded_inners.append(inner)
        if len(recorded_headers) == 1:
            return httpx.Response(200, content=_make_stream_body(refusal_text, is_final=True))
        return httpx.Response(200, content=_make_stream_body("Fallback answer works", is_final=True))

    client = _build_mock_client(handler)
    try:
        chunks = [c async for c in client.generate_stream("Hello", model="gemini-4-hydrogen")]
    finally:
        await client.close()

    full_text = "".join(c.delta for c in chunks if c.type == "text")
    assert full_text == "Fallback answer works"
    assert refusal_text not in full_text
    assert len(recorded_headers) == 2
    assert recorded_headers[0][4] == "56fdd199312815e2"
    assert recorded_headers[0][24] == "Flash4p0"
    assert recorded_headers[1][4] == "8c46e95b1a07cecc"
    assert recorded_headers[1][14] == 6
    assert recorded_headers[1][15] == 1
    assert len(recorded_headers[1]) == 17
    assert recorded_inners[1][64] is None
    assert recorded_inners[1][79] == 6
    assert recorded_inners[1][80] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["bridge_error", "transport_error", "empty_stream"])
async def test_flash4p0_falls_back_once_on_failures(failure_mode: str) -> None:
    """Verifies Flash4p0 retries once on 8c46e95b1a07cecc after bridge/transport error or empty stream."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            if failure_mode == "bridge_error":
                return httpx.Response(400, content=b"Bad request")
            if failure_mode == "transport_error":
                raise httpx.ConnectError("connection reset", request=request)
            return httpx.Response(200, content=_make_stream_body("", is_final=True))
        return httpx.Response(200, content=_make_stream_body("Recovered on fast slot", is_final=True))

    client = _build_mock_client(handler)
    try:
        chunks = [c async for c in client.generate_stream("Ping", model="gemini-4-hydrogen")]
    finally:
        await client.close()

    assert calls == 2
    assert "".join(c.delta for c in chunks if c.type == "text") == "Recovered on fast slot"


@pytest.mark.asyncio
async def test_flash4p0_does_not_retry_past_single_fallback_or_on_auth_error() -> None:
    """Verifies Flash4p0 retries at most once and propagates AuthenticationError immediately."""
    calls_fail = 0

    def always_transport_fail(request: httpx.Request) -> httpx.Response:
        nonlocal calls_fail
        calls_fail += 1
        raise httpx.ConnectError("down", request=request)

    client_fail = _build_mock_client(always_transport_fail)
    try:
        with pytest.raises(httpx.TransportError):
            async for _ in client_fail.generate_stream("Ping", model="gemini-4-hydrogen"):
                pass
    finally:
        await client_fail.close()
    assert calls_fail == 2

    calls_auth = 0

    def auth_fail(request: httpx.Request) -> httpx.Response:
        nonlocal calls_auth
        calls_auth += 1
        return httpx.Response(401, content=b"Unauthorized")

    client_auth = _build_mock_client(auth_fail)
    try:
        with pytest.raises(AuthenticationError):
            async for _ in client_auth.generate_stream("Ping", model="gemini-4-hydrogen"):
                pass
    finally:
        await client_auth.close()
    assert calls_auth == 1


@pytest.mark.asyncio
async def test_flash4p0_normal_short_and_long_streams_emit_without_fallback() -> None:
    """Verifies normal short and long Flash4p0 replies succeed in a single request."""
    for reply in ("OK", "A" * 240):
        calls = 0

        def handler(request: httpx.Request, body: str = reply) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=_make_stream_body(body, is_final=True))

        client = _build_mock_client(handler)
        try:
            chunks = [c async for c in client.generate_stream("Hi", model="gemini-4-hydrogen")]
        finally:
            await client.close()

        assert calls == 1
        assert "".join(c.delta for c in chunks if c.type == "text") == reply


def test_api_models_deduplicates_otaq7b_flash_lite_and_labels_auto() -> None:
    """Verifies /api/models labels cf41b0e0dd7d53e5 as Auto (Gemini) and never duplicates 3.5 Flash-Lite."""
    from backend.app.bridge.models import ModelInfo

    dup_auto = ModelInfo(
        id="cf41b0e0dd7d53e5",
        display_name="3.5 Flash-Lite",
        description="3.5 Flash-Lite",
        model_number=1,
        thinking_flag=1,
    )
    dup_name = ModelInfo(
        id="deadbeef12345678",
        display_name="3.5 Flash-Lite",
        description="Duplicate display name",
        model_number=6,
        thinking_flag=1,
    )
    with TestClient(app, base_url="http://testserver") as client:
        with patch.object(
            GeminiWebClient,
            "list_models",
            new=AsyncMock(return_value=[dup_auto, dup_name]),
        ):
            res = client.get("/api/models")
            assert res.status_code == 200
            models = res.json()["models"]
            flash_lite_entries = [m for m in models if m["display_name"] == "3.5 Flash-Lite"]
            assert len(flash_lite_entries) == 1
            assert flash_lite_entries[0]["id"] == "8c46e95b1a07cecc"
            auto_entries = [m for m in models if m["id"] == "cf41b0e0dd7d53e5"]
            if auto_entries:
                assert auto_entries[0]["display_name"] == "Auto (Gemini)"


def test_flow_status_returns_200_when_flow_client_missing() -> None:
    """Verifies GET /api/flow/status returns HTTP 200 structured JSON when FlowClient is None."""
    import backend.app.api.flow as flow_api

    with patch.object(flow_api, "FlowClient", None):
        with TestClient(app, base_url="http://testserver") as client:
            res = client.get("/api/flow/status")
            assert res.status_code == 200
            data = res.json()
            assert data["ok"] is True
            assert data["available"] is False
            assert data["authenticated"] is False
            assert data["status"] == "unavailable"
            assert data["detail"] == "flow_studio module not installed"
            assert data["engine"] == "google-flow-veo"
            assert "16:9" in data["supported_aspect_ratios"]
            assert "9:16" in data["supported_aspect_ratios"]


def test_chat_generate_persists_and_reloads_cid_rid_rcid_across_turns(tmp_path: Any) -> None:
    """Verifies /api/chat/generate persists (cid, rid, rcid) on Turn 1 and reloads them on Turn 2 via conversation_id."""
    import asyncio
    from backend.app.bridge.streaming_parser import StreamChunk
    from backend.app.storage.database import Database
    from backend.app.storage.repository import ConversationRepository

    db = Database(tmp_path / "test_multiturn_cid.db")
    asyncio.run(db.init_schema())
    repo = ConversationRepository(db)

    seen_calls: list[dict[str, Any]] = []

    async def fake_generate_stream(self: Any, prompt: str, **kwargs: Any) -> Any:
        call_idx = len(seen_calls) + 1
        seen_calls.append(
            {
                "prompt": prompt,
                "cid": kwargs.get("cid"),
                "rid": kwargs.get("rid"),
                "rcid": kwargs.get("rcid"),
            }
        )
        yield StreamChunk(type="text", delta=f"reply_{call_idx}")
        yield StreamChunk(
            type="done",
            delta="",
            metadata={
                "cid": f"c_stateful_1",
                "rid": f"r_turn_{call_idx}",
                "rcid": f"rc_turn_{call_idx}",
                "full_text": f"reply_{call_idx}",
            },
        )

    def _parse_sse(raw: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for line in raw.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        return events

    prev_db = getattr(app.state, "db", None)
    prev_repo = getattr(app.state, "repository", None)
    try:
        with patch.object(GeminiWebClient, "generate_stream", new=fake_generate_stream):
            with TestClient(app, base_url="http://testserver") as client:
                app.state.db = db
                app.state.repository = repo

                # Turn 1: fresh conversation
                r1 = client.post(
                    "/api/chat/generate",
                    json={"prompt": "2 + 2 = ?", "model": "gemini-4-hydrogen"},
                )
                assert r1.status_code == 200
                ev1 = _parse_sse(r1.text)
                start1 = next(e for e in ev1 if e["type"] == "start")
                saved1 = next(e for e in ev1 if e["type"] == "saved")
                conv_id = start1["conversation_id"]
                asst1_id = saved1["assistant_message_id"]
                assert asst1_id is not None

                # Verify Turn 1 persisted (cid, rid, rcid) in SQLite message metadata & conversation remote_conversation_id
                asst1 = asyncio.run(repo.get_message(asst1_id))
                assert asst1 is not None
                assert asst1.metadata.get("cid") == "c_stateful_1"
                assert asst1.metadata.get("rid") == "r_turn_1"
                assert asst1.metadata.get("rcid") == "rc_turn_1"
                conv_row = asyncio.run(repo.get_conversation(conv_id))
                assert conv_row is not None
                assert conv_row.remote_conversation_id == "c_stateful_1"

                # Turn 2: Web UI sends conversation_id + parent_message_id (no explicit cid/rid/rcid)
                r2 = client.post(
                    "/api/chat/generate",
                    json={
                        "conversation_id": conv_id,
                        "parent_message_id": asst1_id,
                        "prompt": "Multiply that result by 10",
                        "model": "gemini-4-hydrogen",
                    },
                )
                assert r2.status_code == 200
                assert len(seen_calls) == 2
                assert seen_calls[0]["cid"] is None
                assert seen_calls[1]["cid"] == "c_stateful_1"
                assert seen_calls[1]["rid"] == "r_turn_1"
                assert seen_calls[1]["rcid"] == "rc_turn_1"

                # Turn 3: conversation_id only (no parent_message_id) still reloads latest Turn 2 state
                r3 = client.post(
                    "/api/chat/generate",
                    json={
                        "conversation_id": conv_id,
                        "prompt": "Add 5",
                        "model": "gemini-4-hydrogen",
                    },
                )
                assert r3.status_code == 200
                assert len(seen_calls) == 3
                assert seen_calls[2]["cid"] == "c_stateful_1"
                assert seen_calls[2]["rid"] == "r_turn_2"
                assert seen_calls[2]["rcid"] == "rc_turn_2"
    finally:
        app.state.db = prev_db
        app.state.repository = prev_repo
        asyncio.run(db.close())


def test_envelope_error_null_message_never_produces_literal_none() -> None:
    """Verifies null/None envelope error messages never produce 'GeminiBridgeError: None'."""
    from backend.app.bridge.exceptions import (
        GeminiBridgeError,
        UsageLimitExceededError,
        map_google_error_code,
    )
    from backend.app.bridge.streaming_parser import StreamingDeltaAccumulator

    # Direct constructor guards
    err_none = GeminiBridgeError(None, code=1055)
    assert str(err_none) != "None"
    assert "1055" in str(err_none)

    err_str_none = GeminiBridgeError("None", code=1055)
    assert str(err_str_none) != "None"
    assert "1055" in str(err_str_none)

    # Wire envelope [..., ..., [[..., [code, None]]]] (exact root cause of GeminiBridgeError: None)
    acc = StreamingDeltaAccumulator()
    frame = [[["wrb.fr", "StreamGenerate", None, None, None, [None, None, [["meta", [1055, None]]]]]]]
    with pytest.raises(GeminiBridgeError) as exc_info:
        acc.process_frame(frame)
    assert exc_info.value.code == 1055
    assert str(exc_info.value) != "None"
    assert "1055" in str(exc_info.value)

    # Auth and rate-limit codes mapped cleanly even with None / "None" messages
    for auth_code in (7, 16, 401, 403):
        with pytest.raises(AuthenticationError) as auth_exc:
            map_google_error_code(auth_code, "None")
        assert str(auth_exc.value) != "None"
        assert auth_exc.value.code == auth_code

    for quota_code in (1037, 429, 8):
        with pytest.raises(UsageLimitExceededError) as quota_exc:
            map_google_error_code(quota_code, None)
        assert str(quota_exc.value) != "None"


def test_limits_reports_active_flag_and_degraded_scrape_health(tmp_path: Any) -> None:
    """Verifies GET /api/limits returns active/is_active for active_account_id and degraded health on scrape failure."""
    import asyncio
    from backend.app.bridge.session import SessionHealthStatus, SessionManager
    from backend.app.storage.database import Database
    from backend.app.storage.vault import AccountProfile, CredentialVault, VaultData

    db = Database(tmp_path / "test_limits_active.db")
    asyncio.run(db.init_schema())
    vault = CredentialVault(tmp_path / "test_limits_vault.enc")
    vault.store_vault_data(
        VaultData(
            version=2,
            active_account_id="acc_working",
            accounts={
                "acc_stale": AccountProfile(
                    id="acc_stale",
                    name="Stale Ultra",
                    cookies={"__Secure-1PSID": "psid_stale"},
                    tier="ultra",
                    priority=1,
                ),
                "acc_working": AccountProfile(
                    id="acc_working",
                    name="Firefox_Ultra",
                    cookies={"__Secure-1PSID": "psid_ok"},
                    snlm0e="valid_at_token",
                    tier="ultra",
                    priority=2,
                ),
            },
        )
    )
    sm = SessionManager(vault=vault)
    sm.active_account_id = "acc_working"
    from backend.app.bridge.session import AccountSession

    sm.sessions["acc_stale"] = AccountSession(
        account_id="acc_stale",
        profile=vault.get_account("acc_stale"),
        context=SessionContext(
            cookies={"__Secure-1PSID": "psid_stale"},
            access_token=None,
            status=SessionHealthStatus.HEALTHY,
            last_error="Transient scrape failure",
            account_id="acc_stale",
        ),
    )
    sm.sessions["acc_working"] = AccountSession(
        account_id="acc_working",
        profile=vault.get_account("acc_working"),
        context=SessionContext(
            cookies={"__Secure-1PSID": "psid_ok"},
            access_token="valid_at_token",
            status=SessionHealthStatus.HEALTHY,
            last_error=None,
            account_id="acc_working",
        ),
    )

    prev_db = getattr(app.state, "db", None)
    prev_sm = getattr(app.state, "session_manager", None)
    prev_vault = getattr(app.state, "vault", None)
    try:
        with TestClient(app, base_url="http://testserver") as client:
            app.state.db = db
            app.state.session_manager = sm
            app.state.vault = vault

            res = client.get("/api/limits")
            assert res.status_code == 200
            data = res.json()
            assert data["active_account_id"] == "acc_working"
            by_id = {a["account_id"]: a for a in data["accounts"]}

            assert by_id["acc_working"]["active"] is True
            assert by_id["acc_working"]["is_active"] is True
            assert by_id["acc_working"]["authenticated"] is True
            assert by_id["acc_working"]["has_access_token"] is True
            assert by_id["acc_working"]["health_status"] == "healthy"

            assert by_id["acc_stale"]["active"] is False
            assert by_id["acc_stale"]["is_active"] is False
            assert by_id["acc_stale"]["authenticated"] is False
            assert by_id["acc_stale"]["has_access_token"] is False
            assert by_id["acc_stale"]["health_status"] == "degraded"
            assert by_id["acc_stale"]["last_error"] == "Transient scrape failure"
    finally:
        app.state.db = prev_db
        app.state.session_manager = prev_sm
        app.state.vault = prev_vault
        asyncio.run(db.close())


@pytest.mark.parametrize("err_kind", ["bridge", "auth", "inconsistent"])
def test_chat_generate_fails_over_on_bridge_and_auth_errors_with_clean_cid(
    tmp_path: Any, err_kind: str
) -> None:
    """Verifies /api/chat/generate fails over to backup account on GeminiBridgeError/Auth/ModelInconsistent with cid=None."""
    import asyncio
    from backend.app.bridge.exceptions import GeminiBridgeError, ModelInconsistentError
    from backend.app.bridge.session import AccountSession, SessionHealthStatus, SessionManager
    from backend.app.bridge.streaming_parser import StreamChunk
    from backend.app.storage.database import Database
    from backend.app.storage.repository import ConversationRepository
    from backend.app.storage.vault import AccountProfile, CredentialVault, FailoverConfig, VaultData

    db = Database(tmp_path / f"test_failover_{err_kind}.db")
    asyncio.run(db.init_schema())
    repo = ConversationRepository(db)
    vault = CredentialVault(tmp_path / f"test_failover_{err_kind}.enc")
    vault.store_vault_data(
        VaultData(
            version=2,
            active_account_id="acc_broken",
            failover_config=FailoverConfig(enabled=True, strategy="priority_tier"),
            accounts={
                "acc_broken": AccountProfile(
                    id="acc_broken",
                    name="Broken Ultra",
                    cookies={"__Secure-1PSID": "psid_broken"},
                    snlm0e="stale_at",
                    tier="ultra",
                    priority=2,
                ),
                "acc_healthy": AccountProfile(
                    id="acc_healthy",
                    name="Healthy Ultra",
                    cookies={"__Secure-1PSID": "psid_ok"},
                    snlm0e="good_at",
                    tier="ultra",
                    priority=1,
                ),
            },
        )
    )
    sm = SessionManager(vault=vault)
    sm.active_account_id = "acc_broken"
    for acc_id in ("acc_broken", "acc_healthy"):
        prof = vault.get_account(acc_id)
        sm.sessions[acc_id] = AccountSession(
            account_id=acc_id,
            profile=prof,
            context=SessionContext(
                cookies=dict(prof.cookies),
                access_token=prof.snlm0e,
                status=SessionHealthStatus.HEALTHY,
                account_tier="ultra",
                account_id=acc_id,
            ),
        )

    calls: list[dict[str, Any]] = []

    async def fake_stream(self: Any, prompt: str, **kwargs: Any) -> Any:
        calls.append(
            {
                "account_id": self.account_id,
                "cid": kwargs.get("cid"),
                "rid": kwargs.get("rid"),
                "rcid": kwargs.get("rcid"),
            }
        )
        if self.account_id == "acc_broken":
            if err_kind == "bridge":
                raise GeminiBridgeError("Google internal error code: 1055", code=1055)
            if err_kind == "auth":
                raise AuthenticationError("Session expired", code=7)
            raise ModelInconsistentError("Model mismatch")
        yield StreamChunk(type="text", delta="Привет! Чем помочь?")
        yield StreamChunk(
            type="done",
            delta="",
            metadata={
                "cid": "c_backup_ok",
                "rid": "r_backup_ok",
                "rcid": "rc_backup_ok",
                "full_text": "Привет! Чем помочь?",
            },
        )

    prev_db = getattr(app.state, "db", None)
    prev_repo = getattr(app.state, "repository", None)
    prev_sm = getattr(app.state, "session_manager", None)
    prev_vault = getattr(app.state, "vault", None)
    try:
        with patch.object(GeminiWebClient, "generate_stream", new=fake_stream):
            with TestClient(app, base_url="http://testserver") as client:
                app.state.db = db
                app.state.repository = repo
                app.state.session_manager = sm
                app.state.vault = vault

                res = client.post(
                    "/api/chat/generate",
                    json={
                        "prompt": "Привет",
                        "model": "gemini-4-carbon",
                        "cid": "c_old_broken_acc",
                        "rid": "r_old",
                        "rcid": "rc_old",
                    },
                )
                assert res.status_code == 200
                events = [
                    json.loads(line[6:])
                    for line in res.text.splitlines()
                    if line.startswith("data: ")
                ]
                assert any(e.get("type") == "failover" and e.get("to_account_id") == "acc_healthy" for e in events)
                text_out = "".join(e.get("delta", "") for e in events if e.get("type") == "text")
                assert text_out == "Привет! Чем помочь?"
                assert not any(e.get("type") == "error" for e in events)
                assert len(calls) == 2
                assert calls[0]["account_id"] == "acc_broken"
                assert calls[1]["account_id"] == "acc_healthy"
                assert calls[1]["cid"] is None
                assert calls[1]["rid"] is None
                assert calls[1]["rcid"] is None
    finally:
        app.state.db = prev_db
        app.state.repository = prev_repo
        app.state.session_manager = prev_sm
        app.state.vault = prev_vault
        asyncio.run(db.close())


