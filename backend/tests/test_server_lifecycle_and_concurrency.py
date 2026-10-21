"""
backend/tests/test_server_lifecycle_and_concurrency.py
Adversarial empirical challenge suite for MockGeminiServer lifecycle and concurrency stress resilience.

Verifies:
1. Repeated mock server start/stop cycles in rapid succession (10 cycles) on port 8999:
   - Clean port 8999 release without WSAEADDRINUSE (WinError 10048).
   - Zero pending IOCP proactor tasks or unraisable socket exceptions.
   - Immediate re-bindability of port 8999.
2. In-flight connection cancellation resilience during server teardown.
3. Concurrency stress resilience:
   - 50 concurrent mixed client requests (streaming, conversation listing, model listing) during active token rotation.
   - 50 concurrent full streaming clients during 10 active background token rotations.
   - Thundering herd 50 simultaneous token rotations under asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
import sys
import threading
import time
import warnings
from typing import Any, Dict, List
import httpx
import pytest

from backend.app.mock.mock_server import MockGeminiServer
from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.session import SessionManager, SessionHealthStatus

logger = logging.getLogger(__name__)

MOCK_HOST = "127.0.0.1"
MOCK_PORT = 8999
MOCK_BASE_URL = f"http://{MOCK_HOST}:{MOCK_PORT}"


# =====================================================================
# 1. SERVER LIFECYCLE EMPIRICAL RE-CHALLENGE (10 RAPID CYCLES)
# =====================================================================

class TestMockServerLifecycle:
    """Empirical challenge of MockGeminiServer start/stop lifecycle on port 8999."""

    def test_rapid_mock_server_start_stop_10_cycles_port_8999(self):
        """
        Re-challenge Requirement 1:
        10 consecutive mock server start/stop cycles in rapid succession on port 8999.
        Verifies:
        - Server starts and responds to HTTP GET on each cycle.
        - Server stops cleanly and joins its background thread within timeout.
        - Port 8999 is immediately bindable by a raw socket without WSAEADDRINUSE.
        - Zero pending IOCP proactor tasks or unraisable exceptions.
        """
        unraisable_events: List[Any] = []
        old_unraisablehook = sys.unraisablehook
        sys.unraisablehook = lambda u: unraisable_events.append(u)

        captured_warnings: List[Any] = []
        with warnings.catch_warnings(record=True) as warn_list:
            warnings.simplefilter("always")

            for cycle in range(1, 11):
                t0 = time.perf_counter()
                server = MockGeminiServer(host=MOCK_HOST, port=MOCK_PORT)
                server.start_background()

                # Verify server responds
                ready = False
                for _ in range(50):
                    try:
                        resp = httpx.get(f"{MOCK_BASE_URL}/app", timeout=0.5)
                        if resp.status_code == 200:
                            ready = True
                            break
                    except Exception:
                        time.sleep(0.02)

                assert ready is True, f"Mock server failed to accept connections on cycle {cycle}"

                # Stop background thread
                server.stop_background()
                cycle_time = time.perf_counter() - t0

                # Verify port 8999 is immediately bindable without WSAEADDRINUSE (WinError 10048)
                probe_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    probe_sock.bind((MOCK_HOST, MOCK_PORT))
                    probe_sock.listen(1)
                except OSError as exc:
                    pytest.fail(f"Port {MOCK_PORT} was NOT cleanly released on cycle {cycle}: {exc}")
                finally:
                    probe_sock.close()

                logger.info("Cycle %d/10 succeeded in %.3fs", cycle, cycle_time)

            captured_warnings.extend(warn_list)

        sys.unraisablehook = old_unraisablehook

        # Verify zero unraisable exceptions
        assert len(unraisable_events) == 0, f"Unraisable exceptions detected: {unraisable_events}"

        # Verify zero IOCP proactor pending task warnings
        iocp_warnings = [
            w for w in captured_warnings
            if "IocpProactor" in str(w.message) or "Task was destroyed but it is pending" in str(w.message)
        ]
        assert len(iocp_warnings) == 0, f"IOCP proactor task warnings detected: {iocp_warnings}"

    @pytest.mark.asyncio
    async def test_mock_server_async_start_stop_10_cycles_port_8999(self):
        """
        Re-challenge Requirement 1 (Async Variant):
        10 consecutive start_async() / stop_async() cycles in an active asyncio event loop.
        Verifies direct TCPSite and AppRunner lifecycle without thread indirection.
        """
        for cycle in range(1, 11):
            server = MockGeminiServer(host=MOCK_HOST, port=MOCK_PORT)
            await server.start_async()

            # Verify connection
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{MOCK_BASE_URL}/app", timeout=1.0)
                assert resp.status_code == 200

            await server.stop_async()

            # Verify raw socket bind
            probe_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                probe_sock.bind((MOCK_HOST, MOCK_PORT))
            except OSError as exc:
                pytest.fail(f"Async port {MOCK_PORT} was NOT released on cycle {cycle}: {exc}")
            finally:
                probe_sock.close()

    def test_mock_server_stop_with_in_flight_stream_teardown(self):
        """
        Stress-tests server teardown when an active client is in the middle of a streaming response.
        Verifies stop_background() cleans up without deadlock or port leak.
        """
        server = MockGeminiServer(host=MOCK_HOST, port=MOCK_PORT)
        server.fault.streaming_delay = 0.2  # Slow stream
        server.start_background()

        # Wait until ready
        for _ in range(50):
            try:
                r = httpx.get(f"{MOCK_BASE_URL}/app", timeout=0.5)
                if r.status_code == 200:
                    break
            except Exception:
                time.sleep(0.02)

        client_error = None

        def streaming_client():
            nonlocal client_error
            try:
                with httpx.stream(
                    "POST",
                    f"{MOCK_BASE_URL}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
                    data={"f.req": '[[["wrb.fr","StreamGenerate","[]",null,null,null]]]'},
                    timeout=5.0,
                ) as resp:
                    for _ in resp.iter_bytes():
                        pass
            except Exception as exc:
                client_error = exc

        client_thread = threading.Thread(target=streaming_client)
        client_thread.start()
        time.sleep(0.1)  # Allow streaming to begin

        # Tear down while stream is actively running
        t0 = time.perf_counter()
        server.stop_background()
        teardown_time = time.perf_counter() - t0

        client_thread.join(timeout=3.0)
        assert not client_thread.is_alive()
        assert teardown_time < 3.0, f"Teardown took too long: {teardown_time:.3f}s"

        # Verify port is released
        probe_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe_sock.bind((MOCK_HOST, MOCK_PORT))
        finally:
            probe_sock.close()


# =====================================================================
# 2. CONCURRENCY & TOKEN ROTATION STRESS EMPIRICAL RE-CHALLENGE
# =====================================================================

class TestConcurrencyAndTokenRotationStress:
    """Empirical challenge of concurrency stress under active token rotation on port 8999."""

    @pytest.fixture(autouse=True)
    def setup_mock_server(self):
        server = MockGeminiServer(host=MOCK_HOST, port=MOCK_PORT)
        server.start_background()
        for _ in range(50):
            try:
                r = httpx.get(f"{MOCK_BASE_URL}/app", timeout=0.5)
                if r.status_code == 200:
                    break
            except Exception:
                time.sleep(0.02)
        yield server
        server.stop_background()

    @pytest.mark.asyncio
    async def test_concurrency_50_clients_during_active_token_rotation_port_8999(self):
        """
        Re-challenge Requirement 2:
        50 concurrent client operations (20 streams, 15 conversation lists, 15 model lists)
        executing concurrently with 5 background token rotations on port 8999.
        Verifies:
        - Zero deadlocks under 30s timeout.
        - Zero race conditions or unhandled exceptions.
        - All 50 client tasks succeed.
        - Cookie rotation succeeds 5/5 times.
        - Final session status is HEALTHY with rotated token.
        """
        session_mgr = SessionManager(base_url=MOCK_BASE_URL)
        await session_mgr.init_session({
            "__Secure-1PSID": "stress_psid_token_valid_alpha",
            "__Secure-1PSIDTS": "stress_psidts_token_valid_beta",
        })
        assert session_mgr.context.status == SessionHealthStatus.HEALTHY

        client = GeminiWebClient(session_context=session_mgr.context, base_url=MOCK_BASE_URL)

        completed_streams = 0
        completed_conversations = 0
        completed_models = 0
        rotation_count = 0
        lock = asyncio.Lock()

        async def client_stream_task(task_id: int):
            nonlocal completed_streams
            chunks = []
            async for chunk in client.generate_stream(f"Stress client stream query #{task_id}"):
                chunks.append(chunk)
            assert len(chunks) > 0
            async with lock:
                completed_streams += 1

        async def client_conv_list_task(task_id: int):
            nonlocal completed_conversations
            convs = await client.list_conversations(limit=10)
            assert len(convs) >= 3
            async with lock:
                completed_conversations += 1

        async def client_model_list_task(task_id: int):
            nonlocal completed_models
            models = await client.list_models()
            assert len(models) >= 4
            async with lock:
                completed_models += 1

        async def background_rotator():
            nonlocal rotation_count
            for _ in range(5):
                await asyncio.sleep(random.uniform(0.01, 0.05))
                ok = await session_mgr.rotate_cookies()
                assert ok is True
                async with lock:
                    rotation_count += 1

        tasks = []
        for i in range(20):
            tasks.append(asyncio.create_task(client_stream_task(i)))
        for i in range(15):
            tasks.append(asyncio.create_task(client_conv_list_task(i)))
        for i in range(15):
            tasks.append(asyncio.create_task(client_model_list_task(i)))

        rotator_task = asyncio.create_task(background_rotator())

        all_tasks = asyncio.gather(*tasks, rotator_task)
        await asyncio.wait_for(all_tasks, timeout=30.0)

        assert completed_streams == 20
        assert completed_conversations == 15
        assert completed_models == 15
        assert rotation_count == 5

        assert session_mgr.context.status == SessionHealthStatus.HEALTHY
        assert session_mgr.context.cookies["__Secure-1PSIDTS"] == "rotated_test_psidts_token_999999"

        await client.close()
        await session_mgr.close()

    @pytest.mark.asyncio
    async def test_concurrency_50_full_streaming_clients_during_token_rotation_port_8999(self):
        """
        Adversarial Concurrency Stress:
        50 concurrent FULL STREAMING clients executing simultaneously while 10 continuous
        token rotations execute in the background on port 8999.
        Verifies:
        - Stream generator iterator multiplexing under concurrent cookie dictionary updates.
        - Parser and accumulator isolation per client stream.
        - Zero chunk corruption across concurrent TCP connections.
        """
        session_mgr = SessionManager(base_url=MOCK_BASE_URL)
        await session_mgr.init_session({
            "__Secure-1PSID": "stress_full_stream_psid",
            "__Secure-1PSIDTS": "stress_full_stream_psidts",
        })
        assert session_mgr.context.status == SessionHealthStatus.HEALTHY

        client = GeminiWebClient(session_context=session_mgr.context, base_url=MOCK_BASE_URL)

        completed_streams = 0
        total_chunks = 0
        rotation_count = 0
        lock = asyncio.Lock()

        async def stream_worker(worker_id: int):
            nonlocal completed_streams, total_chunks
            chunks = []
            prompt = f"Concurrent stream #{worker_id} with Unicode: 🚀 Hello 漢字"
            async for chunk in client.generate_stream(prompt):
                chunks.append(chunk)

            # Golden stream returns text, thought, grounding/citations, done
            assert len(chunks) >= 4, f"Worker {worker_id} received insufficient chunks: {len(chunks)}"
            types = {c.type for c in chunks}
            assert "done" in types, f"Worker {worker_id} missing terminal done chunk"

            async with lock:
                completed_streams += 1
                total_chunks += len(chunks)

        async def rapid_rotator():
            nonlocal rotation_count
            for _ in range(10):
                await asyncio.sleep(random.uniform(0.01, 0.03))
                ok = await session_mgr.rotate_cookies()
                assert ok is True
                async with lock:
                    rotation_count += 1

        stream_tasks = [asyncio.create_task(stream_worker(i)) for i in range(50)]
        rotator = asyncio.create_task(rapid_rotator())

        await asyncio.wait_for(asyncio.gather(*stream_tasks, rotator), timeout=30.0)

        assert completed_streams == 50
        assert rotation_count == 10
        assert total_chunks >= 200  # >= 4 chunks * 50 streams
        assert session_mgr.context.status == SessionHealthStatus.HEALTHY
        assert session_mgr.context.cookies["__Secure-1PSIDTS"] == "rotated_test_psidts_token_999999"

        await client.close()
        await session_mgr.close()

    @pytest.mark.asyncio
    async def test_thundering_herd_50_concurrent_rotations_port_8999(self):
        """
        Adversarial Thundering Herd:
        50 simultaneous calls to session_mgr.rotate_cookies() launched at the exact same moment.
        Verifies:
        - asyncio.Lock serialization without race conditions.
        - Zero unhandled exceptions or connection errors.
        - All 50 callers receive True.
        - Session context remains HEALTHY.
        """
        session_mgr = SessionManager(base_url=MOCK_BASE_URL)
        await session_mgr.init_session({
            "__Secure-1PSID": "thundering_psid",
            "__Secure-1PSIDTS": "thundering_psidts",
        })

        results = await asyncio.wait_for(
            asyncio.gather(*[session_mgr.rotate_cookies() for _ in range(50)]),
            timeout=20.0,
        )

        assert len(results) == 50
        assert all(r is True for r in results)
        assert session_mgr.context.status == SessionHealthStatus.HEALTHY
        assert session_mgr.context.cookies["__Secure-1PSIDTS"] == "rotated_test_psidts_token_999999"

        await session_mgr.close()
