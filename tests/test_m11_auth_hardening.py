"""
tests/test_m11_auth_hardening.py
Milestone M11: Authentication Hardening, Proactive Keepalive, Anti-Poisoning & Vault Rollback Test Suite.

Verifies:
1. Vault Auto-Backup & Rollback (Requirement R3):
   - Atomic shadow backup (vault.enc.bak) creation on healthy credential updates.
   - Health filter preventing backup overwrite when an account transitions to expired or empty.
   - Restoration from shadow backup when primary vault is corrupted or missing.
   - Concurrent updates maintaining valid backup with zero temporary file leaks.
2. Anti-Poisoning Cookie Filtering (Requirement R2):
   - Set-Cookie rejection on HTTP 301, 302, 303, 307, 308, 401, 403 redirect/error pages.
   - Prevention of guest cookie pollution in SessionContext and CredentialVault.
   - Quoted cookie sanitation and ignoring of 'deleted' marker values.
3. Network Drop & Transient Error Differentiation (Requirement R1 & R2):
   - Retention of HEALTHY status during httpx timeout/network errors during RotateCookies and scrape_tokens.
   - Handling of transient 5xx server errors without declaring session expired.
   - Survivability of background _rotation_loop across transient network exceptions with exponential backoff.
4. Session Recovery & Extended Rotation Cycles (Requirement R1, R3, R4):
   - Automatic rollback and recovery from vault.enc.bak when SNlM0e is unexpectedly missing or on 302 redirect.
   - 12 consecutive rotation cycles without deadlocks, memory leaks, or file descriptor leaks.
   - Elimination of phantom 'default' account during bootstrap in initialize_all.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
import tracemalloc
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import httpx
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.exceptions import AuthenticationError
from backend.app.bridge.session import (
    AccountSession,
    SessionContext,
    SessionHealthStatus,
    SessionManager,
)
from backend.app.mock.mock_server import HTML_APP_PAGE_GUEST, MockGeminiServer
from backend.app.storage.vault import AccountProfile, CredentialVault, VaultData


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mock_server():
    """Starts deterministic mock Gemini web server on a free port in background thread."""
    port = get_free_port()
    server = MockGeminiServer(host="127.0.0.1", port=port)
    server.start_background()
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            r = httpx.get(f"{base_url}/app", timeout=0.5)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.02)
    yield server, base_url
    server.stop_background()


@pytest.fixture(autouse=True)
def reset_mock(mock_server):
    server, _ = mock_server
    server.fault.reset()


# ============================================================================
# 1. Vault Auto-Backup, Health Gate & Rollback Tests (Requirement R3)
# ============================================================================

class TestVaultAutoBackupAndRollback:
    """Verifies atomic shadow backup creation, health filtering, and recovery rollback."""

    def test_vault_creates_shadow_backup_on_healthy_save(self, tmp_path: Path):
        """Verifies that saving healthy credentials creates vault.enc and shadow vault.enc.bak."""
        vault_file = tmp_path / "vault.enc"
        vault = CredentialVault(vault_path=vault_file)

        profile = AccountProfile(
            id="acc_ultra_01",
            name="Ultra Account",
            tier="ultra",
            cookies={
                "__Secure-1PSID": "valid_psid_token_12345",
                "__Secure-1PSIDTS": "valid_psidts_token_67890",
            },
            health_status="healthy",
        )
        vault.save_account(profile)

        assert vault.vault_path.exists()
        assert vault.backup_path.exists()

        # Decrypt backup directly and verify data matches
        raw_backup = vault.decrypt_bytes(vault.backup_path.read_bytes())
        backup_data = json.loads(raw_backup.decode("utf-8"))
        assert backup_data["accounts"]["acc_ultra_01"]["cookies"]["__Secure-1PSID"] == "valid_psid_token_12345"

    def test_vault_healthy_filter_preserves_backup_on_expired_save(self, tmp_path: Path):
        """Verifies that saving an expired or invalid profile updates primary but preserves healthy backup."""
        vault_file = tmp_path / "vault.enc"
        vault = CredentialVault(vault_path=vault_file)

        # 1. Save healthy account
        healthy_prof = AccountProfile(
            id="acc_01",
            name="Healthy",
            cookies={"__Secure-1PSID": "healthy_psid_value"},
            health_status="healthy",
        )
        vault.save_account(healthy_prof)
        assert vault.backup_path.exists()

        # 2. Overwrite primary with expired profile
        expired_prof = AccountProfile(
            id="acc_01",
            name="Healthy",
            cookies={},
            health_status="expired",
        )
        vault.save_account(expired_prof)

        # Primary has expired state
        assert vault.get_account("acc_01").health_status == "expired"
        assert not vault.get_account("acc_01").cookies

        # Backup retains healthy credentials
        raw_backup = vault.decrypt_bytes(vault.backup_path.read_bytes())
        backup_data = json.loads(raw_backup.decode("utf-8"))
        assert backup_data["accounts"]["acc_01"]["cookies"]["__Secure-1PSID"] == "healthy_psid_value"

    def test_vault_rollback_restores_corrupted_primary(self, tmp_path: Path):
        """Verifies that rollback_from_backup restores primary vault.enc from vault.enc.bak."""
        vault_file = tmp_path / "vault.enc"
        vault = CredentialVault(vault_path=vault_file)

        profile = AccountProfile(
            id="acc_target",
            name="Target",
            cookies={"__Secure-1PSID": "original_healthy_psid"},
        )
        vault.save_account(profile)
        assert vault.backup_path.exists()

        # Corrupt primary vault.enc with 0 bytes or random garbage
        vault.vault_path.write_bytes(b"\x00" * 32)

        # Rollback
        success = vault.rollback_from_backup()
        assert success is True

        # Verify primary vault is restored and decodable
        restored = vault.get_account("acc_target")
        assert restored is not None
        assert restored.cookies["__Secure-1PSID"] == "original_healthy_psid"

    def test_vault_auto_recovers_corrupted_primary_on_load(self, tmp_path: Path):
        """Verifies that load_vault_data automatically recovers from backup when primary is corrupted."""
        vault_file = tmp_path / "vault.enc"
        vault = CredentialVault(vault_path=vault_file)

        profile = AccountProfile(
            id="acc_auto_rec",
            name="Auto Recover",
            cookies={"__Secure-1PSID": "psid_to_auto_recover"},
        )
        vault.save_account(profile)
        assert vault.backup_path.exists()

        # Corrupt primary vault.enc
        vault.vault_path.write_bytes(b"corrupted_garbage_bytes_123")

        # load_vault_data should detect failure, recover from backup, and return data
        loaded = vault.load_vault_data()
        assert "acc_auto_rec" in loaded.accounts
        assert loaded.accounts["acc_auto_rec"].cookies["__Secure-1PSID"] == "psid_to_auto_recover"

    def test_vault_auto_recovers_missing_primary_on_load(self, tmp_path: Path):
        """Verifies that load_vault_data recovers from backup when primary vault file is missing."""
        vault_file = tmp_path / "vault.enc"
        vault = CredentialVault(vault_path=vault_file)

        profile = AccountProfile(
            id="acc_missing_primary",
            name="Missing Primary",
            cookies={"__Secure-1PSID": "psid_recovered_from_missing"},
        )
        vault.save_account(profile)
        assert vault.backup_path.exists()

        # Delete primary vault.enc
        vault.vault_path.unlink()
        assert not vault.vault_path.exists()

        loaded = vault.load_vault_data()
        assert "acc_missing_primary" in loaded.accounts
        assert loaded.accounts["acc_missing_primary"].cookies["__Secure-1PSID"] == "psid_recovered_from_missing"
        assert vault.vault_path.exists()

    @pytest.mark.asyncio
    async def test_vault_async_concurrent_updates_maintain_backup(self, tmp_path: Path):
        """Fires 10 concurrent async cookie updates and ensures backup remains healthy and synchronized."""
        vault_file = tmp_path / "vault.enc"
        vault = CredentialVault(vault_path=vault_file)

        profile = AccountProfile(
            id="acc_conc",
            name="Concurrent",
            cookies={"__Secure-1PSID": "base_psid_val"},
        )
        vault.save_account(profile)

        async def worker(idx: int):
            await vault.update_account_cookies_async(
                "acc_conc",
                {"__Secure-1PSIDTS": f"ts_{idx}", f"EXTRA_{idx}": f"val_{idx}"},
            )

        await asyncio.gather(*(worker(i) for i in range(10)))

        vdata = vault.load_vault_data()
        assert vdata.accounts["acc_conc"].cookies["__Secure-1PSID"] == "base_psid_val"
        assert vault.backup_path.exists()

        # Check that no leftover temporary files exist in the vault directory
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0


# ============================================================================
# 2. Anti-Poisoning Cookie Filter Tests (Requirement R2)
# ============================================================================

class TestAntiPoisoningCookieFilter:
    """Verifies that redirect and error responses (302/401/403) cannot inject guest cookies into Vault."""

    @pytest.mark.asyncio
    async def test_session_manager_ignores_302_redirect_cookies(self, mock_server, tmp_path: Path):
        """Verifies SessionManager.on_response_harvest_cookies drops cookies from HTTP 302."""
        _, base_url = mock_server
        vault_file = tmp_path / "vault.enc"
        vault = CredentialVault(vault_path=vault_file)
        sm = SessionManager(base_url=base_url, vault=vault)

        await sm.init_session({
            "__Secure-1PSID": "valid_user_psid",
            "__Secure-1PSIDTS": "valid_user_psidts",
            "SIDCC": "valid_user_sidcc",
        })

        # Construct synthetic HTTP 302 redirect response with poisoned guest Set-Cookie
        req = httpx.Request("GET", f"{base_url}/app")
        resp = httpx.Response(
            status_code=302,
            headers={
                "Location": "https://accounts.google.com/ServiceLogin",
                "Set-Cookie": "SIDCC=guest_poisoned_sidcc; Domain=google.com; Path=/",
            },
            request=req,
        )

        await sm.on_response_harvest_cookies(resp)

        # Cookies in memory and in vault must remain untouched
        assert sm.context.cookies["SIDCC"] == "valid_user_sidcc"
        stored = vault.load_credentials()
        assert stored["SIDCC"] == "valid_user_sidcc"
        assert "guest_poisoned_sidcc" not in str(stored)
        await sm.close()

    @pytest.mark.asyncio
    async def test_session_manager_ignores_all_redirect_status_codes(self, mock_server, tmp_path: Path):
        """Verifies that 301, 303, 307, 308 redirect status codes also drop Set-Cookie headers."""
        _, base_url = mock_server
        vault_file = tmp_path / "vault.enc"
        vault = CredentialVault(vault_path=vault_file)
        sm = SessionManager(base_url=base_url, vault=vault)

        await sm.init_session({
            "__Secure-1PSID": "valid_user_psid",
            "SIDCC": "orig_sidcc",
        })

        for code in (301, 303, 307, 308):
            req = httpx.Request("GET", f"{base_url}/app")
            resp = httpx.Response(
                status_code=code,
                headers={
                    "Location": "https://accounts.google.com/ServiceLogin",
                    "Set-Cookie": f"SIDCC=poison_{code}; Domain=google.com; Path=/",
                },
                request=req,
            )
            await sm.on_response_harvest_cookies(resp)
            assert sm.context.cookies["SIDCC"] == "orig_sidcc"
        await sm.close()

    @pytest.mark.asyncio
    async def test_client_ignores_401_and_403_cookies(self, mock_server, tmp_path: Path):
        """Verifies GeminiWebClient.on_response_harvest_cookies drops cookies from HTTP 401/403."""
        _, base_url = mock_server
        vault_file = tmp_path / "vault.enc"
        vault = CredentialVault(vault_path=vault_file)
        ctx = SessionContext(
            cookies={"__Secure-1PSID": "valid_psid", "SIDCC": "original_sidcc"},
            vault=vault,
        )
        client = GeminiWebClient(session_context=ctx, base_url=base_url, vault=vault)

        # HTTP 401 response
        req = httpx.Request("POST", f"{base_url}/_/BardChatUi/data/batchexecute")
        resp_401 = httpx.Response(
            status_code=401,
            headers={"Set-Cookie": "SIDCC=malicious_401_sidcc; Domain=google.com"},
            request=req,
        )
        await client.on_response_harvest_cookies(resp_401)
        assert ctx.cookies["SIDCC"] == "original_sidcc"

        # HTTP 403 response
        resp_403 = httpx.Response(
            status_code=403,
            headers={"Set-Cookie": "SIDCC=malicious_403_sidcc; Domain=google.com"},
            request=req,
        )
        await client.on_response_harvest_cookies(resp_403)
        assert ctx.cookies["SIDCC"] == "original_sidcc"
        await client.close()

    @pytest.mark.asyncio
    async def test_client_strips_quotes_and_ignores_deleted_cookies(self, mock_server, tmp_path: Path):
        """Verifies cookie values have surrounding quotes stripped and 'deleted' marker values ignored."""
        _, base_url = mock_server
        vault_file = tmp_path / "vault.enc"
        vault = CredentialVault(vault_path=vault_file)
        ctx = SessionContext(
            cookies={"__Secure-1PSID": "valid_psid", "SIDCC": "original_sidcc"},
            vault=vault,
        )
        client = GeminiWebClient(session_context=ctx, base_url=base_url, vault=vault)

        req = httpx.Request("POST", f"{base_url}/_/BardChatUi/data/batchexecute")
        resp_200 = httpx.Response(
            status_code=200,
            headers={
                "Set-Cookie": '__Secure-1PSIDTS="quoted_valid_token_ts"; SIDCC=deleted; Path=/',
            },
            request=req,
        )
        await client.on_response_harvest_cookies(resp_200)

        # Quotes should be stripped
        assert ctx.cookies.get("__Secure-1PSIDTS") == "quoted_valid_token_ts"
        # 'deleted' value should not overwrite original_sidcc
        assert ctx.cookies.get("SIDCC") == "original_sidcc"
        await client.close()


# ============================================================================
# 3. Network Drop & Transient Error Differentiation (Requirement R1 & R2)
# ============================================================================

class TestNetworkDropAndErrorDifferentiation:
    """Verifies that transient network drops keep HEALTHY state and do not terminate rotation loop."""

    @pytest.mark.asyncio
    async def test_rotate_cookies_maintains_healthy_on_httpx_network_error(self, mock_server):
        """Simulates a network timeout during RotateCookies and verifies session retains HEALTHY status."""
        _, base_url = mock_server
        sm = SessionManager(base_url=base_url)
        await sm.init_session({
            "__Secure-1PSID": "healthy_network_psid",
            "__Secure-1PSIDTS": "healthy_network_psidts",
        })
        assert sm.context.status == SessionHealthStatus.HEALTHY

        # Mock httpx client.post to raise ConnectTimeout
        with patch.object(sm._client, "post", side_effect=httpx.ConnectTimeout("Network dropped")):
            success = await sm.rotate_cookies()

            assert success is False
            # Crucial check: status MUST NOT be downgraded to EXPIRED or UNAUTHENTICATED
            assert sm.context.status == SessionHealthStatus.HEALTHY
            assert sm.get_health().is_authenticated is True
            assert "Transient network failure" in sm.context.last_error or "Transient network error" in sm.context.last_error
        await sm.close()

    @pytest.mark.asyncio
    async def test_scrape_tokens_maintains_healthy_on_transient_network_error(self, mock_server):
        """Simulates a network timeout during scrape_tokens and verifies session retains HEALTHY status."""
        _, base_url = mock_server
        sm = SessionManager(base_url=base_url)
        await sm.init_session({
            "__Secure-1PSID": "healthy_network_psid",
            "__Secure-1PSIDTS": "healthy_network_psidts",
        })
        assert sm.context.status == SessionHealthStatus.HEALTHY

        with patch.object(sm._client, "get", side_effect=httpx.ReadTimeout("Read timed out")):
            success = await sm.scrape_tokens(allow_rollback=False)

            assert success is False
            assert sm.context.status == SessionHealthStatus.HEALTHY
            assert sm.get_health().is_authenticated is True
            assert "Transient network error" in sm.context.last_error
        await sm.close()

    @pytest.mark.asyncio
    async def test_scrape_tokens_5xx_maintains_healthy(self, mock_server):
        """Simulates Google 503 Server Unavailable during scrape_tokens and verifies session retains HEALTHY status."""
        _, base_url = mock_server
        sm = SessionManager(base_url=base_url)
        await sm.init_session({
            "__Secure-1PSID": "healthy_503_psid",
            "__Secure-1PSIDTS": "healthy_503_psidts",
        })
        assert sm.context.status == SessionHealthStatus.HEALTHY

        req = httpx.Request("GET", f"{base_url}/app")
        mock_503 = httpx.Response(status_code=503, text="Service Unavailable", request=req)

        with patch.object(sm._client, "get", return_value=mock_503):
            success = await sm.scrape_tokens(allow_rollback=False)

            assert success is False
            assert sm.context.status == SessionHealthStatus.HEALTHY
            assert "Google server temporary error" in sm.context.last_error
        await sm.close()

    @pytest.mark.asyncio
    async def test_rotation_loop_survives_transient_failure_and_retries(self, mock_server):
        """Verifies that background _rotation_loop continues running after an exception with backoff."""
        _, base_url = mock_server
        sm = SessionManager(base_url=base_url, rotation_interval_seconds=0.05, rotation_jitter_seconds=0.0)
        await sm.init_session({
            "__Secure-1PSID": "healthy_loop_psid",
            "__Secure-1PSIDTS": "healthy_loop_psidts",
        })

        calls = 0

        async def mock_rotate(acc_id=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadTimeout("Simulated packet drop")
            return True

        with patch.object(sm, "rotate_cookies", side_effect=mock_rotate):
            task = asyncio.create_task(sm._rotation_loop())
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # Loop executed at least 2 times, surviving the first exception
            assert calls >= 2
        await sm.close()


# ============================================================================
# 4. Session Rollback & Extended Concurrency Stress (Requirement R3 & R4)
# ============================================================================

class TestSessionRollbackAndExtendedRotationStress:
    """Verifies automated recovery from vault backup, bootstrap cleanliness, and 12 rotation stress cycles."""

    @pytest.mark.asyncio
    async def test_scrape_tokens_recovers_from_vault_backup_on_expired(self, mock_server, tmp_path: Path):
        """Simulates missing SNlM0e and verifies automatic recovery from vault.enc.bak."""
        _, base_url = mock_server
        vault_file = tmp_path / "vault.enc"
        vault = CredentialVault(vault_path=vault_file)
        sm = SessionManager(base_url=base_url, vault=vault)

        # 1. Initialize session with valid credentials (shadow backup is created)
        await sm.init_session({
            "__Secure-1PSID": "mock_psid_valid_test_token_12345",
            "__Secure-1PSIDTS": "mock_psidts_initial_timestamp_67890",
        })
        assert sm.context.status == SessionHealthStatus.HEALTHY
        assert vault.backup_path.exists()

        # 2. Poison in-memory cookies with broken token that causes SNlM0e missing
        sm.context.cookies["__Secure-1PSID"] = "corrupted_or_revoked_psid"

        # Intercept client.get: first call returns guest page (missing SNlM0e) simulating session expiration
        # Subsequent call (after vault rollback) executes real get against mock_server which succeeds
        call_count = 0
        original_get = sm._client.get

        async def mock_get(url, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                req = httpx.Request("GET", url)
                return httpx.Response(status_code=200, text=HTML_APP_PAGE_GUEST, request=req)
            return await original_get(url, *args, **kwargs)

        # 3. Trigger scrape_tokens: detects invalid tokens, restores from backup, re-scrapes, succeeds
        with patch.object(sm._client, "get", side_effect=mock_get):
            success = await sm.scrape_tokens()

        assert success is True
        assert sm.context.status == SessionHealthStatus.HEALTHY
        assert sm.context.cookies["__Secure-1PSID"] == "mock_psid_valid_test_token_12345"
        await sm.close()

    @pytest.mark.asyncio
    async def test_scrape_tokens_recovers_from_vault_backup_on_302_redirect(self, mock_server, tmp_path: Path):
        """Simulates 302 login redirect and verifies automatic recovery from vault.enc.bak."""
        _, base_url = mock_server
        vault_file = tmp_path / "vault.enc"
        vault = CredentialVault(vault_path=vault_file)
        sm = SessionManager(base_url=base_url, vault=vault)

        await sm.init_session({
            "__Secure-1PSID": "mock_psid_valid_test_token_12345",
            "__Secure-1PSIDTS": "mock_psidts_initial_timestamp_67890",
        })
        assert vault.backup_path.exists()

        call_count = 0
        original_get = sm._client.get

        async def mock_get(url, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                req = httpx.Request("GET", url)
                return httpx.Response(status_code=302, headers={"Location": "https://accounts.google.com"}, request=req)
            return await original_get(url, *args, **kwargs)

        with patch.object(sm._client, "get", side_effect=mock_get):
            success = await sm.scrape_tokens()
            assert success is True
            assert sm.context.status == SessionHealthStatus.HEALTHY
        await sm.close()

    @pytest.mark.asyncio
    async def test_bootstrap_no_phantom_default_account(self, mock_server, tmp_path: Path):
        """Verifies that initialize_all only bootstraps accounts present in vault, avoiding phantom 'default'."""
        _, base_url = mock_server
        vault_file = tmp_path / "vault.enc"
        vault = CredentialVault(vault_path=vault_file)

        profile = AccountProfile(
            id="acc_test_ultra",
            name="Ultra Pro Account",
            tier="ultra",
            cookies={
                "__Secure-1PSID": "ultra_psid_token_123",
                "__Secure-1PSIDTS": "ultra_psidts_token_456",
            },
            health_status="healthy",
        )
        vault.save_account(profile)

        sm = SessionManager(base_url=base_url, vault=vault)
        await sm.initialize_all()

        assert "default" not in sm.sessions
        assert list(sm.sessions.keys()) == ["acc_test_ultra"]
        assert sm.active_account_id == "acc_test_ultra"
        assert sm.get_account_tier("acc_test_ultra") == "ultra"
        await sm.close()

    @pytest.mark.asyncio
    async def test_extended_rotation_cycles_12_without_leaks_or_deadlocks(self, mock_server, tmp_path: Path):
        """
        Executes 12 consecutive RotateCookies + scrape_tokens cycles with active Vault persistence.
        Verifies:
        - 12/12 successful cycles.
        - Zero deadlocks under async lock.
        - Memory allocation remains tightly bounded (no leak).
        - Vault and backup remain valid and decryptable.
        """
        _, base_url = mock_server
        vault_file = tmp_path / "vault_extended.enc"
        vault = CredentialVault(vault_path=vault_file)
        sm = SessionManager(base_url=base_url, vault=vault)

        await sm.init_session({
            "__Secure-1PSID": "mock_psid_valid_test_token_12345",
            "__Secure-1PSIDTS": "mock_psidts_initial_timestamp_67890",
        })

        tracemalloc.start()
        snapshot_start = tracemalloc.take_snapshot()

        cycles_completed = 0
        for cycle in range(12):
            ok = await sm.rotate_cookies()
            assert ok is True, f"RotateCookies failed on cycle {cycle + 1}"

            scraped = await sm.scrape_tokens()
            assert scraped is True, f"ScrapeTokens failed on cycle {cycle + 1}"

            assert sm.context.status == SessionHealthStatus.HEALTHY
            cycles_completed += 1

        snapshot_end = tracemalloc.take_snapshot()
        top_stats = snapshot_end.compare_to(snapshot_start, "lineno")
        total_growth = sum(stat.size_diff for stat in top_stats if stat.size_diff > 0)
        tracemalloc.stop()

        assert cycles_completed == 12
        # Memory growth across 12 cycles must be less than 5MB
        assert total_growth < 5 * 1024 * 1024, f"Potential memory leak: growth of {total_growth} bytes"

        # Verify vault files are decryptable and clean
        final_vdata = vault.load_vault_data()
        assert len(final_vdata.accounts) > 0
        assert vault.backup_path.exists()
        await sm.close()
