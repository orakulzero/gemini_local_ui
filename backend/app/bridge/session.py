"""
backend/app/bridge/session.py
SessionManager state machine:
- Manages SessionContext and in-memory cookie jar
- Scrapes WIZ_global_data dynamic tokens (SNlM0e, cfb2h, FdrFJe, qKIAYe, TuX5cc) from /app
- Background cookie rotation (RotateCookies) with jitter and concurrency lock
- Health diagnostics inspection
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import random
import re
import time
from typing import Any
from collections.abc import Awaitable, Callable
import uuid
import httpx

from backend.app.bridge.exceptions import AuthenticationError
import contextlib

logger = logging.getLogger("gemini_bridge.session")

_RE_SNLM0E = re.compile(r'"SNlM0e":\s*"([^"]+)"')
_RE_CFB2H = re.compile(r'"cfb2h":\s*"([^"]+)"')
_RE_FDRFJE = re.compile(r'"FdrFJe":\s*"([^"]+)"')
_RE_QKIAYE = re.compile(r'"qKIAYe":\s*"([^"]+)"')
_RE_TUX5CC = re.compile(r'"TuX5cc":\s*"([^"]+)"')


class SessionHealthStatus(str, Enum):
    HEALTHY = "healthy"
    REFRESHING = "refreshing"
    EXPIRED = "expired"
    UNAUTHENTICATED = "unauthenticated"


HARVESTED_COOKIE_NAMES = {
    "__Secure-1PSIDTS",
    "__Secure-3PSIDTS",
    "SIDCC",
    "__Secure-1PSIDCC",
    "__Secure-3PSIDCC",
    "NID",
}


@dataclass
class SessionContext:
    cookies: dict[str, str] = field(default_factory=dict)
    access_token: str | None = None                                # SNlM0e ('at')
    build_label: str = "boq_assistant-bard-web-server_20261017.00_p0" # cfb2h ('bl')
    session_id: str | None = None                                  # FdrFJe ('f.sid')
    push_id: str = "feeds/mcudyrk2a4khkz"                             # qKIAYe
    language: str = "en"                                              # TuX5cc ('hl')
    session_uuid: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    req_id: int = 100000
    account_tier: str = "free"
    status: SessionHealthStatus = SessionHealthStatus.UNAUTHENTICATED
    last_refresh: float = field(default_factory=time.time)
    last_error: str | None = None
    passive_rotation: bool = False
    account_id: str | None = None
    vault: Any = None
    on_credentials_updated: Callable[[dict[str, str]], Awaitable[None]] | None = None

    @property
    def at_token(self) -> str:
        """Alias for access_token ('at' parameter)."""
        return self.access_token or ""


@dataclass
class SessionHealthReport:
    status: SessionHealthStatus
    is_authenticated: bool
    has_psid: bool
    has_psidts: bool
    has_access_token: bool
    build_label: str
    last_refresh_timestamp: float
    next_refresh_seconds: float
    account_tier: str
    error_message: str | None = None
    passive_rotation: bool = False


@dataclass
class AccountSession:
    account_id: str
    profile: Any  # AccountProfile from vault
    context: SessionContext
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    rotation_task: asyncio.Task | None = None
    cooldown_until: float | None = None
    cooldown_models: dict[str, float] = field(default_factory=dict)  # model_id -> expire_timestamp
    last_error: str | None = None


class SessionManager:
    """
    Manages Gemini web session state across multiple concurrent Google accounts:
    - Independent SessionContext, locks, and cookie jars per account.
    - Staggered background RotateCookies refresh loops.
    - Full health diagnostics per account and globally.
    - Failover candidate selection for Error 1037/429/1061 recovery.
    """

    def __init__(
        self,
        base_url: str = "https://gemini.google.com",
        rotate_url: str | None = None,
        on_credentials_updated: Callable[[dict[str, str]], Awaitable[None]] | None = None,
        rotation_interval_seconds: float = 540.0,
        rotation_jitter_seconds: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
        vault: Any = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if rotate_url:
            self.rotate_url = rotate_url
        elif "127.0.0.1" in self.base_url or "localhost" in self.base_url:
            self.rotate_url = f"{self.base_url}/RotateCookies"
        else:
            self.rotate_url = "https://accounts.google.com/RotateCookies"

        self.on_credentials_updated = on_credentials_updated
        self.rotation_interval = rotation_interval_seconds
        self.rotation_jitter = rotation_jitter_seconds
        self.vault = vault

        self.sessions: dict[str, AccountSession] = {}
        if self.vault:
            try:
                self.active_account_id = self.vault.get_active_account_id() or "default"
            except Exception:
                self.active_account_id = "default"
        else:
            self.active_account_id = "default"
        self._fallback_context = SessionContext()
        if http_client is not None:
            self._client = http_client
            if hasattr(self._client, "event_hooks") and isinstance(self._client.event_hooks, dict):
                resp_hooks = self._client.event_hooks.setdefault("response", [])
                if self.on_response_harvest_cookies not in resp_hooks:
                    resp_hooks.append(self.on_response_harvest_cookies)
        else:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                event_hooks={"response": [self.on_response_harvest_cookies]},
            )
        self._owns_client = http_client is None

    @property
    def context(self) -> SessionContext:
        """Backwards-compatible accessor for active session context."""
        sess = self.sessions.get(self.active_account_id)
        if sess is not None:
            return sess.context
        if self.sessions:
            first_sess = next(iter(self.sessions.values()))
            return first_sess.context
        return self._fallback_context

    @context.setter
    def context(self, new_ctx: SessionContext) -> None:
        sess = self.sessions.get(self.active_account_id)
        if sess is not None:
            sess.context = new_ctx
        else:
            self._fallback_context = new_ctx

    def get_cookies(self, account_id: str | None = None) -> dict[str, str]:
        """Returns a copy of cookies for the given or active account."""
        sess = self.get_account_session(account_id)
        if sess:
            return dict(sess.context.cookies)
        return dict(self.context.cookies)

    def next_req_id(self, account_id: str | None = None) -> int:
        """Thread-safe incrementing sequence counter for _reqid query parameter."""
        sess = self.get_account_session(account_id)
        ctx = sess.context if sess else self.context
        ctx.req_id += random.randint(1000, 9999)
        return ctx.req_id

    def get_account_session(self, account_id: str | None = None) -> AccountSession | None:
        """Resolves target AccountSession by ID, defaulting to active_account_id."""
        target_id = account_id or self.active_account_id
        return self.sessions.get(target_id)

    def get_session_context(self, account_id: str | None = None) -> SessionContext:
        """Returns SessionContext for the specified account or active account."""
        sess = self.get_account_session(account_id)
        if sess:
            return sess.context
        return self.context

    def get_account_tier(self, account_id: str | None = None) -> str:
        """Returns the tier string ('free', 'pro', 'ultra') for account."""
        sess = self.get_account_session(account_id)
        if sess and sess.profile:
            return getattr(sess.profile, "tier", "free")
        return getattr(self.context, "account_tier", "free")

    async def init_session(
        self,
        cookies: dict[str, str] | None = None,
        account_id: str | None = None,
        xsrf_token: str | None = None,
        gemini_bl: str | None = None,
    ) -> SessionContext:
        """Initializes or updates session for given account_id, scrapes tokens (or applies pre-extracted tokens), and starts rotation."""
        from backend.app.storage.vault import AccountProfile

        if account_id is None:
            if self.vault:
                try:
                    account_id = self.vault.get_active_account_id()
                except Exception:
                    account_id = None
            if not account_id:
                account_id = self.active_account_id or "default"

        target_cookies = dict(cookies) if cookies else {}
        if not target_cookies and self.vault:
            stored = self.vault.load_credentials()
            if stored and isinstance(stored, dict):
                target_cookies = stored

        if account_id in self.sessions:
            sess = self.sessions[account_id]
            async with sess.lock:
                sess.context.cookies = target_cookies
                sess.context.account_id = account_id
                sess.context.vault = self.vault
                sess.context.on_credentials_updated = self.on_credentials_updated
                if sess.profile:
                    sess.profile.cookies = target_cookies
        else:
            profile = None
            if self.vault:
                profile = self.vault.get_account(account_id)
            if not profile:
                profile = AccountProfile(
                    id=account_id,
                    name="Default Account" if account_id == "default" else account_id,
                    tier="free",
                    priority=1,
                    cookies=target_cookies,
                )
            else:
                profile.cookies = target_cookies
            sess = AccountSession(
                account_id=account_id,
                profile=profile,
                context=SessionContext(
                    cookies=target_cookies,
                    account_tier=profile.tier,
                    account_id=account_id,
                    vault=self.vault,
                    on_credentials_updated=self.on_credentials_updated,
                ),
            )
            self.sessions[account_id] = sess

        self.active_account_id = account_id

        clean_xsrf = xsrf_token.strip() if isinstance(xsrf_token, str) and xsrf_token.strip() else None
        clean_bl = gemini_bl.strip() if isinstance(gemini_bl, str) and gemini_bl.strip() else None

        if clean_xsrf:
            async with sess.lock:
                sess.context.access_token = clean_xsrf
                if clean_bl:
                    sess.context.build_label = clean_bl
                sess.context.status = SessionHealthStatus.HEALTHY
                sess.context.last_error = None
                sess.context.last_refresh = time.time()
                if sess.profile:
                    sess.profile.snlm0e = clean_xsrf
                    if clean_bl:
                        sess.profile.cfb2h = clean_bl
                    sess.profile.health_status = "healthy"
                    sess.profile.last_rotated_at = time.time()
                    sess.profile.last_used_at = time.time()
        else:
            await self.scrape_tokens(account_id)
            if sess.profile:
                sess.profile.health_status = (
                    sess.context.status.value
                    if isinstance(sess.context.status, SessionHealthStatus)
                    else str(sess.context.status)
                )

        if self.vault and target_cookies:
            try:
                if hasattr(self.vault, "save_account_async"):
                    await self.vault.save_account_async(sess.profile)
                else:
                    self.vault.save_account(sess.profile)
            except Exception as e:
                logger.warning("Could not persist profile to vault: %s", e)

        # Start rotation loop for this account if healthy and not running
        if sess.context.status == SessionHealthStatus.HEALTHY and (sess.rotation_task is None or sess.rotation_task.done()):
            sess.rotation_task = asyncio.create_task(self._rotation_loop(account_id))

        return sess.context

    async def initialize_all(self, test_stagger: bool = False) -> None:
        """Initializes all accounts discovered in CredentialVault with staggered rotation loops."""
        if not self.vault:
            return

        vdata = self.vault.load_vault_data()
        if vdata.accounts:
            if vdata.active_account_id in vdata.accounts:
                self.active_account_id = vdata.active_account_id
            else:
                self.active_account_id = next(iter(vdata.accounts.keys()))
        else:
            self.active_account_id = vdata.active_account_id or "default"

        for acc_id, profile in vdata.accounts.items():
            if acc_id not in self.sessions:
                sess = AccountSession(
                    account_id=acc_id,
                    profile=profile,
                    context=SessionContext(
                        cookies=dict(profile.cookies),
                        access_token=profile.snlm0e,
                        build_label=profile.cfb2h,
                        session_id=profile.fdrfje,
                        push_id=profile.qkiaye,
                        language=profile.tux5cc,
                        account_tier=profile.tier,
                        account_id=acc_id,
                        vault=self.vault,
                        on_credentials_updated=self.on_credentials_updated,
                    ),
                )
                self.sessions[acc_id] = sess
            else:
                sess = self.sessions[acc_id]
                sess.profile = profile
                sess.context.cookies = dict(profile.cookies)
                if profile.snlm0e:
                    sess.context.access_token = profile.snlm0e
                if profile.cfb2h:
                    sess.context.build_label = profile.cfb2h
                sess.context.account_tier = profile.tier
                sess.context.account_id = acc_id
                sess.context.vault = self.vault
                sess.context.on_credentials_updated = self.on_credentials_updated

            saved_snlm0e = profile.snlm0e
            saved_cfb2h = profile.cfb2h
            scraped = await self.scrape_tokens(acc_id)
            if (
                not scraped
                and saved_snlm0e
                and sess.context.cookies.get("__Secure-1PSID")
                and sess.context.status == SessionHealthStatus.EXPIRED
            ):
                sess.context.access_token = saved_snlm0e
                if saved_cfb2h:
                    sess.context.build_label = saved_cfb2h
                sess.context.status = SessionHealthStatus.HEALTHY
                sess.context.last_error = None
                sess.context.last_refresh = time.time()
                logger.info(
                    "Restored persisted SNlM0e token from vault for account '%s' (HTML scrape omitted SNlM0e)",
                    acc_id,
                )

            if sess.context.status == SessionHealthStatus.HEALTHY and (sess.rotation_task is None or sess.rotation_task.done()):
                initial_delay = random.uniform(0.1, 1.0) if test_stagger else random.uniform(5.0, 45.0)
                sess.rotation_task = asyncio.create_task(
                    self._staggered_rotation_loop(acc_id, initial_delay=initial_delay)
                )

        logger.info(
            "SessionManager initialized %d accounts (active: %s)",
            len(self.sessions),
            self.active_account_id,
        )

    async def recover_from_vault_backup(self, account_id: str | None = None) -> bool:
        """
        Attempts to rollback and recover session state from the latest confirmed
        healthy vault backup snapshot (vault.enc.bak).
        Returns True if recovery succeeded and session restored to HEALTHY.
        """
        if not self.vault:
            return False

        target_acc_id = account_id or self.active_account_id
        logger.warning("Attempting session rollback from vault backup for account '%s'", target_acc_id)

        try:
            if hasattr(self.vault, "rollback_from_backup_async"):
                success = await self.vault.rollback_from_backup_async()
            elif hasattr(self.vault, "rollback_from_backup"):
                success = await asyncio.to_thread(self.vault.rollback_from_backup)
            else:
                return False

            if not success:
                logger.warning("Vault backup rollback returned False; no viable snapshot")
                return False

            restored_profile = self.vault.get_account(target_acc_id)
            if not restored_profile or not restored_profile.cookies:
                vdata = self.vault.load_vault_data()
                restored_profile = vdata.accounts.get(vdata.active_account_id)
                if not restored_profile or not restored_profile.cookies:
                    return False

            sess = self.get_account_session(target_acc_id)
            ctx = sess.context if sess else self.context

            sess_lock = sess.lock if sess else getattr(self, "_lock", None)
            if sess_lock:
                async with sess_lock:
                    ctx.cookies = dict(restored_profile.cookies)
                    if sess:
                        sess.profile = restored_profile
            else:
                ctx.cookies = dict(restored_profile.cookies)
                if sess:
                    sess.profile = restored_profile

            # Verify restored credentials via scrape_tokens without nested rollback
            scraped = await self.scrape_tokens(target_acc_id, allow_rollback=False)
            if scraped and ctx.status == SessionHealthStatus.HEALTHY:
                logger.info("Session for account '%s' successfully recovered from vault backup snapshot", target_acc_id)
                return True
            logger.warning("Scrape with restored backup credentials failed for account '%s'", target_acc_id)
            return False
        except Exception as e:
            logger.error("Exception during session rollback attempt for %s: %s", target_acc_id, e)
            return False

    async def scrape_tokens(self, account_id: str | None = None, allow_rollback: bool = True) -> bool:
        """Fetches /app and extracts SNlM0e, cfb2h, FdrFJe, qKIAYe for given account."""
        sess = self.get_account_session(account_id)
        ctx = sess.context if sess else self.context

        url = f"{self.base_url}/app"
        cookie_header = "; ".join(f"{k}={v}" for k, v in ctx.cookies.items() if v)

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if cookie_header:
            headers["Cookie"] = cookie_header

        prev_status = ctx.status
        try:
            resp = await self._client.get(
                url,
                headers=headers,
                extensions={"account_id": account_id or self.active_account_id},
            )

            # 1. Real authentication rejection: HTTP 401 / 403
            if resp.status_code in (401, 403):
                ctx.status = SessionHealthStatus.UNAUTHENTICATED
                ctx.last_error = f"Gemini token scraping rejected with HTTP {resp.status_code}"
                return False

            # 2. Redirect to Google Accounts login: HTTP 302 / 301 / 307
            if resp.status_code in (301, 302, 303, 307, 308) or getattr(resp, "is_redirect", False):
                if allow_rollback and self.vault and hasattr(self.vault, "rollback_from_backup"):
                    recovered = await self.recover_from_vault_backup(account_id)
                    if recovered:
                        return True
                ctx.status = SessionHealthStatus.EXPIRED
                ctx.last_error = f"Session redirected to login page (HTTP {resp.status_code})"
                return False

            # 3. Transient server errors: HTTP 500 / 502 / 503 / 504
            if resp.status_code >= 500:
                logger.warning("Gemini token scrape received temporary server error HTTP %d", resp.status_code)
                if prev_status == SessionHealthStatus.HEALTHY or (ctx.cookies.get("__Secure-1PSID") and ctx.access_token):
                    ctx.status = SessionHealthStatus.HEALTHY
                ctx.last_error = f"Google server temporary error HTTP {resp.status_code}"
                return False

            # 4. Unexpected non-200
            if resp.status_code != 200:
                logger.warning("Gemini token scrape unexpected HTTP %d", resp.status_code)
                ctx.last_error = f"Unexpected HTTP status {resp.status_code}"
                return False

            body = resp.text

            m_at = _RE_SNLM0E.search(body)
            ctx.access_token = m_at.group(1) if m_at else None

            m_bl = _RE_CFB2H.search(body)
            if m_bl:
                ctx.build_label = m_bl.group(1)

            m_sid = _RE_FDRFJE.search(body)
            if m_sid:
                ctx.session_id = m_sid.group(1)

            m_push = _RE_QKIAYE.search(body)
            if m_push:
                ctx.push_id = m_push.group(1)

            m_lang = _RE_TUX5CC.search(body)
            if m_lang:
                ctx.language = m_lang.group(1)

            if ctx.access_token:
                ctx.status = SessionHealthStatus.HEALTHY
                ctx.last_error = None
                ctx.last_refresh = time.time()

                # Sync scraped tokens back to account profile
                if sess and sess.profile:
                    sess.profile.snlm0e = ctx.access_token
                    sess.profile.cfb2h = ctx.build_label
                    sess.profile.fdrfje = ctx.session_id
                    sess.profile.qkiaye = ctx.push_id
                    sess.profile.tux5cc = ctx.language
                    sess.profile.last_rotated_at = time.time()
                return True
            if ctx.cookies.get("__Secure-1PSID"):
                # Attempt rollback from vault backup before declaring total loss
                if allow_rollback and self.vault and hasattr(self.vault, "rollback_from_backup"):
                    recovered = await self.recover_from_vault_backup(account_id)
                    if recovered:
                        return True
                ctx.status = SessionHealthStatus.EXPIRED
                ctx.last_error = f"Authentication token SNlM0e missing; cookies expired (HTTP {resp.status_code})"
            else:
                ctx.status = SessionHealthStatus.UNAUTHENTICATED
                ctx.last_error = "No cookies provided; unauthenticated guest mode"
            return False

        except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError, ConnectionError, TimeoutError, OSError) as net_err:
            logger.warning("Transient network error scraping Gemini tokens for %s: %s", account_id, net_err)
            # RETAIN HEALTHY status on transient network errors if session was authenticated
            if prev_status == SessionHealthStatus.HEALTHY or (ctx.cookies.get("__Secure-1PSID") and (ctx.access_token or getattr(ctx, "passive_rotation", False))):
                ctx.status = SessionHealthStatus.HEALTHY
            ctx.last_error = f"Transient network error during token scrape: {net_err}"
            return False

        except Exception as e:
            logger.warning("Failed scraping Gemini tokens from %s for %s: %s", url, account_id, e)
            if allow_rollback and self.vault and hasattr(self.vault, "rollback_from_backup"):
                recovered = await self.recover_from_vault_backup(account_id)
                if recovered:
                    return True
            ctx.status = SessionHealthStatus.UNAUTHENTICATED
            ctx.last_error = str(e)
            return False

    async def rotate_cookies(self, account_id: str | None = None) -> bool:
        """Executes RotateCookies request for specified or active account."""
        sess = self.get_account_session(account_id)
        if not sess:
            sess_lock = self._lock if hasattr(self, "_lock") else asyncio.Lock()
            ctx = self.context
            target_profile = None
        else:
            sess_lock = sess.lock
            ctx = sess.context
            target_profile = sess.profile

        async with sess_lock:
            # Circuit breaker short-circuit: if passive rotation is already active
            if getattr(ctx, "passive_rotation", False) is True:
                if ctx.cookies.get("__Secure-1PSID"):
                    ctx.status = SessionHealthStatus.HEALTHY
                    ctx.last_error = None
                    return True
                ctx.status = SessionHealthStatus.EXPIRED
                ctx.last_error = "Session cookies missing under passive rotation"
                return False

            prev_status = ctx.status
            ctx.status = SessionHealthStatus.REFRESHING

            cookie_header = "; ".join(f"{k}={v}" for k, v in ctx.cookies.items() if v)
            headers = {
                "Origin": "https://accounts.google.com",
                "Referer": "https://accounts.google.com/",
                "Content-Type": "application/json",
                "Cookie": cookie_header,
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
                ),
            }

            body = json.dumps([658, "-0000000000000000000"])

        # Execute HTTP POST outside lock to eliminate re-entrant deadlock with response hooks
        try:
            resp = await self._client.post(
                self.rotate_url,
                headers=headers,
                content=body,
                extensions={
                    "account_id": account_id or self.active_account_id,
                    "is_rotate_request": True,
                },
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError, ConnectionError, TimeoutError, OSError) as net_err:
            logger.warning("Transient network failure during RotateCookies for %s: %s", account_id, net_err)
            async with sess_lock:
                if prev_status == SessionHealthStatus.HEALTHY or (ctx.cookies.get("__Secure-1PSID") and (ctx.access_token or getattr(ctx, "passive_rotation", False))):
                    ctx.status = SessionHealthStatus.HEALTHY
                else:
                    ctx.status = prev_status
                ctx.last_error = f"Transient network failure during cookie rotation: {net_err}"
            return False
        except Exception as e:
            logger.warning("Error executing RotateCookies for %s: %s", account_id, e)
            async with sess_lock:
                ctx.status = SessionHealthStatus.EXPIRED
                ctx.last_error = str(e)
            return False

        async with sess_lock:
            try:
                if resp.status_code == 405:
                    ctx.passive_rotation = True
                    if ctx.cookies.get("__Secure-1PSID") and (ctx.access_token or prev_status == SessionHealthStatus.HEALTHY):
                        ctx.status = SessionHealthStatus.HEALTHY
                        ctx.last_error = None
                        ctx.last_refresh = time.time()
                        logger.info(
                            "RotateCookies returned HTTP 405 for account %s (active rotation deprecated). Activated passive rotation circuit breaker; session maintained HEALTHY.",
                            account_id or self.active_account_id,
                        )
                        return True
                    ctx.status = SessionHealthStatus.EXPIRED
                    ctx.last_error = "RotateCookies returned HTTP 405 and session cookies invalid"
                    return False

                if resp.status_code in (401, 403):
                    ctx.status = SessionHealthStatus.UNAUTHENTICATED
                    ctx.last_error = "Cookie rotation rejected with authentication error"
                    raise AuthenticationError("Cookie rotation rejected: HTTP 401/403")

                if resp.status_code != 200:
                    ctx.status = prev_status
                    ctx.last_error = f"RotateCookies failed with HTTP {resp.status_code}"
                    return False

                # Extract rotated cookies
                for header_val in resp.headers.get_list("set-cookie"):
                    for cookie_part in header_val.split(";"):
                        cookie_part = cookie_part.strip()
                        if "=" in cookie_part:
                            k, v = cookie_part.split("=", 1)
                            if k in (
                                "__Secure-1PSIDTS",
                                "__Secure-3PSIDTS",
                                "__Secure-1PSIDCC",
                                "__Secure-3PSIDCC",
                                "SIDCC",
                                "NID",
                            ):
                                ctx.cookies[k] = v

                ctx.last_refresh = time.time()
                ctx.status = SessionHealthStatus.HEALTHY
                ctx.last_error = None

                # Persist updated cookies
                if self.on_credentials_updated:
                    try:
                        await self.on_credentials_updated(dict(ctx.cookies))
                    except Exception as e:
                        logger.error("on_credentials_updated callback failed: %s", e)

                if self.vault:
                    try:
                        if target_profile:
                            target_profile.cookies = dict(ctx.cookies)
                            target_profile.last_rotated_at = time.time()
                            if hasattr(self.vault, "save_account_async"):
                                await self.vault.save_account_async(target_profile)
                            else:
                                await asyncio.to_thread(self.vault.save_account, target_profile)
                        else:
                            if hasattr(self.vault, "store_credentials_async"):
                                await self.vault.store_credentials_async(dict(ctx.cookies))
                            else:
                                await asyncio.to_thread(self.vault.store_credentials, dict(ctx.cookies))
                    except Exception as e:
                        logger.error("Failed saving rotated cookies to vault: %s", e)

                return True

            except AuthenticationError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError, ConnectionError, TimeoutError, OSError) as net_err:
                logger.warning("Transient network error in RotateCookies processing for %s: %s", account_id, net_err)
                if prev_status == SessionHealthStatus.HEALTHY or (ctx.cookies.get("__Secure-1PSID") and (ctx.access_token or getattr(ctx, "passive_rotation", False))):
                    ctx.status = SessionHealthStatus.HEALTHY
                else:
                    ctx.status = prev_status
                ctx.last_error = f"Transient network error during cookie rotation: {net_err}"
                return False
            except Exception as e:
                logger.warning("Error executing RotateCookies for %s: %s", account_id, e)
                ctx.status = SessionHealthStatus.EXPIRED
                ctx.last_error = str(e)
                return False

    async def on_response_harvest_cookies(self, response: httpx.Response) -> None:
        """
        HTTPX response event hook that intercepts passively rotated Google session tokens,
        updates in-memory SessionContext, and immediately persists them to Windows DPAPI vault.
        Anti-poisoning: strictly ignores Set-Cookie on redirects (301, 302, 303, 307, 308),
        authentication errors (401, 403), and non-2xx responses.
        """
        # Anti-poisoning guard: reject 401, 403, 302/redirects, and any non-2xx error responses
        if (
            response.status_code in (301, 302, 303, 307, 308, 401, 403)
            or getattr(response, "is_redirect", False)
            or not (200 <= response.status_code < 300)
        ):
            logger.debug(
                "SessionManager anti-poisoning: ignored Set-Cookie from HTTP %d response (%s)",
                response.status_code,
                response.url,
            )
            return

        raw_cookie_headers = response.headers.get_list("set-cookie")
        if not raw_cookie_headers:
            return

        harvested: dict[str, str] = {}
        for header in raw_cookie_headers:
            parts = header.split(";")
            if not parts:
                continue
            first_segment = parts[0].strip()
            if "=" in first_segment:
                name, val = first_segment.split("=", 1)
                name = name.strip()
                val = val.strip().strip('"')
                if name in HARVESTED_COOKIE_NAMES and val and val != "deleted":
                    harvested[name] = val

        if not harvested:
            return

        logger.debug(
            "SessionManager passively harvested %d cookies from %s: %s",
            len(harvested),
            response.url,
            list(harvested.keys()),
        )

        req_account_id = None
        if hasattr(response, "request") and hasattr(response.request, "extensions"):
            if response.request.extensions.get("is_rotate_request"):
                return
            req_account_id = response.request.extensions.get("account_id")

        target_acc_id = req_account_id or self.active_account_id
        sess = self.get_account_session(target_acc_id)
        ctx = sess.context if sess else self.context

        sess_lock = sess.lock if sess else getattr(self, "_lock", None)
        if sess_lock:
            async with sess_lock:
                ctx.cookies.update(harvested)
                ctx.last_refresh = time.time()
                if ctx.status != SessionHealthStatus.HEALTHY and ctx.cookies.get("__Secure-1PSID"):
                    ctx.status = SessionHealthStatus.HEALTHY
                    ctx.last_error = None
        else:
            ctx.cookies.update(harvested)
            ctx.last_refresh = time.time()
            if ctx.status != SessionHealthStatus.HEALTHY and ctx.cookies.get("__Secure-1PSID"):
                ctx.status = SessionHealthStatus.HEALTHY
                ctx.last_error = None

        if self.vault:
            try:
                acc_id = getattr(ctx, "account_id", None) or target_acc_id
                if acc_id and hasattr(self.vault, "update_account_cookies_async"):
                    await self.vault.update_account_cookies_async(acc_id, harvested)
                    logger.info("Persisted passively harvested cookies for account '%s' to DPAPI vault asynchronously", acc_id)
                    return
                if acc_id and hasattr(self.vault, "save_account_async"):
                    profile = self.vault.get_account(acc_id)
                    if profile:
                        if hasattr(profile, "cookies") and isinstance(profile.cookies, dict):
                            profile.cookies.update(harvested)
                        profile.last_rotated_at = time.time()
                        await self.vault.save_account_async(profile)
                        logger.info("Persisted passively harvested cookies for account '%s' to DPAPI vault asynchronously", acc_id)
                        return
                elif acc_id and hasattr(self.vault, "get_account"):
                    profile = self.vault.get_account(acc_id)
                    if profile:
                        if hasattr(profile, "cookies") and isinstance(profile.cookies, dict):
                            profile.cookies.update(harvested)
                        profile.last_rotated_at = time.time()
                        await asyncio.to_thread(self.vault.save_account, profile)
                        logger.info("Persisted passively harvested cookies for account '%s' to DPAPI vault", acc_id)
                        return
                if hasattr(self.vault, "store_credentials_async"):
                    await self.vault.store_credentials_async(dict(ctx.cookies))
                    logger.info("Persisted passively harvested cookies to active DPAPI vault credentials asynchronously")
                elif hasattr(self.vault, "store_credentials"):
                    await asyncio.to_thread(self.vault.store_credentials, dict(ctx.cookies))
                    logger.info("Persisted passively harvested cookies to active DPAPI vault credentials")
            except Exception as e:
                logger.error("Failed saving harvested cookies to vault: %s", e)

        if self.on_credentials_updated:
            try:
                res = self.on_credentials_updated(dict(ctx.cookies))
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error("on_credentials_updated callback failed: %s", e)

    async def _staggered_rotation_loop(self, account_id: str, initial_delay: float = 0.0) -> None:
        """Starts rotation loop with an initial randomized stagger to prevent thundering herds."""
        if initial_delay > 0:
            try:
                await asyncio.sleep(initial_delay)
            except asyncio.CancelledError:
                return
        await self._rotation_loop(account_id)

    async def _rotation_loop(self, account_id: str | None = None) -> None:
        """
        Background keepalive loop executing periodic cookie rotation and token refresh:
        - Runs every 8-10 minutes (default 540s with +/-30s jitter).
        - Executes rotate_cookies() followed by scrape_tokens() for live SNlM0e/token refresh.
        - On transient failures, retains HEALTHY status and applies exponential backoff retry (15s, 30s, 60s, up to 300s).
        - On successful refresh, resets backoff counter to 0 and resumes regular schedule.
        - Shields the background task from premature termination: never exits unless CancelledError.
        """
        consecutive_failures = 0
        is_test_interval = self.rotation_interval < 1.0
        base_backoff = 0.05 if is_test_interval else 15.0
        max_backoff = 1.0 if is_test_interval else 300.0

        while True:
            if consecutive_failures > 0:
                backoff_factor = 2 ** min(consecutive_failures - 1, 5)
                jitter = random.uniform(-0.01, 0.01) if is_test_interval else random.uniform(-2.0, 2.0)
                min_sleep = 0.01 if is_test_interval else 5.0
                sleep_duration = max(min_sleep, min(max_backoff, base_backoff * backoff_factor + jitter))
                logger.info(
                    "Session keepalive loop for account '%s': retrying in %.1fs (consecutive failures: %d)",
                    account_id,
                    sleep_duration,
                    consecutive_failures,
                )
            else:
                jitter = random.uniform(-self.rotation_jitter, self.rotation_jitter)
                min_interval = 0.01 if is_test_interval else (0.01 if self.rotation_interval < 30.0 else 30.0)
                sleep_duration = max(min_interval, self.rotation_interval + jitter)

            try:
                await asyncio.sleep(sleep_duration)
            except asyncio.CancelledError:
                logger.info("Session keepalive loop cancelled for account '%s'", account_id)
                break

            try:
                sess = self.get_account_session(account_id)
                saved_token = (
                    (sess.context.access_token or (sess.profile.snlm0e if sess.profile else None))
                    if sess
                    else None
                )
                saved_bl = (
                    (sess.context.build_label or (sess.profile.cfb2h if sess.profile else None))
                    if sess
                    else None
                )

                # Step 1: Execute cookie rotation (or passive circuit breaker)
                rotate_ok = await self.rotate_cookies(account_id)

                # Step 2: Proactive keepalive token scrape (/app) to refresh SNlM0e, cfb2h, SIDCC
                scrape_ok = await self.scrape_tokens(account_id)
                if (
                    not scrape_ok
                    and sess
                    and saved_token
                    and sess.context.cookies.get("__Secure-1PSID")
                    and sess.context.status == SessionHealthStatus.EXPIRED
                ):
                    sess.context.access_token = saved_token
                    if saved_bl:
                        sess.context.build_label = saved_bl
                    sess.context.status = SessionHealthStatus.HEALTHY
                    sess.context.last_error = None
                    sess.context.last_refresh = time.time()

                if rotate_ok or scrape_ok:
                    consecutive_failures = 0
                    logger.debug("Session keepalive refresh succeeded for account '%s'", account_id)
                else:
                    consecutive_failures += 1
                    logger.warning(
                        "Session keepalive refresh failed for account '%s' (consecutive failures: %d); will retry with backoff",
                        account_id,
                        consecutive_failures,
                    )
            except asyncio.CancelledError:
                logger.info("Session keepalive loop cancelled for account '%s'", account_id)
                break
            except AuthenticationError as auth_err:
                logger.warning(
                    "Session keepalive detected authentication revocation for '%s': %s",
                    account_id,
                    auth_err,
                )
                consecutive_failures = 0
            except Exception as e:
                consecutive_failures += 1
                logger.warning(
                    "Unexpected exception in keepalive loop for account '%s': %s (consecutive failures: %d)",
                    account_id,
                    e,
                    consecutive_failures,
                )

    def get_health(self, account_id: str | None = None) -> SessionHealthReport:
        """Returns diagnostic session health information for given or active account."""
        sess = self.get_account_session(account_id)
        ctx = sess.context if sess else self.context

        has_psid = bool(ctx.cookies.get("__Secure-1PSID"))
        has_psidts = bool(ctx.cookies.get("__Secure-1PSIDTS"))
        has_at = bool(ctx.access_token)

        next_refresh = max(0.0, (ctx.last_refresh + self.rotation_interval) - time.time())

        return SessionHealthReport(
            status=ctx.status,
            is_authenticated=ctx.status == SessionHealthStatus.HEALTHY,
            has_psid=has_psid,
            has_psidts=has_psidts,
            has_access_token=has_at,
            build_label=ctx.build_label,
            last_refresh_timestamp=ctx.last_refresh,
            next_refresh_seconds=round(next_refresh, 1),
            account_tier=ctx.account_tier,
            error_message=ctx.last_error,
            passive_rotation=bool(getattr(ctx, "passive_rotation", False)),
        )

    def get_all_health(self) -> dict[str, SessionHealthReport]:
        """Returns diagnostic health reports for all managed accounts."""
        if not self.sessions:
            return {self.active_account_id: self.get_health()}
        return {acc_id: self.get_health(acc_id) for acc_id in self.sessions}

    async def update_cookies(
        self, new_cookies: dict[str, str], account_id: str | None = None
    ) -> SessionContext:
        """Hot-swaps session cookies for an account and immediately re-scrapes tokens."""
        sess = self.get_account_session(account_id)
        if sess:
            async with sess.lock:
                sess.context.cookies = dict(new_cookies)
            await self.scrape_tokens(sess.account_id)
            return sess.context
        self.context.cookies = dict(new_cookies)
        await self.scrape_tokens()
        return self.context

    def activate_account(self, account_id: str) -> bool:
        """Sets the active primary account ID in memory and in vault."""
        if account_id in self.sessions:
            self.active_account_id = account_id
            if self.vault:
                self.vault.set_active_account(account_id)
            return True
        if self.vault and self.vault.get_account(account_id):
            self.active_account_id = account_id
            self.vault.set_active_account(account_id)
            return True
        return False

    async def add_or_update_account(
        self, profile: Any, cookies: dict[str, str] | None = None
    ) -> AccountSession:
        """Registers a new account profile, starts its rotation loop, and persists to vault."""
        if cookies:
            profile.cookies = dict(cookies)
        if self.vault:
            self.vault.save_account(profile)

        acc_id = profile.id
        if acc_id in self.sessions:
            sess = self.sessions[acc_id]
            sess.profile = profile
            sess.context.cookies = dict(profile.cookies)
            sess.context.account_tier = profile.tier
        else:
            sess = AccountSession(
                account_id=acc_id,
                profile=profile,
                context=SessionContext(cookies=dict(profile.cookies), account_tier=profile.tier),
            )
            self.sessions[acc_id] = sess

        await self.scrape_tokens(acc_id)

        if sess.context.status == SessionHealthStatus.HEALTHY and (sess.rotation_task is None or sess.rotation_task.done()):
            sess.rotation_task = asyncio.create_task(self._rotation_loop(acc_id))

        return sess

    async def remove_account(self, account_id: str) -> bool:
        """Cancels background tasks, removes account from active sessions, and deletes from vault."""
        sess = self.sessions.pop(account_id, None)
        if sess and sess.rotation_task and not sess.rotation_task.done():
            sess.rotation_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sess.rotation_task

        deleted = False
        if self.vault:
            deleted = self.vault.delete_account(account_id)
            self.active_account_id = self.vault.get_active_account_id()
        elif self.sessions:
            self.active_account_id = next(iter(self.sessions.keys()))

        return deleted or (sess is not None)

    def mark_cooldown(
        self,
        account_id: str,
        model_id: str | None = None,
        duration_seconds: float = 3600.0,
        reason: str | None = None,
    ) -> None:
        """Marks account (or specific model on account) in cooldown."""
        expire_time = time.time() + duration_seconds
        sess = self.get_account_session(account_id)
        if sess:
            if model_id:
                sess.cooldown_models[model_id] = expire_time
            else:
                sess.cooldown_until = expire_time
            if reason:
                sess.last_error = reason

        if self.vault:
            prof = self.vault.get_account(account_id)
            if prof and not model_id:
                prof.cooldown_until = expire_time
                prof.cooldown_reason = reason
                self.vault.save_account(prof)

    def is_in_cooldown(self, account_id: str, model_id: str | None = None) -> bool:
        """Checks if account or model is currently in active cooldown."""
        now = time.time()
        sess = self.get_account_session(account_id)
        if not sess:
            return False
        if sess.cooldown_until and sess.cooldown_until > now:
            return True
        if model_id and sess.cooldown_models.get(model_id, 0) > now:
            return True
        return False

    def get_failover_account(
        self,
        target_model_id: str,
        failed_account_id: str,
        required_tier: str = "free",
    ) -> AccountSession | None:
        """
        Selects next healthy backup account eligible for model/tier execution
        based on priority ranking and cooldown checks.
        """
        tier_weights = {"free": 1, "pro": 2, "ultra": 3}
        min_weight = tier_weights.get(required_tier.lower(), 1)

        failover_cfg = self.vault.get_failover_config() if self.vault else None
        if failover_cfg and not failover_cfg.enabled:
            return None

        candidates: List[AccountSession] = []
        now = time.time()

        for acc_id, sess in self.sessions.items():
            if acc_id == failed_account_id:
                continue
            if sess.context.status != SessionHealthStatus.HEALTHY:
                continue
            if sess.cooldown_until and sess.cooldown_until > now:
                continue
            if sess.cooldown_models.get(target_model_id, 0) > now:
                continue

            acc_tier = getattr(sess.profile, "tier", sess.context.account_tier or "free")
            acc_weight = tier_weights.get(str(acc_tier).lower(), 1)
            if acc_weight >= min_weight:
                candidates.append(sess)

        if not candidates:
            return None

        # 1. Fallback chain order if explicit
        if failover_cfg and failover_cfg.fallback_chain:
            for ordered_id in failover_cfg.fallback_chain:
                for c in candidates:
                    if c.account_id == ordered_id:
                        return c

        # 2. Priority ranking: highest priority, then highest tier, then least recently used
        candidates.sort(
            key=lambda s: (
                getattr(s.profile, "priority", 1),
                tier_weights.get(str(getattr(s.profile, "tier", "free")).lower(), 1),
                -getattr(s.profile, "last_used_at", 0.0),
            ),
            reverse=True,
        )
        return candidates[0]

    async def close(self) -> None:
        """Cancels all background tasks across sessions and releases HTTP resources."""
        for sess in self.sessions.values():
            if sess.rotation_task and not sess.rotation_task.done():
                sess.rotation_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sess.rotation_task
                sess.rotation_task = None

        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()
