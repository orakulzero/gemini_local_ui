"""
backend/app/api/limits.py
FastAPI router for Combined Account Limits & Quota Telemetry Panel (R2):
- Rolling 1h and 24h usage metrics aggregation (requests, prompt/completion/thinking tokens, task mode calls)
- Live otAQ7b policy metadata and quota health
- Per-model cooldown countdowns and soft limit warning thresholds
- Active failover routing state
"""

from __future__ import annotations

import logging
import time
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.app.bridge.models import DEFAULT_MODELS
from backend.app.bridge.session import SessionHealthStatus, SessionManager
from backend.app.storage.database import Database
from backend.app.storage.vault import AccountProfile, CredentialVault

logger = logging.getLogger("gemini_api.limits")
router = APIRouter(prefix="/api/limits", tags=["Limits & Telemetry"])


def get_session_manager(request: Request) -> SessionManager:
    """Resolves active SessionManager singleton from app state."""
    mgr = getattr(request.app.state, "session_manager", None)
    if not mgr:
        import backend.app.main as main_mod
        mgr = getattr(main_mod, "session_manager", None)
    if not mgr:
        raise HTTPException(status_code=500, detail="Session manager not initialized")
    return mgr


def get_vault(request: Request) -> CredentialVault:
    """Resolves active CredentialVault singleton from app state."""
    v = getattr(request.app.state, "vault", None)
    if not v:
        import backend.app.main as main_mod
        v = getattr(main_mod, "vault", None)
    if not v:
        v = CredentialVault()
    return v


def get_db(request: Request) -> Database:
    """Resolves active Database singleton from app state."""
    d = getattr(request.app.state, "db", None) or getattr(request.app.state, "database", None)
    if not d:
        import backend.app.main as main_mod
        d = getattr(main_mod, "db", None)
    if not d:
        d = Database()
    return d


class ThresholdsUpdateRequest(BaseModel):
    daily_request_threshold: int = Field(default=250, ge=1)
    daily_token_threshold: int = Field(default=500000, ge=1)
    hourly_request_threshold: int = Field(default=60, ge=1)


CANONICAL_TELEMETRY_MODELS = [
    ("4 Argon", "95a4c0abcba24364"),
    ("3.1 Pro", "e6fa609c3fa255c0"),
    ("3.8 Flash", "56fdd199312815e2"),
    ("Task", "task_mode"),
]

MODEL_ID_TO_CANONICAL_NAME: dict[str, str] = {
    "95a4c0abcba24364": "4 Argon",
    "e6fa609c3fa255c0": "3.1 Pro",
    "56fdd199312815e2": "3.8 Flash",
    "task_mode": "Task",
    "4 Argon": "4 Argon",
    "3.1 Pro": "3.1 Pro",
    "3.8 Flash": "3.8 Flash",
    "Task": "Task",
}

for _mid, _info in DEFAULT_MODELS.items():
    if _mid not in MODEL_ID_TO_CANONICAL_NAME:
        MODEL_ID_TO_CANONICAL_NAME[_mid] = _info.display_name
    if _info.display_name not in MODEL_ID_TO_CANONICAL_NAME:
        MODEL_ID_TO_CANONICAL_NAME[_info.display_name] = _info.display_name


@router.get("")
async def get_limits(
    account_id: str | None = Query(None, description="Optional account ID filter"),
    session_mgr: SessionManager = Depends(get_session_manager),
    vault: CredentialVault = Depends(get_vault),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """
    Returns live account quota health, per-model rolling usage counters,
    configured thresholds, cooldown status, and failover state.
    """
    now = time.time()

    # 1. Global 24h Summary
    global_24h = await db.get_rolling_usage(account_id=None, window_seconds=86400)

    # 2. Resolve target profiles from vault
    vdata = vault.load_vault_data()
    failover_cfg = vdata.failover_config

    profiles: list[AccountProfile] = []
    if account_id:
        p = vault.get_account(account_id)
        if not p:
            raise HTTPException(status_code=404, detail=f"Account '{account_id}' not found")
        profiles.append(p)
    else:
        profiles = list(vdata.accounts.values())
        if not profiles:
            # Fallback if vault is empty
            profiles = [
                AccountProfile(
                    id=session_mgr.active_account_id,
                    name="Default Account",
                    tier="free",
                )
            ]

    accounts_data: list[dict[str, Any]] = []

    for prof in profiles:
        sess = session_mgr.get_account_session(prof.id)

        # Usage rolling windows
        r_1h = await db.get_rolling_usage(account_id=prof.id, window_seconds=3600)
        r_24h = await db.get_rolling_usage(account_id=prof.id, window_seconds=86400)
        model_usage_24h = await db.get_per_model_usage(account_id=prof.id, window_seconds=86400)

        # Cooldown evaluation
        cooldown_until = sess.cooldown_until if sess else prof.cooldown_until
        is_cooling = bool(cooldown_until and cooldown_until > now)
        rem_sec = max(0, int(cooldown_until - now)) if is_cooling and cooldown_until else 0

        affected_models: list[str] = []
        if is_cooling:
            affected_models.append("All Models")
        if sess and sess.cooldown_models:
            for m_id, expire in sess.cooldown_models.items():
                if expire > now:
                    affected_models.append(m_id)

        # Policy string & Health status
        tier = prof.tier.lower()
        policy_string = f"launch_{tier}_policy"
        if is_cooling:
            health_str = "cooldown"
        elif sess and sess.context and sess.context.status:
            health_str = sess.context.status.value if isinstance(sess.context.status, SessionHealthStatus) else str(sess.context.status)
        else:
            health_str = prof.health_status

        # Aggregate per_model_24h by canonical display name to prevent duplicate rows
        aggregated_model_usage: dict[str, dict[str, Any]] = {}
        for raw_key, stats in model_usage_24h.items():
            raw_id = stats.get("model_id") or raw_key
            canon_name = MODEL_ID_TO_CANONICAL_NAME.get(raw_key) or MODEL_ID_TO_CANONICAL_NAME.get(raw_id) or raw_key
            canon_id = raw_id
            for d_name, m_id in CANONICAL_TELEMETRY_MODELS:
                if d_name == canon_name:
                    canon_id = m_id
                    break

            if canon_name not in aggregated_model_usage:
                aggregated_model_usage[canon_name] = {
                    "model_id": canon_id,
                    "requests": 0,
                    "tokens_est": 0,
                    "error_count": 0,
                }
            aggregated_model_usage[canon_name]["requests"] += stats.get("requests", 0)
            aggregated_model_usage[canon_name]["tokens_est"] += stats.get("tokens_est", 0)
            aggregated_model_usage[canon_name]["error_count"] += stats.get("error_count", 0)

        # Per model 24h metrics dictionary
        per_model: dict[str, Any] = {}
        for disp_name, m_id in CANONICAL_TELEMETRY_MODELS:
            stats = aggregated_model_usage.get(disp_name, {})
            m_cooling = False
            m_rem_sec = 0
            m_model_exp = max(sess.cooldown_models.get(m_id, 0), sess.cooldown_models.get(disp_name, 0)) if sess else 0
            if m_model_exp > now:
                m_cooling = True
                m_rem_sec = max(0, int(m_model_exp - now))
            elif is_cooling:
                m_cooling = True
                m_rem_sec = rem_sec

            per_model[disp_name] = {
                "model_id": m_id,
                "requests": stats.get("requests", 0),
                "tokens_est": stats.get("tokens_est", 0),
                "quota_status": "cooldown" if m_cooling else "normal",
                "cooldown_remaining_seconds": m_rem_sec,
            }

        # Any extra models recorded in DB
        for c_name, stats in aggregated_model_usage.items():
            if c_name not in per_model:
                extra_id = stats.get("model_id", c_name)
                m_extra_exp = max(sess.cooldown_models.get(extra_id, 0), sess.cooldown_models.get(c_name, 0)) if sess else 0
                if m_extra_exp > now:
                    m_cooling = True
                    m_rem_sec = max(0, int(m_extra_exp - now))
                elif is_cooling:
                    m_cooling = True
                    m_rem_sec = rem_sec
                else:
                    m_cooling = False
                    m_rem_sec = 0
                per_model[c_name] = {
                    "model_id": extra_id,
                    "requests": stats.get("requests", 0),
                    "tokens_est": stats.get("tokens_est", 0),
                    "quota_status": "cooldown" if m_cooling else "normal",
                    "cooldown_remaining_seconds": m_rem_sec,
                }

        # Thresholds & warnings
        limits_cfg = await db.get_account_limits_config(prof.id)
        d_req_thresh = limits_cfg.get("daily_request_threshold", 250)
        d_tok_thresh = limits_cfg.get("daily_token_threshold", 500000)
        h_req_thresh = limits_cfg.get("hourly_request_threshold", 60)

        req_pct = round((r_24h["total_requests"] / d_req_thresh) * 100, 1) if d_req_thresh > 0 else 0.0
        tok_pct = round((r_24h["total_tokens"] / d_tok_thresh) * 100, 1) if d_tok_thresh > 0 else 0.0

        if req_pct >= 100.0 or tok_pct >= 100.0:
            warning_status = "critical"
        elif req_pct >= 80.0 or tok_pct >= 80.0:
            warning_status = "warning"
        else:
            warning_status = "normal"

        accounts_data.append({
            "account_id": prof.id,
            "name": prof.name,
            "tier": prof.tier,
            "policy_string": policy_string,
            "health_status": health_str,
            "cooldown": {
                "is_active": is_cooling,
                "reset_timestamp": int(cooldown_until) if cooldown_until else None,
                "remaining_seconds": rem_sec,
                "affected_models": affected_models,
            },
            "rolling_1h": {
                "requests": r_1h["total_requests"],
                "tokens_est": r_1h["total_tokens"],
                "task_calls": r_1h["task_mode_calls"],
            },
            "rolling_24h": {
                "requests": r_24h["total_requests"],
                "prompt_tokens_est": r_24h["total_prompt_tokens"],
                "completion_tokens_est": r_24h["total_completion_tokens"],
                "thinking_tokens_est": r_24h["total_thinking_tokens"],
                "total_tokens_est": r_24h["total_tokens"],
                "task_calls": r_24h["task_mode_calls"],
                "quota_errors": r_24h["quota_error_count"],
            },
            "per_model_24h": per_model,
            "thresholds": {
                "daily_request_threshold": d_req_thresh,
                "daily_request_percentage": req_pct,
                "daily_token_threshold": d_tok_thresh,
                "daily_token_percentage": tok_pct,
                "hourly_request_threshold": h_req_thresh,
                "warning_status": warning_status,
            },
        })

    # Recent failovers from DB
    recent_failover_rows = await db.get_recent_failovers(limit=10)
    recent_failovers = [
        {
            "timestamp": int(r["request_timestamp"] / 1000) if r.get("request_timestamp") else int(now),
            "from_account_name": r.get("failover_from_account") or "Unknown",
            "to_account_name": r.get("account_id") or "Unknown",
            "model": r.get("model_name") or "Unknown",
            "error_code": r.get("status_code", 1037),
        }
        for r in recent_failover_rows
    ]

    return {
        "timestamp": int(now),
        "active_account_id": session_mgr.active_account_id,
        "global_summary_24h": {
            "total_requests": global_24h["total_requests"],
            "total_tokens_est": global_24h["total_tokens"],
            "task_mode_invocations": global_24h["task_mode_calls"],
            "failover_events_count": global_24h["failover_events_count"],
        },
        "accounts": accounts_data,
        "failover_state": {
            "enabled": failover_cfg.enabled,
            "strategy": failover_cfg.strategy,
            "fallback_chain": failover_cfg.fallback_chain,
            "recent_failovers": recent_failovers,
        },
    }


@router.post("/{account_id}/thresholds")
async def update_account_thresholds(
    account_id: str,
    req: ThresholdsUpdateRequest,
    db: Database = Depends(get_db),
    vault: CredentialVault = Depends(get_vault),
) -> dict[str, Any]:
    """Configures daily and hourly alert thresholds for an account."""
    if not vault.get_account(account_id):
        raise HTTPException(status_code=404, detail=f"Account '{account_id}' not found")

    await db.set_account_limits_config(
        account_id=account_id,
        daily_request_threshold=req.daily_request_threshold,
        daily_token_threshold=req.daily_token_threshold,
        hourly_request_threshold=req.hourly_request_threshold,
    )

    return {
        "success": True,
        "account_id": account_id,
        "thresholds": req.model_dump(),
    }
