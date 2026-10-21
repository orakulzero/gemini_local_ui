"""
backend/tests/test_bridge.py
Automated integration test suite for Gemini Bridge, Mock Engine & Auth Vault.
Validates all components against deterministic mock server on 127.0.0.1:8999.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import pytest
import httpx

from backend.app.mock.mock_server import MockGeminiServer
from backend.app.storage.vault import CredentialVault
from backend.app.bridge.browser_extractor import (
    BrowserCookieExtractor,
    BrowserType,
)
from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.exceptions import (
    AuthenticationError,
    ModelInconsistentError,
    TemporaryBackendOverloadError,
    UsageLimitExceededError,
)
from backend.app.bridge.models import (
    DEFAULT_MODELS,
    GemSerializer,
    HeaderBuilder,
    resolve_model,
)
from backend.app.bridge.session import (
    SessionContext,
    SessionHealthStatus,
    SessionManager,
)
from backend.app.bridge.streaming_parser import (
    StreamChunk,
    StreamingDeltaAccumulator,
    StreamingFrameParser,
)
from backend.app.bridge.uploader import (
    GeminiUploader,
    ResumableUploader,
)
from backend.app.main import app

MOCK_PORT = 8999
MOCK_HOST = "127.0.0.1"
MOCK_BASE_URL = f"http://{MOCK_HOST}:{MOCK_PORT}"


@pytest.fixture(scope="module")
def mock_server():
    """Starts deterministic mock Gemini web server in background thread for the test session."""
    server = MockGeminiServer(host=MOCK_HOST, port=MOCK_PORT)
    server.start_background()
    # Wait until server accepts connections
    import time
    for _ in range(50):
        try:
            r = httpx.get(f"{MOCK_BASE_URL}/app", timeout=0.5)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.05)

    yield server
    server.stop_background()


@pytest.fixture(autouse=True)
def reset_mock_faults(mock_server: MockGeminiServer):
    """Resets fault injection rules and request recordings before each test."""
    mock_server.fault.reset()


@pytest.fixture
def mock_cookies():
    return {
        "__Secure-1PSID": "mock_psid_valid_test_token_12345",
        "__Secure-1PSIDTS": "mock_psidts_initial_timestamp_67890",
    }


# =====================================================================
# 1. Mock Server Route Tests
# =====================================================================

@pytest.mark.asyncio
async def test_mock_server_app_route(mock_server: MockGeminiServer, mock_cookies: dict[str, str]):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{MOCK_BASE_URL}/app",
            headers={"Cookie": f"__Secure-1PSID={mock_cookies['__Secure-1PSID']}"},
        )
        assert resp.status_code == 200
        assert "WIZ_global_data" in resp.text
        assert "SNlM0e" in resp.text
        assert "cfb2h" in resp.text
        assert "qKIAYe" in resp.text


@pytest.mark.asyncio
async def test_mock_server_rotate_cookies(mock_server: MockGeminiServer, mock_cookies: dict[str, str]):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{MOCK_BASE_URL}/RotateCookies",
            headers={"Cookie": f"__Secure-1PSID={mock_cookies['__Secure-1PSID']}"},
            json=["gemini", ""],
        )
        assert resp.status_code == 200
        set_cookie = resp.headers.get("Set-Cookie", "")
        assert "__Secure-1PSIDTS=" in set_cookie
        assert "rotated_test_psidts_token_999999" in set_cookie


# =====================================================================
# 2. Session Manager & Auto-Refresh Tests
# =====================================================================

@pytest.mark.asyncio
async def test_session_init_and_scrape_tokens(mock_server: MockGeminiServer, mock_cookies: dict[str, str]):
    session = SessionManager(base_url=MOCK_BASE_URL)
    try:
        ctx = await session.init_session(mock_cookies)
        assert ctx.access_token == "AIzaSyMockSNlM0eToken_2026_TestFixture_12345:1729144800"
        assert ctx.build_label == "boq_assistant-bard-web-server_20261017.00_p0"
        assert ctx.session_id == "9876543210123456789"
        assert ctx.push_id == "feeds/mock_push_feed_alpha_999"
        assert session.get_health().status == SessionHealthStatus.HEALTHY
        assert session.get_health().is_authenticated is True
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_session_token_rotation_loop(mock_server: MockGeminiServer, mock_cookies: dict[str, str]):
    session = SessionManager(base_url=MOCK_BASE_URL)
    try:
        await session.init_session(mock_cookies)
        rotated = await session.rotate_cookies()
        assert rotated is True

        current_cookies = session.get_cookies()
        assert current_cookies["__Secure-1PSIDTS"] == "rotated_test_psidts_token_999999"
        assert session.get_health().status == SessionHealthStatus.HEALTHY
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_session_revocation_detection(mock_server: MockGeminiServer, mock_cookies: dict[str, str]):
    session = SessionManager(base_url=MOCK_BASE_URL)
    try:
        await session.init_session(mock_cookies)

        # Force expired session in mock server
        mock_server.fault.force_expired_session = True

        with pytest.raises(AuthenticationError):
            await session.rotate_cookies()

        assert session.get_health().status == SessionHealthStatus.UNAUTHENTICATED
    finally:
        await session.close()


# =====================================================================
# 3. Batchexecute RPC Engine Tests
# =====================================================================

@pytest.mark.asyncio
async def test_batchexecute_model_discovery(mock_server: MockGeminiServer, mock_cookies: dict[str, str]):
    client = GeminiWebClient(base_url=MOCK_BASE_URL, cookies=mock_cookies)
    try:
        models = await client.list_models()
        assert len(models) >= 3

        model_ids = [m.id for m in models]
        assert "gemini-2.5-flash" in model_ids
        assert "gemini-2.5-pro" in model_ids
        assert "gemini-2.0-flash-thinking" in model_ids

        thinking_model = next(m for m in models if m.id == "gemini-2.0-flash-thinking")
        assert thinking_model.supports_thinking is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_batchexecute_conversation_listing(mock_server: MockGeminiServer, mock_cookies: dict[str, str]):
    client = GeminiWebClient(base_url=MOCK_BASE_URL, cookies=mock_cookies)
    try:
        conversations = await client.list_conversations(limit=10)
        assert len(conversations) == 3
        assert conversations[0]["cid"] == "c_mock_conv_001"
        assert "Architecture Review" in conversations[0]["title"]
        assert conversations[1]["is_pinned"] is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_batchexecute_conversation_turns(mock_server: MockGeminiServer, mock_cookies: dict[str, str]):
    client = GeminiWebClient(base_url=MOCK_BASE_URL, cookies=mock_cookies)
    try:
        turns = await client.get_conversation_turns("c_mock_conv_001")
        assert turns is not None
        assert turns[0] == "c_mock_conv_001"
        assert len(turns[1]) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_batchexecute_gem_listing(mock_server: MockGeminiServer, mock_cookies: dict[str, str]):
    client = GeminiWebClient(base_url=MOCK_BASE_URL, cookies=mock_cookies)
    try:
        gems = await client.list_gems()
        assert len(gems) == 2
        assert gems[0].id == "gem_code_architect"
        assert "software architect" in gems[0].description.lower()
        assert gems[1].id == "gem_security_auditor"
    finally:
        await client.close()


# =====================================================================
# 4. Streaming Parser & UTF-16 Multi-byte Emojis
# =====================================================================

def test_streaming_parser_utf16_surrogates():
    """
    Tests that emojis (codepoints > 0xFFFF, which are 2 UTF-16 code units)
    have their length calculated properly according to Google's protocol.
    """
    parser = StreamingFrameParser()

    # 🚀 is codepoint U+1F680 -> 2 code units in UTF-16, 4 bytes in UTF-8
    test_json = json.dumps([[["wrb.fr", "test", '{"text":"Rocket 🚀 Launch"}']]])
    utf16_len = len(test_json.encode("utf-16le")) // 2
    raw_packet = f"\n\n{utf16_len}\n{test_json}\n\n".encode("utf-8")

    # Feed packet in two fragmented slices across the emoji boundary
    frames_1 = parser.feed(raw_packet[:20])
    assert len(frames_1) == 0  # Buffered

    frames_2 = parser.feed(raw_packet[20:])
    assert len(frames_2) == 1
    assert "Rocket 🚀 Launch" in str(frames_2[0])


def test_streaming_delta_accumulator_flicker_stripping():
    """Verifies that intermediate flicker escapes are stripped and code blocks don't flicker."""
    accumulator = StreamingDeltaAccumulator()

    # Intermediate frame ending with trailing escape
    frame1 = [[
        "wrb.fr",
        "StreamGenerate",
        json.dumps([None, None, None, None, [["rc_1", ["Analyzing `code`\\*"], None, None, None, None, None, None, [0]]]])
    ]]
    chunks1 = accumulator.process_frame(frame1)
    text1 = "".join(c.delta for c in chunks1 if c.type == "text")
    assert not text1.endswith("\\*")

    # Next frame completes the markdown
    frame2 = [[
        "wrb.fr",
        "StreamGenerate",
        json.dumps([None, None, None, None, [["rc_1", ["Analyzing `code`* and done."], None, None, None, None, None, None, [2]]]])
    ]]
    chunks2 = accumulator.process_frame(frame2)
    text2 = "".join(c.delta for c in chunks2 if c.type == "text")
    assert "done." in text2
    done_chunk = next(c for c in chunks2 if c.type == "done")
    assert done_chunk.metadata["full_text"] == "Analyzing `code`* and done."


# =====================================================================
# 5. Full Streaming Generation Lifecycle
# =====================================================================

@pytest.mark.asyncio
async def test_stream_generate_full_lifecycle(mock_server: MockGeminiServer, mock_cookies: dict[str, str]):
    client = GeminiWebClient(base_url=MOCK_BASE_URL, cookies=mock_cookies)
    try:
        thought_chunks = []
        text_chunks = []
        citations = []
        is_done = False

        async for chunk in client.generate_stream(
            prompt="Explain Windows DPAPI",
            model="gemini-2.0-flash-thinking",
        ):
            if chunk.type == "thought":
                thought_chunks.append(chunk.delta)
            elif chunk.type == "text":
                text_chunks.append(chunk.delta)
            elif chunk.type in ("citation", "grounding"):
                citations.append(chunk.metadata)
            elif chunk.type == "done":
                is_done = True

        # 1. Assert thought chain received
        full_thoughts = "".join(thought_chunks)
        assert "cryptographic vaults" in full_thoughts
        assert "ctypes" in full_thoughts

        # 2. Assert text deltas assembled into full markdown
        full_text = "".join(text_chunks)
        assert "Windows **DPAPI**" in full_text
        assert "```python" in full_text
        assert "DATA_BLOB" in full_text

        # 3. Assert completion signal received
        assert is_done is True

        # 4. Assert citations received
        assert len(citations) > 0

        # 5. Assert request header x-goog-ext-525001261-jspb was properly synthesized
        recorded = mock_server.fault.recorded_requests
        stream_req = next(r for r in recorded if "StreamGenerate" in r["path"])
        jspb_header = stream_req["headers"].get("x-goog-ext-525001261-jspb")
        assert jspb_header is not None
        parsed_jspb = json.loads(jspb_header)
        assert parsed_jspb[4] == "gemini-2.0-flash-thinking"
        assert parsed_jspb[15] == 2  # thinking_flag = 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stream_generate_with_gem(mock_server: MockGeminiServer, mock_cookies: dict[str, str]):
    client = GeminiWebClient(base_url=MOCK_BASE_URL, cookies=mock_cookies)
    try:
        async for _ in client.generate_stream(
            prompt="Write an API schema",
            model="gemini-2.5-pro",
            gem_id="gem_code_architect",
        ):
            pass

        recorded = mock_server.fault.recorded_requests
        stream_req = next(r for r in recorded if "StreamGenerate" in r["path"])
        f_req = stream_req["body_params"]["f.req"]
        parsed_outer = json.loads(f_req)
        parsed_inner = json.loads(parsed_outer[1])
        # Assert gem_id placed in inner[19]
        assert parsed_inner[19] == "gem_code_architect"
    finally:
        await client.close()


# =====================================================================
# 6. Multimodal File Upload Tests
# =====================================================================

@pytest.mark.asyncio
async def test_multimodal_upload_and_attachment(mock_server: MockGeminiServer, mock_cookies: dict[str, str]):
    uploader = ResumableUploader(base_url=MOCK_BASE_URL, cookies=mock_cookies)
    dummy_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 50

    uri = await uploader.upload_file(
        data=dummy_png_bytes,
        filename="diagram.png",
        mime_type="image/png",
        push_id="feeds/mock_push_feed_alpha_999",
    )
    assert uri == "/contrib_service/ttl_1d/mock_uploaded_resource_hash_897123"

    # Verify attachment is formatted and passed to StreamGenerate
    client = GeminiWebClient(base_url=MOCK_BASE_URL, cookies=mock_cookies)
    try:
        async for _ in client.generate_stream(
            prompt="Analyze this diagram",
            attachments=[{"uri": uri, "filename": "diagram.png"}],
        ):
            pass

        recorded = mock_server.fault.recorded_requests
        stream_req = next(r for r in recorded if "StreamGenerate" in r["path"])
        f_req = stream_req["body_params"]["f.req"]
        parsed_outer = json.loads(f_req)
        parsed_inner = json.loads(parsed_outer[1])

        attachments_block = parsed_inner[0][3]
        assert attachments_block[0][0][0] == uri
        assert attachments_block[0][1] == "diagram.png"
    finally:
        await client.close()


# =====================================================================
# 7. Fault Recovery & Error Handling Tests
# =====================================================================

@pytest.mark.asyncio
async def test_fault_recovery_quota_exhaustion(mock_server: MockGeminiServer, mock_cookies: dict[str, str]):
    mock_server.fault.force_quota_exhausted = True
    client = GeminiWebClient(base_url=MOCK_BASE_URL, cookies=mock_cookies)
    try:
        with pytest.raises(UsageLimitExceededError) as exc_info:
            async for _ in client.generate_stream(prompt="Test quota"):
                pass
        assert exc_info.value.code == 1037
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_fault_recovery_model_inconsistent(mock_server: MockGeminiServer, mock_cookies: dict[str, str]):
    mock_server.fault.force_model_inconsistent = True
    client = GeminiWebClient(base_url=MOCK_BASE_URL, cookies=mock_cookies)
    try:
        with pytest.raises(ModelInconsistentError) as exc_info:
            async for _ in client.generate_stream(prompt="Switch model mid-thread"):
                pass
        assert exc_info.value.code == 1050
    finally:
        await client.close()


def test_fault_recovery_corrupt_chunks():
    """Asserts that malformed frames are skipped without aborting stream."""
    parser = StreamingFrameParser()
    corrupt_data = b"\n\n15\n[MALFORMED_JSON\n\n"
    frames = parser.feed(corrupt_data)
    assert len(frames) == 0


# =====================================================================
# 8. Credential Vault DPAPI & Fallback Tests
# =====================================================================

def test_credential_vault_dpapi_roundtrip(tmp_path: Path):
    vault_file = tmp_path / "test_vault.enc"
    vault = CredentialVault(vault_path=vault_file)

    test_creds = {
        "__Secure-1PSID": "secret_psid_abc123",
        "__Secure-1PSIDTS": "secret_psidts_xyz789",
        "access_token": "secret_snlm0e_token",
    }

    vault.store_credentials(test_creds)
    assert vault_file.exists()

    # Ensure ciphertext is encrypted, not plaintext
    raw_content = vault_file.read_bytes()
    assert b"secret_psid_abc123" not in raw_content

    # Decrypt and verify equality
    loaded = vault.load_credentials()
    assert loaded == test_creds

    # Clear vault
    vault.clear()
    assert not vault_file.exists()


# =====================================================================
# 9. Browser Extractor & Cookie Validation Tests
# =====================================================================

def test_browser_extractor_manual_parser():
    # 1. Raw header
    raw_header = "Cookie: __Secure-1PSID=token_123; __Secure-1PSIDTS=ts_456; other=abc"
    parsed1 = BrowserCookieExtractor.parse_manual(raw_header)
    assert parsed1["__Secure-1PSID"] == "token_123"
    assert parsed1["__Secure-1PSIDTS"] == "ts_456"
    assert parsed1["other"] == "abc"

    # 2. JSON array
    json_array = json.dumps([
        {"name": "__Secure-1PSID", "value": "val_psid"},
        {"name": "__Secure-1PSIDTS", "value": "val_ts"},
    ])
    parsed2 = BrowserCookieExtractor.parse_manual(json_array)
    assert parsed2["__Secure-1PSID"] == "val_psid"
    assert parsed2["__Secure-1PSIDTS"] == "val_ts"

    # 3. JSON dictionary
    json_dict = json.dumps({"__Secure-1PSID": "dict_psid", "__Secure-1PSIDTS": "dict_ts"})
    parsed3 = BrowserCookieExtractor.parse_manual(json_dict)
    assert parsed3["__Secure-1PSID"] == "dict_psid"

    # Validation
    val_ok = BrowserCookieExtractor.validate_cookies(parsed1)
    assert val_ok.is_valid is True
    assert val_ok.has_psid is True
    assert val_ok.has_psidts is True

    val_bad = BrowserCookieExtractor.validate_cookies({"other": "only"})
    assert val_bad.is_valid is False
    assert val_bad.has_psid is False


# =====================================================================
# 10. FastAPI Daemon & Local Security Tests
# =====================================================================

@pytest.mark.asyncio
async def test_fastapi_endpoints(mock_server: MockGeminiServer):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # 1. Health
        h_resp = await client.get("/health")
        assert h_resp.status_code == 200
        assert h_resp.json() == {"status": "ok"}

        # 2. Models
        m_resp = await client.get("/api/models")
        assert m_resp.status_code == 200
        models_data = m_resp.json()["models"]
        assert len(models_data) >= 3

        # 3. Host validation: Reject external DNS Host header
        bad_host_resp = await client.get("/health", headers={"Host": "malicious-site.com"})
        assert bad_host_resp.status_code == 400
        assert "Invalid Host header" in bad_host_resp.text
