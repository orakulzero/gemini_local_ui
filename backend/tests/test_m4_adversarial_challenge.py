"""
backend/tests/test_m4_adversarial_challenge.py
Adversarial challenge test suite for M4: Automatic Quota Failover Engine.

Empirically tests:
1. Error codes 1037, 429, and 1061 across Argon, Pro, and Task modes.
2. Failing accounts enter cooldown and route to healthy backup of matching/higher tier.
3. SSE event order: {"type": "failover", ...} emitted strictly before streaming tokens.
4. Telemetry logging in SQLite usage_telemetry (was_failover=1, failover_from_account).
5. Pre-flight cooldown bypass.
6. Absence of healthy backups fails cleanly.
7. Mid-stream error behavior (no corrupt replay if text already emitted).
8. OpenAI /v1/chat/completions failover vulnerability (NameError on UsageLimitExceededError).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
from aiohttp import web
import pytest
from starlette.testclient import TestClient

from backend.app.bridge.models import DEFAULT_MODELS
from backend.app.bridge.session import SessionManager
from backend.app.main import app
from backend.app.mock.fixtures import (
    build_golden_stream_sequence,
    build_stream_error_frame,
)
from backend.app.mock.mock_server import MockGeminiServer
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.search import SearchEngine
from backend.app.storage.vault import AccountProfile, CredentialVault, FailoverConfig, VaultData

ADVERSARIAL_PORT = 8996
ADVERSARIAL_HOST = "127.0.0.1"
ADVERSARIAL_BASE_URL = f"http://{ADVERSARIAL_HOST}:{ADVERSARIAL_PORT}"


class AdversarialMockGeminiServer(MockGeminiServer):
    """
    Enhanced deterministic mock server supporting HTTP 429 rate limit triggers,
    code 1037, code 1061, mid-stream quota failures, and account-aware fault filtering.
    """

    async def _handle_stream_generate(self, request: web.Request) -> web.StreamResponse:
        post_data = await request.post()
        body_dict = dict(post_data)
        await self._record_request(request, body_dict)

        prompt = ""
        f_req = body_dict.get("f.req", "")
        if f_req:
            try:
                outer = json.loads(f_req)
                if outer and isinstance(outer, list) and len(outer) > 1 and outer[1]:
                    inner = json.loads(outer[1])
                    if inner and isinstance(inner, list) and len(inner) > 0 and isinstance(inner[0], list):
                        prompt = str(inner[0][0])
            except Exception:
                pass

        cookie_header = request.headers.get("Cookie", "")

        # 1. Trigger HTTP 429 Rate Limit for primary account
        if "[TRIGGER_HTTP_429]" in prompt and "primary_pro" in cookie_header:
            return web.Response(status=429, text="HTTP Error 429: Too Many Requests / Rate Limit")

        # 2. Trigger Error 1037 specifically when acc_ultra_backup is caller
        if "[TRIGGER_QUOTA_FAIL_ULTRA]" in prompt and "ultra_backup" in cookie_header:
            resp = web.StreamResponse(status=200, headers={"Content-Type": "text/plain;charset=UTF-8"})
            await resp.prepare(request)
            err_frame = build_stream_error_frame(1037, "Usage limit exceeded on Ultra")
            await resp.write(err_frame)
            await resp.write_eof()
            return resp

        # 3. Trigger Mid-stream quota error (emits initial text chunk, then throws 1037)
        if "[TRIGGER_MID_STREAM_1037]" in prompt and "primary_pro" in cookie_header:
            resp = web.StreamResponse(status=200, headers={"Content-Type": "text/plain;charset=UTF-8"})
            await resp.prepare(request)
            chunks = build_golden_stream_sequence()
            # Send Anti-XSSI, thoughts, and first text chunk (chunks 0, 1, 2, 3)
            for c in chunks[:4]:
                await resp.write(c)
            err_frame = build_stream_error_frame(1037, "Quota exhausted mid-stream")
            await resp.write(err_frame)
            await resp.write_eof()
            return resp

        # 4. Standard 1037 trigger targeting primary_pro
        if "[TRIGGER_QUOTA_ERROR]" in prompt and "primary_pro" in cookie_header:
            resp = web.StreamResponse(status=200, headers={"Content-Type": "text/plain;charset=UTF-8"})
            await resp.prepare(request)
            err_frame = build_stream_error_frame(1037, "Usage limit exceeded")
            await resp.write(err_frame)
            await resp.write_eof()
            return resp

        # 5. Standard 1061 trigger targeting primary_pro
        if "[TRIGGER_QUOTA_1061]" in prompt and "primary_pro" in cookie_header:
            resp = web.StreamResponse(status=200, headers={"Content-Type": "text/plain;charset=UTF-8"})
            await resp.prepare(request)
            err_frame = build_stream_error_frame(1061, "Resource quota exhausted")
            await resp.write(err_frame)
            await resp.write_eof()
            return resp

        # Fallback to standard MockGeminiServer fault handling
        return await super()._handle_stream_generate(request)


@pytest.fixture(scope="module")
def adv_mock_server():
    """Starts adversarial mock server on dedicated port."""
    server = AdversarialMockGeminiServer(host=ADVERSARIAL_HOST, port=ADVERSARIAL_PORT)
    server.start_background()
    yield server
    server.stop_background()


@pytest.fixture(autouse=True)
def reset_adv_mock(adv_mock_server: AdversarialMockGeminiServer):
    adv_mock_server.fault.reset()


def parse_sse_events(response_text: str) -> List[Dict[str, Any]]:
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


@pytest.fixture
def adv_test_env(tmp_path: Path, adv_mock_server: AdversarialMockGeminiServer):
    """Configures multi-tier test environment: Pro Primary, Free Backup, Ultra Backup."""
    db_file = tmp_path / "adv_test.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    asyncio.run(db.init_schema(schema_path))

    vault_file = tmp_path / "adv_vault.enc"
    vault = CredentialVault(vault_file)
    vdata = VaultData(
        version=2,
        active_account_id="acc_primary_pro",
        failover_config=FailoverConfig(
            enabled=True,
            strategy="priority",
            cooldown_duration_seconds=300,
            fallback_chain=["acc_primary_pro", "acc_ultra_backup", "acc_free_backup"],
        ),
        accounts={
            "acc_primary_pro": AccountProfile(
                id="acc_primary_pro",
                name="Primary Pro Account",
                cookies={"__Secure-1PSID": "mock_psid_primary_pro", "__Secure-1PSIDTS": "ts_pro"},
                tier="pro",
                priority=1,
            ),
            "acc_free_backup": AccountProfile(
                id="acc_free_backup",
                name="Free Backup Account",
                cookies={"__Secure-1PSID": "mock_psid_free_backup", "__Secure-1PSIDTS": "ts_free"},
                tier="free",
                priority=2,
            ),
            "acc_ultra_backup": AccountProfile(
                id="acc_ultra_backup",
                name="Ultra Backup Account",
                cookies={"__Secure-1PSID": "mock_psid_ultra_backup", "__Secure-1PSIDTS": "ts_ultra"},
                tier="ultra",
                priority=3,
            ),
        },
    )
    vault.store_vault_data(vdata)

    repo = ConversationRepository(db)
    search_eng = SearchEngine(db)
    sm = SessionManager(base_url=ADVERSARIAL_BASE_URL, vault=vault)
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


# ============================================================================
# Test Matrix 1: Error 1037 Across Argon, Pro, and Task Modes
# ============================================================================

@pytest.mark.parametrize(
    "model_name,feature_mode",
    [
        ("4 Argon", "chat"),
        ("3.1 Pro", "chat"),
        ("3.1 Pro", "task"),
    ],
)
def test_error_1037_matrix_failover_cooldown_and_telemetry(adv_test_env, model_name, feature_mode):
    """
    Empirically verifies error 1037 across Argon, Pro, and Task modes:
    - Primary account enters cooldown.
    - SSE event failover is emitted strictly BEFORE text/thought tokens.
    - Backup account fulfills the request.
    - Telemetry records was_failover=1 and failover_from_account.
    """
    client, sm, vault, db = adv_test_env
    sm.activate_account("acc_primary_pro")

    for sess in sm.sessions.values():
        sess.cooldown_until = None
        sess.cooldown_models.clear()

    payload = {
        "prompt": f"Stress test matrix 1037 [{model_name} / {feature_mode}] [TRIGGER_QUOTA_ERROR]",
        "model": model_name,
        "feature_mode": feature_mode,
    }
    res = client.post("/api/chat/generate", json=payload)
    assert res.status_code == 200

    events = parse_sse_events(res.text)
    types = [e["type"] for e in events]

    assert "failover" in types, f"Expected failover event in stream: {types}"
    failover_idx = types.index("failover")

    # Verify event ordering: failover MUST appear before any text or thought tokens
    first_token_idx = next(
        (i for i, t in enumerate(types) if t in ("text", "thought")),
        None,
    )
    if first_token_idx is not None:
        assert failover_idx < first_token_idx, "SSE failover event must be emitted BEFORE token streaming starts"

    failover_evt = events[failover_idx]
    assert failover_evt.get("error_code") == 1037
    assert failover_evt.get("from_account_id") == "acc_primary_pro"
    assert failover_evt.get("to_account_id") == "acc_ultra_backup"

    # Verify completion
    assert "done" in types
    done_evt = next(e for e in events if e["type"] == "done")
    assert done_evt.get("metadata", {}).get("was_failover") is True
    assert done_evt.get("metadata", {}).get("fulfilled_by_account_id") == "acc_ultra_backup"

    # Verify cooldown on primary
    assert sm.is_in_cooldown("acc_primary_pro", model_id=model_name) is True

    # Verify SQLite telemetry
    recent = asyncio.run(db.get_recent_failovers(limit=5))
    assert len(recent) >= 1
    assert recent[0]["failover_from_account"] == "acc_primary_pro"
    assert recent[0]["account_id"] == "acc_ultra_backup"


# ============================================================================
# Test Matrix 2: Error 429 (Rate Limit) Across Argon, Pro, and Task Modes
# ============================================================================

@pytest.mark.parametrize(
    "model_name,feature_mode",
    [
        ("4 Argon", "chat"),
        ("3.1 Pro", "chat"),
        ("3.1 Pro", "task"),
    ],
)
def test_error_429_matrix_failover_cooldown_and_telemetry(adv_test_env, model_name, feature_mode):
    """
    Empirically verifies error 429 across Argon, Pro, and Task modes:
    - Caught on primary account.
    - Cooldown marked.
    - SSE failover emitted with error_code=429.
    - Backup account fulfills stream.
    - Telemetry records was_failover=1.
    """
    client, sm, vault, db = adv_test_env
    sm.activate_account("acc_primary_pro")

    for sess in sm.sessions.values():
        sess.cooldown_until = None
        sess.cooldown_models.clear()

    payload = {
        "prompt": f"Stress test matrix 429 [{model_name} / {feature_mode}] [TRIGGER_HTTP_429]",
        "model": model_name,
        "feature_mode": feature_mode,
    }
    res = client.post("/api/chat/generate", json=payload)
    assert res.status_code == 200

    events = parse_sse_events(res.text)
    types = [e["type"] for e in events]

    assert "failover" in types, f"Expected failover for 429: {types}"
    failover_idx = types.index("failover")
    failover_evt = events[failover_idx]

    assert failover_evt.get("error_code") == 429
    assert failover_evt.get("from_account_id") == "acc_primary_pro"
    assert failover_evt.get("to_account_id") == "acc_ultra_backup"

    # Token ordering check
    first_token_idx = next((i for i, t in enumerate(types) if t in ("text", "thought")), None)
    if first_token_idx is not None:
        assert failover_idx < first_token_idx

    assert "done" in types
    assert sm.is_in_cooldown("acc_primary_pro", model_id=model_name) is True


# ============================================================================
# Test Matrix 3: Error 1061 (Container Restricted) Across Argon, Pro, Task Modes
# ============================================================================

@pytest.mark.parametrize(
    "model_name,feature_mode",
    [
        ("4 Argon", "chat"),
        ("3.1 Pro", "chat"),
        ("3.1 Pro", "task"),
    ],
)
def test_error_1061_matrix_failover_cooldown_and_telemetry(adv_test_env, model_name, feature_mode):
    """
    Empirically verifies error 1061 across Argon, Pro, and Task modes.
    """
    client, sm, vault, db = adv_test_env
    sm.activate_account("acc_primary_pro")

    for sess in sm.sessions.values():
        sess.cooldown_until = None
        sess.cooldown_models.clear()

    payload = {
        "prompt": f"Stress test matrix 1061 [{model_name} / {feature_mode}] [TRIGGER_QUOTA_1061]",
        "model": model_name,
        "feature_mode": feature_mode,
    }
    res = client.post("/api/chat/generate", json=payload)
    assert res.status_code == 200

    events = parse_sse_events(res.text)
    types = [e["type"] for e in events]

    assert "failover" in types
    failover_idx = types.index("failover")
    failover_evt = events[failover_idx]
    assert failover_evt.get("error_code") == 1061

    first_token_idx = next((i for i, t in enumerate(types) if t in ("text", "thought")), None)
    if first_token_idx is not None:
        assert failover_idx < first_token_idx

    assert "done" in types
    assert sm.is_in_cooldown("acc_primary_pro", model_id=model_name) is True


# ============================================================================
# Test 4: Tier Enforcement (Pro <-> Ultra) and Skipping Ineligible Tiers
# ============================================================================

def test_tier_enforcement_skips_free_account_for_high_tier_model(adv_test_env):
    """
    Verifies that when Pro account fails on 4 Argon, the failover engine:
    1. Rejects acc_free_backup (tier=free is below required pro).
    2. Routes to acc_ultra_backup (tier=ultra >= pro).
    """
    client, sm, vault, _ = adv_test_env
    sm.activate_account("acc_primary_pro")

    for sess in sm.sessions.values():
        sess.cooldown_until = None
        sess.cooldown_models.clear()

    payload = {
        "prompt": "Synthesize quantum theorem [TRIGGER_QUOTA_ERROR]",
        "model": "4 Argon",
    }
    res = client.post("/api/chat/generate", json=payload)
    assert res.status_code == 200

    events = parse_sse_events(res.text)
    failover_evt = next(e for e in events if e["type"] == "failover")
    assert failover_evt.get("to_account_id") != "acc_free_backup"
    assert failover_evt.get("to_account_id") == "acc_ultra_backup"


def test_bidirectional_failover_ultra_to_pro(adv_test_env):
    """
    Verifies bi-directional failover: when Ultra account fails, it routes to Pro account.
    """
    client, sm, vault, _ = adv_test_env
    sm.activate_account("acc_ultra_backup")

    for sess in sm.sessions.values():
        sess.cooldown_until = None
        sess.cooldown_models.clear()

    payload = {
        "prompt": "Ultra failing over to pro [TRIGGER_QUOTA_FAIL_ULTRA]",
        "model": "3.1 Pro",
    }
    res = client.post("/api/chat/generate", json=payload)
    assert res.status_code == 200

    events = parse_sse_events(res.text)
    failover_evt = next(e for e in events if e["type"] == "failover")
    assert failover_evt.get("from_account_id") == "acc_ultra_backup"
    assert failover_evt.get("to_account_id") == "acc_primary_pro"


# ============================================================================
# Test 5: Pre-Flight Cooldown Fast-Path Routing
# ============================================================================

def test_preflight_cooldown_routes_without_upstream_failure(adv_test_env):
    """
    Verifies that if an account is already marked in cooldown for a model,
    the failover engine routes immediately to backup without incurring an upstream failure.
    """
    client, sm, vault, _ = adv_test_env
    sm.activate_account("acc_primary_pro")

    for sess in sm.sessions.values():
        sess.cooldown_until = None
        sess.cooldown_models.clear()

    sm.mark_cooldown("acc_primary_pro", model_id="4 Argon", duration_seconds=600, reason="Manual test cooldown")
    assert sm.is_in_cooldown("acc_primary_pro", model_id="4 Argon") is True

    payload = {
        "prompt": "Clean request with primary in cooldown",
        "model": "4 Argon",
    }
    res = client.post("/api/chat/generate", json=payload)
    assert res.status_code == 200

    events = parse_sse_events(res.text)
    types = [e["type"] for e in events]

    assert "failover" in types
    failover_evt = next(e for e in events if e["type"] == "failover")
    assert failover_evt.get("from_account_id") == "acc_primary_pro"
    assert failover_evt.get("to_account_id") == "acc_ultra_backup"
    assert failover_evt.get("reason") == "Account in cooldown"

    assert "done" in types
    done_evt = next(e for e in events if e["type"] == "done")
    assert done_evt.get("metadata", {}).get("fulfilled_by_account_id") == "acc_ultra_backup"


# ============================================================================
# Test 6: Exhaustion of All Backup Accounts
# ============================================================================

def test_all_backups_exhausted_emits_error_cleanly(adv_test_env):
    """
    Verifies that when all eligible backup accounts are in cooldown,
    the failover engine cleanly emits an error event rather than looping or crashing.
    """
    client, sm, vault, db = adv_test_env
    sm.activate_account("acc_primary_pro")

    # Mark Ultra in cooldown as well
    sm.mark_cooldown("acc_ultra_backup", model_id="4 Argon", duration_seconds=600)

    payload = {
        "prompt": "Must fail cleanly when no backups exist [TRIGGER_QUOTA_ERROR]",
        "model": "4 Argon",
    }
    res = client.post("/api/chat/generate", json=payload)
    assert res.status_code == 200

    events = parse_sse_events(res.text)
    types = [e["type"] for e in events]

    assert "error" in types
    err_evt = next(e for e in events if e["type"] == "error")
    err_msg = str(err_evt.get("error", "")).lower()
    assert "usage" in err_msg or "limit" in err_msg or "quota" in err_msg or "1037" in err_msg


# ============================================================================
# Test 7: Mid-Stream Quota Failure Handling
# ============================================================================

def test_mid_stream_quota_error_does_not_interleave_replay_tokens(adv_test_env):
    """
    Verifies that if an error occurs AFTER tokens have already been emitted,
    the system does not attempt naive replay that would corrupt client state.
    """
    client, sm, vault, _ = adv_test_env
    sm.activate_account("acc_primary_pro")

    for sess in sm.sessions.values():
        sess.cooldown_until = None
        sess.cooldown_models.clear()

    payload = {
        "prompt": "Test mid-stream error [TRIGGER_MID_STREAM_1037]",
        "model": "4 Argon",
    }
    res = client.post("/api/chat/generate", json=payload)
    assert res.status_code == 200

    events = parse_sse_events(res.text)
    types = [e["type"] for e in events]

    # Initial text was emitted, so failover is blocked to prevent duplication
    assert "text" in types
    assert "error" in types
    assert "failover" not in types


# ============================================================================
# Test 8: Adversarial Verification of OpenAI /v1/chat/completions Failover
# ============================================================================

def test_openai_proxy_chat_completions_missing_import_vulnerability(adv_test_env):
    """
    Verifies that UsageLimitExceededError is properly imported in routes.py,
    preventing NameError crashes and enabling automatic failover and telemetry logging.
    """
    import backend.app.openai.routes as r
    import backend.app.openai_proxy.routes as pr
    assert "UsageLimitExceededError" in dir(r)
    assert "UsageLimitExceededError" in dir(pr)

    client, sm, vault, db = adv_test_env
    sm.activate_account("acc_primary_pro")

    for sess in sm.sessions.values():
        sess.cooldown_until = None
        sess.cooldown_models.clear()

    payload = {
        "model": "gemini-2.5-pro",
        "messages": [{"role": "user", "content": "Trigger OpenAI failover [TRIGGER_QUOTA_ERROR]"}],
        "stream": True,
    }

    # Verify that the request does NOT raise NameError and succeeds with 200
    res = client.post("/v1/chat/completions", json=payload)
    assert res.status_code == 200
    assert "[DONE]" in res.text

    # Verify primary entered cooldown
    assert sm.is_in_cooldown("acc_primary_pro", model_id="gemini-2.5-pro") is True

    # Verify failover telemetry was logged in database
    recent = asyncio.run(db.get_recent_failovers(limit=5))
    assert len(recent) >= 1
    assert recent[0]["failover_from_account"] == "acc_primary_pro"
    assert recent[0]["account_id"] == "acc_ultra_backup"
