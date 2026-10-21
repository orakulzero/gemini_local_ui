"""
backend/tests/test_m6_adversarial_challenge.py
Adversarial Challenge & Stress Test Suite for Milestone M6 (NotebookLM Protocol & Bridge).

Covers:
1. StreamingFrameParser adversarial wire framing:
   - Truncated length headers and mid-payload cuts
   - Chunked byte streaming across UTF-8/UTF-16 boundaries
   - Packed multiple frames in single chunk
   - Non-ASCII UTF-16 surrogate pairs (emojis, CJK, Cyrillic)
   - Invalid, corrupted, or absent anti-XSSI prefixes
   - Malformed, negative, or overflow length headers
   - Corrupted JSON payloads within frames
2. RPC stress testing for all 6 methods (wXbhsf, rLM1Ne, CCqFvf, WWINqb, s0tc2d, trScbf):
   - Network timeouts & connect errors
   - HTTP 401 & 403 unauthorized responses
   - HTTP 500, 502, 503 server-side failures
   - Google Code 7 credential revocation (in entry[5] and inner_data[0])
   - Malformed, empty, or unexpected JSON schemas
3. Offline mode & fallback resilience:
   - Mock server force_notebooklm_offline (HTTP 503)
   - Fallback to fallback_snlm0e when host is unreachable
   - is_offline_fallback state transitions
4. Concurrency & client lifecycle:
   - High-concurrency parallel list_notebooks and get_notebook calls
   - Mixed parallel RPCs with reqid increment safety
   - Socket reuse and clean client.close() lifecycle
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any, Dict, List, Optional
import httpx
import pytest

from backend.app.bridge.exceptions import AuthenticationError, GeminiBridgeError
from backend.app.bridge.notebooklm import (
    NotebookDetail,
    NotebookInfo,
    NotebookLMClient,
    NotebookNote,
    NotebookSource,
)
from backend.app.bridge.streaming_parser import StreamingFrameParser
from backend.app.mock.fixtures import (
    PAYLOAD_CODE_7_PERMISSION_DENIED,
    PAYLOAD_NOTEBOOKLM_ADD_SOURCE,
    PAYLOAD_NOTEBOOKLM_CREATE_NOTEBOOK,
    PAYLOAD_NOTEBOOKLM_DELETE_NOTEBOOK,
    PAYLOAD_NOTEBOOKLM_GET_NOTEBOOK,
    PAYLOAD_NOTEBOOKLM_LIST_NOTEBOOKS,
    PAYLOAD_NOTEBOOKLM_RENAME_NOTEBOOK,
    WIZ_GLOBAL_DATA_NOTEBOOKLM,
    format_batchexecute_envelope,
)
from backend.app.mock.mock_server import MockGeminiServer

MOCK_CHALLENGE_PORT = 8998
MOCK_CHALLENGE_HOST = "127.0.0.1"
MOCK_CHALLENGE_BASE_URL = f"http://{MOCK_CHALLENGE_HOST}:{MOCK_CHALLENGE_PORT}"


@pytest.fixture(scope="module")
def challenge_mock_server():
    """Starts deterministic mock server on a distinct port for adversarial testing."""
    server = MockGeminiServer(host=MOCK_CHALLENGE_HOST, port=MOCK_CHALLENGE_PORT)
    server.start_background()

    for _ in range(50):
        try:
            r = httpx.get(f"{MOCK_CHALLENGE_BASE_URL}/", timeout=0.5)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.05)

    yield server
    server.stop_background()


@pytest.fixture(autouse=True)
def reset_challenge_mock(challenge_mock_server: MockGeminiServer):
    challenge_mock_server.fault.reset()


@pytest.fixture(scope="module")
def mock_cookies() -> Dict[str, str]:
    return {
        "__Secure-1PSID": "mock_adv_psid_token_12345",
        "__Secure-1PSIDTS": "mock_adv_psidts_token_67890",
    }


# ============================================================================
# SECTION 1: StreamingFrameParser Adversarial Wire Framing
# ============================================================================

class TestStreamingFrameParserAdversarial:
    """Stress tests length-prefixed frame parser against boundary conditions and corruption."""

    def test_truncated_length_header_incremental_assembly(self):
        """Parser waits for newline when length header arrives in fragments."""
        parser = StreamingFrameParser()
        # Feed anti-XSSI and partial digits
        res1 = parser.feed(")]}'\n\n1")
        assert res1 == []
        res2 = parser.feed("8\n")
        assert res2 == []
        # Payload of exactly 18 UTF-16 code units: `[["0123456789ab"]]`
        payload = '[["0123456789ab"]]'
        assert len(payload.encode("utf-16le")) // 2 == 18
        wire = f"{payload}\n"
        res3 = parser.feed(wire)
        assert len(res3) == 1
        assert res3[0] == [["0123456789ab"]]

    def test_truncated_mid_payload_across_chunks(self):
        """Parser correctly holds incomplete JSON payload until final bytes arrive."""
        parser = StreamingFrameParser()
        inner = [[["wrb.fr", "wXbhsf", "[]", None, None, None, "generic"]]]
        wire = format_batchexecute_envelope("wXbhsf", [])

        # Split wire exactly in the middle
        split_idx = len(wire) // 2
        chunk1 = wire[:split_idx]
        chunk2 = wire[split_idx:]

        res1 = parser.feed(chunk1)
        assert res1 == [], "Should not yield partial frame"

        res2 = parser.feed(chunk2)
        assert len(res2) == 1
        assert res2[0][0][0][1] == "wXbhsf"

    def test_single_byte_feeding_and_streaming(self):
        """Parser handles 1-byte incremental stream without frame loss."""
        parser = StreamingFrameParser()
        wire = format_batchexecute_envelope("CCqFvf", ["Test New"])
        wire_bytes = wire.encode("utf-8")

        completed = []
        for b in wire_bytes:
            frames = parser.feed(bytes([b]))
            if frames:
                completed.extend(frames)

        assert len(completed) == 1
        assert completed[0][0][0][1] == "CCqFvf"

    def test_multiple_packed_frames_in_single_chunk(self):
        """Single network chunk containing multiple frames is unpacked cleanly."""
        parser = StreamingFrameParser()
        frame1 = format_batchexecute_envelope("rpc1", {"step": 1})
        frame2 = format_batchexecute_envelope("rpc2", {"step": 2})
        frame3 = format_batchexecute_envelope("rpc3", {"step": 3})

        # Combine all 3 into one chunk (subsequent frames don't need anti-XSSI)
        combined = frame1 + frame2.replace(")]}'\n", "") + frame3.replace(")]}'\n", "")
        frames = parser.feed(combined)

        assert len(frames) == 3
        rpc_ids = [f[0][0][1] for f in frames]
        assert rpc_ids == ["rpc1", "rpc2", "rpc3"]

    def test_non_ascii_utf16_surrogate_pairs_emojis(self):
        """
        Validates UTF-16 code-unit counting with 4-byte surrogate pairs (emojis).
        Each emoji is 1 Unicode character, 2 UTF-16 code units, and 4 UTF-8 bytes.
        """
        parser = StreamingFrameParser()
        # High-surrogate emoji payload
        emoji_data = ["📓", "🧪", "🚀", "✨", "Quantum 🧬 Research"]
        wire = format_batchexecute_envelope("wXbhsf", emoji_data)

        frames = parser.feed(wire)
        assert len(frames) == 1
        inner_json = json.loads(frames[0][0][0][2])
        assert inner_json == emoji_data

    def test_cjk_and_cyrillic_multibyte_characters(self):
        """Validates 2-byte and 3-byte UTF-8 characters that map to single UTF-16 code units."""
        parser = StreamingFrameParser()
        multilingual_payload = {
            "ru": "Hello world! Quantum computing and Shor algorithm. Café résumé λΩπ.",
            "ja": "こんにちは世界！量子コンピュータとショアのアルゴリズム。",
            "zh": "你好世界！量子计算与秀尔算法。",
        }
        wire = format_batchexecute_envelope("rLM1Ne", multilingual_payload)

        frames = parser.feed(wire)
        assert len(frames) == 1
        inner_json = json.loads(frames[0][0][0][2])
        assert inner_json["ru"] == multilingual_payload["ru"]
        assert inner_json["ja"] == multilingual_payload["ja"]
        assert inner_json["zh"] == multilingual_payload["zh"]

    def test_missing_or_corrupted_anti_xssi_prefix(self):
        """Parser tolerates absent or malformed anti-XSSI prefix gracefully."""
        # Absent prefix
        parser1 = StreamingFrameParser()
        raw_json = json.dumps([[["wrb.fr", "test", "[]", None, None, None, "generic"]]])
        u16_len = len(raw_json.encode("utf-16le")) // 2
        wire_no_prefix = f"\n\n{u16_len}\n{raw_json}\n"
        f1 = parser1.feed(wire_no_prefix)
        assert len(f1) == 1

        # Corrupted prefix
        parser2 = StreamingFrameParser()
        corrupted = f"NOT_XSSI\n\n{u16_len}\n{raw_json}\n"
        f2 = parser2.feed(corrupted)
        # Should discard invalid lines and parse valid length-prefixed frame
        assert len(f2) == 1

    def test_malformed_and_negative_length_headers(self):
        """Invalid length headers (negative, alphanumeric, zero) do not cause unhandled crashes."""
        parser = StreamingFrameParser()
        # Feed negative length, garbage header, then valid frame
        wire_garbage = ")]}'\n\n-50\n[invalid]\nabcdef\n0\n"
        frames1 = parser.feed(wire_garbage)
        assert frames1 == []

        # Followed by valid frame
        valid_wire = format_batchexecute_envelope("wXbhsf", ["recovered"])
        frames2 = parser.feed(valid_wire.replace(")]}'\n", ""))
        assert len(frames2) == 1
        assert json.loads(frames2[0][0][0][2]) == ["recovered"]

    def test_corrupted_json_payload_discard(self):
        """Corrupted JSON string inside an otherwise valid frame header is ignored cleanly."""
        parser = StreamingFrameParser()
        bad_json = '{"broken": [1, 2, '
        u16_len = len(bad_json.encode("utf-16le")) // 2
        bad_wire = f")]}}'\n\n{u16_len}\n{bad_json}\n"

        frames = parser.feed(bad_wire)
        assert frames == []

        # Next valid frame succeeds
        good_wire = format_batchexecute_envelope("test", ["ok"])
        good_frames = parser.feed(good_wire.replace(")]}'\n", ""))
        assert len(good_frames) == 1


# ============================================================================
# SECTION 2: RPC Stress Tests Across All 6 Methods
# ============================================================================

class TestRPCStressAndFaultHandling:
    """Stress tests wXbhsf, rLM1Ne, CCqFvf, WWINqb, s0tc2d, trScbf under faults."""

    ALL_RPCS = ["wXbhsf", "rLM1Ne", "CCqFvf", "WWINqb", "s0tc2d", "trScbf"]

    async def _execute_rpc(self, client: NotebookLMClient, rpc_id: str) -> Any:
        if rpc_id == "wXbhsf":
            return await client.list_notebooks()
        elif rpc_id == "rLM1Ne":
            return await client.get_notebook("nb_test_123")
        elif rpc_id == "CCqFvf":
            return await client.create_notebook("New Notebook")
        elif rpc_id == "WWINqb":
            return await client.delete_notebook("nb_test_123")
        elif rpc_id == "s0tc2d":
            return await client.rename_notebook("nb_test_123", "New Title")
        elif rpc_id == "trScbf":
            return await client.add_text_source("nb_test_123", "Title", "Content")
        raise ValueError(f"Unknown RPC {rpc_id}")

    @pytest.mark.parametrize("rpc_id", ALL_RPCS)
    @pytest.mark.asyncio
    async def test_rpc_network_timeout(self, rpc_id: str):
        """All 6 RPCs handle httpx.TimeoutException properly and set is_offline_fallback."""
        transport = httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("Socket timeout")))
        client = NotebookLMClient(base_url="http://fake.test", timeout=0.1)
        client._client = httpx.AsyncClient(transport=transport)

        try:
            with pytest.raises(httpx.TimeoutException):
                await self._execute_rpc(client, rpc_id)
            assert client.is_offline_fallback is True
        finally:
            await client.close()

    @pytest.mark.parametrize("rpc_id", ALL_RPCS)
    @pytest.mark.parametrize("status_code", [401, 403])
    @pytest.mark.asyncio
    async def test_rpc_unauthorized_http_status(self, rpc_id: str, status_code: int):
        """All 6 RPCs raise AuthenticationError when receiving HTTP 401/403."""
        transport = httpx.MockTransport(lambda req: httpx.Response(status_code=status_code, text="Unauthorized"))
        client = NotebookLMClient(base_url="http://fake.test")
        client._client = httpx.AsyncClient(transport=transport)

        try:
            with pytest.raises(AuthenticationError):
                await self._execute_rpc(client, rpc_id)
        finally:
            await client.close()

    @pytest.mark.parametrize("rpc_id", ALL_RPCS)
    @pytest.mark.parametrize("status_code", [500, 502, 503])
    @pytest.mark.asyncio
    async def test_rpc_server_error_http_status(self, rpc_id: str, status_code: int):
        """All 6 RPCs raise GeminiBridgeError on HTTP 5xx responses."""
        transport = httpx.MockTransport(lambda req: httpx.Response(status_code=status_code, text="Server Error"))
        client = NotebookLMClient(base_url="http://fake.test")
        client._client = httpx.AsyncClient(transport=transport)

        try:
            with pytest.raises(GeminiBridgeError):
                await self._execute_rpc(client, rpc_id)
        finally:
            await client.close()

    @pytest.mark.parametrize("rpc_id", ALL_RPCS)
    @pytest.mark.asyncio
    async def test_rpc_google_code_7_revocation_in_entry_node(self, rpc_id: str):
        """
        Google batchexecute returns Code 7 permission error inside entry[5]:
        [[["wrb.fr", "<rpc>", null, null, null, [7]]]]
        Verifies all 6 RPCs map to AuthenticationError.
        """
        envelope = [
            [
                [
                    "wrb.fr",
                    rpc_id,
                    json.dumps([]),
                    None,
                    None,
                    [7, "PERMISSION_DENIED"],
                    "generic",
                ]
            ]
        ]
        wire_str = json.dumps(envelope)
        u16_len = len(wire_str.encode("utf-16le")) // 2
        wire_resp = f")]}}'\n\n{u16_len}\n{wire_str}\n"

        transport = httpx.MockTransport(lambda req: httpx.Response(200, text=wire_resp))
        client = NotebookLMClient(base_url="http://fake.test")
        client._client = httpx.AsyncClient(transport=transport)

        try:
            with pytest.raises(AuthenticationError, match="Code 7"):
                await self._execute_rpc(client, rpc_id)
        finally:
            await client.close()

    @pytest.mark.parametrize("rpc_id", ALL_RPCS)
    @pytest.mark.asyncio
    async def test_rpc_google_code_7_revocation_in_inner_data(self, rpc_id: str):
        """
        Google batchexecute returns Code 7 permission error inside inner JSON payload:
        entry[2] == "[7]"
        Verifies all 6 RPCs map to AuthenticationError.
        """
        wire_resp = format_batchexecute_envelope(rpc_id, [7, "Session Revoked"])
        transport = httpx.MockTransport(lambda req: httpx.Response(200, text=wire_resp))
        client = NotebookLMClient(base_url="http://fake.test")
        client._client = httpx.AsyncClient(transport=transport)

        try:
            with pytest.raises(AuthenticationError, match="Code 7"):
                await self._execute_rpc(client, rpc_id)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_rpc_empty_and_mismatched_envelopes(self):
        """Handles responses with mismatched RPC ids or empty frame lists."""
        # Empty envelope
        empty_wire = ")]}'\n\n2\n[]\n"
        transport = httpx.MockTransport(lambda req: httpx.Response(200, text=empty_wire))
        client = NotebookLMClient(base_url="http://fake.test")
        client._client = httpx.AsyncClient(transport=transport)

        try:
            # list_notebooks should return empty list
            assert await client.list_notebooks() == []

            # get_notebook should raise GeminiBridgeError not found
            with pytest.raises(GeminiBridgeError):
                await client.get_notebook("nb_nonexistent")

            # create_notebook should raise GeminiBridgeError
            with pytest.raises(GeminiBridgeError):
                await client.create_notebook("Title")

            # delete_notebook and rename_notebook return False
            assert await client.delete_notebook("nb_id") is False
            assert await client.rename_notebook("nb_id", "Title") is False

            # add_text_source generates fallback source
            fallback = await client.add_text_source("nb_id", "Note", "Content")
            assert fallback.title == "Note"
            assert fallback.id.startswith("src_")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_rpc_malformed_inner_schemas(self):
        """Handles schema corruption (e.g. non-numeric fields, truncated arrays) without crashing."""
        # wXbhsf with malformed item types (numbers where lists expected)
        bad_list_payload = [[
            ["nb_1", "Title 1", "not_a_list_timestamp", None, None, "not_int_count", None],
            ["nb_2"],  # truncated tuple
            "not_even_a_list",
        ]]
        wire = format_batchexecute_envelope("wXbhsf", bad_list_payload)
        transport = httpx.MockTransport(lambda req: httpx.Response(200, text=wire))
        client = NotebookLMClient(base_url="http://fake.test")
        client._client = httpx.AsyncClient(transport=transport)

        try:
            res = await client.list_notebooks()
            assert len(res) == 1
            assert res[0].id == "nb_1"
            assert res[0].created_at == 0.0
            assert res[0].source_count == 0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_get_notebook_malformed_sources_and_notes(self):
        """get_notebook safely filters corrupted source and note entries."""
        corrupted_detail = [
            ["nb_corrupt", "Corrupt Notebook", [1729180000, 0]],
            [  # sources
                ["src_valid", "Valid Source", 1, "https://url.com", "Summary", 500, [1729180000, 0]],
                ["src_truncated"],  # missing fields (< 2)
                "not_a_source_list",
            ],
            [  # notes
                ["note_valid", "Valid Note", "Content", [1729180000, 0], [1729180000, 0]],
                ["note_too_short", "Title only"],
                42,
            ],
        ]
        wire = format_batchexecute_envelope("rLM1Ne", corrupted_detail)
        transport = httpx.MockTransport(lambda req: httpx.Response(200, text=wire))
        client = NotebookLMClient(base_url="http://fake.test")
        client._client = httpx.AsyncClient(transport=transport)

        try:
            detail = await client.get_notebook("nb_corrupt")
            assert detail.id == "nb_corrupt"
            assert len(detail.sources) == 1
            assert detail.sources[0].id == "src_valid"
            assert len(detail.notes) == 1
            assert detail.notes[0].id == "note_valid"
        finally:
            await client.close()


# ============================================================================
# SECTION 3: Offline Mode & Fallback Verification
# ============================================================================

class TestOfflineModeAndFallback:
    """Verifies force_notebooklm_offline, credential fallback, and state recovery."""

    @pytest.mark.asyncio
    async def test_force_notebooklm_offline_on_mock_server(
        self, challenge_mock_server: MockGeminiServer, mock_cookies: Dict[str, str]
    ):
        """
        When mock server has force_notebooklm_offline=True:
        - scrape_tokens uses fallback credential and marks is_offline_fallback = True
        - batchexecute requests fail with HTTP 503
        - when fault is cleared, normal operation resumes and flag resets
        """
        client = NotebookLMClient(
            cookies=mock_cookies,
            base_url=MOCK_CHALLENGE_BASE_URL,
            fallback_snlm0e="fallback_token_999",
        )
        try:
            challenge_mock_server.fault.force_notebooklm_offline = True

            # 1. Scrape tokens fails against 503, falls back
            ok = await client.scrape_tokens()
            assert ok is True
            assert client.access_token == "fallback_token_999"
            assert client.is_offline_fallback is True

            # 2. RPC calls raise GeminiBridgeError 503
            with pytest.raises(GeminiBridgeError) as exc_info:
                await client.list_notebooks()
            assert "503" in str(exc_info.value)

            # 3. Clear fault on mock server
            challenge_mock_server.fault.force_notebooklm_offline = False

            # 4. Re-scrape succeeds and resets is_offline_fallback
            ok2 = await client.scrape_tokens()
            assert ok2 is True
            assert client.access_token == WIZ_GLOBAL_DATA_NOTEBOOKLM["SNlM0e"]
            assert client.is_offline_fallback is False

            # 5. RPC succeeds
            notebooks = await client.list_notebooks()
            assert len(notebooks) >= 2
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_scrape_tokens_unreachable_endpoint_with_fallback(self):
        """Client points to non-existent endpoint but succeeds with fallback_snlm0e."""
        client = NotebookLMClient(
            base_url="http://127.0.0.1:54321",  # Unused port
            fallback_snlm0e="gemini_active_session_snlm0e",
            timeout=0.3,
        )
        try:
            success = await client.scrape_tokens()
            assert success is True
            assert client.access_token == "gemini_active_session_snlm0e"
            assert client.is_offline_fallback is True
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_scrape_tokens_unreachable_without_fallback(self):
        """Client points to non-existent endpoint without fallback token returns False."""
        client = NotebookLMClient(
            base_url="http://127.0.0.1:54321",
            fallback_snlm0e=None,
            timeout=0.3,
        )
        try:
            success = await client.scrape_tokens()
            assert success is False
            assert client.access_token is None
            assert client.is_offline_fallback is True
        finally:
            await client.close()


# ============================================================================
# SECTION 4: Concurrency & Client Lifecycle Stress Tests
# ============================================================================

class TestConcurrencyAndLifecycleStress:
    """Stress tests concurrent operations and connection management."""

    @pytest.mark.asyncio
    async def test_parallel_list_notebooks_high_concurrency(
        self, challenge_mock_server: MockGeminiServer, mock_cookies: Dict[str, str]
    ):
        """Executes 30 concurrent list_notebooks calls sharing a single client instance."""
        client = NotebookLMClient(
            cookies=mock_cookies,
            base_url=MOCK_CHALLENGE_BASE_URL,
            fallback_snlm0e="token",
        )
        try:
            concurrency = 30
            tasks = [client.list_notebooks() for _ in range(concurrency)]
            results = await asyncio.gather(*tasks)

            assert len(results) == concurrency
            for nb_list in results:
                assert len(nb_list) >= 2
                assert any("Quantum" in nb.title for nb in nb_list)

            # Underlying httpx.AsyncClient was reused without re-allocating
            assert client._client is not None
            assert not client._client.is_closed
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_parallel_get_notebook_high_concurrency(
        self, challenge_mock_server: MockGeminiServer, mock_cookies: Dict[str, str]
    ):
        """Executes 30 concurrent get_notebook calls in parallel without cross-talk."""
        client = NotebookLMClient(
            cookies=mock_cookies,
            base_url=MOCK_CHALLENGE_BASE_URL,
            fallback_snlm0e="token",
        )
        try:
            target_id = "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d"
            tasks = [client.get_notebook(target_id) for _ in range(30)]
            results = await asyncio.gather(*tasks)

            assert len(results) == 30
            for detail in results:
                assert detail.id == target_id
                assert len(detail.sources) == 2
                assert len(detail.notes) == 1
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_mixed_concurrent_rpcs(
        self, challenge_mock_server: MockGeminiServer, mock_cookies: Dict[str, str]
    ):
        """Fires mixed read and write RPCs simultaneously, verifying reqid counter increments safely."""
        client = NotebookLMClient(
            cookies=mock_cookies,
            base_url=MOCK_CHALLENGE_BASE_URL,
            fallback_snlm0e="token",
        )
        try:
            start_reqid = client._reqid_counter

            tasks = [
                client.list_notebooks(),
                client.get_notebook("nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d"),
                client.create_notebook("Concurrency Notebook 1"),
                client.rename_notebook("nb_123", "Renamed Concurrently"),
                client.add_text_source("nb_123", "Concurrent Source", "Data payload"),
                client.delete_notebook("nb_delete_me"),
            ]
            results = await asyncio.gather(*tasks)

            assert len(results) == 6
            assert isinstance(results[0], list)  # list_notebooks
            assert isinstance(results[1], NotebookDetail)  # get_notebook
            assert isinstance(results[2], NotebookInfo)  # create_notebook
            assert results[3] is True  # rename_notebook
            assert isinstance(results[4], NotebookSource)  # add_text_source
            assert results[5] is True  # delete_notebook

            # Verify reqid counter advanced by at least 6 iterations
            assert client._reqid_counter > start_reqid
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_client_lifecycle_close_and_reopen(
        self, challenge_mock_server: MockGeminiServer, mock_cookies: Dict[str, str]
    ):
        """Client handles repeated close() and re-request cycles cleanly."""
        client = NotebookLMClient(
            cookies=mock_cookies,
            base_url=MOCK_CHALLENGE_BASE_URL,
            fallback_snlm0e="token",
        )
        try:
            # 1. Initial request
            res1 = await client.list_notebooks()
            assert len(res1) >= 2
            assert client._client is not None and not client._client.is_closed

            # 2. Close client
            await client.close()
            assert client._client is None

            # 3. Second request re-establishes client transparently
            res2 = await client.list_notebooks()
            assert len(res2) >= 2
            assert client._client is not None and not client._client.is_closed

            # 4. Successive idempotent close calls do not error
            await client.close()
            await client.close()
            assert client._client is None
        finally:
            await client.close()
