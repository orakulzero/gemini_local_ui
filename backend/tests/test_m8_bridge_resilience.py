"""
backend/tests/test_m8_bridge_resilience.py
Comprehensive test suite verifying Milestone M8 Resilience & Scale enhancements:
- R1: Passive Cookie Harvesting & DPAPI Vault Sync (HTTP 405 Circuit Breaker & on_response_harvest_cookies)
- R2: Incompatibility Caching for feature_mode=40 (Code 1173 fallback, fast-path, TTL, and account isolation)
- R3: Non-blocking Client Disconnect Detection during prolonged reasoning (stream_with_disconnect_check)
- R4: UTF-16 High Surrogate Buffering in Delta Accumulator (sanitize_and_buffer_surrogates)
"""

import asyncio
import json
import socket
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional
import httpx
import pytest

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.exceptions import GeminiBridgeError
from backend.app.bridge.session import (
    SessionContext,
    SessionHealthStatus,
    SessionManager,
)
from backend.app.bridge.streaming_parser import (
    StreamChunk,
    StreamingDeltaAccumulator,
    is_high_surrogate,
    is_low_surrogate,
    sanitize_and_buffer_surrogates,
)
from backend.app.api.chat import stream_with_disconnect_check
from backend.app.mock.mock_server import MockGeminiServer
from backend.app.storage.vault import AccountProfile, CredentialVault


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ============================================================================
# R1 Tests: Passive Cookie Harvesting & DPAPI Vault Sync
# ============================================================================

class TestPassiveCookieHarvestingAndVaultSync:
    """Verifies R1 circuit breaker on HTTP 405 and response-hook cookie harvesting."""

    @pytest.mark.asyncio
    async def test_rotate_cookies_http_405_circuit_breaker(self):
        """
        When RotateCookies endpoint returns HTTP 405 (Method Not Allowed),
        the circuit breaker trips:
        - ctx.passive_rotation is set to True
        - ctx.status is maintained as HEALTHY
        - returns True
        - get_health() reports passive_rotation is True
        """
        async def mock_handler(request: httpx.Request) -> httpx.Response:
            if "RotateCookies" in str(request.url):
                return httpx.Response(405, text="Method Not Allowed")
            return httpx.Response(200, text="OK")

        transport = httpx.MockTransport(mock_handler)
        client = httpx.AsyncClient(transport=transport)

        sm = SessionManager(
            base_url="https://gemini.google.com",
            rotate_url="https://accounts.google.com/RotateCookies",
            http_client=client,
        )
        ctx = await sm.init_session(
            cookies={"__Secure-1PSID": "valid_psid_token_123", "__Secure-1PSIDTS": "ts_initial"},
            account_id="acc_test_r1",
        )
        ctx.status = SessionHealthStatus.HEALTHY
        ctx.access_token = "mock_token_snlm0e"

        # Execute rotate_cookies
        success = await sm.rotate_cookies("acc_test_r1")

        assert success is True
        assert ctx.passive_rotation is True
        assert ctx.status == SessionHealthStatus.HEALTHY
        assert ctx.last_error is None

        health = sm.get_health("acc_test_r1")
        assert health.status == SessionHealthStatus.HEALTHY
        assert health.passive_rotation is True

    @pytest.mark.asyncio
    async def test_rotate_cookies_circuit_breaker_short_circuit(self):
        """
        Once passive_rotation is True, subsequent rotate_cookies calls
        short-circuit and return True immediately without any HTTP request.
        """
        request_count = 0

        async def failing_handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            raise httpx.ConnectError("Network is disconnected")

        transport = httpx.MockTransport(failing_handler)
        client = httpx.AsyncClient(transport=transport)

        sm = SessionManager(http_client=client)
        ctx = await sm.init_session(
            cookies={"__Secure-1PSID": "valid_psid_token_123"},
            account_id="acc_test_short_circuit",
        )
        ctx.status = SessionHealthStatus.HEALTHY
        ctx.passive_rotation = True

        # Reset counter after init_session to isolate rotate_cookies network calls
        request_count = 0

        # Call rotate_cookies
        success = await sm.rotate_cookies("acc_test_short_circuit")

        assert success is True
        assert request_count == 0  # Zero network requests made by rotate_cookies!

    @pytest.mark.asyncio
    async def test_passive_cookie_harvesting_via_response_hook_and_vault_sync(self, tmp_path):
        """
        Outgoing Google calls receiving Set-Cookie headers for target tokens
        (__Secure-1PSIDTS, SIDCC, etc.) automatically update SessionContext
        and persist credentials to the DPAPI vault.
        """
        vault_file = tmp_path / "test_vault_r1.enc"
        vault = CredentialVault(vault_file)
        vault.save_account(AccountProfile(
            id="acc_harvest_test",
            name="Harvest Profile",
            tier="ultra",
            cookies={"__Secure-1PSID": "psid_alpha", "__Secure-1PSIDTS": "old_ts"},
        ))

        async def harvest_handler(request: httpx.Request) -> httpx.Response:
            headers = [
                ("set-cookie", "__Secure-1PSIDTS=new_harvested_ts_999; Domain=.google.com; Path=/; Secure"),
                ("set-cookie", "SIDCC=new_sidcc_token_777; Domain=.google.com; Path=/"),
            ]
            return httpx.Response(200, headers=headers, text="OK")

        transport = httpx.MockTransport(harvest_handler)
        client = httpx.AsyncClient(transport=transport)

        sm = SessionManager(vault=vault, http_client=client)
        ctx = await sm.init_session(
            cookies={"__Secure-1PSID": "psid_alpha", "__Secure-1PSIDTS": "old_ts"},
            account_id="acc_harvest_test",
        )

        web_client = GeminiWebClient(
            session_context=ctx,
            vault=vault,
            account_id="acc_harvest_test",
        )
        # Execute a request through client (hook fires on response)
        resp = await client.get("https://gemini.google.com/test_ping")
        await web_client.on_response_harvest_cookies(resp)

        # 1. In-memory context updated
        assert ctx.cookies["__Secure-1PSIDTS"] == "new_harvested_ts_999"
        assert ctx.cookies["SIDCC"] == "new_sidcc_token_777"

        # 2. DPAPI Vault updated
        saved_profile = vault.get_account("acc_harvest_test")
        assert saved_profile is not None
        assert saved_profile.cookies["__Secure-1PSIDTS"] == "new_harvested_ts_999"
        assert saved_profile.cookies["SIDCC"] == "new_sidcc_token_777"


# ============================================================================
# R2 Tests: Incompatibility Caching for feature_mode=40 (Code 1173)
# ============================================================================

class TestFeatureMode40IncompatibilityCache:
    """Verifies R2 caching of Code 1173 to bypass 250ms RTT penalty on subsequent turns."""

    @pytest.fixture(autouse=True)
    def clean_cache(self):
        GeminiWebClient.clear_feature_mode_40_cache()
        yield
        GeminiWebClient.clear_feature_mode_40_cache()

    @pytest.mark.asyncio
    async def test_first_turn_catches_1173_and_populates_cache(self):
        """
        On first turn with feature_mode=40 returning Code 1173:
        - Stream falls back and succeeds.
        - Account incompatibility is cached in _feature_mode_40_unsupported.
        """
        port = get_free_port()
        server = MockGeminiServer(host="127.0.0.1", port=port)
        server.start_background()
        for _ in range(50):
            if server._site is not None:
                break
            time.sleep(0.05)

        try:
            ctx = SessionContext()
            ctx.account_id = "acc_non_task_user"
            ctx.cookies = {"__Secure-1PSID": "mock_psid_user1"}
            ctx.access_token = "mock_snlm0e"

            client = GeminiWebClient(
                session_context=ctx,
                base_url=f"http://127.0.0.1:{port}",
                account_id="acc_non_task_user",
                timeout=10.0,
            )

            assert GeminiWebClient.is_feature_mode_40_unsupported("acc_non_task_user") is False

            chunks = []
            async for chunk in client.generate_stream(
                prompt="Review PR [TRIGGER_TASK_1173]",
                model="gemini-4-argon",
                feature_mode="task",
            ):
                if chunk.type == "text" and chunk.delta:
                    chunks.append(chunk.delta)

            assert len(chunks) > 0
            # Cache must now be populated
            assert GeminiWebClient.is_feature_mode_40_unsupported("acc_non_task_user") is True

        finally:
            server.stop_background()

    @pytest.mark.asyncio
    async def test_second_turn_fast_path_uses_standard_envelope(self):
        """
        On subsequent turns for a cached account:
        - Stream proactively clears eff_feature_mode=0 before constructing envelope.
        - Completes in exactly 1 HTTP request with zero Code 1173 errors.
        """
        port = get_free_port()
        server = MockGeminiServer(host="127.0.0.1", port=port)
        server.start_background()
        for _ in range(50):
            if server._site is not None:
                break
            time.sleep(0.05)

        try:
            ctx = SessionContext()
            ctx.account_id = "acc_fastpath_user"
            ctx.cookies = {"__Secure-1PSID": "mock_psid_user2"}
            ctx.access_token = "mock_snlm0e"

            client = GeminiWebClient(
                session_context=ctx,
                base_url=f"http://127.0.0.1:{port}",
                account_id="acc_fastpath_user",
                timeout=10.0,
            )

            # Pre-populate cache (as if first turn completed)
            GeminiWebClient.mark_feature_mode_40_unsupported("acc_fastpath_user")
            server.fault.recorded_requests.clear()

            chunks = []
            async for chunk in client.generate_stream(
                prompt="Optimize code [TRIGGER_TASK_1173]",
                model="gemini-4-argon",
                feature_mode="task",
            ):
                if chunk.type == "text" and chunk.delta:
                    chunks.append(chunk.delta)

            assert len(chunks) > 0

            # Verify only 1 StreamGenerate request was sent
            stream_reqs = [r for r in server.fault.recorded_requests if "StreamGenerate" in r["path"]]
            assert len(stream_reqs) == 1

            # Verify envelope inner[49] was NOT set to 40
            f_req = stream_reqs[0].get("body_params", {}).get("f.req")
            parsed_outer = json.loads(f_req)
            parsed_inner = json.loads(parsed_outer[1])
            assert parsed_inner[49] is None

        finally:
            server.stop_background()

    def test_ttl_expiration_and_account_isolation(self):
        """
        Verifies 24h TTL expiration and account isolation:
        - Expired cache keys return False.
        - Account A cached does not affect Account B.
        """
        GeminiWebClient.mark_feature_mode_40_unsupported("acc_alpha", ttl_seconds=1.0)
        assert GeminiWebClient.is_feature_mode_40_unsupported("acc_alpha") is True
        assert GeminiWebClient.is_feature_mode_40_unsupported("acc_beta") is False

        # Simulate expiration
        GeminiWebClient._feature_mode_40_unsupported["acc_alpha"] = time.time() - 5.0
        assert GeminiWebClient.is_feature_mode_40_unsupported("acc_alpha") is False


# ============================================================================
# R3 Tests: Non-blocking Client Disconnect Detection
# ============================================================================

class TestClientDisconnectDetectionDuringReasoning:
    """Verifies R3 non-blocking disconnect detection during prolonged reasoning silence."""

    @pytest.mark.asyncio
    async def test_stream_with_disconnect_check_detects_disconnect_during_silence(self):
        """
        When upstream generation pauses in reasoning silence (e.g. 5 seconds),
        and the client disconnects at t = 0.5s, stream_with_disconnect_check
        detects the disconnect within ~1.0s and aborts without hanging.
        """
        is_disconnected_flag = False

        class MockRequest:
            async def is_disconnected(self) -> bool:
                return is_disconnected_flag

        async def slow_reasoning_generator() -> AsyncIterator[str]:
            yield "thought: starting deep reasoning"
            # Simulate 10 seconds of reasoning silence
            for _ in range(100):
                await asyncio.sleep(0.1)
                yield "thought: intermediate pulse"
            yield "text: final response"

        req = MockRequest()

        # Trigger disconnect after 0.3s
        async def disconnect_client():
            nonlocal is_disconnected_flag
            await asyncio.sleep(0.3)
            is_disconnected_flag = True

        asyncio.create_task(disconnect_client())

        start_t = time.time()
        emitted = []
        async for item in stream_with_disconnect_check(slow_reasoning_generator(), req, timeout=0.2):
            emitted.append(item)

        elapsed = time.time() - start_t
        # Must finish in under 1.5s rather than 10.0s!
        assert elapsed < 1.5
        assert len(emitted) >= 1
        assert "text: final response" not in emitted

    @pytest.mark.asyncio
    async def test_stream_with_disconnect_check_normal_full_stream(self):
        """Normal stream when client stays connected completes all chunks cleanly."""
        class MockConnectedRequest:
            async def is_disconnected(self) -> bool:
                return False

        async def normal_generator() -> AsyncIterator[str]:
            for i in range(5):
                yield f"chunk_{i}"
                await asyncio.sleep(0.01)

        req = MockConnectedRequest()
        collected = []
        async for chunk in stream_with_disconnect_check(normal_generator(), req, timeout=0.5):
            collected.append(chunk)

        assert collected == ["chunk_0", "chunk_1", "chunk_2", "chunk_3", "chunk_4"]


# ============================================================================
# R4 Tests: UTF-16 High Surrogate Buffering in Delta Accumulator
# ============================================================================

class TestSurrogateBufferingInDeltaAccumulator:
    """Verifies R4 buffering of isolated high surrogates at streaming boundaries."""

    def test_surrogate_predicate_functions(self):
        """Tests is_high_surrogate and is_low_surrogate character boundaries."""
        high_surr = chr(0xD83D)
        high_surr2 = chr(0xD83E)
        low_surr = chr(0xDE80)

        assert is_high_surrogate(high_surr) is True
        assert is_high_surrogate(high_surr2) is True
        assert is_high_surrogate(low_surr) is False
        assert is_high_surrogate("A") is False

        assert is_low_surrogate(low_surr) is True
        assert is_low_surrogate(high_surr) is False
        assert is_low_surrogate("Z") is False

    def test_streaming_delta_accumulator_incomplete_surrogate_buffering(self):
        """
        When a frame arrives ending with an isolated high surrogate,
        _compute_delta buffers it and does NOT emit the unclosed surrogate.
        When the low surrogate arrives in the subsequent frame, the combined
        Unicode character (🚀) is emitted cleanly as a valid UTF-8 delta.
        """
        acc = StreamingDeltaAccumulator()
        high_surr = chr(0xD83D)

        # Frame 1: Text ends with trailing high surrogate (half of 🚀)
        text_with_high_surrogate = "Processing " + high_surr
        d1, s1 = acc._compute_delta(text_with_high_surrogate, "", is_final=False)

        # Delta must buffer the high surrogate
        assert d1 == "Processing "
        assert s1 == "Processing "
        # UTF-8 encoding must succeed without UnicodeEncodeError
        assert d1.encode("utf-8") == b"Processing "

        # Frame 2: Low surrogate arrives, forming full 🚀
        text_complete = "Processing 🚀 and done"
        d2, s2 = acc._compute_delta(text_complete, s1, is_final=False)

        assert d2 == "🚀 and done"
        assert s2 == "Processing 🚀 and done"
        assert d2.encode("utf-8") == "🚀 and done".encode("utf-8")

    def test_streaming_delta_accumulator_lone_surrogate_on_final(self):
        """
        When is_final=True, any lingering isolated surrogate is safely replaced
        with U+FFFD (replacement character) to prevent UnicodeEncodeError in SSE.
        """
        acc = StreamingDeltaAccumulator()
        high_surr = chr(0xD83D)
        d_final, s_final = acc._compute_delta("Broken " + high_surr, "", is_final=True)

        assert high_surr not in d_final
        # Can encode to UTF-8 without error
        encoded = d_final.encode("utf-8")
        assert b"\xef\xbf\xbd" in encoded or "Broken" in d_final

    def test_adjacent_surrogate_pair_recombination(self):
        """Adjacent surrogate pairs are merged into authentic 32-bit Unicode."""
        split_surrogate_str = "Rocket: " + chr(0xD83D) + chr(0xDE80) + "!"
        sanitized = sanitize_and_buffer_surrogates(split_surrogate_str, is_final=False)
        assert sanitized == "Rocket: 🚀!"
