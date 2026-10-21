"""
Milestone M12: Extension Sync Bridge (Zero-Drop Eternal Session) Verification Suite.

Tests:
1. POST /admin/session with full extension payload (cookie + xsrf_token + gemini_bl + sapisid)
   - Immediate HEALTHY transition without network scrape
   - Atomic persistence to vault.enc and vault.enc.bak
2. POST /admin/session with cookie-only payload (auto-scrape fallback)
3. Stripping of FOREIGN_SERVICE_COOKIES (OSID, __Secure-OSID, LSID, __Host-1PLSID, __Host-3PLSID)
4. Rejection of empty or invalid cookie payloads (HTTP 400 with ok=False)
5. GET /admin/session status and Chrome Extension CORS preflight (chrome-extension:// origin)
6. Extension directory & default gatewayUrl (http://localhost:8000) verification
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.bridge.session import SessionHealthStatus, SessionManager
from backend.app.storage.vault import CredentialVault


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolates vault and session_manager to tmp_path so live vault.enc is never touched."""
    vault_file = tmp_path / "vault.enc"
    test_vault = CredentialVault(vault_path=vault_file)
    test_sm = SessionManager(
        base_url="http://127.0.0.1:19999",
        vault=test_vault,
        rotation_interval_seconds=3600.0,
    )
    monkeypatch.setattr(main_module, "vault", test_vault)
    monkeypatch.setattr(main_module, "session_manager", test_sm)
    monkeypatch.setattr(main_module.app.state, "vault", test_vault, raising=False)
    monkeypatch.setattr(main_module.app.state, "session_manager", test_sm, raising=False)
    yield test_vault, test_sm, tmp_path


def test_post_admin_session_full_payload_zero_scrape(isolated_env):
    """Full extension payload with xsrf_token and gemini_bl updates session immediately without scraping."""
    test_vault, test_sm, tmp_path = isolated_env

    payload = {
        "cookie": (
            "__Secure-1PSID=g.a000ValidPsidTokenValue1234567890; "
            "__Secure-1PSIDTS=sidts-ValidRotatingTicket1234567890; "
            "__Secure-1PSIDCC=AKEyXzValidSidccToken12345"
        ),
        "sapisid": "W344P6oDJS752Exo/AXFxXhuC1vuSRHU4-",
        "auth_user": "0",
        "xsrf_token": "AF1_Qp_LiveExtensionSnlM0eToken:1792496105",
        "gemini_bl": "boq_assistant-bard-web-server_20261020.08_p0",
    }

    with patch.object(test_sm, "scrape_tokens", new_callable=AsyncMock) as mock_scrape:
        with TestClient(main_module.app) as client:
            # Re-bind after lifespan in case lifespan replaced globals
            main_module.vault = test_vault
            main_module.session_manager = test_sm

            resp = client.post("/admin/session", json=payload)

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "ok"
    assert data["authenticated"] is True
    assert "gemini-4-argon" in data["models"]
    assert data["health"]["status"] == "healthy"
    assert data["health"]["has_psid"] is True
    assert data["health"]["has_psidts"] is True
    assert data["health"]["has_access_token"] is True
    assert data["health"]["build_label"] == "boq_assistant-bard-web-server_20261020.08_p0"

    # Verify scrape_tokens was bypassed because xsrf_token was supplied by the extension
    mock_scrape.assert_not_called()

    # Verify SAPISID was injected into session cookies
    active_ctx = test_sm.context
    assert active_ctx.cookies.get("SAPISID") == "W344P6oDJS752Exo/AXFxXhuC1vuSRHU4-"
    assert active_ctx.access_token == "AF1_Qp_LiveExtensionSnlM0eToken:1792496105"
    assert active_ctx.status == SessionHealthStatus.HEALTHY

    # Verify atomic persistence to both vault.enc and vault.enc.bak
    assert (tmp_path / "vault.enc").exists()
    assert (tmp_path / "vault.enc.bak").exists()


def test_post_admin_session_strips_foreign_service_cookies(isolated_env):
    """Foreign Google cookies (OSID, LSID, etc.) must be stripped on ingestion to prevent FIFE 403 errors."""
    test_vault, test_sm, _ = isolated_env

    payload = {
        "cookie": (
            "__Secure-1PSID=g.a000ValidPsidTokenValue1234567890; "
            "__Secure-1PSIDTS=sidts-ValidRotatingTicket1234567890; "
            "SAPISID=W344P6oDJS752Exo/AXFxXhuC1vuSRHU4-; "
            "OSID=ForeignGmailOsidToken; "
            "__Secure-OSID=ForeignSecureOsidToken; "
            "LSID=ForeignAccountLsidToken; "
            "__Host-1PLSID=ForeignHost1Plsid; "
            "__Host-3PLSID=ForeignHost3Plsid"
        ),
        "xsrf_token": "SNlM0e_LiveToken_999",
        "gemini_bl": "boq_assistant-bard-web-server_20261020.08_p0",
    }

    with TestClient(main_module.app) as client:
        main_module.vault = test_vault
        main_module.session_manager = test_sm
        resp = client.post("/admin/session", json=payload)

    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    stored_cookies = test_sm.context.cookies
    assert "__Secure-1PSID" in stored_cookies
    assert "SAPISID" in stored_cookies
    for forbidden in ("OSID", "__Secure-OSID", "LSID", "__Host-1PLSID", "__Host-3PLSID"):
        assert forbidden not in stored_cookies


def test_post_admin_session_cookie_only_triggers_scrape(isolated_env):
    """When xsrf_token is omitted, POST /admin/session falls back to scraping tokens."""
    test_vault, test_sm, _ = isolated_env

    async def fake_scrape(account_id=None):
        sess = test_sm.sessions[account_id or test_sm.active_account_id]
        sess.context.access_token = "Scraped_SNlM0e_Token"
        sess.context.build_label = "boq_scraped_bl"
        sess.context.status = SessionHealthStatus.HEALTHY
        return True

    payload = {
        "cookie": (
            "__Secure-1PSID=g.a000ValidPsidTokenValue1234567890; "
            "__Secure-1PSIDTS=sidts-ValidRotatingTicket1234567890"
        ),
    }

    with patch.object(test_sm, "scrape_tokens", side_effect=fake_scrape) as mock_scrape:
        with TestClient(main_module.app) as client:
            main_module.vault = test_vault
            main_module.session_manager = test_sm
            resp = client.post("/admin/session", json=payload)

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["authenticated"] is True
    mock_scrape.assert_called_once()
    assert test_sm.context.access_token == "Scraped_SNlM0e_Token"


def test_post_admin_session_rejects_invalid_or_empty_payloads(isolated_env):
    """Empty cookie string or missing __Secure-1PSID returns HTTP 400 with ok=False."""
    test_vault, test_sm, _ = isolated_env

    with TestClient(main_module.app) as client:
        main_module.vault = test_vault
        main_module.session_manager = test_sm

        # 1. Empty cookie
        r_empty = client.post("/admin/session", json={"cookie": "   "})
        assert r_empty.status_code == 400
        assert r_empty.json()["ok"] is False
        assert r_empty.json()["status"] == "error"

        # 2. Missing __Secure-1PSID
        r_no_psid = client.post("/admin/session", json={"cookie": "SAPISID=only_sapisid_value"})
        assert r_no_psid.status_code == 400
        assert r_no_psid.json()["ok"] is False
        assert "Invalid cookies" in r_no_psid.json()["error"]


def test_get_admin_session_and_chrome_extension_cors(isolated_env):
    """GET /admin/session returns status and OPTIONS /admin/session allows chrome-extension:// origin."""
    test_vault, test_sm, _ = isolated_env

    with TestClient(main_module.app) as client:
        main_module.vault = test_vault
        main_module.session_manager = test_sm

        # Preflight from Chrome extension
        ext_origin = "chrome-extension://mpklnfbmcnkddoklbdhlncddkllfjcba"
        r_cors = client.options(
            "/admin/session",
            headers={
                "Origin": ext_origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert r_cors.status_code == 200
        assert r_cors.headers.get("access-control-allow-origin") == ext_origin

        # GET /admin/session
        r_get = client.get("/admin/session")
        assert r_get.status_code == 200
        assert r_get.json()["ok"] is True


def test_extension_bundle_deployed_and_configured():
    """Verifies extensions/gemini-cookie-sync-extension exists and defaults to http://localhost:8000."""
    ext_dir = Path(__file__).resolve().parent.parent / "extensions" / "gemini-cookie-sync-extension"
    assert ext_dir.is_dir(), f"Missing extension directory: {ext_dir}"

    manifest_path = ext_dir / "manifest.json"
    common_js_path = ext_dir / "common.js"
    popup_html_path = ext_dir / "popup.html"
    setup_md_path = ext_dir / "SETUP.md"

    assert manifest_path.is_file()
    assert common_js_path.is_file()
    assert popup_html_path.is_file()
    assert setup_md_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3

    common_js = common_js_path.read_text(encoding="utf-8")
    assert 'gatewayUrl: "http://localhost:8000"' in common_js

    popup_html = popup_html_path.read_text(encoding="utf-8")
    assert 'placeholder="http://localhost:8000"' in popup_html


@pytest.mark.asyncio
async def test_initialize_all_preserves_persisted_snlm0e_when_html_scrape_omits_it(tmp_path: Path):
    """When vault.enc holds a valid snlm0e, initialize_all preserves it even if GET /app omits SNlM0e."""
    from backend.app.storage.vault import AccountProfile

    vault_file = tmp_path / "vault.enc"
    test_vault = CredentialVault(vault_path=vault_file)
    profile = AccountProfile(
        id="acc_ultra_live",
        name="Ultra Account",
        tier="ultra",
        cookies={
            "__Secure-1PSID": "g.a000ValidPsidTokenValue1234567890",
            "__Secure-1PSIDTS": "sidts-OlderTicketInVault12345",
        },
        snlm0e="AIaPT3OPxdTqjGIPDo1U_ValidPersistedSnlM0e",
        cfb2h="boq_gemini-web-uiserver_20261019.01_p0",
        health_status="healthy",
    )
    test_vault.save_account(profile)

    sm = SessionManager(
        base_url="http://127.0.0.1:19999",
        vault=test_vault,
        rotation_interval_seconds=3600.0,
    )

    async def fake_guest_html_scrape(account_id=None, allow_rollback=True):
        sess = sm.get_account_session(account_id)
        sess.context.access_token = None
        sess.context.status = SessionHealthStatus.EXPIRED
        sess.context.last_error = "Authentication token SNlM0e missing; cookies expired (HTTP 200)"
        return False

    with patch.object(sm, "scrape_tokens", side_effect=fake_guest_html_scrape):
        await sm.initialize_all()

    health = sm.get_health("acc_ultra_live")
    assert health.is_authenticated is True
    assert health.status == SessionHealthStatus.HEALTHY
    assert sm.context.access_token == "AIaPT3OPxdTqjGIPDo1U_ValidPersistedSnlM0e"
    assert sm.context.build_label == "boq_gemini-web-uiserver_20261019.01_p0"
    await sm.close()

