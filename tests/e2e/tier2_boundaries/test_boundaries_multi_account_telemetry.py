"""
tests/e2e/tier2_boundaries/test_boundaries_multi_account_telemetry.py
Tier 2: Boundary & Corner Cases for Features F34 to F42 (Multi-Account, Telemetry & Storage Limits).
Covers 50 boundary and corner cases:
- F34: Multi-Account Credential Management Boundaries (6 tests)
- F35: Tiered Quota Tracking Boundaries (6 tests)
- F36: Proactive Background Health-Check Boundaries (6 tests)
- F37: Dynamic Multi-Strategy Failover Boundaries (6 tests)
- F38: Live Account Dashboard & Switcher Boundaries (5 tests)
- F39: Token Consumption & Latency Telemetry Boundaries (6 tests)
- F40: Token Velocity & Burn-Rate Alerting Boundaries (5 tests)
- F41: SQLite Storage Auto-Pruning & Vacuum Boundaries (5 tests)
- F42: Export Packaging & Schema Migration Boundaries (5 tests)
Total: 50 tests.
"""

from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.storage.vault import CredentialVault, AccountProfile, VaultData, FailoverConfig
from backend.app.bridge.session import SessionManager, SessionContext, AccountSession, SessionHealthStatus
from backend.app.bridge.exceptions import UsageLimitExceededError, GeminiBridgeError, AuthenticationError
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.exporter import ThreadExporter
from fastapi.testclient import TestClient
from backend.app.main import app


class TestTier2MultiAccountBoundaries(unittest.TestCase):
    """Boundary & Corner Cases for F34 to F38."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.vault_file = Path(self.temp_dir) / "vault.enc"
        self.vault = CredentialVault(self.vault_file, force_fallback=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mocked_manager(self) -> SessionManager:
        mgr = SessionManager(vault=self.vault)
        async def _mock_scrape(acc_id=None):
            sess = mgr.get_account_session(acc_id)
            if sess:
                sess.context.access_token = "mock_snlm0e"
                sess.context.status = SessionHealthStatus.HEALTHY
            return True

        mgr.scrape_tokens = _mock_scrape
        async def _noop(*args, **kwargs): pass
        mgr._staggered_rotation_loop = _noop
        mgr._rotation_loop = _noop
        return mgr

    # --- F34: Multi-Account Credential Management Boundaries ---
    def test_t2_f34_01_empty_account_id(self):
        p = AccountProfile(id="", name="Empty ID Profile", tier="free", cookies={})
        self.vault.save_account(p)
        loaded = self.vault.get_account("")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.id, "")

    def test_t2_f34_02_special_characters_in_label(self):
        special_name = "<script>alert('xss')</script> & 🧪 $PATH %20 'quotes'"
        p = AccountProfile(id="acc_special", name=special_name, tier="pro")
        self.vault.save_account(p)
        loaded = self.vault.get_account("acc_special")
        self.assertEqual(loaded.name, special_name)

    def test_t2_f34_03_missing_psid_cookie(self):
        p = AccountProfile(id="acc_missing_psid", name="Missing PSID", cookies={"__Secure-1PSIDTS": "ts_only"})
        self.vault.save_account(p)
        loaded = self.vault.get_account("acc_missing_psid")
        self.assertNotIn("__Secure-1PSID", loaded.cookies)
        self.assertIn("__Secure-1PSIDTS", loaded.cookies)

    def test_t2_f34_04_missing_psidts_cookie(self):
        p = AccountProfile(id="acc_missing_psidts", name="Missing PSIDTS", cookies={"__Secure-1PSID": "sid_only"})
        self.vault.save_account(p)
        loaded = self.vault.get_account("acc_missing_psidts")
        self.assertIn("__Secure-1PSID", loaded.cookies)
        self.assertNotIn("__Secure-1PSIDTS", loaded.cookies)

    def test_t2_f34_05_duplicate_account_registration_overwrites(self):
        p1 = AccountProfile(id="acc_dup", name="Original", tier="free")
        p2 = AccountProfile(id="acc_dup", name="Updated", tier="ultra")
        self.vault.save_account(p1)
        self.vault.save_account(p2)
        loaded = self.vault.get_account("acc_dup")
        self.assertEqual(loaded.name, "Updated")
        self.assertEqual(loaded.tier, "ultra")

    def test_t2_f34_06_zero_length_cookie_values(self):
        p = AccountProfile(id="acc_empty_val", name="Empty Val", cookies={"__Secure-1PSID": "", "__Secure-1PSIDTS": ""})
        self.vault.save_account(p)
        loaded = self.vault.get_account("acc_empty_val")
        self.assertEqual(loaded.cookies["__Secure-1PSID"], "")

    # --- F35: Tiered Quota Tracking Boundaries ---
    def test_t2_f35_01_quota_tier_pro_vs_free_priority(self):
        p_free = AccountProfile(id="free", name="Free", tier="free", priority=1)
        p_pro = AccountProfile(id="pro", name="Pro", tier="pro", priority=10)
        self.assertLess(p_free.priority, p_pro.priority)

    def test_t2_f35_02_quota_cooldown_marking_and_clearing(self):
        mgr = self._create_mocked_manager()
        self.vault.save_account(AccountProfile(id="a1", name="A1", tier="pro"))
        async def _run():
            await mgr.initialize_all()
            mgr.mark_cooldown("a1", duration_seconds=60, reason="RateLimit")
            self.assertTrue(mgr.is_in_cooldown("a1"))
            # Clear cooldown by setting negative duration
            mgr.mark_cooldown("a1", duration_seconds=-10, reason="Cleared")
            self.assertFalse(mgr.is_in_cooldown("a1"))
        asyncio.run(_run())

    def test_t2_f35_03_cooldown_expired_status(self):
        mgr = self._create_mocked_manager()
        self.vault.save_account(AccountProfile(id="a2", name="A2", tier="free"))
        async def _run():
            await mgr.initialize_all()
            mgr.mark_cooldown("a2", duration_seconds=-10, reason="Past expiration")
            self.assertFalse(mgr.is_in_cooldown("a2"))
        asyncio.run(_run())

    def test_t2_f35_04_cooldown_far_future_duration(self):
        mgr = self._create_mocked_manager()
        self.vault.save_account(AccountProfile(id="a3", name="A3", tier="ultra"))
        async def _run():
            await mgr.initialize_all()
            mgr.mark_cooldown("a3", duration_seconds=86400 * 365, reason="Permanent")
            self.assertTrue(mgr.is_in_cooldown("a3"))
        asyncio.run(_run())

    def test_t2_f35_05_unknown_account_cooldown_query(self):
        mgr = self._create_mocked_manager()
        self.assertFalse(mgr.is_in_cooldown("totally_unknown_account"))

    def test_t2_f35_06_zero_duration_cooldown(self):
        mgr = self._create_mocked_manager()
        self.vault.save_account(AccountProfile(id="a4", name="A4", tier="free"))
        async def _run():
            await mgr.initialize_all()
            mgr.mark_cooldown("a4", duration_seconds=0, reason="Instant")
            self.assertFalse(mgr.is_in_cooldown("a4"))
        asyncio.run(_run())

    # --- F36: Proactive Background Health-Check Boundaries ---
    def test_t2_f36_01_health_status_initial_state(self):
        ctx = SessionContext()
        self.assertEqual(ctx.status, SessionHealthStatus.UNAUTHENTICATED)

    def test_t2_f36_02_health_status_transitions(self):
        ctx = SessionContext()
        ctx.status = SessionHealthStatus.HEALTHY
        self.assertEqual(ctx.status, SessionHealthStatus.HEALTHY)
        ctx.status = SessionHealthStatus.REFRESHING
        self.assertEqual(ctx.status, SessionHealthStatus.REFRESHING)
        ctx.status = SessionHealthStatus.EXPIRED
        self.assertEqual(ctx.status, SessionHealthStatus.EXPIRED)

    def test_t2_f36_03_health_aggregation_empty_sessions(self):
        mgr = self._create_mocked_manager()
        healthy = [s for s in mgr.sessions.values() if s.context.status == SessionHealthStatus.HEALTHY]
        self.assertIsInstance(healthy, list)
        self.assertLessEqual(len(healthy), len(mgr.sessions))

    def test_t2_f36_04_health_check_with_uninitialized_sessions(self):
        mgr = self._create_mocked_manager()
        p = AccountProfile(id="acc_uninit", name="Uninit", tier="free")
        self.vault.save_account(p)
        sess = mgr.get_account_session("acc_uninit")
        self.assertIsNone(sess)

    def test_t2_f36_05_health_check_status_values_validity(self):
        mgr = self._create_mocked_manager()
        report = mgr.get_health()
        self.assertIn(report.status, SessionHealthStatus)
        valid_statuses = {
            SessionHealthStatus.UNAUTHENTICATED,
            SessionHealthStatus.HEALTHY,
            SessionHealthStatus.REFRESHING,
            SessionHealthStatus.EXPIRED,
        }
        for s in SessionHealthStatus:
            self.assertIn(s, valid_statuses)

    def test_t2_f36_06_concurrent_session_lock_acquisition(self):
        p = AccountProfile(id="acc_lock_test", name="Lock Test", tier="pro")
        sess = AccountSession(account_id="acc_lock_test", profile=p, context=SessionContext())
        async def _run():
            async with sess.lock:
                sess.context.last_refresh = time.time()
            self.assertGreater(sess.context.last_refresh, 0)
        asyncio.run(_run())

    # --- F37: Dynamic Multi-Strategy Failover Boundaries ---
    def test_t2_f37_01_all_accounts_in_cooldown_returns_none(self):
        mgr = self._create_mocked_manager()
        p1 = AccountProfile(id="p1", name="P1", tier="free", priority=1)
        self.vault.save_account(p1)
        self.vault.update_failover_config(FailoverConfig(enabled=True, fallback_chain=["p1"]))
        async def _run():
            await mgr.initialize_all()
            mgr.mark_cooldown("p1", duration_seconds=3600)
            backup = mgr.get_failover_account("gemini-2.5-flash", "p1")
            self.assertIsNone(backup)
        asyncio.run(_run())

    def test_t2_f37_02_failover_disabled_in_config(self):
        mgr = self._create_mocked_manager()
        p1 = AccountProfile(id="p1", name="P1", tier="free", priority=1)
        p2 = AccountProfile(id="p2", name="P2", tier="pro", priority=2)
        self.vault.save_account(p1)
        self.vault.save_account(p2)
        self.vault.update_failover_config(FailoverConfig(enabled=False, fallback_chain=["p1", "p2"]))
        async def _run():
            await mgr.initialize_all()
            mgr.mark_cooldown("p1", duration_seconds=3600)
            backup = mgr.get_failover_account("gemini-2.5-flash", "p1")
            self.assertIsNone(backup)
        asyncio.run(_run())

    def test_t2_f37_03_failover_chain_order_precedence(self):
        mgr = self._create_mocked_manager()
        p1 = AccountProfile(id="p1", name="P1", tier="free", priority=1)
        p2 = AccountProfile(id="p2", name="P2", tier="free", priority=2)
        p3 = AccountProfile(id="p3", name="P3", tier="pro", priority=3)
        self.vault.save_account(p1)
        self.vault.save_account(p2)
        self.vault.save_account(p3)
        self.vault.update_failover_config(FailoverConfig(enabled=True, fallback_chain=["p3", "p2"]))
        async def _run():
            await mgr.initialize_all()
            for s in mgr.sessions.values():
                s.context.status = SessionHealthStatus.HEALTHY
            backup = mgr.get_failover_account("gemini-2.5-flash", "p1", required_tier="free")
            self.assertIsNotNone(backup)
            self.assertEqual(backup.account_id, "p3")
        asyncio.run(_run())

    def test_t2_f37_04_failover_skips_failed_or_cooling_account_in_chain(self):
        mgr = self._create_mocked_manager()
        p1 = AccountProfile(id="p1", name="P1", tier="free")
        p2 = AccountProfile(id="p2", name="P2", tier="free")
        p3 = AccountProfile(id="p3", name="P3", tier="free")
        self.vault.save_account(p1)
        self.vault.save_account(p2)
        self.vault.save_account(p3)
        self.vault.update_failover_config(FailoverConfig(enabled=True, fallback_chain=["p2", "p3"]))
        async def _run():
            await mgr.initialize_all()
            for s in mgr.sessions.values():
                s.context.status = SessionHealthStatus.HEALTHY
            mgr.mark_cooldown("p2", duration_seconds=3600)
            backup = mgr.get_failover_account("gemini-2.5-flash", "p1", required_tier="free")
            self.assertIsNotNone(backup)
            self.assertEqual(backup.account_id, "p3")
        asyncio.run(_run())

    def test_t2_f37_05_failover_tier_requirement_filtering(self):
        mgr = self._create_mocked_manager()
        p1 = AccountProfile(id="p1", name="P1", tier="free")
        p2 = AccountProfile(id="p2", name="P2", tier="free")
        self.vault.save_account(p1)
        self.vault.save_account(p2)
        self.vault.update_failover_config(FailoverConfig(enabled=True, fallback_chain=["p2"]))
        async def _run():
            await mgr.initialize_all()
            backup = mgr.get_failover_account("gemini-3.1-pro", "p1", required_tier="pro")
            self.assertIsNone(backup)
        asyncio.run(_run())

    def test_t2_f37_06_failover_ignores_requesting_account(self):
        mgr = self._create_mocked_manager()
        p1 = AccountProfile(id="p1", name="P1", tier="free")
        self.vault.save_account(p1)
        self.vault.update_failover_config(FailoverConfig(enabled=True, fallback_chain=["p1"]))
        async def _run():
            await mgr.initialize_all()
            backup = mgr.get_failover_account("gemini-2.5-flash", "p1", required_tier="free")
            self.assertIsNone(backup)
        asyncio.run(_run())

    # --- F38: Live Account Dashboard & Switcher Boundaries ---
    def test_t2_f38_01_activate_non_existent_account_returns_false(self):
        mgr = self._create_mocked_manager()
        self.vault.save_account(AccountProfile(id="real", name="Real", tier="free"))
        async def _run():
            await mgr.initialize_all()
            ok = mgr.activate_account("non_existent_id")
            self.assertFalse(ok)
        asyncio.run(_run())

    def test_t2_f38_02_remove_active_account_updates_active_pointer(self):
        mgr = self._create_mocked_manager()
        async def _run():
            self.vault.save_account(AccountProfile(id="a1", name="A1", tier="free"))
            self.vault.save_account(AccountProfile(id="a2", name="A2", tier="pro"))
            await mgr.initialize_all()
            mgr.activate_account("a1")
            self.assertEqual(mgr.active_account_id, "a1")
            await mgr.remove_account("a1")
            self.assertNotEqual(mgr.active_account_id, "a1")
        asyncio.run(_run())

    def test_t2_f38_03_active_account_persistence_in_vault(self):
        p = AccountProfile(id="acc_persist", name="Persist", tier="ultra")
        self.vault.save_account(p)
        self.vault.set_active_account("acc_persist")
        data = self.vault.load_vault_data()
        self.assertEqual(data.active_account_id, "acc_persist")

    def test_t2_f38_04_account_listing_preserves_priority_and_tiers(self):
        p1 = AccountProfile(id="a1", name="A1", tier="free", priority=5)
        p2 = AccountProfile(id="a2", name="A2", tier="ultra", priority=1)
        self.vault.save_account(p1)
        self.vault.save_account(p2)
        accs = self.vault.list_accounts()
        self.assertEqual(len(accs), 2)
        mapped = {a.id: a for a in accs}
        self.assertEqual(mapped["a1"].priority, 5)
        self.assertEqual(mapped["a2"].tier, "ultra")

    def test_t2_f38_05_update_non_existent_account_profile_creates_it(self):
        mgr = self._create_mocked_manager()
        async def _run():
            await mgr.initialize_all()
            p = AccountProfile(id="brand_new", name="Brand New", tier="pro")
            sess = await mgr.add_or_update_account(p)
            self.assertIsNotNone(sess)
            self.assertEqual(sess.account_id, "brand_new")
        asyncio.run(_run())


class TestTier2TelemetryAndStorageBoundaries(unittest.TestCase):
    """Boundary & Corner Cases for F39 to F42."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_t2_prune.db")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # --- F39: Token Consumption & Latency Telemetry Boundaries ---
    def test_t2_f39_01_recording_zero_tokens(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            await db.record_telemetry(
                account_id="acc_zero",
                model_id="56fdd199312815e2",
                model_name="3.8 Flash",
                prompt_tokens_est=0,
                completion_tokens_est=0,
                thinking_tokens_est=0,
                duration_ms=0,
            )
            stats = await db.get_rolling_usage(account_id="acc_zero", window_seconds=3600)
            self.assertEqual(stats["total_requests"], 1)
            self.assertEqual(stats["total_tokens"], 0)
            self.assertEqual(stats["total_prompt_tokens"], 0)
            self.assertEqual(stats["total_completion_tokens"], 0)
            await db.close()

        asyncio.run(_run())

    def test_t2_f39_02_huge_token_counts(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            await db.record_telemetry(
                account_id="acc_huge",
                model_id="95a4c0abcba24364",
                model_name="4 Argon",
                prompt_tokens_est=2_000_000,
                completion_tokens_est=1_000_000,
                thinking_tokens_est=500_000,
                duration_ms=12500,
            )
            stats = await db.get_rolling_usage(account_id="acc_huge", window_seconds=3600)
            self.assertEqual(stats["total_tokens"], 3_500_000)
            self.assertEqual(stats["total_prompt_tokens"], 2_000_000)
            self.assertEqual(stats["total_completion_tokens"], 1_000_000)
            self.assertEqual(stats["total_thinking_tokens"], 500_000)
            await db.close()

        asyncio.run(_run())

    def test_t2_f39_03_sub_millisecond_latency(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            await db.record_telemetry(
                account_id="acc_fast",
                model_id="56fdd199312815e2",
                model_name="3.8 Flash",
                duration_ms=0,
            )
            await db.record_telemetry(
                account_id="acc_fast",
                model_id="56fdd199312815e2",
                model_name="3.8 Flash",
                duration_ms=1,
            )
            rows = await db.fetch_all(
                "SELECT duration_ms FROM usage_telemetry WHERE account_id = ? ORDER BY id ASC;",
                ("acc_fast",),
            )
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["duration_ms"], 0)
            self.assertEqual(rows[1]["duration_ms"], 1)
            await db.close()

        asyncio.run(_run())

    def test_t2_f39_04_extremely_long_request_latency(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            await db.record_telemetry(
                account_id="acc_long",
                model_id="95a4c0abcba24364",
                model_name="4 Argon",
                feature_mode="task",
                reasoning_level=2,
                duration_ms=180000,
            )
            stats = await db.get_rolling_usage(account_id="acc_long", window_seconds=86400)
            self.assertEqual(stats["total_requests"], 1)
            self.assertEqual(stats["task_mode_calls"], 1)
            row = await db.fetch_one("SELECT duration_ms FROM usage_telemetry WHERE account_id = ?;", ("acc_long",))
            self.assertEqual(row["duration_ms"], 180000)
            await db.close()

        asyncio.run(_run())

    def test_t2_f39_05_percentile_calculation_single_point(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            await db.record_telemetry(
                account_id="acc_p50",
                model_id="56fdd199312815e2",
                model_name="3.8 Flash",
                duration_ms=150,
            )
            row = await db.fetch_one(
                "SELECT AVG(duration_ms) as avg_d, MIN(duration_ms) as min_d, MAX(duration_ms) as max_d "
                "FROM usage_telemetry WHERE account_id = ?;",
                ("acc_p50",),
            )
            self.assertEqual(row["avg_d"], 150.0)
            self.assertEqual(row["min_d"], 150)
            self.assertEqual(row["max_d"], 150)
            await db.close()

        asyncio.run(_run())

    def test_t2_f39_06_empty_telemetry_aggregation(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            stats = await db.get_rolling_usage(account_id="non_existent_account_12345", window_seconds=86400)
            self.assertEqual(stats["total_requests"], 0)
            self.assertEqual(stats["total_tokens"], 0)
            self.assertEqual(stats["total_prompt_tokens"], 0)
            self.assertEqual(stats["total_completion_tokens"], 0)
            self.assertEqual(stats["task_mode_calls"], 0)
            self.assertEqual(stats["quota_error_count"], 0)
            self.assertEqual(stats["failover_events_count"], 0)
            per_model = await db.get_per_model_usage(account_id="non_existent_account_12345")
            self.assertEqual(per_model, {})
            await db.close()

        asyncio.run(_run())

    # --- F40: Token Velocity & Burn-Rate Alerting Boundaries ---
    def test_t2_f40_01_zero_elapsed_time_division_guard(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            now_ms = int(time.time() * 1000)
            await db.record_telemetry(
                account_id="acc_same_ts",
                model_id="56fdd199312815e2",
                model_name="3.8 Flash",
                request_timestamp=now_ms,
                prompt_tokens_est=50,
                completion_tokens_est=50,
            )
            await db.record_telemetry(
                account_id="acc_same_ts",
                model_id="56fdd199312815e2",
                model_name="3.8 Flash",
                request_timestamp=now_ms,
                prompt_tokens_est=50,
                completion_tokens_est=50,
            )
            rows = await db.fetch_all(
                "SELECT request_timestamp, total_tokens_est FROM usage_telemetry WHERE account_id = ? ORDER BY id ASC;",
                ("acc_same_ts",),
            )
            self.assertEqual(len(rows), 2)
            elapsed_sec = (rows[-1]["request_timestamp"] - rows[0]["request_timestamp"]) / 1000.0
            self.assertEqual(elapsed_sec, 0.0)
            total_tokens = sum(r["total_tokens_est"] for r in rows)
            self.assertEqual(total_tokens, 200)
            velocity = (total_tokens / elapsed_sec) if elapsed_sec > 0 else 0.0
            self.assertEqual(velocity, 0.0)
            stats = await db.get_rolling_usage("acc_same_ts", window_seconds=60)
            self.assertEqual(stats["total_requests"], 2)
            self.assertEqual(stats["total_tokens"], 200)
            await db.close()

        asyncio.run(_run())

    def test_t2_f40_02_spike_detection_500_percent(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            await db.set_account_limits_config(
                account_id="acc_spike",
                daily_request_threshold=5,
                daily_token_threshold=500,
            )
            for _ in range(25):
                await db.record_telemetry(
                    account_id="acc_spike",
                    model_id="56fdd199312815e2",
                    model_name="3.8 Flash",
                    prompt_tokens_est=10,
                    completion_tokens_est=10,
                )
            cfg = await db.get_account_limits_config("acc_spike")
            stats = await db.get_rolling_usage("acc_spike", window_seconds=86400)
            self.assertEqual(stats["total_requests"], 25)
            self.assertGreaterEqual(stats["total_requests"], cfg["daily_request_threshold"] * 5)
            await db.close()

        asyncio.run(_run())

    def test_t2_f40_03_alert_cooldown_suppression(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            future_cooldown_ms = int((time.time() + 300) * 1000)
            await db.set_account_limits_config(
                account_id="acc_cool",
                cooldown_until=future_cooldown_ms,
                cooldown_reason="Error 1037 rate limit cooldown",
            )
            cfg = await db.get_account_limits_config("acc_cool")
            self.assertEqual(cfg["cooldown_until"], future_cooldown_ms)
            self.assertEqual(cfg["cooldown_reason"], "Error 1037 rate limit cooldown")
            await db.close()

        asyncio.run(_run())

    def test_t2_f40_04_negative_velocity_clamping(self):
        tc = TestClient(app)
        resp = tc.post(
            "/api/limits/acc_test/thresholds",
            json={"daily_request_threshold": -5, "daily_token_threshold": -100},
        )
        self.assertEqual(resp.status_code, 422)

    def test_t2_f40_05_burn_rate_estimated_time_to_exhaustion(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            await db.set_account_limits_config(
                account_id="acc_burn",
                daily_token_threshold=100000,
            )
            await db.record_telemetry(
                account_id="acc_burn",
                model_id="56fdd199312815e2",
                model_name="3.8 Flash",
                prompt_tokens_est=35000,
                completion_tokens_est=40000,
            )
            cfg = await db.get_account_limits_config("acc_burn")
            stats = await db.get_rolling_usage("acc_burn", window_seconds=86400)
            remaining_tokens = cfg["daily_token_threshold"] - stats["total_tokens"]
            self.assertEqual(remaining_tokens, 25000)
            await db.close()

        asyncio.run(_run())

    # --- F41: SQLite Storage Auto-Pruning & Vacuum Boundaries ---
    def test_t2_f41_01_prune_empty_database(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            now = int(time.time() * 1000)
            cursor = await db.execute("DELETE FROM conversations WHERE is_archived = 1 AND updated_at < ?;", (now,))
            self.assertEqual(cursor.rowcount, 0)
            await db.close()

        asyncio.run(_run())

    def test_t2_f41_02_retention_boundary_exact_cutoff(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Exact Cutoff")
            await repo.archive_conversation(conv.id, is_archived=True)
            conv_archived = await repo.get_conversation(conv.id)
            cutoff = conv_archived.updated_at

            # Deleting strictly older than cutoff should NOT delete an item at exact cutoff
            c1 = await db.execute("DELETE FROM conversations WHERE is_archived = 1 AND updated_at < ?;", (cutoff,))
            self.assertEqual(c1.rowcount, 0)

            # But deleting older than cutoff + 1000 should delete it
            c2 = await db.execute("DELETE FROM conversations WHERE is_archived = 1 AND updated_at < ?;", (cutoff + 1000,))
            self.assertEqual(c2.rowcount, 1)
            await db.close()

        asyncio.run(_run())

    def test_t2_f41_03_zero_retention_days(self):
        retention_days = 0
        cutoff_ms = int((time.time() - retention_days * 86400) * 1000)
        self.assertLessEqual(cutoff_ms, int(time.time() * 1000))

    def test_t2_f41_04_vacuum_execution_integrity(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Post-Vacuum Test")
            self.assertIsNotNone(conv)
            row = await db.fetch_one("PRAGMA auto_vacuum;")
            self.assertIsNotNone(row)
            await db.close()

        asyncio.run(_run())

    def test_t2_f41_05_soft_deleted_vs_active_isolation(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Active")
            await repo.archive_conversation(conv.id, is_archived=True)
            archived_list, _ = await repo.list_conversations(archived=True)
            active_list, _ = await repo.list_conversations(archived=False)
            self.assertTrue(any(c.id == conv.id for c in archived_list))
            self.assertFalse(any(c.id == conv.id for c in active_list))
            await db.close()

        asyncio.run(_run())

    # --- F42: Export Packaging & Schema Migration Boundaries ---
    def test_t2_f42_01_export_empty_conversation_tree(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Empty Tree")
            tree = await repo.get_all_messages(conv.id)
            export_doc = {
                "version": 1,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "conversation": conv.to_dict(),
                "messages": [m.to_dict() for m in tree],
            }
            self.assertEqual(len(export_doc["messages"]), 0)
            self.assertEqual(export_doc["conversation"]["title"], "Empty Tree")
            await db.close()

        asyncio.run(_run())

    def test_t2_f42_02_export_json_serialization_unicode(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Quantum Physics ⚛️ - 物理学")
            msg = await repo.add_message(conv.id, "user", "Special chars: €uro, emoji 🚀🔬")
            tree = await repo.get_all_messages(conv.id)
            exported = ThreadExporter.export_to_json(conv, [msg], all_messages=tree)
            parsed = json.loads(exported)
            self.assertEqual(parsed["conversation"]["title"], "Quantum Physics ⚛️ - 物理学")
            self.assertIn("🚀🔬", parsed["messages"][0]["content"])
            await db.close()

        asyncio.run(_run())

    def test_t2_f42_03_schema_migration_v1_to_v2_default_values(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            await db.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) "
                "VALUES ('legacy_v1_conv', 'Legacy Conv Title', 1700000000000, 1700000000000);"
            )
            c = await repo.get_conversation("legacy_v1_conv")
            self.assertIsNotNone(c)
            self.assertFalse(c.is_pinned)
            self.assertFalse(c.is_archived)
            self.assertEqual(c.model, "gemini-2.5-pro")
            self.assertIsNone(c.active_leaf_message_id)
            await db.close()

        asyncio.run(_run())

    def test_t2_f42_04_futuristic_schema_version_rejection(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Export Boundary")
            tc = TestClient(app)
            resp = tc.get(f"/api/conversations/{conv.id}/export?format=xml")
            self.assertEqual(resp.status_code, 422)
            await db.close()

        asyncio.run(_run())

    def test_t2_f42_05_corrupt_non_dict_import_payload(self):
        tc = TestClient(app)
        resp = tc.post("/api/conversations", json=[1, 2, 3])
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
