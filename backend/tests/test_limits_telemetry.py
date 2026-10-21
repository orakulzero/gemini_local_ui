"""
backend/tests/test_limits_telemetry.py
Automated test suite for SQLite usage telemetry, rolling 1h/24h aggregations,
per-model quota tracking, and the /api/limits REST API.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import time
from typing import AsyncIterator
import pytest
import pytest_asyncio
from starlette.testclient import TestClient

from backend.app.bridge.session import SessionManager
from backend.app.main import app
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.search import SearchEngine
from backend.app.storage.vault import AccountProfile, CredentialVault, VaultData


@pytest_asyncio.fixture
async def test_db(tmp_path: Path) -> AsyncIterator[Database]:
    """Provides a fresh temporary SQLite database initialized with schema.sql."""
    db_file = tmp_path / "test_telemetry.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    await db.init_schema(schema_path)
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_telemetry_recording_and_rolling_aggregations(test_db: Database) -> None:
    """Verifies inserting telemetry records and querying rolling 1h/24h metrics."""
    now_ms = int(time.time() * 1000)
    account_id = "acc_test_1"

    # Insert events within 1h (e.g. 100s and 50s ago)
    await test_db.record_telemetry(
        account_id=account_id,
        model_id="95a4c0abcba24364",
        model_name="4 Argon",
        feature_mode="chat",
        reasoning_level=1,
        request_timestamp=now_ms - 100_000,
        prompt_tokens_est=100,
        completion_tokens_est=200,
        thinking_tokens_est=50,
    )
    await test_db.record_telemetry(
        account_id=account_id,
        model_id="95a4c0abcba24364",
        model_name="4 Argon",
        feature_mode="chat",
        reasoning_level=1,
        request_timestamp=now_ms - 50_000,
        prompt_tokens_est=150,
        completion_tokens_est=300,
        thinking_tokens_est=100,
    )
    # Insert event at 2 hours ago (in 24h, but not 1h)
    await test_db.record_telemetry(
        account_id=account_id,
        model_id="e6fa609c3fa255c0",
        model_name="3.1 Pro",
        feature_mode="task",
        reasoning_level=2,
        request_timestamp=now_ms - 7_200_000,
        prompt_tokens_est=400,
        completion_tokens_est=600,
        thinking_tokens_est=200,
    )
    # Insert event at 30 hours ago (outside 24h)
    await test_db.record_telemetry(
        account_id=account_id,
        model_id="56fdd199312815e2",
        model_name="3.8 Flash",
        feature_mode="chat",
        reasoning_level=1,
        request_timestamp=now_ms - 108_000_000,
        prompt_tokens_est=500,
        completion_tokens_est=500,
        thinking_tokens_est=0,
    )

    # 1. Rolling 1h
    usage_1h = await test_db.get_rolling_usage(account_id, window_seconds=3600)
    assert usage_1h["total_requests"] == 2
    assert usage_1h["total_prompt_tokens"] == 250
    assert usage_1h["total_completion_tokens"] == 500
    assert usage_1h["total_thinking_tokens"] == 150
    assert usage_1h["total_tokens"] == 900
    assert usage_1h["task_mode_calls"] == 0

    # 2. Rolling 24h
    usage_24h = await test_db.get_rolling_usage(account_id, window_seconds=86400)
    assert usage_24h["total_requests"] == 3
    assert usage_24h["total_prompt_tokens"] == 650
    assert usage_24h["total_completion_tokens"] == 1100
    assert usage_24h["total_thinking_tokens"] == 350
    assert usage_24h["total_tokens"] == 2100
    assert usage_24h["task_mode_calls"] == 1

    # 3. Per-model breakdown
    per_model = await test_db.get_per_model_usage(account_id, window_seconds=86400)
    assert "4 Argon" in per_model
    assert per_model["4 Argon"]["requests"] == 2
    assert per_model["4 Argon"]["tokens_est"] == 900

    assert "3.1 Pro" in per_model
    assert per_model["3.1 Pro"]["requests"] == 1
    assert per_model["3.1 Pro"]["tokens_est"] == 1200
    assert "3.8 Flash" not in per_model  # was > 24h ago


@pytest.mark.asyncio
async def test_failover_telemetry_tracking(test_db: Database) -> None:
    """Verifies logging and retrieving failover events."""
    now_ms = int(time.time() * 1000)

    await test_db.record_telemetry(
        account_id="acc_backup",
        model_id="95a4c0abcba24364",
        model_name="4 Argon",
        feature_mode="chat",
        request_timestamp=now_ms - 10_000,
        status_code=200,
        error_code="1037",
        was_failover=1,
        failover_from_account="acc_primary",
    )

    recent = await test_db.get_recent_failovers(limit=10)
    assert len(recent) == 1
    assert recent[0]["account_id"] == "acc_backup"
    assert recent[0]["failover_from_account"] == "acc_primary"
    assert recent[0]["model_name"] == "4 Argon"
    assert recent[0]["error_code"] == "1037"


@pytest.mark.asyncio
async def test_account_limits_config_persistence(test_db: Database) -> None:
    """Verifies default threshold loading and custom threshold updating."""
    # Check default
    cfg = await test_db.get_account_limits_config("acc_new")
    assert cfg["daily_request_threshold"] == 250
    assert cfg["daily_token_threshold"] == 500_000

    # Update config
    await test_db.set_account_limits_config(
        account_id="acc_new",
        daily_request_threshold=750,
        daily_token_threshold=3_500_000,
        hourly_request_threshold=80,
    )

    updated = await test_db.get_account_limits_config("acc_new")
    assert updated["daily_request_threshold"] == 750
    assert updated["daily_token_threshold"] == 3_500_000
    assert updated["hourly_request_threshold"] == 80


@pytest.fixture
def limits_api_client(tmp_path: Path):
    """Creates isolated TestClient with fresh Database, CredentialVault V2, and SessionManager."""
    db_file = tmp_path / "test_limits_api.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    asyncio.run(db.init_schema(schema_path))

    vault_file = tmp_path / "test_vault.enc"
    vault = CredentialVault(vault_file)
    vault_data = VaultData(
        version=2,
        active_account_id="acc_main",
        accounts={
            "acc_main": AccountProfile(
                id="acc_main",
                name="Main Pro",
                email="main@test.com",
                cookies={"__Secure-1PSID": "psid_main", "__Secure-1PSIDTS": "psidts_main"},
                tier="pro",
                priority=1,
            ),
            "acc_backup": AccountProfile(
                id="acc_backup",
                name="Backup Ultra",
                email="backup@test.com",
                cookies={"__Secure-1PSID": "psid_backup", "__Secure-1PSIDTS": "psidts_backup"},
                tier="ultra",
                priority=2,
            ),
        },
    )
    vault.store_vault_data(vault_data)

    repo = ConversationRepository(db)
    search_eng = SearchEngine(db)
    sm = SessionManager(vault=vault)
    asyncio.run(sm.initialize_all())

    # Pre-populate some telemetry in db
    now_ms = int(time.time() * 1000)
    asyncio.run(
        db.record_telemetry(
            account_id="acc_main",
            model_id="95a4c0abcba24364",
            model_name="4 Argon",
            feature_mode="chat",
            request_timestamp=now_ms - 200_000,
            prompt_tokens_est=300,
            completion_tokens_est=600,
            thinking_tokens_est=100,
        )
    )

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
    yield client

    asyncio.run(db.close())


def test_get_limits_endpoint(limits_api_client: TestClient) -> None:
    """Verifies GET /api/limits returns aggregated telemetry and per-account data."""
    resp = limits_api_client.get("/api/limits")
    assert resp.status_code == 200
    data = resp.json()

    assert "global_summary_24h" in data
    assert data["global_summary_24h"]["total_requests"] >= 1
    assert data["global_summary_24h"]["total_tokens_est"] >= 1000

    assert "accounts" in data
    assert len(data["accounts"]) == 2

    main_acc = next((a for a in data["accounts"] if a["account_id"] == "acc_main"), None)
    assert main_acc is not None
    assert main_acc["tier"] == "pro"
    assert main_acc["rolling_24h"]["requests"] >= 1
    assert main_acc["rolling_24h"]["total_tokens_est"] >= 1000
    assert "4 Argon" in main_acc["per_model_24h"]
    assert main_acc["thresholds"]["daily_request_threshold"] == 250
    assert main_acc["thresholds"]["warning_status"] == "normal"


def test_update_thresholds_endpoint(limits_api_client: TestClient) -> None:
    """Verifies POST /api/limits/{account_id}/thresholds updates limits configuration."""
    update_payload = {
        "daily_request_threshold": 100,
        "daily_token_threshold": 500000,
        "hourly_request_threshold": 25,
    }
    resp = limits_api_client.post("/api/limits/acc_main/thresholds", json=update_payload)
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["success"] is True
    assert res_data["thresholds"]["daily_request_threshold"] == 100

    # Verify reflected in GET /api/limits
    get_resp = limits_api_client.get("/api/limits?account_id=acc_main")
    assert get_resp.status_code == 200
    data = get_resp.json()
    main_acc = next((a for a in data["accounts"] if a["account_id"] == "acc_main"), None)
    assert main_acc is not None
    assert main_acc["thresholds"]["daily_request_threshold"] == 100
    assert main_acc["thresholds"]["daily_token_threshold"] == 500000
