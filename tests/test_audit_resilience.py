"""
tests/test_audit_resilience.py
Resilience and edge-case stress test suite for Gemini Local UI & Task Mode.
Zero-facade, authentic system integration testing across:
1. StreamingFrameParser: multi-byte UTF-8 byte fragmentation, surrogatepass resilience (line 129),
   length prefix parsing limits, and StreamingDeltaAccumulator flicker stripping.
2. GeminiWebClient: Task container envelope fallback (codes 1061, 1173 from feature_mode 40 to None),
   and immediate raise of codes 1037 (usage limit) and 7 (auth).
3. POST /api/chat/generate: Client mid-stream disconnect handling during reasoning, zero corrupt state,
   and stream error event emission with is_error=True assistant message persistence.
4. GeminiUploader & gemini_task: TLS bad record MAC retry recovery, HTTP 413 non-retry rejection,
   context packing noise directory filtering, and materials extraction path traversal blocking.
5. Database SQLite WAL: high-concurrency readers + writers without lock errors, and atomic transaction rollback.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# CLI task module path
SKILL_SCRIPT_DIR = PROJECT_ROOT / ".agents" / "skills" / "gemini-task" / "scripts"
if SKILL_SCRIPT_DIR.exists() and str(SKILL_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPT_DIR))

import gemini_task

from backend.app.bridge.exceptions import (
    AuthenticationError,
    GeminiBridgeError,
    ModelInconsistentError,
    TemporaryBackendOverloadError,
    UsageLimitExceededError,
)
from backend.app.bridge.streaming_parser import (
    StreamChunk,
    StreamingDeltaAccumulator,
    StreamingFrameParser,
)
from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.session import AccountSession, SessionContext, SessionManager
from backend.app.bridge.uploader import GeminiUploader
from backend.app.mock.fixtures import (
    build_stream_error_frame,
    format_stream_frame,
)
from backend.app.mock.mock_server import MockGeminiServer
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.vault import AccountProfile, CredentialVault
from backend.tests.test_adversarial_challenge import _encode_frame, _make_stream_envelope


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class BackgroundUvicornServer:
    """Runs FastAPI app on a dedicated TCP port for authentic network tests."""

    def __init__(self, app_instance, host: str = "127.0.0.1", port: int = 8998) -> None:
        self.host = host
        self.port = port
        self.config = uvicorn.Config(app_instance, host=host, port=port, log_level="warning")
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        timeout = 5.0
        start = time.time()
        while not self.server.started and (time.time() - start) < timeout:
            time.sleep(0.05)
        if not self.server.started:
            raise RuntimeError(f"Uvicorn test server failed to start within {timeout}s")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=3.0)


# ============================================================================
# Class 1: StreamingParser Surrogate & Framing Resilience
# ============================================================================

class TestStreamingParserSurrogateAndFramingResilience:
    """Adversarial framing, UTF-8 fragmentation, surrogatepass, and accumulator testing."""

    def test_utf8_byte_fragmentation_astral_plane_and_multilingual(self):
        """Feeds astral emojis and multilingual text byte-by-byte, proving no replacement character corruption."""
        parser = StreamingFrameParser()
        content = "🚀 Rocket 🌍 Earth 🧪 Test — Hello world — こんにちは世界 — مرحبا"
        payload = [["content", content]]
        wire_data = b")]}'\n" + _encode_frame(payload)

        extracted_frames = []
        for b in wire_data:
            res = parser.feed(bytes([b]))
            if res:
                extracted_frames.extend(res)

        assert len(extracted_frames) == 1
        text_received = extracted_frames[0][0][1]
        assert text_received == content
        assert "\ufffd" not in text_received

    def test_unpaired_surrogate_surrogatepass_resilience(self):
        """Verifies that StreamingFrameParser safely processes incomplete surrogate fragments without UnicodeEncodeError at line 129."""
        parser = StreamingFrameParser()
        # High surrogate fed in incomplete frame
        high_surrogate = "\ud83d"
        # Length header of 50 code units, remainder begins with high surrogate
        chunk1 = f"50\n{{\"char\": \"{high_surrogate}"
        frames1 = parser.feed(chunk1)
        # Without errors="surrogatepass", line 129 crashes here with UnicodeEncodeError
        assert frames1 == []
        assert high_surrogate in parser._buffer

        # Complete the surrogate pair with low surrogate \ude00 (😄)
        low_surrogate = "\ude00"
        completed_body = f'{{"char": "{high_surrogate}{low_surrogate}"}}\n'
        body_u16_len = len(completed_body.encode("utf-16le", errors="surrogatepass")) // 2
        padding_needed = max(0, 50 - body_u16_len)
        chunk2 = f"{low_surrogate}\"}}{' ' * padding_needed}\n"
        frames2 = parser.feed(chunk2)
        assert isinstance(frames2, list)

    def test_fragmented_length_header_and_buffer_limits(self):
        """Feeds length prefix byte-by-byte and verifies enforcement of maximum frame buffer boundaries."""
        parser = StreamingFrameParser()
        payload = [["greeting", "Hello Framing"]]
        frame = _encode_frame(payload)

        # Feed prefix 1 byte at a time
        idx = frame.find(b"\n")
        header_part = frame[:idx+1]
        body_part = frame[idx+1:]

        for b in header_part:
            res = parser.feed(bytes([b]))
            assert res == []

        frames = parser.feed(body_part)
        assert len(frames) == 1
        assert frames[0][0][1] == "Hello Framing"

        # Negative length header recovery
        parser.reset()
        res1 = parser.feed("-100\n[1, 2, 3]\n")
        assert res1 == []
        res2 = parser.feed(_encode_frame([["recovered"]]))
        assert len(res2) == 1
        assert res2[0][0][0] == "recovered"

        # Oversized length header recovery
        parser.reset()
        res3 = parser.feed("20000000\n[1, 2, 3]\n")
        assert res3 == []
        res4 = parser.feed(_encode_frame([["recovered_oversized"]]))
        assert len(res4) == 1
        assert res4[0][0][0] == "recovered_oversized"

    def test_streaming_delta_accumulator_cumulative_and_flicker_stripping(self):
        """Tests delta calculation and progressive thought/text emission."""
        acc = StreamingDeltaAccumulator()

        # Phase 1: Thought progress
        f1 = _make_stream_envelope("", thoughts="Thinking about solution...")
        c1 = acc.process_frame(f1)
        assert len(c1) >= 1
        assert c1[0].type == "thought"
        assert c1[0].delta == "Thinking about solution..."

        # Phase 2: Cumulative text delta
        f2 = _make_stream_envelope("Hello")
        c2 = acc.process_frame(f2)
        assert any(c.type == "text" and c.delta == "Hello" for c in c2)

        f3 = _make_stream_envelope("Hello World", is_final=True)
        c3 = acc.process_frame(f3)
        assert any(c.type == "text" and c.delta == " World" for c in c3)
        assert acc.full_raw_text == "Hello World"
        assert acc.is_completed is True


# ============================================================================
# Class 2: Client RPC & Fallback Resilience
# ============================================================================

class TestClientRpcAndFallbackResilience:
    """Validates GeminiWebClient Task mode envelope fallback and error code propagation."""

    @pytest.fixture(autouse=True)
    def setup_mock(self):
        GeminiWebClient.clear_feature_mode_40_cache()
        self.mock_port = get_free_port()
        self.mock_server = MockGeminiServer(host="127.0.0.1", port=self.mock_port)
        self.mock_server.start_background()
        for _ in range(50):
            if self.mock_server._site is not None:
                break
            time.sleep(0.05)
        self.ctx = SessionContext()
        self.ctx.cookies = {"__Secure-1PSID": "mock_psid_test", "__Secure-1PSIDTS": "mock_psidts_test"}
        self.ctx.access_token = "mock_snlm0e_test"
        yield
        GeminiWebClient.clear_feature_mode_40_cache()
        self.mock_server.stop_background()

    @pytest.mark.asyncio
    async def test_task_mode_1061_fallback_to_standard_envelope(self):
        """Verifies that feature_mode=task with error 1061 strips inner[49]=40 and succeeds on fallback."""
        client = GeminiWebClient(
            session_context=self.ctx,
            base_url=f"http://127.0.0.1:{self.mock_port}",
            timeout=10.0,
        )
        collected_text = []
        async for chunk in client.generate_stream(
            prompt="Analyze code [TRIGGER_TASK_1061]",
            model="gemini-4-argon",
            feature_mode="task",
        ):
            if chunk.type == "text" and chunk.delta:
                collected_text.append(chunk.delta)

        assert len(collected_text) > 0
        stream_reqs = [r for r in self.mock_server.fault.recorded_requests if "StreamGenerate" in r["path"]]
        assert len(stream_reqs) == 2

    @pytest.mark.asyncio
    async def test_task_mode_1173_fallback_to_standard_envelope(self):
        """Verifies that feature_mode=task with error 1173 strips inner[49]=40 and succeeds on fallback."""
        client = GeminiWebClient(
            session_context=self.ctx,
            base_url=f"http://127.0.0.1:{self.mock_port}",
            timeout=10.0,
        )
        collected_text = []
        async for chunk in client.generate_stream(
            prompt="Refactor module [TRIGGER_TASK_1173]",
            model="gemini-4-argon",
            feature_mode="task",
        ):
            if chunk.type == "text" and chunk.delta:
                collected_text.append(chunk.delta)

        assert len(collected_text) > 0
        stream_reqs = [r for r in self.mock_server.fault.recorded_requests if "StreamGenerate" in r["path"]]
        assert len(stream_reqs) == 2

    @pytest.mark.asyncio
    async def test_usage_limit_1037_raises_immediately_without_retry(self):
        """Verifies that UsageLimitExceededError (code 1037) is raised immediately on attempt 1."""
        client = GeminiWebClient(
            session_context=self.ctx,
            base_url=f"http://127.0.0.1:{self.mock_port}",
            timeout=10.0,
        )
        with pytest.raises(UsageLimitExceededError) as exc_info:
            async for _ in client.generate_stream(prompt="Trigger quota [TRIGGER_QUOTA_ERROR]"):
                pass

        assert exc_info.value.code == 1037
        stream_reqs = [r for r in self.mock_server.fault.recorded_requests if "StreamGenerate" in r["path"]]
        assert len(stream_reqs) == 1

    @pytest.mark.asyncio
    async def test_authentication_error_7_raises_immediately(self):
        """Verifies that AuthenticationError (code 7) is raised immediately without retries."""
        client = GeminiWebClient(
            session_context=self.ctx,
            base_url=f"http://127.0.0.1:{self.mock_port}",
            timeout=10.0,
        )
        with pytest.raises(AuthenticationError) as exc_info:
            async for _ in client.generate_stream(prompt="Test auth [TRIGGER_PERMISSION_DENIED]"):
                pass

        assert exc_info.value.code == 7
        stream_reqs = [r for r in self.mock_server.fault.recorded_requests if "StreamGenerate" in r["path"]]
        assert len(stream_reqs) == 1


# ============================================================================
# Class 3: Chat Stream Disconnect & Reasoning Resilience
# ============================================================================

class TestChatStreamDisconnectAndReasoningResilience:
    """Validates SSE client disconnect during reasoning and error turn persistence."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        self.temp_dir = tempfile.mkdtemp(prefix="gemini_resilience_chat_")
        self.mock_port = get_free_port()
        self.daemon_port = get_free_port()
        self.mock_server = MockGeminiServer(host="127.0.0.1", port=self.mock_port)
        self.mock_server.start_background()
        for _ in range(50):
            if self.mock_server._site is not None:
                break
            time.sleep(0.05)

        os.environ["GEMINI_BASE_URL"] = f"http://127.0.0.1:{self.mock_port}"

        self.db_path = Path(self.temp_dir) / "test_chat_resilience.db"
        self.db = Database(self.db_path)
        schema_path = PROJECT_ROOT / "backend" / "app" / "storage" / "schema.sql"
        asyncio.run(self.db.init_schema(schema_path))
        self.repo = ConversationRepository(self.db)

        vault_path = Path(self.temp_dir) / "vault.json"
        vault = CredentialVault(vault_path=vault_path)
        vdata = vault.load_vault_data()
        vdata.active_account_id = "default"
        mock_cookies = {"__Secure-1PSID": "mock_psid_resilience", "__Secure-1PSIDTS": "mock_psidts_resilience"}
        vdata.accounts["default"] = AccountProfile(
            id="default",
            name="Default Account",
            tier="ultra",
            cookies=mock_cookies,
            snlm0e="mock_snlm0e_resilience",
        )
        vault.store_vault_data(vdata)

        self.sm = SessionManager(base_url=f"http://127.0.0.1:{self.mock_port}", vault=vault)
        ultra_ctx = SessionContext()
        ultra_ctx.cookies = mock_cookies
        ultra_ctx.access_token = "mock_snlm0e_resilience"
        ultra_ctx.account_tier = "ultra"
        sess = AccountSession(account_id="default", profile=vdata.accounts["default"], context=ultra_ctx)
        self.sm.sessions["default"] = sess

        import backend.app.main as main_mod
        from backend.app.main import app
        self.app = app
        self.app.state.db = self.db
        self.app.state.repository = self.repo
        self.app.state.session_manager = self.sm
        self.app.state.vault = vault
        main_mod.db = self.db
        main_mod.repository = self.repo
        main_mod.session_manager = self.sm
        main_mod.vault = vault

        self.uvicorn_server = BackgroundUvicornServer(self.app, host="127.0.0.1", port=self.daemon_port)
        self.uvicorn_server.start()

        self.client = TestClient(self.app)
        yield
        try:
            self.uvicorn_server.stop()
        except Exception:
            pass
        try:
            asyncio.run(self.db.close())
        except Exception:
            pass
        self.mock_server.stop_background()
        os.environ.pop("GEMINI_BASE_URL", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_stream_error_emits_sse_error_and_persists_assistant_turn(self):
        """Verifies that when a stream error occurs, SSE sends error event AND assistant turn is saved with is_error=True."""
        conv = asyncio.run(self.repo.create_conversation("Stream Error Test"))
        payload = {
            "prompt": "Test error turn [TRIGGER_MODEL_ERROR]",
            "conversation_id": conv.id,
            "model": "gemini-4-argon",
        }
        resp = self.client.post("/api/chat/generate", json=payload)
        assert resp.status_code == 200

        lines = [l[5:].strip() for l in resp.text.splitlines() if l.startswith("data:")]
        events = [json.loads(l) for l in lines if l]
        types = [e.get("type") for e in events]
        assert "start" in types
        assert "error" in types

        # Check database persistence via get_all_messages
        messages = asyncio.run(self.repo.get_all_messages(conv.id))
        assert len(messages) == 2
        user_msg, asst_msg = messages[0], messages[1]
        assert user_msg.role == "user"
        assert asst_msg.role == "assistant"
        assert asst_msg.is_error is True

    @pytest.mark.asyncio
    async def test_client_disconnect_during_reasoning_stream(self):
        """Simulates client disconnect while reasoning stream is in progress and validates zero DB corruption."""
        self.mock_server.fault.streaming_delay = 0.5
        conv = await self.repo.create_conversation("Disconnect Test")

        payload = {
            "prompt": "Long reasoning prompt",
            "conversation_id": conv.id,
            "model": "gemini-4-argon",
        }

        # Connect to real Uvicorn server over TCP so socket disconnect triggers genuine ASGI cancellation
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{self.daemon_port}") as async_cli:
            async with async_cli.stream("POST", "/api/chat/generate", json=payload) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        ev = json.loads(line[5:].strip())
                        if ev.get("type") in ("start", "thought"):
                            # Close client socket immediately
                            break

        # Give Uvicorn event loop a moment to handle socket close and cancel generator
        await asyncio.sleep(0.5)

        # Verify database state: user message was recorded, but no completed/corrupted assistant message
        messages = await self.repo.get_all_messages(conv.id)
        assert len(messages) == 1
        assert messages[0].role == "user"


# ============================================================================
# Class 4: Uploader Network & Archive Resilience
# ============================================================================

class TestUploaderNetworkAndArchiveResilience:
    """Validates GeminiUploader retry mechanism, HTTP 413, context pruning, and traversal defenses."""

    @pytest.mark.asyncio
    async def test_uploader_retry_on_network_transport_error(self):
        """Proves GeminiUploader recovers from transient TLS/network failure on attempt 1 and succeeds on attempt 2."""
        ctx = SessionContext()
        ctx.cookies = {"__Secure-1PSID": "test_psid"}
        uploader = GeminiUploader(session_context=ctx, base_url="http://127.0.0.1:8999", timeout=5.0)

        mock_resp = httpx.Response(200, text="/contrib_service/ttl_1d/recovered_mock_hash")
        with patch.object(
            httpx.AsyncClient,
            "post",
            side_effect=[httpx.TransportError("TLS bad record MAC"), mock_resp],
        ) as mock_post:
            uri = await uploader.upload_file(data=b"test file payload", filename="archive.zip")
            assert uri == "/contrib_service/ttl_1d/recovered_mock_hash"
            assert mock_post.call_count == 2

    @pytest.mark.asyncio
    async def test_uploader_http_413_payload_too_large_immediate_rejection(self):
        """Proves HTTP 413 (Payload Too Large) is immediately rejected without retries."""
        ctx = SessionContext()
        uploader = GeminiUploader(session_context=ctx, base_url="http://127.0.0.1:8999", timeout=5.0)

        mock_resp = httpx.Response(413, text="Payload Too Large")
        with patch.object(httpx.AsyncClient, "post", return_value=mock_resp) as mock_post:
            with pytest.raises(GeminiBridgeError) as exc_info:
                await uploader.upload_file(data=b"large data" * 1000, filename="huge.zip")

            assert exc_info.value.code == 413
            assert mock_post.call_count == 1

    def test_context_pack_prunes_noise_directories_and_extensions(self, tmp_path: Path):
        """Verifies pack_context_path recursively packs code but strips .git, node_modules, and binaries."""
        work_dir = tmp_path / "project_source"
        work_dir.mkdir()
        (work_dir / ".git").mkdir()
        (work_dir / ".git" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
        (work_dir / "node_modules").mkdir()
        (work_dir / "node_modules" / "pkg.js").write_text("module.exports = {}", encoding="utf-8")
        (work_dir / "__pycache__").mkdir()
        (work_dir / "__pycache__" / "cached.pyc").write_bytes(b"\x00\x01\x02\x03")
        (work_dir / "module.exe").write_bytes(b"MZ\x90\x00")
        (work_dir / "app.py").write_text("print('hello')", encoding="utf-8")

        zip_data, zip_filename, file_count = gemini_task.pack_context_path(str(work_dir))
        assert file_count == 1
        assert zip_filename == "project_source_context.zip"
        assert isinstance(zip_data, bytes)

        with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zf:
            namelist = zf.namelist()
            assert "app.py" in namelist
            assert not any(".git" in n for n in namelist)
            assert not any("node_modules" in n for n in namelist)
            assert not any("__pycache__" in n for n in namelist)
            assert not any(".exe" in n for n in namelist)

    def test_extract_materials_path_traversal_blocking(self, tmp_path: Path):
        """Verifies extract_files_from_response blocks parent traversal, sibling traversal, and absolute paths."""
        extract_dir = tmp_path / "safe_extract"
        extract_dir.mkdir()
        sibling_dir = tmp_path / "safe_extract_sibling"
        sibling_dir.mkdir()

        adversarial_response = (
            "### File: ../escape.py\n```python\n# escape\n```\n"
            "### File: ../safe_extract_sibling/hack.py\n```python\n# sibling hack\n```\n"
            "### File: C:\\Windows\\evil.py\n```python\n# win evil\n```\n"
            "### File: valid_code.py\n```python\n# valid\n```\n"
        )

        extracted = gemini_task.extract_files_from_response(adversarial_response, str(extract_dir))
        assert not (tmp_path / "escape.py").exists()
        assert not (sibling_dir / "hack.py").exists()
        assert (extract_dir / "valid_code.py").exists()
        assert any(item["path"] == "valid_code.py" for item in extracted)


# ============================================================================
# Class 5: Database WAL Concurrency Resilience
# ============================================================================

class TestDatabaseWalConcurrencyResilience:
    """Validates SQLite WAL concurrency (15 readers + 5 writers) and atomic rollback."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path: Path):
        self.db_path = tmp_path / "wal_stress_test.db"
        self.db = Database(self.db_path)
        schema_path = PROJECT_ROOT / "backend" / "app" / "storage" / "schema.sql"
        asyncio.run(self.db.init_schema(schema_path))
        self.repo = ConversationRepository(self.db)
        yield
        try:
            asyncio.run(self.db.close())
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_wal_concurrent_readers_and_writers(self):
        """Executes 15 concurrent readers and 5 concurrent writers without database lock errors."""
        conv = await self.repo.create_conversation("WAL Concurrency Test")

        # Seed initial message
        seed_msg = await self.repo.add_message(
            conversation_id=conv.id,
            role="user",
            content="Initial seed message",
            created_at=int(time.time() * 1000),
        )

        async def reader_task(reader_id: int):
            for _ in range(10):
                msgs = await self.repo.get_all_messages(conv.id)
                assert len(msgs) >= 1
                c = await self.repo.get_conversation(conv.id)
                assert c is not None
                await asyncio.sleep(0.005)

        async def writer_task(writer_id: int):
            for i in range(5):
                await self.repo.add_message(
                    conversation_id=conv.id,
                    role="assistant" if i % 2 == 0 else "user",
                    content=f"Message from writer {writer_id} turn {i}",
                    parent_id=seed_msg.id,
                    created_at=int(time.time() * 1000),
                )
                await self.db.record_telemetry(
                    account_id=f"acc_{writer_id}",
                    model_id="gemini-4-argon",
                    model_name="Gemini 4 Argon",
                    feature_mode="task",
                    status_code=200,
                    duration_ms=150,
                )
                await asyncio.sleep(0.01)

        readers = [reader_task(i) for i in range(15)]
        writers = [writer_task(i) for i in range(5)]

        # Run all 20 concurrent tasks
        await asyncio.gather(*readers, *writers)

        # Verify final message count: 1 seed + 5 writers * 5 turns = 26 messages
        final_msgs = await self.repo.get_all_messages(conv.id)
        assert len(final_msgs) == 26

    @pytest.mark.asyncio
    async def test_atomic_transaction_rollback_on_failure(self):
        """Proves that a failed transaction rolls back all partial writes atomically."""
        conv = await self.repo.create_conversation("Transaction Rollback Test")

        try:
            async with self.db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?);",
                    (conv.id, "user", "Content to rollback", int(time.time() * 1000), int(time.time() * 1000)),
                )
                raise RuntimeError("Simulated transaction crash")
        except RuntimeError:
            pass

        # After rollback, no message should exist with that content
        row_after = await self.db.fetch_one("SELECT id FROM messages WHERE content = ?", ("Content to rollback",))
        assert row_after is None
