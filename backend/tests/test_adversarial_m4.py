"""
backend/tests/test_adversarial_m4.py
Adversarial stress test suite for M4:
1. Rolling 1h and 24h consumption metrics calculations in database.py (boundary cutoffs, multi-account isolation, global aggregation).
2. Warning status transitions (normal -> warning -> critical) when thresholds are crossed.
3. Model grouping and canonical model mapping in GET /api/limits.
4. Account switching via /api/accounts/{id}/activate and X-Gemini-Account session isolation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import time
from typing import AsyncIterator
import pytest
import pytest_asyncio
from starlette.testclient import TestClient

from backend.app.main import app
from backend.app.bridge.session import AccountSession, SessionContext, SessionHealthStatus, SessionManager
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.search import SearchEngine
from backend.app.storage.vault import AccountProfile, CredentialVault


@pytest_asyncio.fixture
async def clean_db(tmp_path: Path) -> AsyncIterator[Database]:
    db_file = tmp_path / "adversarial_test.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    await db.init_schema(schema_path)
    yield db
    await db.close()


# ==============================================================================
# SECTION 1: Rolling Window & Consumption Calculations
# ==============================================================================

@pytest.mark.asyncio
async def test_rolling_windows_boundary_conditions(clean_db: Database):
    """
    Stress-test rolling window boundaries:
    - Events inside 1h window (100s, 3000s ago)
    - Event outside 1h but inside 24h window (7200s ago)
    - Event inside 24h window (80000s ago)
    - Event outside 24h window (90000s ago)
    - Future event (offset +60s)
    """
    now = time.time()
    now_ms = int(now * 1000)

    t_recent = now_ms - (100 * 1000)
    t_inside_1h = now_ms - (3000 * 1000)
    t_outside_1h_inside_24h = now_ms - (7200 * 1000)
    t_inside_24h = now_ms - (80000 * 1000)
    t_outside_24h = now_ms - (95000 * 1000)
    t_future = now_ms + (60 * 1000)

    # 1. Recent (10 tokens)
    await clean_db.record_telemetry(
        account_id="acc_bound",
        model_id="model_bound",
        model_name="Model Bound",
        request_timestamp=t_recent,
        prompt_tokens_est=10,
        completion_tokens_est=10,
    )

    # 2. Inside 1h (20 tokens)
    await clean_db.record_telemetry(
        account_id="acc_bound",
        model_id="model_bound",
        model_name="Model Bound",
        request_timestamp=t_inside_1h,
        prompt_tokens_est=20,
        completion_tokens_est=20,
    )

    # 3. Outside 1h, inside 24h (300 tokens)
    await clean_db.record_telemetry(
        account_id="acc_bound",
        model_id="model_bound",
        model_name="Model Bound",
        request_timestamp=t_outside_1h_inside_24h,
        prompt_tokens_est=150,
        completion_tokens_est=150,
    )

    # 4. Inside 24h (2000 tokens)
    await clean_db.record_telemetry(
        account_id="acc_bound",
        model_id="model_bound",
        model_name="Model Bound",
        request_timestamp=t_inside_24h,
        prompt_tokens_est=1000,
        completion_tokens_est=1000,
    )

    # 5. Outside 24h (10000 tokens)
    await clean_db.record_telemetry(
        account_id="acc_bound",
        model_id="model_bound",
        model_name="Model Bound",
        request_timestamp=t_outside_24h,
        prompt_tokens_est=5000,
        completion_tokens_est=5000,
    )

    # 6. Future record (5 tokens)
    await clean_db.record_telemetry(
        account_id="acc_bound",
        model_id="model_bound",
        model_name="Model Bound",
        request_timestamp=t_future,
        prompt_tokens_est=2,
        completion_tokens_est=3,
    )

    # Verify 1h window:
    # Includes: t_recent (20) + t_inside_1h (40) + t_future (5) = 3 reqs, 65 tokens.
    usage_1h = await clean_db.get_rolling_usage(account_id="acc_bound", window_seconds=3600)
    assert usage_1h["total_requests"] == 3
    assert usage_1h["total_tokens"] == 65

    # Verify 24h window:
    # Includes: t_recent (20) + t_inside_1h (40) + t_outside_1h_inside_24h (300) + t_inside_24h (2000) + t_future (5)
    # Total = 5 reqs, 2365 tokens.
    # Excludes: t_outside_24h (10000 tokens)
    usage_24h = await clean_db.get_rolling_usage(account_id="acc_bound", window_seconds=86400)
    assert usage_24h["total_requests"] == 5
    assert usage_24h["total_tokens"] == 2365


@pytest.mark.asyncio
async def test_multi_account_telemetry_isolation(clean_db: Database):
    """Verifies that telemetry queries for Account A never leak Account B's records."""
    now_ms = int(time.time() * 1000)

    for i in range(10):
        await clean_db.record_telemetry(
            account_id="acc_alpha",
            model_id="model_alpha",
            model_name="Alpha Model",
            request_timestamp=now_ms - (i * 1000),
            prompt_tokens_est=100,
            completion_tokens_est=100,
            feature_mode="task" if i % 2 == 0 else "chat",
        )

    for i in range(5):
        await clean_db.record_telemetry(
            account_id="acc_beta",
            model_id="model_beta",
            model_name="Beta Model",
            request_timestamp=now_ms - (i * 1000),
            prompt_tokens_est=500,
            completion_tokens_est=500,
            status_code=1037 if i == 0 else 200,
        )

    alpha_usage = await clean_db.get_rolling_usage(account_id="acc_alpha")
    beta_usage = await clean_db.get_rolling_usage(account_id="acc_beta")
    global_usage = await clean_db.get_rolling_usage(account_id=None)

    assert alpha_usage["total_requests"] == 10
    assert alpha_usage["total_tokens"] == 2000
    assert alpha_usage["task_mode_calls"] == 5
    assert alpha_usage["quota_error_count"] == 0

    assert beta_usage["total_requests"] == 5
    assert beta_usage["total_tokens"] == 5000
    assert beta_usage["quota_error_count"] == 1
    assert beta_usage["task_mode_calls"] == 0

    assert global_usage["total_requests"] == 15
    assert global_usage["total_tokens"] == 7000
    assert global_usage["quota_error_count"] == 1
    assert global_usage["task_mode_calls"] == 5


@pytest.mark.asyncio
async def test_empty_and_zero_telemetry_handling(clean_db: Database):
    """Verifies empty database queries return zeroed metrics without NULL errors."""
    res_none = await clean_db.get_rolling_usage(account_id="non_existent")
    assert res_none["total_requests"] == 0
    assert res_none["total_prompt_tokens"] == 0
    assert res_none["total_tokens"] == 0
    assert res_none["task_mode_calls"] == 0
    assert res_none["quota_error_count"] == 0

    per_model = await clean_db.get_per_model_usage(account_id="non_existent")
    assert per_model == {}


# ==============================================================================
# SECTION 2: Adversarial Warning Status Transitions (normal -> warning -> critical)
# ==============================================================================

@pytest.mark.asyncio
async def test_warning_status_transitions_and_critical_omission(tmp_path: Path):
    """
    Empirically verifies warning status transitions across threshold boundary values:
    - Usage < 80%: 'normal'
    - 80% <= Usage < 100%: 'warning'
    - Usage >= 100%: Worker claims 'critical', but limits.py line 186 lacks 'critical' transition!
    """
    db_file = tmp_path / "test_warning_transitions.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    await db.init_schema(schema_path)

    vault_file = tmp_path / "mock_vault_warnings.json"
    vault = CredentialVault(vault_path=str(vault_file))
    vault._dpapi_available = False

    prof = AccountProfile(
        id="acc_warn",
        name="Warn Account",
        tier="pro",
        cookies={"__Secure-1PSID": "test_cookie"},
    )
    vault.save_account(prof)
    vault.set_active_account("acc_warn")

    await db.set_account_limits_config(
        account_id="acc_warn",
        daily_request_threshold=100,
        daily_token_threshold=100000,
        hourly_request_threshold=20,
    )

    sm = SessionManager(vault=vault)
    await sm.initialize_all()

    import backend.app.main as main_mod
    main_mod.db = db
    main_mod.session_manager = sm
    main_mod.vault = vault

    app.state.db = db
    app.state.session_manager = sm
    app.state.vault = vault

    client = TestClient(app, base_url="http://testserver")

    # CASE A: 0 requests (0%) -> 'normal'
    resp = client.get("/api/limits?account_id=acc_warn")
    assert resp.status_code == 200
    data = resp.json()["accounts"][0]
    assert data["thresholds"]["warning_status"] == "normal"

    # CASE B: 79 requests (79%) -> 'normal'
    now_ms = int(time.time() * 1000)
    for _ in range(79):
        await db.record_telemetry(
            account_id="acc_warn",
            model_id="m1",
            model_name="M1",
            request_timestamp=now_ms,
            prompt_tokens_est=10,
        )
    resp = client.get("/api/limits?account_id=acc_warn")
    data = resp.json()["accounts"][0]
    assert data["thresholds"]["daily_request_percentage"] == 79.0
    assert data["thresholds"]["warning_status"] == "normal"

    # CASE C: 80 requests (80%) -> 'warning'
    await db.record_telemetry(
        account_id="acc_warn",
        model_id="m1",
        model_name="M1",
        request_timestamp=now_ms,
        prompt_tokens_est=10,
    )
    resp = client.get("/api/limits?account_id=acc_warn")
    data = resp.json()["accounts"][0]
    assert data["thresholds"]["daily_request_percentage"] == 80.0
    assert data["thresholds"]["warning_status"] == "warning"

    # CASE D: 100 requests (100%) -> Should be 'critical' per specification,
    # but backend/app/api/limits.py line 186 returns 'warning'
    for _ in range(20):
        await db.record_telemetry(
            account_id="acc_warn",
            model_id="m1",
            model_name="M1",
            request_timestamp=now_ms,
            prompt_tokens_est=10,
        )
    resp = client.get("/api/limits?account_id=acc_warn")
    data = resp.json()["accounts"][0]
    assert data["thresholds"]["daily_request_percentage"] == 100.0
    
    actual_status_at_100 = data["thresholds"]["warning_status"]
    assert actual_status_at_100 == "critical", "Limits router should return critical status at >= 100%"
    assert data["thresholds"]["hourly_request_threshold"] == 20

    await db.close()


# ==============================================================================
# SECTION 3: Per-Model Telemetry Key De-duplication Verification
# ==============================================================================

@pytest.mark.asyncio
async def test_per_model_telemetry_key_duplication_anomaly(tmp_path: Path):
    """
    Verifies how GET /api/limits formats per_model_24h when requests use model_id as model_name.
    Empirical observation: If model_name in DB matches model_id ('95a4c0abcba24364'),
    it appears both under canonical display name '4 Argon' AND as a duplicate entry '95a4c0abcba24364'.
    """
    db_file = tmp_path / "test_model_keys.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    await db.init_schema(schema_path)

    vault_file = tmp_path / "mock_vault_models.json"
    vault = CredentialVault(vault_path=str(vault_file))
    vault._dpapi_available = False

    prof = AccountProfile(id="acc_mod", name="Model Acc", tier="pro", cookies={"__Secure-1PSID": "c"})
    vault.save_account(prof)
    vault.set_active_account("acc_mod")

    sm = SessionManager(vault=vault)
    await sm.initialize_all()

    now_ms = int(time.time() * 1000)
    # Insert telemetry where model_name equals model_id
    await db.record_telemetry(
        account_id="acc_mod",
        model_id="95a4c0abcba24364",
        model_name="95a4c0abcba24364",
        request_timestamp=now_ms,
        prompt_tokens_est=100,
    )

    import backend.app.main as main_mod
    main_mod.db = db
    main_mod.session_manager = sm
    main_mod.vault = vault
    app.state.db = db
    app.state.session_manager = sm
    app.state.vault = vault

    client = TestClient(app, base_url="http://testserver")
    resp = client.get("/api/limits?account_id=acc_mod")
    assert resp.status_code == 200
    acc_data = resp.json()["accounts"][0]

    per_model_keys = list(acc_data["per_model_24h"].keys())
    # Canonical '4 Argon' is present, and raw ID '95a4c0abcba24364' is deduplicated
    assert "4 Argon" in per_model_keys
    assert "95a4c0abcba24364" not in per_model_keys
    assert acc_data["per_model_24h"]["4 Argon"]["requests"] == 1

    await db.close()


# ==============================================================================
# SECTION 4: Session Isolation & Account Switching Verification
# ==============================================================================

@pytest.mark.asyncio
async def test_account_activation_and_session_isolation(tmp_path: Path):
    """
    Verifies that:
    1. Switching accounts via POST /api/accounts/{id}/activate switches primary routing.
    2. Header X-Gemini-Account strictly overrides active account per-request.
    3. SessionContext and cookies used belong strictly to the targeted account.
    """
    db_file = tmp_path / "test_iso.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    await db.init_schema(schema_path)

    vault_file = tmp_path / "test_iso_vault.json"
    vault = CredentialVault(vault_path=str(vault_file))
    vault._dpapi_available = False

    prof_1 = AccountProfile(
        id="acc_user_1",
        name="User One (Free)",
        tier="free",
        cookies={"__Secure-1PSID": "cookie_user_1", "__Secure-1PSIDTS": "ts_1"},
    )
    prof_2 = AccountProfile(
        id="acc_user_2",
        name="User Two (Ultra)",
        tier="ultra",
        cookies={"__Secure-1PSID": "cookie_user_2", "__Secure-1PSIDTS": "ts_2"},
    )
    vault.save_account(prof_1)
    vault.save_account(prof_2)
    vault.set_active_account("acc_user_1")

    sm = SessionManager(vault=vault)
    await sm.initialize_all()

    # Pre-populate contexts with distinct tokens
    sm.sessions["acc_user_1"].context.access_token = "token_user_1"
    sm.sessions["acc_user_1"].context.build_label = "build_user_1"
    sm.sessions["acc_user_2"].context.access_token = "token_user_2"
    sm.sessions["acc_user_2"].context.build_label = "build_user_2"

    import backend.app.main as main_mod
    main_mod.db = db
    main_mod.session_manager = sm
    main_mod.vault = vault

    app.state.db = db
    app.state.session_manager = sm
    app.state.vault = vault

    client = TestClient(app, base_url="http://testserver")

    # 1. Verify initial active account is acc_user_1
    status_resp = client.get("/api/accounts")
    assert status_resp.status_code == 200
    assert status_resp.json()["active_account_id"] == "acc_user_1"

    # Context without header resolves acc_user_1
    ctx_default = sm.get_session_context(None)
    assert ctx_default.access_token == "token_user_1"
    assert ctx_default.cookies["__Secure-1PSID"] == "cookie_user_1"

    # 2. Activate acc_user_2 via POST /api/accounts/acc_user_2/activate
    act_resp = client.post("/api/accounts/acc_user_2/activate")
    assert act_resp.status_code == 200
    assert act_resp.json()["active_account_id"] == "acc_user_2"
    assert sm.active_account_id == "acc_user_2"

    # Now default context resolves acc_user_2
    ctx_switched = sm.get_session_context(None)
    assert ctx_switched.access_token == "token_user_2"
    assert ctx_switched.cookies["__Secure-1PSID"] == "cookie_user_2"

    # 3. Test X-Gemini-Account header override: request with X-Gemini-Account: acc_user_1
    ctx_header_override = sm.get_session_context("acc_user_1")
    assert ctx_header_override.access_token == "token_user_1"
    assert ctx_header_override.cookies["__Secure-1PSID"] == "cookie_user_1"

    # 4. Unknown account ID in header safely falls back to active account without crash
    ctx_unknown = sm.get_session_context("non_existent_account_xyz")
    assert ctx_unknown.access_token == "token_user_2"

    await db.close()
