"""
backend/tests/test_accounts_and_failover.py
Automated test suite for Multi-Account isolation, DPAPI Vault V1->V2 migration,
X-Gemini-Account routing, and Automatic Quota Failover Engine (Errors 1037/1061).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time
from typing import Any, Dict, List
import pytest
from starlette.testclient import TestClient

from backend.app.bridge.session import SessionManager
from backend.app.main import app
from backend.app.mock.mock_server import MockGeminiServer
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.search import SearchEngine
from backend.app.storage.vault import AccountProfile, CredentialVault, FailoverConfig, VaultData

MOCK_PORT = 8999
MOCK_HOST = "127.0.0.1"
MOCK_BASE_URL = f"http://{MOCK_HOST}:{MOCK_PORT}"


@pytest.fixture(scope="module")
def mock_server():
    """Starts deterministic mock Gemini web server in background thread."""
    server = MockGeminiServer(host=MOCK_HOST, port=MOCK_PORT)
    server.start_background()
    yield server
    server.stop_background()


@pytest.fixture(autouse=True)
def reset_mock(mock_server: MockGeminiServer):
    """Resets mock faults and recorded requests between tests."""
    mock_server.fault.reset()


def parse_sse_events(response_text: str) -> List[Dict[str, Any]]:
    """Extracts JSON event objects from SSE stream."""
    events: List[Dict[str, Any]] = []
    for line in response_text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                try:
                    events.append(json.loads(payload))
                except json.JSONDecodeError:
                    pass
    return events


# ============================================================================
# 1. Vault V1 -> V2 Migration & Storage Isolation
# ============================================================================

def test_vault_v1_to_v2_seamless_migration(tmp_path: Path):
    """Verifies that legacy V1 flat credential vault files seamlessly migrate to V2."""
    vault_file = tmp_path / "legacy_v1_vault.enc"
    vault = CredentialVault(vault_file)

    # Simulate legacy V1 storage: flat dict encrypted via store_credentials
    legacy_cookies = {
        "__Secure-1PSID": "v1_legacy_psid_abc123",
        "__Secure-1PSIDTS": "v1_legacy_psidts_xyz789",
        "SNlM0e": "v1_snlm0e_token",
    }
    vault.store_credentials(legacy_cookies)

    # Read back through V2 interface
    vdata = vault.load_vault_data()
    assert vdata.version == 2
    assert vdata.active_account_id == "default"
    assert "default" in vdata.accounts

    default_acc = vdata.accounts["default"]
    assert default_acc.name == "Default Account"
    assert default_acc.cookies["__Secure-1PSID"] == "v1_legacy_psid_abc123"
    assert default_acc.cookies["__Secure-1PSIDTS"] == "v1_legacy_psidts_xyz789"
    assert default_acc.tier == "free"

    # Verify backwards-compatible load_credentials returns the active account's cookies
    loaded_compat = vault.load_credentials()
    assert loaded_compat["__Secure-1PSID"] == "v1_legacy_psid_abc123"


# ============================================================================
# 2. Accounts CRUD and Public Secret Redaction
# ============================================================================

@pytest.fixture
def multi_account_client(tmp_path: Path, mock_server: MockGeminiServer):
    """Provides isolated TestClient configured with fresh Database and multi-account SessionManager."""
    db_file = tmp_path / "test_accounts.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    asyncio.run(db.init_schema(schema_path))

    vault_file = tmp_path / "test_multi_vault.enc"
    vault = CredentialVault(vault_file)
    vdata = VaultData(
        version=2,
        active_account_id="acc_primary",
        failover_config=FailoverConfig(
            enabled=True,
            strategy="priority",
            cooldown_duration_seconds=300,
            fallback_chain=["acc_primary", "acc_backup"],
        ),
        accounts={
            "acc_primary": AccountProfile(
                id="acc_primary",
                name="Primary Account",
                email="primary@example.com",
                cookies={
                    "__Secure-1PSID": "mock_psid_primary_token",
                    "__Secure-1PSIDTS": "mock_psidts_primary_token",
                },
                tier="pro",
                priority=1,
            ),
            "acc_backup": AccountProfile(
                id="acc_backup",
                name="Backup Ultra Account",
                email="backup@example.com",
                cookies={
                    "__Secure-1PSID": "mock_psid_backup_token",
                    "__Secure-1PSIDTS": "mock_psidts_backup_token",
                },
                tier="ultra",
                priority=2,
            ),
        },
    )
    vault.store_vault_data(vdata)

    repo = ConversationRepository(db)
    search_eng = SearchEngine(db)
    sm = SessionManager(base_url=MOCK_BASE_URL, vault=vault)
    asyncio.run(sm.initialize_all())

    import backend.app.main as main_mod
    main_mod.db = db
    main_mod.repository = repo
    main_mod.search_engine = search_eng
    main_mod.session_manager = sm
    main_mod.vault = vault

    app.state.db = db
    app.state.database = db
    app.state.repository = repo
    app.state.search_engine = search_eng
    app.state.session_manager = sm
    app.state.vault = vault

    client = TestClient(app, base_url="http://testserver")
    yield client, sm, vault, db

    asyncio.run(db.close())


def test_accounts_crud_and_secret_redaction(multi_account_client):
    """Verifies listing, creating, activating, and deleting accounts with secret sanitization."""
    client, sm, vault, _ = multi_account_client

    # 1. GET /api/accounts
    res = client.get("/api/accounts")
    assert res.status_code == 200
    data = res.json()
    assert data["active_account_id"] == "acc_primary"
    assert len(data["accounts"]) == 2

    # Verify secret redaction: no raw cookie values in public list
    for acc in data["accounts"]:
        assert "cookies" not in acc
        assert "mock_psid" not in json.dumps(acc)
        assert acc["has_psid"] is True
        assert acc["has_psidts"] is True

    # 2. POST /api/accounts (create new account)
    create_payload = {
        "name": "Tertiary Work",
        "cookies": "__Secure-1PSID=mock_psid_tertiary; __Secure-1PSIDTS=mock_psidts_tertiary",
        "tier_override": "pro",
        "priority": 3,
    }
    res_create = client.post("/api/accounts", json=create_payload)
    assert res_create.status_code == 201
    created_acc = res_create.json()["account"]
    assert created_acc["name"] == "Tertiary Work"
    assert created_acc["tier"] == "pro"
    assert created_acc["priority"] == 3
    assert created_acc["has_psid"] is True
    # Ensure secrets not leaked in create response
    assert "cookies" not in created_acc
    assert "mock_psid_tertiary" not in json.dumps(created_acc)

    new_id = created_acc["id"]

    # 3. POST /api/accounts/{id}/activate
    res_act = client.post(f"/api/accounts/{new_id}/activate")
    assert res_act.status_code == 200
    assert res_act.json()["active_account_id"] == new_id
    assert sm.active_account_id == new_id

    # 4. POST /api/accounts/failover-config
    res_cfg = client.post(
        "/api/accounts/failover-config",
        json={"enabled": True, "cooldown_duration_seconds": 600},
    )
    assert res_cfg.status_code == 200
    assert res_cfg.json()["config"]["cooldown_duration_seconds"] == 600

    # 5. Switch back to acc_primary before deleting new account
    client.post("/api/accounts/acc_primary/activate")

    # 6. DELETE /api/accounts/{id}
    res_del = client.delete(f"/api/accounts/{new_id}")
    assert res_del.status_code == 200
    assert res_del.json()["success"] is True

    # Verify deleted
    res_list = client.get("/api/accounts")
    ids = [a["id"] for a in res_list.json()["accounts"]]
    assert new_id not in ids


# ============================================================================
# 3. Request Routing via X-Gemini-Account Header
# ============================================================================

def test_x_gemini_account_header_routing(multi_account_client):
    """Verifies that passing X-Gemini-Account routes requests to the specified account session."""
    client, sm, vault, _ = multi_account_client

    # Verify session manager has both account sessions initialized
    sess_primary = sm.get_account_session("acc_primary")
    sess_backup = sm.get_account_session("acc_backup")
    assert sess_primary is not None
    assert sess_backup is not None
    assert sess_primary.profile.id == "acc_primary"
    assert sess_backup.profile.id == "acc_backup"

    # Make request explicitly routing to backup account
    res = client.post(
        "/api/chat/generate",
        json={"prompt": "Hello from explicit backup account"},
        headers={"X-Gemini-Account": "acc_backup"},
    )
    assert res.status_code == 200
    events = parse_sse_events(res.text)
    types = [e["type"] for e in events]
    assert "start" in types
    assert "done" in types


# ============================================================================
# 4. Automatic Quota Failover Engine (Errors 1037 & 1061)
# ============================================================================

def test_auto_quota_failover_on_error_1037(multi_account_client):
    """
    Verifies that encountering Error 1037 (RESOURCE_EXHAUSTED) on high-compute model (4 Argon):
    1. Catches the error on primary account.
    2. Emits SSE line with type: 'failover'.
    3. Places primary account in cooldown.
    4. Automatically replays request to backup account (acc_backup).
    5. Completes response successfully and records failover telemetry.
    """
    client, sm, vault, db = multi_account_client

    # Ensure acc_primary is active
    sm.activate_account("acc_primary")

    payload = {
        "prompt": "Synthesize research paper [TRIGGER_QUOTA_ERROR]",
        "model": "4 Argon",
        "feature_mode": "chat",
    }
    res = client.post("/api/chat/generate", json=payload)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    events = parse_sse_events(res.text)
    event_types = [e["type"] for e in events]

    # Verify failover SSE event was emitted
    assert "failover" in event_types, f"Expected failover event in stream: {event_types}"
    failover_evt = next(e for e in events if e["type"] == "failover")
    assert failover_evt.get("from_id") == "acc_primary" or failover_evt.get("from") in ("acc_primary", "Primary Account")
    assert failover_evt.get("to_id") == "acc_backup" or failover_evt.get("to") in ("acc_backup", "Backup Ultra Account")
    assert failover_evt.get("error_code") == 1037

    # Verify response completed on backup account
    assert "text" in event_types
    assert "done" in event_types
    done_evt = next(e for e in events if e["type"] == "done")
    metadata = done_evt.get("metadata", {})
    assert metadata.get("was_failover") is True
    assert metadata.get("failover_from_account") == "acc_primary"

    # Verify primary account cooldown state
    assert sm.is_in_cooldown("acc_primary", model_id="4 Argon") is True
    primary_sess = sm.get_account_session("acc_primary")
    assert (primary_sess.cooldown_models.get("4 Argon", 0) > time.time()) or (
        primary_sess.cooldown_until and primary_sess.cooldown_until > time.time()
    )

    # Verify SQLite failover telemetry recorded
    recent_failovers = asyncio.run(db.get_recent_failovers(limit=5))
    assert len(recent_failovers) >= 1
    assert recent_failovers[0]["failover_from_account"] == "acc_primary"
    assert recent_failovers[0]["model_name"] == "4 Argon"


def test_auto_quota_failover_on_error_1061(multi_account_client):
    """Verifies that encountering Error 1061 triggers the failover workflow."""
    client, sm, vault, db = multi_account_client

    # Clear previous cooldown on primary account
    primary_sess = sm.get_account_session("acc_primary")
    primary_sess.cooldown_until = None
    primary_sess.cooldown_models.clear()

    payload = {
        "prompt": "Deep analysis task [TRIGGER_QUOTA_1061]",
        "model": "3.1 Pro",
        "feature_mode": "task",
    }
    res = client.post("/api/chat/generate", json=payload)
    assert res.status_code == 200

    events = parse_sse_events(res.text)
    event_types = [e["type"] for e in events]

    assert "failover" in event_types
    failover_evt = next(e for e in events if e["type"] == "failover")
    assert failover_evt.get("from_id") == "acc_primary" or failover_evt.get("from") in ("acc_primary", "Primary Account")
    assert failover_evt.get("to_id") == "acc_backup" or failover_evt.get("to") in ("acc_backup", "Backup Ultra Account")
    assert failover_evt.get("error_code") == 1061

    assert "done" in event_types


def test_failover_disabled_preserves_error(multi_account_client):
    """Verifies that when failover is disabled, error 1037 is emitted directly without failover."""
    client, sm, vault, db = multi_account_client

    # Disable failover via API
    res_cfg = client.post("/api/accounts/failover-config", json={"enabled": False})
    assert res_cfg.status_code == 200

    primary_sess = sm.get_account_session("acc_primary")
    primary_sess.cooldown_until = None
    primary_sess.cooldown_models.clear()

    payload = {
        "prompt": "Complex calculation [TRIGGER_QUOTA_ERROR]",
        "model": "4 Argon",
    }
    res = client.post("/api/chat/generate", json=payload)
    assert res.status_code == 200

    events = parse_sse_events(res.text)
    event_types = [e["type"] for e in events]

    # Must NOT have failover event
    assert "failover" not in event_types
    # Must have error event
    assert "error" in event_types
    err_evt = next(e for e in events if e["type"] == "error")
    assert "1037" in str(err_evt.get("error", "")) or "limit" in str(err_evt.get("error", "")).lower()
