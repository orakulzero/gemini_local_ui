"""
tests/adversarial/test_backend_extreme_boundaries.py
Adversarial challenge test suite for extreme boundary conditions:
- Zero quota on all accounts & simultaneous exhaustion without 500
- Burst telemetry concurrency & extreme numbers (0, negative, huge, sub-ms)
- Malformed citations, corrupt candidate chunks, and pathological payloads
- Empty source content, zero-length summaries, missing keys in NotebookLM
"""

import asyncio
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app.bridge.exceptions import (
    AuthenticationError,
    GeminiBridgeError,
    UsageLimitExceededError,
)
from backend.app.bridge.notebooklm import (
    NotebookDetail,
    NotebookInfo,
    NotebookLMClient,
    NotebookNote,
    NotebookSource,
)
from backend.app.bridge.session import (
    AccountSession,
    SessionContext,
    SessionHealthStatus,
    SessionManager,
)
from backend.app.bridge.streaming_parser import StreamingDeltaAccumulator
from backend.app.main import app
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.vault import AccountProfile, CredentialVault, FailoverConfig


class TestAdversarialExtremeBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_adv.db")
        self.vault_file = os.path.join(self.temp_dir, "vault.enc")
        self.vault = CredentialVault(self.vault_file, force_fallback=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # =========================================================================
    # 1. Zero Quota & Cooldown Edge Cases (No Unhandled 500s)
    # =========================================================================
    def test_adv_zero_quota_all_accounts_exhausted_failover(self):
        """When every account in the system is exhausted / 0 quota, failover gracefully returns None, no 500."""
        mgr = SessionManager(vault=self.vault)
        p1 = AccountProfile(id="free_0", name="Free Zero", tier="free", priority=1)
        p2 = AccountProfile(id="pro_0", name="Pro Zero", tier="pro", priority=2)
        p3 = AccountProfile(id="ultra_0", name="Ultra Zero", tier="ultra", priority=3)
        self.vault.save_account(p1)
        self.vault.save_account(p2)
        self.vault.save_account(p3)
        self.vault.update_failover_config(FailoverConfig(enabled=True, fallback_chain=["pro_0", "ultra_0"]))

        async def _run():
            await mgr.initialize_all()
            for s in mgr.sessions.values():
                s.context.status = SessionHealthStatus.HEALTHY

            # All accounts marked in cooldown (0 quota remaining)
            mgr.mark_cooldown("free_0", duration_seconds=3600, reason="Quota 1037")
            mgr.mark_cooldown("pro_0", duration_seconds=3600, reason="Quota 1037")
            mgr.mark_cooldown("ultra_0", duration_seconds=3600, reason="Quota 1037")

            backup = mgr.get_failover_account("gemini-3.1-pro", "free_0", required_tier="pro")
            self.assertIsNone(backup)

            # Querying cooldown for nonexistent account must not crash
            self.assertFalse(mgr.is_in_cooldown("nonexistent_account_12345"))

            # Empty string defaults to active_account_id which is in cooldown
            self.assertTrue(mgr.is_in_cooldown(""))

            # Zero and negative cooldown duration clears cooldown
            mgr.mark_cooldown("free_0", duration_seconds=0, reason="Zero duration")
            self.assertFalse(mgr.is_in_cooldown("free_0"))
            mgr.mark_cooldown("free_0", duration_seconds=-500, reason="Negative duration")
            self.assertFalse(mgr.is_in_cooldown("free_0"))

        asyncio.run(_run())

    def test_adv_api_limits_zero_and_empty_accounts(self):
        """GET /api/limits with nonexistent account filter returns 404 safely without unhandled 500."""
        with TestClient(app) as client:
            resp = client.get("/api/limits?account_id=totally_invalid_account_id_99999")
            self.assertEqual(resp.status_code, 404)
            self.assertIn("not found", resp.json()["detail"].lower())

            # Global limits query returns valid 200 payload
            resp_global = client.get("/api/limits")
            self.assertEqual(resp_global.status_code, 200)
            data = resp_global.json()
            self.assertIn("accounts", data)
            self.assertIn("global_summary_24h", data)

    # =========================================================================
    # 2. Burst Telemetry & Concurrency Stresses
    # =========================================================================
    def test_adv_burst_concurrent_telemetry_reads_and_writes(self):
        """Massive burst of concurrent DB telemetry operations must not cause sqlite3.OperationalError or crash."""
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)

            convs = [await repo.create_conversation(title=f"Burst Conv {k}") for k in range(5)]

            async def _record(i: int):
                target_conv = convs[i % len(convs)]
                await repo.add_message(
                    conversation_id=target_conv.id,
                    role="assistant",
                    content=f"Telemetry burst chunk {i}",
                    model="gemini-2.5-flash",
                )

            tasks = [_record(i) for i in range(100)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    self.fail(f"Concurrent telemetry DB operation failed with: {res}")

            # Verify all 100 messages were written cleanly
            total_msgs = 0
            for c in convs:
                msgs = await repo.get_all_messages(c.id)
                total_msgs += len(msgs)
            self.assertEqual(total_msgs, 100)

            await db.close()

        asyncio.run(_run())

    def test_adv_telemetry_extreme_numerical_bounds(self):
        """Telemetry calculations under negative tokens, zero elapsed time, and astronomical numbers."""
        # Division by zero guard
        elapsed_sec = 0.0
        tokens = 500
        rate = (tokens / elapsed_sec) if elapsed_sec > 0 else 0.0
        self.assertEqual(rate, 0.0)

        # Negative token count clamping
        negative_tokens = -999999
        sanitized_tokens = max(0, negative_tokens)
        self.assertEqual(sanitized_tokens, 0)

        # Astronomical token count (e.g. 10 billion) does not overflow Python int
        huge_tokens = 10_000_000_000
        self.assertEqual(huge_tokens + 1, 10_000_000_001)

    # =========================================================================
    # 3. Pathological Citations & Corrupt Candidate Structures
    # =========================================================================
    def test_adv_streaming_parser_malformed_and_hostile_citations(self):
        """StreamingDeltaAccumulator handles hostile/malformed citations without raising exceptions."""
        acc = StreamingDeltaAccumulator()

        hostile_citations = [
            # Missing fields
            [],
            [1],
            [1, None],
            # Hostile URLs
            [1, "javascript:alert(1)", "XSS Title", "javascript:"],
            [1, "data:text/html,<script>alert(1)</script>", "Data URI", "data:"],
            [1, "file:///C:/Windows/System32/drivers/etc/hosts", "Local File", "file:"],
            # Pathological URL (100k characters)
            [1, "https://example.com/" + "a" * 100_000, "Huge URL", "example.com"],
            # Null domain
            [1, "https://example.com/test", "No domain", None],
            # Invalid non-string URL
            [1, 123456, {}, []],
            # Unicode domain with zero-width spaces and control chars
            [1, "https://\u200Bex\u0000ample.com/\u202Eevil", "Unicode Domain", None],
        ]

        normalized = acc._normalize_citations(hostile_citations)
        self.assertIsInstance(normalized, list)
        for item in normalized:
            self.assertIsInstance(item, dict)
            self.assertIn("url", item)
            self.assertIn("title", item)
            self.assertIn("domain", item)

    def test_adv_streaming_parser_corrupted_candidate_data(self):
        """StreamingDeltaAccumulator handles frames with corrupted candidate_data envelopes."""
        acc = StreamingDeltaAccumulator()

        corrupt_frames = [
            # Empty list
            [],
            # wrb.fr with malformed inner json
            [["wrb.fr", None, "{malformed_json_syntax"]],
            # wrb.fr with empty json
            [["wrb.fr", None, ""]],
            # wrb.fr with null json
            [["wrb.fr", None, "null"]],
            # wrb.fr with non-array inner payload
            [["wrb.fr", None, json.dumps({"not": "a list"})]],
            # Inner part with candidates as string
            [["wrb.fr", None, json.dumps([None, ["c1", "r1", "rcid1"], None, None, "candidates_not_a_list"])]],
            # Candidate with non-list text
            [["wrb.fr", None, json.dumps([None, ["c1", "r1", "rcid1"], None, None, [["rcid1", 12345]]])]],
            # Candidate with non-dict candidate[12]
            [["wrb.fr", None, json.dumps([None, ["c1", "r1", "rcid1"], None, None, [["rcid1", ["text"], None, None, None, None, None, None, [2], None, None, None, "bad_12"]]])]],
        ]

        for frame in corrupt_frames:
            try:
                chunks = list(acc.process_frame(frame))
                self.assertIsInstance(chunks, list)
            except Exception as e:
                self.fail(f"StreamingDeltaAccumulator crashed on corrupted frame {frame}: {e}")

    # =========================================================================
    # 4. Empty Source Content & NotebookLM API Boundaries
    # =========================================================================
    def test_adv_notebooklm_empty_and_null_source_content(self):
        """NotebookLMClient and attach-context safely handle zero-length and missing source data."""
        client = NotebookLMClient(fallback_snlm0e="valid_test_token")

        async def _run():
            empty_detail = NotebookDetail(
                id="nb_empty_adv",
                title="",
                created_at=0,
                updated_at=0,
                sources=[
                    NotebookSource(
                        id="src_empty",
                        title="",
                        source_type=1,
                        url=None,
                        summary="",
                        token_count=0,
                        created_at=0,
                    )
                ],
                notes=[
                    NotebookNote(
                        id="note_empty",
                        title="",
                        content="",
                        created_at=0,
                        updated_at=0,
                    )
                ],
            )

            with patch.object(client, "get_notebook", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = empty_detail

                with TestClient(app) as tc:
                    with patch("backend.app.api.notebooklm.get_notebooklm_client", return_value=client):
                        resp = tc.post(
                            "/api/notebooklm/attach-context",
                            json={
                                "notebook_id": "nb_empty_adv",
                                "source_ids": ["src_empty"],
                                "note_ids": ["note_empty"],
                                "include_notes": True,
                            },
                        )
                        self.assertEqual(resp.status_code, 200)
                        data = resp.json()
                        self.assertIn("formatted_context", data)
                        self.assertEqual(data["source_count"], 1)
                        self.assertEqual(data["note_count"], 1)
                        self.assertIn("<notebooklm_reference", data["formatted_context"])

        asyncio.run(_run())

    def test_adv_notebooklm_export_hostile_inputs(self):
        """POST /api/notebooklm/export with hostile inputs returns 400/422, never unhandled 500."""
        with TestClient(app) as tc:
            # 1. Invalid JSON body
            resp1 = tc.post("/api/notebooklm/export", content="not json", headers={"Content-Type": "application/json"})
            self.assertEqual(resp1.status_code, 422)

            # 2. Empty content and missing conversation_id
            resp2 = tc.post("/api/notebooklm/export", json={"notebook_id": "nb_1", "content": ""})
            self.assertEqual(resp2.status_code, 400)

            # 3. Nonexistent conversation_id
            resp3 = tc.post("/api/notebooklm/export", json={"notebook_id": "nb_1", "conversation_id": "conv_missing_999"})
            self.assertEqual(resp3.status_code, 404)

            # 4. Valid content payload with mock client
            client = NotebookLMClient(fallback_snlm0e="valid_token")
            with patch.object(client, "add_text_source", new_callable=AsyncMock) as mock_add:
                mock_add.return_value = NotebookSource(
                    id="src_mock",
                    title="Mock Source",
                    source_type=1,
                    url=None,
                    summary="Exported content summary",
                    token_count=100,
                    created_at=int(time.time()),
                )
                with patch("backend.app.api.notebooklm.get_notebooklm_client", return_value=client):
                    resp4 = tc.post(
                        "/api/notebooklm/export",
                        json={"notebook_id": "nb_1", "content": "Valid research data", "title": "Mock Source"},
                    )
                    self.assertEqual(resp4.status_code, 200)
                    data = resp4.json()
                    self.assertTrue(data["success"])
                    self.assertEqual(data["source"]["id"], "src_mock")


if __name__ == "__main__":
    unittest.main()
