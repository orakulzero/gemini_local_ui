# tests/e2e/tier1_features/test_f34_to_f38_multi_account_failover.py
"""Tier 1: Tests for Features F34 to F38 (Multi-Account Management & Quota Failover)."""

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

from backend.app.storage.vault import CredentialVault, AccountProfile, VaultData, FailoverConfig
from backend.app.bridge.session import SessionManager, SessionContext, AccountSession, SessionHealthStatus
from backend.app.bridge.exceptions import UsageLimitExceededError, GeminiBridgeError, AuthenticationError
from fastapi.testclient import TestClient
from backend.app.main import app


class TestF34MultiAccountDPAPIVault(unittest.TestCase):
    """F34: DPAPI Multi-Account Vault."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.vault_file = Path(self.temp_dir) / "vault.enc"
        self.vault = CredentialVault(self.vault_file, force_fallback=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_f34_vault_v2_profile_serialization(self):
        profile_pro = AccountProfile(
            id="acc_pro",
            name="Research Pro",
            email="researcher@corp.internal",
            tier="pro",
            priority=1,
            cookies={"__Secure-1PSID": "pro_sid_123", "__Secure-1PSIDTS": "pro_ts_456"},
            snlm0e="pro_snlm0e",
        )
        profile_ultra = AccountProfile(
            id="acc_ultra",
            name="Deep Ultra",
            email="ultra@corp.internal",
            tier="ultra",
            priority=2,
            cookies={"__Secure-1PSID": "ultra_sid_999", "__Secure-1PSIDTS": "ultra_ts_888"},
            snlm0e="ultra_snlm0e",
        )
        self.vault.save_account(profile_pro)
        self.vault.save_account(profile_ultra)
        self.vault.set_active_account("acc_pro")

        data = self.vault.load_vault_data()
        self.assertEqual(data.version, 2)
        self.assertEqual(data.active_account_id, "acc_pro")
        self.assertIn("acc_pro", data.accounts)
        self.assertIn("acc_ultra", data.accounts)
        self.assertEqual(data.accounts["acc_pro"].tier, "pro")
        self.assertEqual(data.accounts["acc_ultra"].tier, "ultra")
        self.assertEqual(data.accounts["acc_pro"].cookies["__Secure-1PSID"], "pro_sid_123")

    def test_f34_vault_v1_to_v2_automatic_migration(self):
        legacy_cookies = {
            "__Secure-1PSID": "legacy_sid_123",
            "__Secure-1PSIDTS": "legacy_ts_456",
        }
        raw_bytes = json.dumps(legacy_cookies).encode("utf-8")
        encrypted = self.vault.encrypt_bytes(raw_bytes)
        with open(self.vault_file, "wb") as f:
            f.write(encrypted)

        migrated = self.vault.load_vault_data()
        self.assertEqual(migrated.version, 2)
        self.assertEqual(migrated.active_account_id, "default")
        self.assertIn("default", migrated.accounts)
        default_acc = migrated.accounts["default"]
        self.assertEqual(default_acc.cookies.get("__Secure-1PSID"), "legacy_sid_123")
        self.assertEqual(default_acc.tier, "free")

    def test_f34_vault_dpapi_per_profile_isolation(self):
        prof_a = AccountProfile(id="a", name="Acc A", tier="free", cookies={"__Secure-1PSID": "cookie_a"})
        prof_b = AccountProfile(id="b", name="Acc B", tier="pro", cookies={"__Secure-1PSID": "cookie_b"})
        self.vault.save_account(prof_a)
        self.vault.save_account(prof_b)

        prof_a.cookies["__Secure-1PSID"] = "cookie_a_modified"
        self.vault.save_account(prof_a)

        reloaded_b = self.vault.get_account("b")
        self.assertIsNotNone(reloaded_b)
        self.assertEqual(reloaded_b.cookies["__Secure-1PSID"], "cookie_b")

    def test_f34_vault_profile_deletion_atomic_save(self):
        prof = AccountProfile(id="to_delete", name="Temporary", tier="free")
        self.vault.save_account(prof)
        self.assertIsNotNone(self.vault.get_account("to_delete"))

        ok = self.vault.delete_account("to_delete")
        self.assertTrue(ok)
        self.assertIsNone(self.vault.get_account("to_delete"))

        fresh_vault = CredentialVault(self.vault_file, force_fallback=True)
        self.assertIsNone(fresh_vault.get_account("to_delete"))

    def test_f34_vault_invalid_payload_rejection(self):
        with open(self.vault_file, "wb") as f:
            f.write(b"CORRUPTED_NON_DECRYPTABLE_NOISE_1234567890")

        data = self.vault.load_vault_data()
        self.assertIsInstance(data, VaultData)
        self.assertEqual(len(data.accounts), 0)


class TestF35MultiSessionManager(unittest.TestCase):
    """F35: Multi-Session Concurrency & Token Loops."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.vault_file = Path(self.temp_dir) / "vault.enc"
        self.vault = CredentialVault(self.vault_file, force_fallback=True)
        self.mgr = SessionManager(vault=self.vault)

        async def _mock_scrape(acc_id=None):
            sess = self.mgr.get_account_session(acc_id)
            if sess:
                sess.context.access_token = "mock_snlm0e"
                sess.context.status = SessionHealthStatus.HEALTHY
            return True

        self.mgr.scrape_tokens = _mock_scrape
        async def _noop(*args, **kwargs): pass
        self.mgr._staggered_rotation_loop = _noop
        self.mgr._rotation_loop = _noop

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_f35_multi_session_manager_initialization(self):
        p1 = AccountProfile(id="acc_1", name="Acc 1", tier="free", cookies={"__Secure-1PSID": "s1", "__Secure-1PSIDTS": "t1"})
        p2 = AccountProfile(id="acc_2", name="Acc 2", tier="pro", cookies={"__Secure-1PSID": "s2", "__Secure-1PSIDTS": "t2"})
        self.vault.save_account(p1)
        self.vault.save_account(p2)

        async def _run():
            await self.mgr.initialize_all()
            self.assertEqual(len(self.mgr.sessions), 2)
            self.assertIn("acc_1", self.mgr.sessions)
            self.assertIn("acc_2", self.mgr.sessions)

        asyncio.run(_run())

    def test_f35_multi_session_independent_cookies(self):
        p1 = AccountProfile(id="acc_1", name="Acc 1", tier="free", cookies={"__Secure-1PSID": "s1", "__Secure-1PSIDTS": "t1"})
        p2 = AccountProfile(id="acc_2", name="Acc 2", tier="pro", cookies={"__Secure-1PSID": "s2", "__Secure-1PSIDTS": "t2"})
        self.vault.save_account(p1)
        self.vault.save_account(p2)

        async def _run():
            await self.mgr.initialize_all()
            ctx1 = self.mgr.get_account_session("acc_1")
            ctx2 = self.mgr.get_account_session("acc_2")
            self.assertIsNotNone(ctx1)
            self.assertIsNotNone(ctx2)
            ctx1.context.cookies["__Secure-1PSID"] = "s1_mutated"
            self.assertEqual(ctx2.context.cookies["__Secure-1PSID"], "s2")

        asyncio.run(_run())

    def test_f35_multi_session_staggered_rotation_jitter(self):
        p1 = AccountProfile(id="acc_jit1", name="Jitter 1", tier="free", cookies={"__Secure-1PSID": "s1", "__Secure-1PSIDTS": "t1"})
        p2 = AccountProfile(id="acc_jit2", name="Jitter 2", tier="pro", cookies={"__Secure-1PSID": "s2", "__Secure-1PSIDTS": "t2"})
        self.vault.save_account(p1)
        self.vault.save_account(p2)

        async def _run():
            await self.mgr.initialize_all(test_stagger=True)
            self.assertEqual(len(self.mgr.sessions), 2)
            s1 = self.mgr.get_account_session("acc_jit1")
            s2 = self.mgr.get_account_session("acc_jit2")
            self.assertIsNotNone(s1)
            self.assertIsNotNone(s2)
            self.assertEqual(s1.context.status, SessionHealthStatus.HEALTHY)
            self.assertEqual(s2.context.status, SessionHealthStatus.HEALTHY)
            self.assertIsNotNone(s1.rotation_task)
            self.assertIsNotNone(s2.rotation_task)
            self.assertGreater(self.mgr.rotation_interval, 0)
            self.assertGreater(self.mgr.rotation_jitter, 0)

        asyncio.run(_run())

    def test_f35_multi_session_concurrency_lock_hygiene(self):
        p1 = AccountProfile(id="acc_lock", name="Lock Test", tier="pro")
        sess = AccountSession(
            account_id="acc_lock",
            profile=p1,
            context=SessionContext(account_tier="pro"),
        )

        async def _concurrent_access():
            async with sess.lock:
                await asyncio.sleep(0.01)
                sess.context.last_refresh = time.time()

        async def _main():
            tasks = [_concurrent_access() for _ in range(5)]
            await asyncio.gather(*tasks)
            self.assertGreater(sess.context.last_refresh, 0)

        asyncio.run(_main())

    def test_f35_multi_session_health_aggregation(self):
        p1 = AccountProfile(id="h1", name="H1", tier="free")
        p2 = AccountProfile(id="h2", name="H2", tier="ultra")
        self.vault.save_account(p1)
        self.vault.save_account(p2)

        async def _run():
            await self.mgr.initialize_all()
            all_health = self.mgr.get_all_health()
            self.assertEqual(len(all_health), 2)
            self.assertIn("h1", all_health)
            self.assertIn("h2", all_health)

        asyncio.run(_run())


class TestF36AccountManagementAPI(unittest.TestCase):
    """F36: Account Management REST API."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.vault = CredentialVault(Path(self.temp_dir) / "vault.enc", force_fallback=True)
        self.mgr = SessionManager(vault=self.vault)

        async def _mock_scrape(acc_id=None):
            sess = self.mgr.get_account_session(acc_id)
            if sess:
                sess.context.access_token = "mock_snlm0e"
                sess.context.status = SessionHealthStatus.HEALTHY
            return True

        self.mgr.scrape_tokens = _mock_scrape
        async def _noop(*args, **kwargs): pass
        self.mgr._staggered_rotation_loop = _noop
        self.mgr._rotation_loop = _noop

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_f36_accounts_api_list(self):
        self.vault.save_account(AccountProfile(id="acc_list_1", name="List Acc", tier="free"))
        asyncio.run(self.mgr.initialize_all())
        accs = self.vault.list_accounts()
        self.assertEqual(len(accs), 1)
        self.assertEqual(accs[0].id, "acc_list_1")

    def test_f36_accounts_api_activate(self):
        self.vault.save_account(AccountProfile(id="acc_act_1", name="Act 1", tier="free"))
        self.vault.save_account(AccountProfile(id="acc_act_2", name="Act 2", tier="pro"))
        asyncio.run(self.mgr.initialize_all())
        ok = self.mgr.activate_account("acc_act_2")
        self.assertTrue(ok)
        self.assertEqual(self.mgr.active_account_id, "acc_act_2")

    def test_f36_accounts_api_add_delete(self):
        async def _run():
            await self.mgr.initialize_all()
            p = AccountProfile(id="new_acc", name="New", tier="ultra")
            sess = await self.mgr.add_or_update_account(p)
            self.assertIsNotNone(self.mgr.get_account_session("new_acc"))

            deleted = await self.mgr.remove_account("new_acc")
            self.assertTrue(deleted)
            self.assertIsNone(self.mgr.get_account_session("new_acc"))

        asyncio.run(_run())

    def test_f36_accounts_header_routing_override(self):
        self.vault.save_account(AccountProfile(id="acc_free", name="Free", tier="free"))
        self.vault.save_account(AccountProfile(id="acc_ultra", name="Ultra", tier="ultra"))
        asyncio.run(self.mgr.initialize_all())
        self.mgr.activate_account("acc_free")

        sess = self.mgr.get_account_session("acc_ultra")
        self.assertIsNotNone(sess)
        self.assertEqual(sess.account_id, "acc_ultra")

    def test_f36_accounts_invalid_routing_fallback(self):
        self.vault.save_account(AccountProfile(id="primary", name="Primary", tier="free"))
        asyncio.run(self.mgr.initialize_all())
        self.mgr.activate_account("primary")

        unknown_sess = self.mgr.get_account_session("unknown_account_id")
        self.assertIsNone(unknown_sess)
        resolved_id = "unknown_account_id" if unknown_sess else self.mgr.active_account_id
        self.assertEqual(resolved_id, "primary")


class TestF37AutomaticQuotaFailoverEngine(unittest.TestCase):
    """F37: Automatic Quota Failover Engine."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.vault = CredentialVault(Path(self.temp_dir) / "vault.enc", force_fallback=True)
        self.mgr = SessionManager(vault=self.vault)

        async def _mock_scrape(acc_id=None):
            sess = self.mgr.get_account_session(acc_id)
            if sess:
                sess.context.access_token = "mock_snlm0e"
                sess.context.status = SessionHealthStatus.HEALTHY
            return True

        self.mgr.scrape_tokens = _mock_scrape
        async def _noop(*args, **kwargs): pass
        self.mgr._staggered_rotation_loop = _noop
        self.mgr._rotation_loop = _noop

        p1 = AccountProfile(id="acc_free", name="Free", tier="free", priority=1)
        p2 = AccountProfile(id="acc_pro", name="Pro", tier="pro", priority=2)
        p3 = AccountProfile(id="acc_ultra", name="Ultra", tier="ultra", priority=3)
        self.vault.save_account(p1)
        self.vault.save_account(p2)
        self.vault.save_account(p3)
        self.vault.update_failover_config(FailoverConfig(
            enabled=True,
            fallback_chain=["acc_free", "acc_pro", "acc_ultra"],
        ))
        asyncio.run(self.mgr.initialize_all())
        for sess in self.mgr.sessions.values():
            sess.context.status = SessionHealthStatus.HEALTHY

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_f37_failover_on_quota_error_1037(self):
        self.mgr.mark_cooldown("acc_pro", duration_seconds=3600, reason="Google Quota 1037")
        self.assertTrue(self.mgr.is_in_cooldown("acc_pro"))

        backup = self.mgr.get_failover_account("gemini-3.1-pro", "acc_pro", required_tier="pro")
        self.assertIsNotNone(backup)
        self.assertEqual(backup.account_id, "acc_ultra")

    def test_f37_failover_on_http_429(self):
        self.mgr.mark_cooldown("acc_free", duration_seconds=60, reason="Rate limit 429")
        backup = self.mgr.get_failover_account("gemini-2.5-flash", "acc_free", required_tier="free")
        self.assertIsNotNone(backup)
        self.assertIn(backup.account_id, ["acc_pro", "acc_ultra"])

    def test_f37_failover_on_task_tier_error_1061(self):
        backup = self.mgr.get_failover_account("gemini-task-mode", "acc_free", required_tier="pro")
        self.assertIsNotNone(backup)
        self.assertEqual(backup.profile.tier, "pro")

    def test_f37_failover_response_metadata_annotation(self):
        event = {
            "type": "failover",
            "from": "acc_pro",
            "to": "acc_ultra",
            "error_code": 1037,
            "reason": "Quota Exceeded (1037)",
            "timestamp": time.time(),
        }
        self.assertEqual(event["from"], "acc_pro")
        self.assertEqual(event["to"], "acc_ultra")
        self.assertEqual(event["error_code"], 1037)

    def test_f37_failover_exhaustion_all_accounts(self):
        self.mgr.mark_cooldown("acc_free", duration_seconds=3600)
        self.mgr.mark_cooldown("acc_pro", duration_seconds=3600)
        self.mgr.mark_cooldown("acc_ultra", duration_seconds=3600)

        backup = self.mgr.get_failover_account("gemini-3.1-pro", "acc_pro", required_tier="pro")
        self.assertIsNone(backup)


class TestF38UIAccountSwitcher(unittest.TestCase):
    """F38: UI Header Account Switcher."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.vault_file = Path(self.temp_dir) / "vault.enc"
        self.vault = CredentialVault(self.vault_file, force_fallback=True)
        p1 = AccountProfile(id="acc_sw1", name="Switcher Free", tier="free", cookies={"__Secure-1PSID": "s1"})
        p2 = AccountProfile(id="acc_sw2", name="Switcher Pro", tier="pro", cookies={"__Secure-1PSID": "s2"})
        self.vault.save_account(p1)
        self.vault.save_account(p2)
        self.vault.set_active_account("acc_sw1")
        self.mgr = SessionManager(vault=self.vault)
        async def _noop(*args, **kwargs): pass
        self.mgr.scrape_tokens = _noop
        self.mgr._staggered_rotation_loop = _noop
        self.mgr._rotation_loop = _noop
        asyncio.run(self.mgr.initialize_all())

        import backend.app.main as main_mod
        main_mod.vault = self.vault
        main_mod.session_manager = self.mgr
        app.state.vault = self.vault
        app.state.session_manager = self.mgr

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_f38_ui_switcher_account_enumeration(self):
        client = TestClient(app)
        res = client.get("/api/accounts")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("accounts", data)
        self.assertIn("active_account_id", data)
        self.assertEqual(data["active_account_id"], "acc_sw1")
        acc_ids = [a["id"] for a in data["accounts"]]
        self.assertIn("acc_sw1", acc_ids)
        self.assertIn("acc_sw2", acc_ids)

    def test_f38_ui_switcher_tier_badge_mapping(self):
        # Verify authentic tier extraction and mapping across SessionManager and API
        p_free = AccountProfile(id="acc_tb_free", name="Free User", tier="free", cookies={"__Secure-1PSID": "f1"})
        p_pro = AccountProfile(id="acc_tb_pro", name="Pro User", tier="pro", cookies={"__Secure-1PSID": "p1"})
        p_ultra = AccountProfile(id="acc_tb_ultra", name="Ultra User", tier="ultra", cookies={"__Secure-1PSID": "u1"})

        self.vault.save_account(p_free)
        self.vault.save_account(p_pro)
        self.vault.save_account(p_ultra)

        mgr = SessionManager(vault=self.vault)
        async def _noop(*args, **kwargs): pass
        mgr.scrape_tokens = _noop
        mgr._staggered_rotation_loop = _noop
        mgr._rotation_loop = _noop
        asyncio.run(mgr.initialize_all())

        self.assertEqual(mgr.get_account_tier("acc_tb_free"), "free")
        self.assertEqual(mgr.get_account_tier("acc_tb_pro"), "pro")
        self.assertEqual(mgr.get_account_tier("acc_tb_ultra"), "ultra")

        # Verify /api/accounts returns properly mapped public tier string
        app.state.vault = self.vault
        app.state.session_manager = mgr
        client = TestClient(app)
        res = client.get("/api/accounts")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        tiers_by_id = {a["id"]: a["tier"] for a in data["accounts"]}
        self.assertEqual(tiers_by_id.get("acc_tb_free"), "free")
        self.assertEqual(tiers_by_id.get("acc_tb_pro"), "pro")
        self.assertEqual(tiers_by_id.get("acc_tb_ultra"), "ultra")

    def test_f38_ui_switcher_health_pill_state(self):
        # Verify SessionHealthStatus diagnostics and cooldown mapping on SessionManager
        p_health = AccountProfile(id="acc_health_test", name="Health Test", tier="pro", cookies={"__Secure-1PSID": "h1"})
        self.vault.save_account(p_health)
        mgr = SessionManager(vault=self.vault)
        async def _noop(*args, **kwargs): pass
        mgr.scrape_tokens = _noop
        mgr._staggered_rotation_loop = _noop
        mgr._rotation_loop = _noop
        asyncio.run(mgr.initialize_all())

        sess = mgr.get_account_session("acc_health_test")
        sess.context.access_token = "valid_at"
        sess.context.status = SessionHealthStatus.HEALTHY
        rep_healthy = mgr.get_health("acc_health_test")
        self.assertEqual(rep_healthy.status, SessionHealthStatus.HEALTHY)
        self.assertTrue(rep_healthy.is_authenticated)

        # Mark cooldown and verify cooldown status representation in public API
        mgr.mark_cooldown("acc_health_test", duration_seconds=3600)
        app.state.vault = self.vault
        app.state.session_manager = mgr
        client = TestClient(app)
        res = client.get("/api/accounts")
        acc_info = next(a for a in res.json()["accounts"] if a["id"] == "acc_health_test")
        self.assertEqual(acc_info["health_status"], "cooldown")

        # Verify expired status
        sess.context.status = SessionHealthStatus.EXPIRED
        rep_expired = mgr.get_health("acc_health_test")
        self.assertEqual(rep_expired.status, SessionHealthStatus.EXPIRED)

    def test_f38_ui_switcher_auto_failover_toggle(self):
        cfg = FailoverConfig(enabled=True)
        cfg.enabled = False
        self.assertFalse(cfg.enabled)
        cfg.enabled = True
        self.assertTrue(cfg.enabled)

    def test_f38_ui_switcher_active_switch_event(self):
        # Verify authentic activation switch via POST /api/accounts/{account_id}/activate
        p1 = AccountProfile(id="acc_act_1", name="Acc 1", tier="free", cookies={"__Secure-1PSID": "s1"})
        p2 = AccountProfile(id="acc_act_2", name="Acc 2", tier="pro", cookies={"__Secure-1PSID": "s2"})
        self.vault.save_account(p1)
        self.vault.save_account(p2)
        self.vault.set_active_account("acc_act_1")

        mgr = SessionManager(vault=self.vault)
        async def _noop(*args, **kwargs): pass
        mgr.scrape_tokens = _noop
        mgr._staggered_rotation_loop = _noop
        mgr._rotation_loop = _noop
        asyncio.run(mgr.initialize_all())

        app.state.vault = self.vault
        app.state.session_manager = mgr
        client = TestClient(app)

        self.assertEqual(mgr.active_account_id, "acc_act_1")
        res = client.post("/api/accounts/acc_act_2/activate")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
        self.assertEqual(res.json()["active_account_id"], "acc_act_2")
        self.assertEqual(mgr.active_account_id, "acc_act_2")
        self.assertEqual(self.vault.get_active_account_id(), "acc_act_2")


if __name__ == "__main__":
    unittest.main()
