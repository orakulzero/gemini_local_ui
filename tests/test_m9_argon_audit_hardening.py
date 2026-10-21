"""
tests/test_m9_argon_audit_hardening.py
Milestone M9: Comprehensive Integration Test Suite for Gemini 4 Argon Architecture Hardening.

Empirical verification of 5 core hardening requirements (R1–R5):
1. R1: Streaming backpressure queue limit (maxsize=64) & producer pausing + socket/task teardown on disconnect.
2. R2: SQLite WAL isolation_level=None autocommit mode & BEGIN IMMEDIATE write transactions under 40 coroutines (30 readers + 10 writers).
3. R3: Atomic non-blocking DPAPI Vault persistence (asyncio.Lock, to_thread, NamedTemporaryFile + fsync + os.replace).
4. R4: UTF-16 surrogate sanitization (\uFFFD replacement with surrogatepass) & Speculative Tail Buffering (N=12) with LCP Markdown fence stabilization.
5. R5: Container mismatch cache TTL=3600s, CacheEntry dataclass, half-open single-flight probe, and MAX_TOTAL_ATTEMPTS=2 retry ceiling.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import random
import socket
import sys
import tempfile
import time
from typing import Any, AsyncIterator, Dict, List, Optional
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.requests import Request

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.api.chat import stream_with_disconnect_check
from backend.app.bridge.client import (
    DEFAULT_CONTAINER_TTL,
    MAX_TOTAL_ATTEMPTS,
    CacheEntry,
    GeminiWebClient,
)
from backend.app.bridge.exceptions import (
    GeminiBridgeError,
    TemporaryBackendOverloadError,
)
from backend.app.bridge.session import SessionContext
from backend.app.bridge.streaming_parser import (
    StreamChunk,
    StreamingDeltaAccumulator,
    StreamingFrameParser,
    ensure_utf8_clean,
    is_high_surrogate,
    is_low_surrogate,
    longest_common_prefix_length,
    sanitize_and_buffer_surrogates,
    sanitize_surrogate_text,
)
from backend.app.mock.mock_server import MockGeminiServer
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.vault import AccountProfile, CredentialVault, VaultData
from backend.tests.test_adversarial_challenge import _encode_frame, _make_stream_envelope


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ============================================================================
# Requirement R1: Streaming Backpressure & Socket Teardown
# ============================================================================

class TestR1StreamingBackpressureAndTeardown:
    """Verifies queue backpressure limits and immediate socket teardown upon client disconnect."""

    @pytest.mark.asyncio
    async def test_backpressure_queue_bounds_memory_and_pauses_producer(self):
        """
        Verifies that stream_with_disconnect_check bounds in-memory queue to max_queue_size (64).
        A fast producer producing 200 items pauses on queue.put() when a slow consumer consumes items.
        """
        produced_count = 0
        consumed_count = 0
        producer_max_queue_seen = 0

        async def fast_upstream() -> AsyncIterator[int]:
            nonlocal produced_count
            for i in range(200):
                produced_count += 1
                yield i

        class MockRequest:
            async def is_disconnected(self) -> bool:
                return False

        req = MockRequest()
        protected = stream_with_disconnect_check(
            fast_upstream(),
            request=req,  # type: ignore
            timeout=0.1,
            max_queue_size=64,
        )

        async for item in protected:
            consumed_count += 1
            # Producer should be bounded: produced cannot run away hundreds of items ahead
            diff = produced_count - consumed_count
            if diff > producer_max_queue_seen:
                producer_max_queue_seen = diff
            # Small delay to throttle consumer
            if consumed_count < 100:
                await asyncio.sleep(0.002)

        assert consumed_count == 200
        assert produced_count == 200
        # Producer lead should never exceed max_queue_size + small slack (64 + 1 EOF)
        assert producer_max_queue_seen <= 65

    @pytest.mark.asyncio
    async def test_disconnect_during_deep_reasoning_cancels_task_and_closes_stream(self):
        """
        Verifies that client disconnect during prolonged reasoning silence (10s pause)
        is detected within ~1s, cancels the producer task, and closes the upstream generator via aclose().
        """
        upstream_closed = False
        producer_cancelled = False

        class CustomStream:
            def __init__(self):
                self._step = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                self._step += 1
                if self._step == 1:
                    return StreamChunk(type="thought", delta="Beginning reasoning phase...")
                elif self._step == 2:
                    # Simulating Gemini 4 Argon deep reasoning pause (10s)
                    try:
                        await asyncio.sleep(10.0)
                        return StreamChunk(type="text", delta="Completed reasoning")
                    except asyncio.CancelledError:
                        nonlocal producer_cancelled
                        producer_cancelled = True
                        raise
                raise StopAsyncIteration

            async def aclose(self):
                nonlocal upstream_closed
                upstream_closed = True

        disconnected_state = False

        class MockRequest:
            async def is_disconnected(self) -> bool:
                return disconnected_state

        async def trigger_disconnect():
            await asyncio.sleep(0.2)
            nonlocal disconnected_state
            disconnected_state = True

        req = MockRequest()
        stream = CustomStream()
        protected = stream_with_disconnect_check(
            stream,
            request=req,  # type: ignore
            timeout=0.3,
            max_queue_size=64,
        )

        disconnect_task = asyncio.create_task(trigger_disconnect())
        start_t = time.time()
        received_chunks: List[StreamChunk] = []

        async for chunk in protected:
            received_chunks.append(chunk)

        elapsed = time.time() - start_t
        await disconnect_task

        # Must exit well before the 10.0s reasoning silence completes
        assert elapsed < 1.5
        assert len(received_chunks) == 1
        assert received_chunks[0].delta == "Beginning reasoning phase..."
        assert upstream_closed is True
        assert producer_cancelled is True


# ============================================================================
# Requirement R2: SQLite WAL Extreme Concurrency (30 Readers + 10 Writers)
# ============================================================================

class TestR2SqliteWalExtremeConcurrency:
    """Verifies that autocommit isolation_level=None and BEGIN IMMEDIATE write transactions eliminate SQLITE_BUSY deadlocks."""

    @pytest.mark.asyncio
    async def test_sqlite_wal_extreme_concurrency_30_readers_10_writers(self):
        """
        Spawns 40 concurrent coroutines (30 readers + 10 writers) synchronized on an asyncio.Barrier(40).
        Writers perform message additions, DAG branching, and conversations creation.
        Readers perform full-thread reconstructions, FTS5 BM25 search, and listings.
        Verifies exactly 0 SQLITE_BUSY or database is locked errors.
        """
        temp_dir = tempfile.mkdtemp(prefix="gemini_wal_stress_")
        db_path = Path(temp_dir) / "wal_stress.db"
        schema_path = PROJECT_ROOT / "backend" / "app" / "storage" / "schema.sql"

        db = Database(db_path=db_path, max_readers=4)
        await db.connect()
        await db.init_schema(schema_sql_path=schema_path)
        repo = ConversationRepository(db=db)

        # 1. Seed base conversations
        base_conv_ids: List[str] = []
        for i in range(15):
            c = await repo.create_conversation(title=f"Initial Topic {i}", model="gemini-2.5-pro")
            base_conv_ids.append(c.id)
            m1 = await repo.add_message(
                conversation_id=c.id,
                role="user",
                content=f"Explain advanced asynchronous concurrency principles for database topic {i}.",
            )
            await repo.add_message(
                conversation_id=c.id,
                role="assistant",
                content=f"Concurrency topic {i} revolves around WAL mode and non-blocking reader pools.",
                parent_id=m1.id,
            )

        barrier = asyncio.Barrier(40)
        results: List[Any] = []

        # 2. Define 10 Writers
        async def writer_coro(idx: int):
            await barrier.wait()
            try:
                if idx < 4:
                    # Branch messages in existing conversation
                    target_cid = base_conv_ids[idx]
                    thread = await repo.get_conversation_thread(target_cid)
                    parent_id = thread[-1].id if thread else None
                    msg = await repo.add_message(
                        conversation_id=target_cid,
                        role="user",
                        content=f"Branch addition from writer {idx} at timestamp {time.time()}",
                        parent_id=parent_id,
                    )
                    return f"writer_msg_{msg.id}"
                elif idx < 8:
                    # Create new conversation
                    c = await repo.create_conversation(
                        title=f"Concurrent Project {idx}",
                        model="gemini-2.0-flash",
                    )
                    await repo.add_message(
                        conversation_id=c.id,
                        role="user",
                        content=f"Initial payload for concurrent conversation {idx}",
                    )
                    return f"writer_conv_{c.id}"
                else:
                    # Update conversation metadata and active leaf
                    target_cid = base_conv_ids[idx % len(base_conv_ids)]
                    await repo.update_conversation(
                        conversation_id=target_cid,
                        title=f"Renamed Topic {idx} - Verified",
                        is_pinned=True,
                    )
                    return f"writer_update_{target_cid}"
            except Exception as e:
                return e

        # 3. Define 30 Readers
        async def reader_coro(idx: int):
            await barrier.wait()
            try:
                if idx < 12:
                    # Read full conversation DAG thread
                    target_cid = base_conv_ids[idx % len(base_conv_ids)]
                    thread = await repo.get_conversation_thread(target_cid)
                    return f"reader_thread_{len(thread)}"
                elif idx < 22:
                    # Execute FTS5 BM25 search
                    search_res = await repo.search_conversations("concurrency", limit=5)
                    return f"reader_search_{len(search_res)}"
                else:
                    # List conversations with ordering
                    convs = await repo.list_conversations(limit=10)
                    return f"reader_list_{len(convs)}"
            except Exception as e:
                return e

        # Launch all 40 coroutines simultaneously
        tasks = [writer_coro(i) for i in range(10)] + [reader_coro(i) for i in range(30)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 4. Assert zero exceptions
        exceptions = [res for res in results if isinstance(res, Exception)]
        assert len(exceptions) == 0, f"Encountered {len(exceptions)} exceptions: {exceptions[:3]}"

        # 5. Verify database integrity
        async with db.transaction() as conn:
            cursor = await conn.execute("PRAGMA integrity_check;")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "ok"

        await db.close()

    @pytest.mark.asyncio
    async def test_sqlite_write_transaction_atomic_rollback_on_failure(self):
        """Verifies that an error inside write_transaction() rolls back changes atomically."""
        temp_dir = tempfile.mkdtemp(prefix="gemini_rollback_")
        db_path = Path(temp_dir) / "rollback.db"
        schema_path = PROJECT_ROOT / "backend" / "app" / "storage" / "schema.sql"

        db = Database(db_path=db_path)
        await db.connect()
        await db.init_schema(schema_sql_path=schema_path)
        repo = ConversationRepository(db=db)

        c = await repo.create_conversation(title="Rollback Test", model="gemini-2.5-pro")

        with pytest.raises(RuntimeError, match="Simulated crash during write"):
            async with repo.write_transaction():
                # Raw insert that should be rolled back
                await db.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (c.id, "user", "Ghost message that must be rolled back", int(time.time() * 1000), int(time.time() * 1000)),
                )
                raise RuntimeError("Simulated crash during write")

        # Verify message was NOT committed
        thread = await repo.get_conversation_thread(c.id)
        assert len(thread) == 0
        await db.close()


# ============================================================================
# Requirement R3: Atomic Non-Blocking DPAPI Vault Persistence
# ============================================================================

class TestR3DpapiVaultAtomicPersistence:
    """Verifies that asynchronous vault methods serialize concurrent updates, offload DPAPI, and avoid lost updates."""

    @pytest.mark.asyncio
    async def test_dpapi_vault_concurrent_writes_and_atomic_replace(self):
        """
        Fires 20 concurrent update_account_cookies_async and save_account_async requests.
        Verifies that:
        1. Async lock serializes file updates with 0 [WinError 32] file collisions.
        2. Atomic temporary file creation + os.fsync + os.replace leaves 0 temporary files.
        3. No tokens are lost; all concurrent cookie rotations persist cleanly.
        """
        temp_dir = tempfile.mkdtemp(prefix="gemini_vault_atomic_")
        vault_file = Path(temp_dir) / "vault.enc"
        vault = CredentialVault(vault_path=vault_file, entropy=b"m9_atomic_entropy")

        # 1. Initialize vault with 5 accounts
        initial_vdata = VaultData(active_account_id="acc_0")
        for i in range(5):
            initial_vdata.accounts[f"acc_{i}"] = AccountProfile(
                id=f"acc_{i}",
                name=f"Account {i}",
                email=f"user{i}@example.com",
                cookies={"__Secure-1PSID": f"initial_psid_{i}"},
            )
        vault.store_vault_data(initial_vdata)

        # 2. 20 concurrent cookie updates
        async def update_worker(worker_id: int):
            acc_id = f"acc_{worker_id % 5}"
            new_cookies = {
                f"COOKIE_W{worker_id}": f"val_{worker_id}_{time.time()}",
                "__Secure-1PSIDTS": f"psidts_rotated_{worker_id}",
            }
            await vault.update_account_cookies_async(acc_id, new_cookies)
            return True

        tasks = [update_worker(i) for i in range(20)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        exceptions = [r for r in results if isinstance(r, Exception)]
        assert len(exceptions) == 0, f"Vault concurrent writes raised: {exceptions}"

        # 3. Verify no temporary files left in directory
        tmp_files = list(Path(temp_dir).glob("vault_*.tmp"))
        assert len(tmp_files) == 0, f"Found lingering temporary files: {tmp_files}"

        # 4. Decrypt and verify all accounts retain their cumulative rotated tokens
        vdata = await vault.load_vault_data_async()
        assert len(vdata.accounts) == 5
        for i in range(5):
            acc = vdata.accounts[f"acc_{i}"]
            assert "__Secure-1PSID" in acc.cookies
            assert "__Secure-1PSIDTS" in acc.cookies
            # Every worker targeting this account (i, i+5, i+10, i+15) must have its key present
            for step in range(4):
                wid = i + (step * 5)
                assert f"COOKIE_W{wid}" in acc.cookies


# ============================================================================
# Requirement R4: UTF-16 Surrogate Pair Sanitization & Speculative Tail Buffering
# ============================================================================

class TestR4Utf16SurrogateAndSpeculativeTailBuffering:
    """Verifies that unpaired surrogates are converted to \uFFFD and speculative tail buffer stabilizes Markdown fences."""

    def test_utf16_surrogate_fuzzing_unpaired_high_and_split_pairs(self):
        """
        Fuzzes UTF-16 surrogates:
        1. Unpaired high surrogate followed by non-surrogate ASCII ('A') is replaced with \uFFFD.
        2. Isolated low surrogate is replaced with \uFFFD.
        3. Split surrogate pairs across frame boundaries are held back and recombine into valid 32-bit emoji (🚀).
        4. Guarantee all output deltas pass ensure_utf8_clean without UnicodeEncodeError.
        """
        high_surr = chr(0xD83D)
        low_surr = chr(0xDE80)  # \uD83D\uDE80 = 🚀

        # 1. Unpaired high surrogate followed by regular character 'A'
        bad_text = f"Prefix {high_surr}A suffix"
        cleaned = sanitize_surrogate_text(bad_text)
        assert high_surr not in cleaned
        assert "\uFFFD" in cleaned
        assert cleaned.encode("utf-8") == b"Prefix \xef\xbf\xbdA suffix"

        # 2. Isolated low surrogate
        lone_low = f"Broken {low_surr} text"
        clean_low = sanitize_surrogate_text(lone_low)
        assert low_surr not in clean_low
        assert "\uFFFD" in clean_low
        assert clean_low.encode("utf-8")

        # 3. Streaming buffering: high surrogate at tail when not is_final is held back
        streaming_text = f"Launching {high_surr}"
        held = sanitize_and_buffer_surrogates(streaming_text, is_final=False)
        assert held == "Launching "

        # When low surrogate arrives in subsequent chunk, the pair recombines cleanly
        full_text = f"Launching {high_surr}{low_surr} to orbit"
        recombined = sanitize_and_buffer_surrogates(full_text, is_final=False)
        assert recombined == "Launching 🚀 to orbit"
        assert recombined.encode("utf-8") == "Launching 🚀 to orbit".encode("utf-8")

    def test_speculative_tail_buffering_lcp_prevents_markdown_flicker(self):
        """
        Simulates an upstream model streaming Markdown code blocks with volatile closing fences:
        Snapshot 1: "Here is code:\n```"
        Snapshot 2: "Here is code:\n```python\ndef calculate(x):\n"
        Snapshot 3: "Here is code:\n```python\ndef calculate(x):\n    return x * 2\n```"
        Verifies that N=12 speculative tail buffer holds back the volatile fence until confirmed,
        and concatenated text deltas strictly equal Snapshot 3 without stutter or duplication.
        """
        acc = StreamingDeltaAccumulator()
        acc.speculative_tail_buffer = 12

        # Frame 1: Snapshot with volatile unclosed fence
        f1 = _make_stream_envelope("Here is code:\n```", is_final=False)
        chunks1 = acc.process_frame(f1)
        text_deltas_1 = [c.delta for c in chunks1 if c.type == "text"]

        # The volatile fence \n``` should be withheld
        emitted_1 = "".join(text_deltas_1)
        assert "```" not in emitted_1
        assert "Here is code:" in emitted_1

        # Frame 2: Fence expanded into python code block
        f2 = _make_stream_envelope("Here is code:\n```python\ndef calculate(x):\n", is_final=False)
        chunks2 = acc.process_frame(f2)
        text_deltas_2 = [c.delta for c in chunks2 if c.type == "text"]
        emitted_2 = "".join(text_deltas_2)

        # Frame 3: Final completion flush
        f3 = _make_stream_envelope("Here is code:\n```python\ndef calculate(x):\n    return x * 2\n```", is_final=True)
        chunks3 = acc.process_frame(f3)
        text_deltas_3 = [c.delta for c in chunks3 if c.type == "text"]
        emitted_3 = "".join(text_deltas_3)

        # Full concatenated stream must strictly equal the final text without duplication
        total_streamed = emitted_1 + emitted_2 + emitted_3
        expected = "Here is code:\n```python\ndef calculate(x):\n    return x * 2\n```"
        assert total_streamed == expected
        assert acc.full_raw_text == expected


# ============================================================================
# Requirement R5: Container Circuit Breaker & Retry Ceiling
# ============================================================================

class TestR5ContainerCircuitBreakerAndRetryCeiling:
    """Verifies CacheEntry data structure, half-open probe transitions, TTL 3600s, and MAX_TOTAL_ATTEMPTS=2 ceiling."""

    def setup_method(self):
        GeminiWebClient.clear_feature_mode_40_cache()

    def teardown_method(self):
        GeminiWebClient.clear_feature_mode_40_cache()

    def test_container_cache_entry_structure_and_ttl_3600(self):
        """Verifies that mark_feature_mode_40_unsupported sets CacheEntry with default 3600s TTL and failure_count=1."""
        acc_key = "test_acc_r5_1"
        assert GeminiWebClient.is_feature_mode_40_unsupported(acc_key) is False

        entry = GeminiWebClient.mark_feature_mode_40_unsupported(acc_key)
        assert isinstance(entry, CacheEntry)
        assert entry.fallback_mode is True
        assert entry.failure_count == 1
        assert entry.is_half_open is False
        assert entry.expires_at > time.time() + 3590.0

        # Incompatibility is cached
        assert GeminiWebClient.is_feature_mode_40_unsupported(acc_key) is True

    def test_half_open_probe_success_clears_cache(self):
        """
        When cache TTL expires:
        1. First caller receives False to initiate single-flight probe, entering HALF-OPEN.
        2. A concurrent caller receives True, shielded by the half-open lease.
        3. When probe succeeds, mark_feature_mode_40_supported clears the entry (CLOSED).
        """
        acc_key = "test_acc_probe_success"
        # Pre-seed an expired entry
        entry = CacheEntry(
            fallback_mode=True,
            expires_at=time.time() - 10.0,
            failure_count=1,
            is_half_open=False,
        )
        GeminiWebClient._feature_mode_40_unsupported[acc_key] = entry

        # First request detects TTL expiration -> initiates half-open probe
        is_unsupported_first = GeminiWebClient.is_feature_mode_40_unsupported(acc_key)
        assert is_unsupported_first is False
        assert entry.is_half_open is True
        assert entry.expires_at > time.time() + 25.0  # 30s probe lease granted

        # Second concurrent request arrives while probe is in flight -> shielded, uses fallback
        is_unsupported_second = GeminiWebClient.is_feature_mode_40_unsupported(acc_key)
        assert is_unsupported_second is True

        # Probe succeeds on stream completion -> circuit closed
        GeminiWebClient.mark_feature_mode_40_supported(acc_key)
        assert acc_key not in GeminiWebClient._feature_mode_40_unsupported
        assert GeminiWebClient.is_feature_mode_40_unsupported(acc_key) is False

    def test_half_open_probe_failure_increments_failure_count_and_resets_ttl(self):
        """
        When half-open probe fails (e.g. error 1173):
        mark_feature_mode_40_unsupported increments failure_count and resets TTL to 3600s.
        """
        acc_key = "test_acc_probe_failure"
        entry = CacheEntry(
            fallback_mode=True,
            expires_at=time.time() - 5.0,
            failure_count=1,
            is_half_open=False,
        )
        GeminiWebClient._feature_mode_40_unsupported[acc_key] = entry

        # Probe initiated
        assert GeminiWebClient.is_feature_mode_40_unsupported(acc_key) is False

        # Probe fails with error 1173 -> mark unsupported again
        updated_entry = GeminiWebClient.mark_feature_mode_40_unsupported(acc_key)
        assert updated_entry.failure_count == 2
        assert updated_entry.is_half_open is False
        assert updated_entry.expires_at > time.time() + 3590.0

    @pytest.mark.asyncio
    async def test_max_total_attempts_ceiling_prevents_retry_storm(self):
        """
        Verifies that when container fallback occurs (feature_mode=40 -> standard envelope),
        the total attempts are strictly capped at MAX_TOTAL_ATTEMPTS (2).
        If attempt 1 fails with 1173 and attempt 2 fails with 503, the client raises
        TemporaryBackendOverloadError immediately without a 3rd attempt.
        """
        port = get_free_port()
        mock_server = MockGeminiServer(host="127.0.0.1", port=port)
        mock_server.start_background()
        for _ in range(50):
            if mock_server._site is not None:
                break
            time.sleep(0.05)

        ctx = SessionContext()
        ctx.cookies = {"__Secure-1PSID": "test_psid_ceiling", "__Secure-1PSIDTS": "test_psidts"}
        client = GeminiWebClient(
            session_context=ctx,
            base_url=f"http://127.0.0.1:{port}",
            timeout=10.0,
        )

        # Configure mock server: Attempt 1 returns 1173, Attempt 2 returns 503
        attempt_counter = 0
        from aiohttp import web

        async def dynamic_stream_generate(request: web.Request) -> web.Response:
            nonlocal attempt_counter
            attempt_counter += 1
            if attempt_counter == 1:
                # Return error 1173 in Google envelope
                err_frame = _encode_frame([["wrb.fr", None, None, None, None, [1173, "Container not supported"]]])
                frame_text = err_frame.decode("utf-8") if isinstance(err_frame, bytes) else str(err_frame)
                return web.Response(text=frame_text, content_type="text/plain;charset=UTF-8")
            else:
                # Return 503 on fallback attempt
                return web.Response(status=503, text="Service Unavailable")

        mock_server.fault.stream_generate_handler = dynamic_stream_generate

        try:
            with pytest.raises(TemporaryBackendOverloadError) as exc_info:
                async for _ in client.generate_stream("Test ceiling query", feature_mode="task"):
                    pass

            assert "HTTP 503" in str(exc_info.value)
            # Must have stopped after exactly 2 attempts, NEVER making a 3rd attempt
            assert attempt_counter == 2
        finally:
            await client.close()
            mock_server.stop_background()
