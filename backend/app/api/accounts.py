"""
backend/app/api/accounts.py
FastAPI router for Multi-Account Management (R1):
- List, create, switch, and delete Google account profiles
- Automatic failover policy configuration
- Redacted credential presentation
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, Field

from backend.app.bridge.browser_extractor import BrowserCookieExtractor
from backend.app.bridge.session import SessionHealthStatus, SessionManager
from backend.app.storage.vault import AccountProfile, CredentialVault, FailoverConfig

logger = logging.getLogger("gemini_api.accounts")
router = APIRouter(prefix="/api/accounts", tags=["Accounts"])


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


# --- Schemas ---

class AccountPublicInfo(BaseModel):
    id: str
    name: str
    email: str = ""
    tier: str = "free"
    priority: int = 1
    is_active: bool = False
    health_status: str = "healthy"
    has_psid: bool = False
    has_psidts: bool = False
    has_access_token: bool = False
    build_label: str = ""
    last_refresh_timestamp: float = 0.0
    next_refresh_seconds: float = 0.0
    cooldown_until: Optional[float] = None
    cooldown_models: Dict[str, float] = Field(default_factory=dict)


class AccountCreateRequest(BaseModel):
    name: str
    cookies: Optional[str] = None
    auto_import: bool = False
    browser: Optional[str] = None
    profile_dir: Optional[str] = None
    tier_override: Optional[str] = None
    priority: int = 1


class FailoverConfigUpdate(BaseModel):
    enabled: bool = True
    strategy: str = "priority_tier"
    cooldown_duration_seconds: int = 3600
    fallback_chain: List[str] = Field(default_factory=list)
    notify_client_on_failover: bool = True


class AccountsListResponse(BaseModel):
    active_account_id: str
    accounts: List[AccountPublicInfo]
    failover_config: FailoverConfigUpdate


def build_public_info(
    profile: AccountProfile,
    session_mgr: SessionManager,
) -> AccountPublicInfo:
    """Builds redacted, health-enriched public view of an account."""
    now = time.time()
    sess = session_mgr.get_account_session(profile.id)
    is_active = (profile.id == session_mgr.active_account_id)

    has_psid = bool(profile.cookies.get("__Secure-1PSID"))
    has_psidts = bool(profile.cookies.get("__Secure-1PSIDTS"))

    if sess:
        ctx = sess.context
        has_at = bool(ctx.access_token)
        build_label = ctx.build_label
        last_refresh = ctx.last_refresh
        next_refresh = max(0.0, (last_refresh + session_mgr.rotation_interval) - now)
        cooldown_until = sess.cooldown_until or profile.cooldown_until
        cooldown_models = sess.cooldown_models

        if cooldown_until and cooldown_until > now:
            health_str = "cooldown"
        elif ctx.status:
            health_str = ctx.status.value if isinstance(ctx.status, SessionHealthStatus) else str(ctx.status)
        else:
            health_str = "healthy" if has_at else "unauthenticated"
    else:
        has_at = bool(profile.snlm0e)
        build_label = profile.cfb2h
        last_refresh = profile.last_rotated_at
        next_refresh = max(0.0, (last_refresh + session_mgr.rotation_interval) - now)
        cooldown_until = profile.cooldown_until
        cooldown_models = {}
        if cooldown_until and cooldown_until > now:
            health_str = "cooldown"
        else:
            health_str = profile.health_status

    return AccountPublicInfo(
        id=profile.id,
        name=profile.name,
        email=profile.email,
        tier=profile.tier,
        priority=profile.priority,
        is_active=is_active,
        health_status=health_str,
        has_psid=has_psid,
        has_psidts=has_psidts,
        has_access_token=has_at,
        build_label=build_label,
        last_refresh_timestamp=last_refresh,
        next_refresh_seconds=round(next_refresh, 1),
        cooldown_until=cooldown_until,
        cooldown_models=cooldown_models,
    )


# --- Endpoints ---

@router.get("", response_model=AccountsListResponse)
async def list_accounts(
    session_mgr: SessionManager = Depends(get_session_manager),
    vault: CredentialVault = Depends(get_vault),
) -> AccountsListResponse:
    """Lists all stored Google Gemini account profiles with live health and failover configuration."""
    vdata = vault.load_vault_data()
    failover_cfg = vdata.failover_config

    accounts_out: List[AccountPublicInfo] = []
    for profile in vdata.accounts.values():
        accounts_out.append(build_public_info(profile, session_mgr))

    return AccountsListResponse(
        active_account_id=session_mgr.active_account_id,
        accounts=accounts_out,
        failover_config=FailoverConfigUpdate(
            enabled=failover_cfg.enabled,
            strategy=failover_cfg.strategy,
            cooldown_duration_seconds=failover_cfg.cooldown_duration_seconds,
            fallback_chain=failover_cfg.fallback_chain,
            notify_client_on_failover=failover_cfg.notify_client_on_failover,
        ),
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_account(
    req: AccountCreateRequest,
    session_mgr: SessionManager = Depends(get_session_manager),
    vault: CredentialVault = Depends(get_vault),
) -> Dict[str, Any]:
    """Registers or imports a new Google Gemini account profile."""
    cookies_dict: Dict[str, str] = {}
    if req.auto_import:
        try:
            cookies_dict, _ = BrowserCookieExtractor.auto_extract()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Auto extraction failed: {e}")
    elif req.cookies:
        cookies_dict = BrowserCookieExtractor.parse_manual(req.cookies)
    else:
        raise HTTPException(
            status_code=400,
            detail="Either cookies string or auto_import=True must be provided",
        )

    validation = BrowserCookieExtractor.validate_cookies(cookies_dict)
    if not validation.is_valid:
        raise HTTPException(
            status_code=400,
            detail=validation.error_message or "Missing __Secure-1PSID cookie",
        )

    acc_id = f"acc_{uuid.uuid4().hex[:8]}"
    tier = req.tier_override or "free"
    if "pro" in req.name.lower() and not req.tier_override:
        tier = "pro"
    elif "ultra" in req.name.lower() and not req.tier_override:
        tier = "ultra"

    profile = AccountProfile(
        id=acc_id,
        name=req.name,
        tier=tier,
        priority=req.priority,
        cookies=cookies_dict,
    )

    await session_mgr.add_or_update_account(profile, cookies=cookies_dict)
    public_info = build_public_info(profile, session_mgr)

    return {
        "success": True,
        "account_id": acc_id,
        "account": public_info.model_dump(),
    }


@router.get("/{account_id}")
async def get_account_detail(
    account_id: str = Path(...),
    session_mgr: SessionManager = Depends(get_session_manager),
    vault: CredentialVault = Depends(get_vault),
) -> Dict[str, Any]:
    """Retrieves metadata and health for a single account."""
    profile = vault.get_account(account_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Account '{account_id}' not found")

    public_info = build_public_info(profile, session_mgr)
    return {"account": public_info.model_dump()}


@router.post("/{account_id}/activate")
async def activate_account(
    account_id: str = Path(...),
    session_mgr: SessionManager = Depends(get_session_manager),
    vault: CredentialVault = Depends(get_vault),
) -> Dict[str, Any]:
    """Switches the active primary Google Gemini account."""
    profile = vault.get_account(account_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Account '{account_id}' not found")

    success = session_mgr.activate_account(account_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to activate account")

    return {
        "success": True,
        "active_account_id": session_mgr.active_account_id,
    }


@router.delete("/{account_id}")
async def delete_account(
    account_id: str = Path(...),
    session_mgr: SessionManager = Depends(get_session_manager),
    vault: CredentialVault = Depends(get_vault),
) -> Dict[str, Any]:
    """Deletes an account profile and halts its background rotation task."""
    profile = vault.get_account(account_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Account '{account_id}' not found")

    await session_mgr.remove_account(account_id)

    return {
        "success": True,
        "message": f"Account '{account_id}' removed",
        "new_active_account_id": session_mgr.active_account_id,
    }


@router.post("/failover-config")
async def update_failover_config(
    req: FailoverConfigUpdate,
    vault: CredentialVault = Depends(get_vault),
) -> Dict[str, Any]:
    """Updates auto-failover policy and fallback chain."""
    config = FailoverConfig(
        enabled=req.enabled,
        strategy=req.strategy,
        cooldown_duration_seconds=req.cooldown_duration_seconds,
        fallback_chain=req.fallback_chain,
        notify_client_on_failover=req.notify_client_on_failover,
    )
    vault.update_failover_config(config)

    return {
        "success": True,
        "config": req.model_dump(),
    }
