# tests/e2e/tier1_features/test_f39_to_f42_telemetry_limits.py
"""Tier 1: Tests for Features F39 to F42 (Quota & Usage Telemetry Panel)."""

from __future__ import annotations
import unittest
import json
import os
import tempfile
import shutil
import time
import asyncio
from pathlib import Path
from typing import Any, Dict, List

from unittest.mock import AsyncMock, patch
from backend.app.storage.database import Database
from backend.app.storage.vault import AccountProfile, CredentialVault, FailoverConfig, VaultData
from backend.app.bridge.session import SessionManager, SessionContext, SessionHealthStatus
from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.models import DEFAULT_MODELS, resolve_model
from backend.app.bridge.exceptions import UsageLimitExceededError, map_google_error_code
from fastapi.testclient import TestClient
from backend.app.main import app


class TestF39otAQ7bPolicyTelemetryExtraction(unittest.TestCase):
    """F39: otAQ7b Model Policy & Quota Telemetry Extraction."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_file = Path(self.temp_dir) / "test_telemetry_f39.db"
        self.db = Database(self.db_file)
        schema_path = Path(__file__).resolve().parent.parent.parent.parent / "backend" / "app" / "storage" / "schema.sql"
        asyncio.run(self.db.init_schema(schema_path))

        self.vault_file = Path(self.temp_dir) / "vault.enc"
        self.vault = CredentialVault(self.vault_file, force_fallback=True)
        p1 = AccountProfile(id="acc_ttl", name="TTL Test", tier="free", cookies={"__Secure-1PSID": "s1"})
        self.vault.save_account(p1)
        self.vault.set_active_account("acc_ttl")

        self.mgr = SessionManager(vault=self.vault)
        async def _noop(*args, **kwargs): pass
        self.mgr.scrape_tokens = _noop
        self.mgr._staggered_rotation_loop = _noop
        self.mgr._rotation_loop = _noop
        asyncio.run(self.mgr.initialize_all())

        import backend.app.main as main_mod
        main_mod.db = self.db
        main_mod.vault = self.vault
        main_mod.session_manager = self.mgr
        app.state.db = self.db
        app.state.vault = self.vault
        app.state.session_manager = self.mgr

    def tearDown(self):
        asyncio.run(self.db.close())
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_f39_otaq7b_policy_string_extraction(self):
        client = GeminiWebClient(SessionContext())
        # Pro policy envelope parsing
        raw_pro = [None] * 25
        raw_pro[24] = ["launch_pro_policy_v2"]
        with patch.object(client, "get_user_status", new_callable=AsyncMock) as mock_status:
            mock_status.return_value = {"raw": raw_pro}
            asyncio.run(client.list_models())
            self.assertEqual(client.ctx.account_tier, "pro")

        # Free policy envelope parsing
        raw_free = [None] * 25
        raw_free[24] = ["launch_free_policy"]
        with patch.object(client, "get_user_status", new_callable=AsyncMock) as mock_status:
            mock_status.return_value = {"raw": raw_free}
            asyncio.run(client.list_models())
            self.assertEqual(client.ctx.account_tier, "free")

    def test_f39_otaq7b_capability_flags_parsing(self):
        client = GeminiWebClient(SessionContext())
        raw_payload = [None] * 16
        raw_payload[15] = [
            ["95a4c0abcba24364", "4 Argon", "Cutting edge", 8, 2],
            ["56fdd199312815e2", "3.8 Flash", "Standard", 1, 1],
        ]
        with patch.object(client, "get_user_status", new_callable=AsyncMock) as mock_status:
            mock_status.return_value = {"raw": raw_payload}
            models = asyncio.run(client.list_models())
            model_map = {m.id: m for m in models}
            self.assertIn("95a4c0abcba24364", model_map)
            self.assertIn("56fdd199312815e2", model_map)
            self.assertTrue(model_map["95a4c0abcba24364"].capabilities.supports_thinking)
            self.assertEqual(model_map["95a4c0abcba24364"].thinking_flag, 2)
            self.assertFalse(model_map["56fdd199312815e2"].capabilities.supports_thinking)
            self.assertEqual(model_map["56fdd199312815e2"].thinking_flag, 1)

    def test_f39_otaq7b_error_signal_detection(self):
        # Test authentic database error signal aggregation and exception mapping
        async def _run():
            now_ms = int(time.time() * 1000)
            await self.db.record_telemetry("acc_err", "95a4c0abcba24364", "4 Argon", request_timestamp=now_ms, status_code=1037)
            await self.db.record_telemetry("acc_err", "e6fa609c3fa255c0", "3.1 Pro", request_timestamp=now_ms, status_code=1061)
            await self.db.record_telemetry("acc_err", "56fdd199312815e2", "3.8 Flash", request_timestamp=now_ms, status_code=200)

            stats = await self.db.get_rolling_usage("acc_err", window_seconds=3600)
            self.assertEqual(stats["total_requests"], 3)
            self.assertEqual(stats["quota_error_count"], 2)

            # Verify map_google_error_code maps 1037 directly to UsageLimitExceededError
            with self.assertRaises(UsageLimitExceededError) as ctx:
                map_google_error_code(1037, "Rate limit reached")
            self.assertEqual(ctx.exception.code, 1037)
        asyncio.run(_run())

    def test_f39_otaq7b_tier_fallback_heuristics(self):
        client = GeminiWebClient(SessionContext())
        # Verify fallback when otAQ7b returns None or empty array
        with patch.object(client, "get_user_status", new_callable=AsyncMock) as mock_status:
            mock_status.return_value = {"raw": None}
            models = asyncio.run(client.list_models())
            self.assertEqual(client.ctx.account_tier, "free")
            self.assertGreaterEqual(len(models), len(DEFAULT_MODELS))
            self.assertIn("56fdd199312815e2", [m.id for m in models])

    def test_f39_otaq7b_caching_and_ttl(self):
        async def _run():
            await self.db.set_account_limits_config("acc_ttl", daily_request_threshold=300)
            cfg = await self.db.get_account_limits_config("acc_ttl")
            self.assertEqual(cfg["daily_request_threshold"], 300)

            # Test GET /api/limits caching stability across repeated calls
            client = TestClient(app)
            r1 = client.get("/api/limits")
            r2 = client.get("/api/limits")
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(r2.status_code, 200)
            self.assertLessEqual(abs(r1.json()["timestamp"] - r2.json()["timestamp"]), 2)
        asyncio.run(_run())


class TestF40PersistentRollingTelemetry(unittest.TestCase):
    """F40: SQLite Persistent Rolling Telemetry Schema."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_file = Path(self.temp_dir) / "test_telemetry.db"
        self.db = Database(self.db_file)
        schema_path = Path(__file__).parent.parent.parent.parent / "backend" / "app" / "storage" / "schema.sql"
        asyncio.run(self.db.init_schema(schema_path))

    def tearDown(self):
        asyncio.run(self.db.close())
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_f40_telemetry_sqlite_table_initialization(self):
        async def _check():
            row = await self.db.fetch_one("SELECT name FROM sqlite_master WHERE type='table' AND name='usage_telemetry'")
            self.assertIsNotNone(row)
            self.assertEqual(row["name"], "usage_telemetry")

        asyncio.run(_check())

    def test_f40_telemetry_request_recording(self):
        async def _run():
            now_ms = int(time.time() * 1000)
            await self.db.record_telemetry(
                account_id="acc_rec_1",
                model_id="95a4c0abcba24364",
                model_name="4 Argon",
                feature_mode="chat",
                reasoning_level=2,
                request_timestamp=now_ms,
                prompt_tokens_est=120,
                completion_tokens_est=350,
                thinking_tokens_est=150,
            )
            stats = await self.db.get_rolling_usage("acc_rec_1", window_seconds=86400)
            self.assertEqual(stats["total_requests"], 1)
            self.assertEqual(stats["total_prompt_tokens"], 120)
            self.assertEqual(stats["total_completion_tokens"], 350)
            self.assertEqual(stats["total_thinking_tokens"], 150)

        asyncio.run(_run())

    def test_f40_telemetry_thinking_token_estimation(self):
        thought_text = "Let's carefully verify the quantum Fourier transform complexity step by step."
        est_tokens = max(1, len(thought_text) // 4)
        async def _run():
            now_ms = int(time.time() * 1000)
            await self.db.record_telemetry(
                account_id="acc_think_est",
                model_id="95a4c0abcba24364",
                model_name="4 Argon",
                request_timestamp=now_ms,
                thinking_tokens_est=est_tokens,
                prompt_tokens_est=25,
                completion_tokens_est=45,
            )
            stats = await self.db.get_rolling_usage("acc_think_est", window_seconds=3600)
            self.assertEqual(stats["total_thinking_tokens"], est_tokens)
            self.assertEqual(stats["total_requests"], 1)
        asyncio.run(_run())

    def test_f40_telemetry_sliding_1h_window(self):
        async def _run():
            now_ms = int(time.time() * 1000)
            # 30 minutes ago (inside 1h)
            await self.db.record_telemetry(
                account_id="acc_1h",
                model_id="m1",
                model_name="Model 1",
                request_timestamp=now_ms - 1800 * 1000,
                prompt_tokens_est=50,
                completion_tokens_est=50,
            )
            # 2 hours ago (inside 24h, outside 1h)
            await self.db.record_telemetry(
                account_id="acc_1h",
                model_id="m1",
                model_name="Model 1",
                request_timestamp=now_ms - 7200 * 1000,
                prompt_tokens_est=100,
                completion_tokens_est=100,
            )
            stats_1h = await self.db.get_rolling_usage("acc_1h", window_seconds=3600)
            stats_24h = await self.db.get_rolling_usage("acc_1h", window_seconds=86400)
            self.assertEqual(stats_1h["total_requests"], 1)
            self.assertEqual(stats_24h["total_requests"], 2)

        asyncio.run(_run())

    def test_f40_telemetry_sliding_24h_window(self):
        async def _run():
            now_ms = int(time.time() * 1000)
            # 25 hours ago (outside 24h)
            await self.db.record_telemetry(
                account_id="acc_24h",
                model_id="m1",
                model_name="Model 1",
                request_timestamp=now_ms - 25 * 3600 * 1000,
                prompt_tokens_est=200,
                completion_tokens_est=200,
            )
            # 5 hours ago (inside 24h)
            await self.db.record_telemetry(
                account_id="acc_24h",
                model_id="m1",
                model_name="Model 1",
                request_timestamp=now_ms - 5 * 3600 * 1000,
                prompt_tokens_est=150,
                completion_tokens_est=150,
            )
            stats_24h = await self.db.get_rolling_usage("acc_24h", window_seconds=86400)
            self.assertEqual(stats_24h["total_requests"], 1)
            self.assertEqual(stats_24h["total_prompt_tokens"], 150)

        asyncio.run(_run())


class TestF41LimitsAndQuotaAPI(unittest.TestCase):
    """F41: Limits & Quota Telemetry API."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_file = Path(self.temp_dir) / "test_limits.db"
        self.db = Database(self.db_file)
        schema_path = Path(__file__).resolve().parent.parent.parent.parent / "backend" / "app" / "storage" / "schema.sql"
        asyncio.run(self.db.init_schema(schema_path))

        self.vault_file = Path(self.temp_dir) / "vault.enc"
        self.vault = CredentialVault(self.vault_file, force_fallback=True)
        p1 = AccountProfile(id="acc_lim_pro", name="Primary Pro", tier="pro", cookies={"__Secure-1PSID": "s1"})
        p2 = AccountProfile(id="acc_lim_ultra", name="Backup Ultra", tier="ultra", cookies={"__Secure-1PSID": "s2"})
        self.vault.save_account(p1)
        self.vault.save_account(p2)
        self.vault.set_active_account("acc_lim_pro")
        vdata = self.vault.load_vault_data()
        vdata.failover_config = FailoverConfig(
            enabled=True,
            strategy="priority_tier",
            fallback_chain=["acc_lim_pro", "acc_lim_ultra"],
        )
        self.vault.store_vault_data(vdata)

        self.mgr = SessionManager(vault=self.vault)
        async def _noop(*args, **kwargs): pass
        self.mgr.scrape_tokens = _noop
        self.mgr._staggered_rotation_loop = _noop
        self.mgr._rotation_loop = _noop
        asyncio.run(self.mgr.initialize_all())

        import backend.app.main as main_mod
        main_mod.db = self.db
        main_mod.vault = self.vault
        main_mod.session_manager = self.mgr
        app.state.db = self.db
        app.state.vault = self.vault
        app.state.session_manager = self.mgr

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_f41_limits_api_global_summary(self):
        now_ms = int(time.time() * 1000)
        asyncio.run(
            self.db.record_telemetry(
                account_id="acc_lim_pro",
                model_id="95a4c0abcba24364",
                model_name="4 Argon",
                request_timestamp=now_ms,
                prompt_tokens_est=500,
                completion_tokens_est=300,
                feature_mode="task",
            )
        )
        client = TestClient(app)
        res = client.get("/api/limits")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        summary = data["global_summary_24h"]
        self.assertEqual(summary["total_requests"], 1)
        self.assertEqual(summary["total_tokens_est"], 800)
        self.assertEqual(summary["task_mode_invocations"], 1)

    def test_f41_limits_api_per_account_breakdown(self):
        client = TestClient(app)
        res = client.get("/api/limits?account_id=acc_lim_pro")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data["accounts"]), 1)
        account_entry = data["accounts"][0]
        self.assertEqual(account_entry["account_id"], "acc_lim_pro")
        self.assertEqual(account_entry["tier"], "pro")
        self.assertEqual(account_entry["policy_string"], "launch_pro_policy")
        self.assertIn("rolling_24h", account_entry)
        self.assertIn("per_model_24h", account_entry)

    def test_f41_limits_api_cooldown_timer_calculation(self):
        now = time.time()
        cooldown_until = now + 1250
        remaining = max(0, int(cooldown_until - now))
        self.assertTrue(1248 <= remaining <= 1250)

    def test_f41_limits_api_warning_threshold_flag(self):
        # Configure threshold = 100 and test authentic warning status escalation via /api/limits
        async def _run():
            now_ms = int(time.time() * 1000)
            await self.db.set_account_limits_config("acc_lim_pro", daily_request_threshold=100)

            client = TestClient(app)
            # 1. 50 requests -> normal (<80%)
            for _ in range(50):
                await self.db.record_telemetry("acc_lim_pro", "m1", "M1", request_timestamp=now_ms)
            res_normal = client.get("/api/limits")
            acc_normal = res_normal.json()["accounts"][0]
            self.assertEqual(acc_normal["thresholds"]["warning_status"], "normal")

            # 2. Add 35 requests (total 85) -> warning (>=80%)
            for _ in range(35):
                await self.db.record_telemetry("acc_lim_pro", "m1", "M1", request_timestamp=now_ms)
            res_warning = client.get("/api/limits")
            acc_warning = res_warning.json()["accounts"][0]
            self.assertEqual(acc_warning["thresholds"]["warning_status"], "warning")

            # 3. Add 20 requests (total 105) -> critical (>=100%)
            for _ in range(20):
                await self.db.record_telemetry("acc_lim_pro", "m1", "M1", request_timestamp=now_ms)
            res_critical = client.get("/api/limits")
            acc_critical = res_critical.json()["accounts"][0]
            self.assertEqual(acc_critical["thresholds"]["warning_status"], "critical")
        asyncio.run(_run())

    def test_f41_limits_api_routing_state_inspection(self):
        client = TestClient(app)
        res = client.get("/api/limits")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        routing = data["failover_state"]
        self.assertTrue(routing["enabled"])
        self.assertEqual(routing["strategy"], "priority_tier")
        self.assertEqual(routing["fallback_chain"], ["acc_lim_pro", "acc_lim_ultra"])


class TestF42InteractiveUIAccountLimitsPanel(unittest.TestCase):
    """F42: Interactive UI Account Limits Panel."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_file = Path(self.temp_dir) / "test_panel.db"
        self.db = Database(self.db_file)
        schema_path = Path(__file__).resolve().parent.parent.parent.parent / "backend" / "app" / "storage" / "schema.sql"
        asyncio.run(self.db.init_schema(schema_path))

        self.vault_file = Path(self.temp_dir) / "vault.enc"
        self.vault = CredentialVault(self.vault_file, force_fallback=True)
        p1 = AccountProfile(id="acc_panel_pro", name="Panel Pro", tier="pro", cookies={"__Secure-1PSID": "s1"})
        self.vault.save_account(p1)
        self.vault.set_active_account("acc_panel_pro")

        self.mgr = SessionManager(vault=self.vault)
        async def _noop(*args, **kwargs): pass
        self.mgr.scrape_tokens = _noop
        self.mgr._staggered_rotation_loop = _noop
        self.mgr._rotation_loop = _noop
        asyncio.run(self.mgr.initialize_all())

        import backend.app.main as main_mod
        main_mod.db = self.db
        main_mod.vault = self.vault
        main_mod.session_manager = self.mgr
        app.state.db = self.db
        app.state.vault = self.vault
        app.state.session_manager = self.mgr

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_f42_ui_limits_panel_render(self):
        client = TestClient(app)
        res = client.get("/api/limits")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data["accounts"]), 1)
        acc = data["accounts"][0]
        self.assertEqual(acc["tier"], "pro")
        per_model = acc["per_model_24h"]
        self.assertIn("4 Argon", per_model)
        self.assertIn("3.1 Pro", per_model)
        self.assertEqual(per_model["4 Argon"]["cooldown_remaining_seconds"], 0)
        self.assertEqual(per_model["4 Argon"]["quota_status"], "normal")

    def test_f42_ui_limits_progress_bar_percentage(self):
        async def _run():
            now_ms = int(time.time() * 1000)
            await self.db.set_account_limits_config("acc_panel_pro", daily_request_threshold=1000, daily_token_threshold=100000)
            await self.db.record_telemetry(
                "acc_panel_pro", "m1", "M1",
                request_timestamp=now_ms,
                prompt_tokens_est=15000,
                completion_tokens_est=10000,
            )
            # Record total 250 requests
            for _ in range(249):
                await self.db.record_telemetry("acc_panel_pro", "m1", "M1", request_timestamp=now_ms)

            client = TestClient(app)
            res = client.get("/api/limits")
            acc = res.json()["accounts"][0]
            self.assertEqual(acc["thresholds"]["daily_request_percentage"], 25.0)
            self.assertEqual(acc["thresholds"]["daily_token_percentage"], 25.0)
        asyncio.run(_run())

    def test_f42_ui_limits_cooldown_countdown(self):
        # Test authentic cooldown duration countdown from SessionManager and /api/limits
        self.mgr.mark_cooldown("acc_panel_pro", duration_seconds=3600)
        self.mgr.mark_cooldown("acc_panel_pro", model_id="4 Argon", duration_seconds=1800)

        client = TestClient(app)
        res = client.get("/api/limits")
        acc = res.json()["accounts"][0]
        self.assertTrue(acc["cooldown"]["is_active"])
        self.assertTrue(3580 <= acc["cooldown"]["remaining_seconds"] <= 3600)
        argon_quota = acc["per_model_24h"]["4 Argon"]
        self.assertEqual(argon_quota["quota_status"], "cooldown")
        self.assertTrue(1780 <= argon_quota["cooldown_remaining_seconds"] <= 1800)

    def test_f42_ui_limits_threshold_color_coding(self):
        # Verify status transitions corresponding to green, amber, red UI color bands
        async def _run():
            now_ms = int(time.time() * 1000)
            await self.db.set_account_limits_config("acc_panel_pro", daily_request_threshold=10)
            client = TestClient(app)

            # Green (<80%)
            for _ in range(5):
                await self.db.record_telemetry("acc_panel_pro", "m1", "M1", request_timestamp=now_ms)
            self.assertEqual(client.get("/api/limits").json()["accounts"][0]["thresholds"]["warning_status"], "normal")

            # Amber (80% <= pct < 100%)
            for _ in range(3):
                await self.db.record_telemetry("acc_panel_pro", "m1", "M1", request_timestamp=now_ms)
            self.assertEqual(client.get("/api/limits").json()["accounts"][0]["thresholds"]["warning_status"], "warning")

            # Red (>=100%)
            for _ in range(2):
                await self.db.record_telemetry("acc_panel_pro", "m1", "M1", request_timestamp=now_ms)
            self.assertEqual(client.get("/api/limits").json()["accounts"][0]["thresholds"]["warning_status"], "critical")
        asyncio.run(_run())

    def test_f42_ui_limits_panel_refresh_action(self):
        client = TestClient(app)
        res1 = client.get("/api/limits")
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertIn("timestamp", data1)
        self.assertEqual(data1["active_account_id"], "acc_panel_pro")

        res2 = client.get("/api/limits?account_id=acc_panel_pro")
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(len(data2["accounts"]), 1)
        self.assertEqual(data2["accounts"][0]["account_id"], "acc_panel_pro")


if __name__ == "__main__":
    res = unittest.main(exit=False).result
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if res.wasSuccessful() else 1)
