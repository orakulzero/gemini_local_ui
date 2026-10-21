# Architecture & Implementation Plan: Continuous Auth & Passive Cookie Harvesting

## 1. Root Cause Analysis Matrix (Authentication Loss Scenarios)

Analysis of the session management architecture across `session.py`, `client.py`, `vault.py`, `main.py`, and `accounts.py` identifies five critical vectors where a live Google Gemini (Ultra) session with valid master cookies (`__Secure-1PSID`, `__Secure-3PSID`) is falsely marked as `expired`/`unauthenticated` or irreversibly corrupted in storage.

| # | Failure Scenario | Modules | Failure Mechanism | Session Impact |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **Vault Poisoning via Passive Cookie Harvesting (Cookie Poisoning)** | `session.py`, `vault.py` | On a guest response or redirect, Google sends `Set-Cookie` headers with empty values (`__Secure-1PSID=; Max-Age=0` or `Expires=1970...`) or anonymous cookies (`NID`). Unconditional dumping of `client.cookies` into `vault.py` overwrites valid master cookies. | **Fatal:** Master cookies are permanently wiped on disk in the DPAPI Vault. |
| **2** | **HTTP 302 Redirect Trap (`ServiceLogin` vs `RotateCookiesPage`)** | `session.py`, `client.py` | Unconditional `follow_redirects=True` during a cross-domain redirect (`gemini.google.com` $\rightarrow$ `accounts.google.com/ServiceLogin`) loads the login HTML page (HTTP 200 without `SNlM0e`) and captures clearing `Set-Cookie` headers from the intermediate `302` response. | **Critical:** Parser fails to find `SNlM0e`, assumes the session is dead, and marks the account `unauthenticated`. |
| **3** | **Transient Network Failures & Proxy/TLS Flapping** | `session.py`, `client.py`, `accounts.py` | `httpx.ConnectError`, `ReadTimeout`, `RemoteProtocolError`, `SSLError`, or HTTP `502`/`503`/`429` codes during initialization or health checks are caught by a broad `except Exception` block that transitions state to `expired`/`unauthenticated`. | **False Reset:** A brief network blip disables a healthy account in `accounts.py` until manual restart. |
| **4** | **`SNlM0e` / `SIDCC` Idle Drift & Expiration** | `main.py`, `session.py`, `client.py` | Master cookie `__Secure-1PSID` lasts for months, but CSRF token `SNlM0e` (`at`) and rotating cookies `__Secure-1PSIDTS` / `SIDCC` expire within hours. Without a background heartbeat worker in `main.py`, the first request after idle in `client.py` fails with RPC/HTTP 400/403. | **Service Disruption:** Request fails instead of transparently refreshing `SNlM0e` and performing a silent retry. |
| **5** | **Refresh Race Condition & Non-Atomic Writes** | `session.py`, `vault.py`, `client.py` | Under concurrent requests in `client.py`, multiple coroutines simultaneously detect a stale `SNlM0e` and trigger parallel initialization and writes in `vault.py`, overwriting a newer `__Secure-1PSIDTS` with an older one. | **Desynchronization:** Cookie-jar mismatch and corrupted vault state. |

---

## 2. Architectural Solutions & Code Patterns (Zero-Drop Session Architecture)

To achieve **zero authentication drops** while master cookies remain valid, we introduce the **Immortality Guard** architecture governed by four invariants:
1. **Master Key Immutability During Passive Rotation:** Passive harvesting from `Set-Cookie` can never delete or blank out `__Secure-1PSID` and `__Secure-3PSID`.
2. **Strict Error Taxonomy:** Network failures (`TransientAuthError`) and token expiration (`TokenStaleError`) are structurally separated from session revocation (`MasterSessionRevokedError`).
3. **Controlled Redirect Interception:** Redirects to `RotateCookiesPage` are permitted; redirects to `ServiceLogin` / `AccountChooser` are blocked before their `Set-Cookie` headers are applied.
4. **Single-Flight Refresh + Proactive Heartbeat:** Concurrent refreshes are serialized via `asyncio.Lock` with TTL checks, and a background worker in `main.py` keeps `SIDCC`, `__Secure-1PSIDTS`, and `SNlM0e` fresh.

---

### 2.1. Module `vault.py`: Non-Destructive Merge & Master Cookie Protection

In `vault.py`, we enforce **Master Cookie Pinning** and atomic writes with automatic `.bak` backups. Even if external code attempts to persist a dictionary with empty or missing master cookies during passive updates, `vault.py` blocks the degradation.

```python
# Changes for vault.py
import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Critical master cookies that MUST NEVER be deleted or overwritten with empty values during passive rotation
CRITICAL_MASTER_COOKIES = frozenset({
    "__Secure-1PSID",
    "__Secure-3PSID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
})

# Rotatable session cookies updated passively
ROTATABLE_SESSION_COOKIES = frozenset({
    "__Secure-1PSIDTS",
    "__Secure-3PSIDTS",
    "SIDCC",
    "__Secure-1PSIDCC",
    "__Secure-3PSIDCC",
    "NID",
    "AEC",
})

INVALID_COOKIE_VALUES = frozenset({"", "deleted", "null", "none", "expired"})


def is_valid_cookie_value(value: Optional[str]) -> bool:
    """Checks that a cookie value is neither a deletion marker nor an empty string."""
    if not value or not isinstance(value, str):
        return False
    cleaned = value.strip()
    if len(cleaned) < 4 or cleaned.lower() in INVALID_COOKIE_VALUES:
        return False
    return True


class SafeCookieVaultMixin:
    """
    Safe cookie merge and atomic persistence pattern for DPAPI Vault.
    Integrates into the existing Vault class in vault.py.
    """
    def __init__(self, vault_path: Path):
        self.vault_path = vault_path
        self.backup_path = vault_path.with_suffix(vault_path.suffix + ".bak")
        self._io_lock = asyncio.Lock()

    @staticmethod
    def merge_cookies_safely(
        existing_cookies: Dict[str, str],
        incoming_cookies: Dict[str, str],
        allow_master_override: bool = False,
    ) -> Dict[str, str]:
        """
        Non-Destructive Cookie Merge.
        Guarantees preservation of __Secure-1PSID / __Secure-3PSID during passive harvesting.
        """
        merged = dict(existing_cookies)

        for name, new_val in incoming_cookies.items():
            # 1. Reject empty values and deletion directives (Max-Age=0 / deleted)
            if not is_valid_cookie_value(new_val):
                if name in CRITICAL_MASTER_COOKIES:
                    logger.warning(
                        "VaultGuard: Blocked attempt to wipe master cookie %s with empty value!",
                        name,
                    )
                continue

            # 2. Protect master cookies during passive rotation
            if name in CRITICAL_MASTER_COOKIES and name in merged:
                if not allow_master_override and merged[name] != new_val:
                    logger.info(
                        "VaultGuard: Ignoring passive overwrite of master cookie %s (allow_master_override=False).",
                        name,
                    )
                    continue

            merged[name] = new_val.strip()

        return merged

    async def update_account_cookies_atomic(
        self,
        account_id: str,
        incoming_cookies: Dict[str, str],
        allow_master_override: bool = False,
    ) -> Dict[str, str]:
        """
        Thread-safe and coroutine-safe account cookie update
        with integrity verification and .bak backup creation.
        """
        async with self._io_lock:
            current_data = self._load_all_decrypted()  # existing DPAPI read method
            account_entry = current_data.get(account_id, {})
            existing_cookies = account_entry.get("cookies", {})

            merged_cookies = self.merge_cookies_safely(
                existing_cookies=existing_cookies,
                incoming_cookies=incoming_cookies,
                allow_master_override=allow_master_override,
            )

            # Invariant: never persist state if a previously present __Secure-1PSID disappeared
            if "__Secure-1PSID" in existing_cookies and "__Secure-1PSID" not in merged_cookies:
                logger.error("VaultGuard: Critical __Secure-1PSID invariant violation! Rolling back operation.")
                return existing_cookies

            if merged_cookies == existing_cookies:
                return existing_cookies

            # Create backup before write if current master cookies are valid
            if self.vault_path.exists() and is_valid_cookie_value(existing_cookies.get("__Secure-1PSID")):
                shutil.copy2(self.vault_path, self.backup_path)

            account_entry["cookies"] = merged_cookies
            current_data[account_id] = account_entry

            # Atomic write via temporary file
            tmp_path = self.vault_path.with_suffix(self.vault_path.suffix + ".tmp")
            encrypted_bytes = self._encrypt_payload(json.dumps(current_data).encode("utf-8"))
            tmp_path.write_bytes(encrypted_bytes)
            os.replace(tmp_path, self.vault_path)

            return merged_cookies
```

---

### 2.2. Module `session.py`: `Set-Cookie` Filtering, 302 Interception & Single-Flight Refresh

In `session.py`, three vulnerabilities are eliminated simultaneously:
1. **Poisoning Protection via HTTP Hooks:** `Set-Cookie` headers are parsed manually from `httpx.Response.headers.get_list("set-cookie")`. If a response is a redirect to a login page (`ServiceLogin`, `InteractiveLogin`, `AccountChooser`) or contains `Max-Age=0` / `Expires=1970`, those cookies are discarded and **restored in in-memory `client.cookies`** so `httpx` does not wipe its own in-memory session.
2. **Controlled Redirects:** Instead of blind `follow_redirects=True`, the target `Location` is inspected. Redirect hops are permitted only across trusted endpoints (`gemini.google.com`, `RotateCookiesPage`, `CheckCookie`).
3. **Single-Flight Lock (`_refresh_lock`) & Token Cache TTL:** Eliminates refresh storms under concurrent load.

```python
# Changes for session.py
import asyncio
import re
import time
import logging
from enum import Enum
from http.cookies import SimpleCookie
from typing import Dict, Optional, Tuple
import httpx

from .vault import is_valid_cookie_value, CRITICAL_MASTER_COOKIES, ROTATABLE_SESSION_COOKIES

logger = logging.getLogger(__name__)


class SessionHealthState(str, Enum):
    AUTHENTICATED = "authenticated"      # Session active, SNlM0e valid
    TOKEN_STALE = "token_stale"          # SNlM0e / SIDCC refresh needed (master cookies alive)
    DEGRADED_NET = "degraded_network"    # Transient network failure (session is NOT reset!)
    QUARANTINED = "quarantined"          # Suspected master cookie revocation (requires verification)


class TransientSessionError(Exception):
    """Network failure, timeout, or HTTP 5xx/429. Strictly NOT an authentication reset."""


class TokenStaleError(Exception):
    """Expired SNlM0e or SIDCC. Requires silent refresh without account invalidation."""


class MasterCookieRevokedError(Exception):
    """Confirmed redirect to ServiceLogin during refresh attempt."""


LOGIN_REDIRECT_PATTERNS = (
    "ServiceLogin",
    "InteractiveLogin",
    "AccountChooser",
    "signin/v2",
    "PassiveLogin",
)

SNLM0E_REGEX = re.compile(r'"SNlM0e"\s*:\s*"([^"]+)"')
BL_REGEX = re.compile(r'"cfb2h"\s*:\s*"([^"]+)"')
FSID_REGEX = re.compile(r'"FdrFJe"\s*:\s*"([^"]+)"')


class ImmortalGeminiSession:
    """
    Resilient session manager for session.py with authentication loss protection.
    """
    TOKEN_TTL_SECONDS = 1200  # Proactive refresh every 20 minutes
    MIN_REFRESH_COOLDOWN = 5.0  # Duplicate refresh guard (Single-Flight window)

    def __init__(self, account_id: str, initial_cookies: Dict[str, str], vault):
        self.account_id = account_id
        self.vault = vault
        self._cookies: Dict[str, str] = dict(initial_cookies)
        self.snlm0e: Optional[str] = None
        self.bl: Optional[str] = None
        self.fsid: Optional[str] = None

        self.state: SessionHealthState = SessionHealthState.TOKEN_STALE
        self.last_refresh_ts: float = 0.0
        self.consecutive_auth_failures: int = 0

        self._refresh_lock = asyncio.Lock()
        self._client: Optional[httpx.AsyncClient] = None

    def _is_login_redirect(self, response: httpx.Response) -> bool:
        """Determines whether a 302 redirect points to an authentication login page."""
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location", "")
            return any(pattern in location for pattern in LOGIN_REDIRECT_PATTERNS)
        return False

    def _extract_safe_rotated_cookies(self, response: httpx.Response) -> Dict[str, str]:
        """
        Extracts only valid rotatable cookies from Set-Cookie headers.
        Blocks session poisoning from guest responses, login redirects, and Max-Age=0 directives.
        """
        if self._is_login_redirect(response):
            logger.warning(
                "[%s] Intercepted login redirect (%s). Discarding all Set-Cookie headers from this response.",
                self.account_id,
                response.headers.get("location"),
            )
            return {}

        safe_updates: Dict[str, str] = {}
        raw_set_cookies = response.headers.get_list("set-cookie")

        for raw_header in raw_set_cookies:
            parsed = SimpleCookie()
            try:
                parsed.load(raw_header)
            except Exception:
                continue

            for morsel_name, morsel in parsed.items():
                val = morsel.value
                max_age = morsel.get("max-age", "")
                expires = morsel.get("expires", "")

                # Ignore cookies expiring in the past or with zero Max-Age
                if str(max_age).strip() == "0" or "1970" in str(expires):
                    logger.debug("[%s] Ignoring cookie deletion for %s (Max-Age=0/1970).", self.account_id, morsel_name)
                    continue

                if not is_valid_cookie_value(val):
                    continue

                # Allow passive updates of rotatable cookies and valid __Secure-* cookies
                if morsel_name in ROTATABLE_SESSION_COOKIES or morsel_name in CRITICAL_MASTER_COOKIES:
                    # Only update master cookie if its length matches Google's format (>40 chars)
                    if morsel_name in CRITICAL_MASTER_COOKIES and len(val) < 40:
                        continue
                    safe_updates[morsel_name] = val

        return safe_updates

    async def _on_response_hook(self, response: httpx.Response) -> None:
        """
        Event hook for httpx.AsyncClient: protects in-memory jar from corruption
        and syncs valid rotations (__Secure-1PSIDTS, SIDCC) into the Vault.
        """
        safe_updates = self._extract_safe_rotated_cookies(response)

        # Restore master cookies in httpx in-memory jar if server sent a clearing header
        if self._client is not None:
            for master_key in CRITICAL_MASTER_COOKIES:
                if master_key in self._cookies:
                    current_jar_val = self._client.cookies.get(master_key)
                    if not is_valid_cookie_value(current_jar_val):
                        self._client.cookies.set(master_key, self._cookies[master_key], domain=".google.com")

        if safe_updates:
            self._cookies.update(safe_updates)
            if self._client is not None:
                for k, v in safe_updates.items():
                    self._client.cookies.set(k, v, domain=".google.com")
            await self.vault.update_account_cookies_atomic(
                self.account_id,
                safe_updates,
                allow_master_override=False,
            )

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            jar = httpx.Cookies()
            for k, v in self._cookies.items():
                if is_valid_cookie_value(v):
                    jar.set(k, v, domain=".google.com")

            self._client = httpx.AsyncClient(
                cookies=jar,
                follow_redirects=False,  # Control 302 redirects manually!
                timeout=httpx.Timeout(connect=10.0, read=30.0, write=15.0, pool=10.0),
                event_hooks={"response": [self._on_response_hook]},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                    "Origin": "https://gemini.google.com",
                    "Referer": "https://gemini.google.com/",
                },
            )
        return self._client

    def is_token_fresh(self) -> bool:
        return (
            self.snlm0e is not None
            and self.state == SessionHealthState.AUTHENTICATED
            and (time.monotonic() - self.last_refresh_ts) < self.TOKEN_TTL_SECONDS
        )

    async def ensure_authenticated(self, force: bool = False) -> str:
        """
        Single-Flight method for obtaining a fresh SNlM0e token.
        Guarantees concurrent requests do not trigger a refresh race condition.
        """
        if not force and self.is_token_fresh():
            return self.snlm0e  # type: ignore[return-value]

        async with self._refresh_lock:
            now = time.monotonic()
            # Double-checked locking: if a sibling coroutine just refreshed the token
            if not force and self.is_token_fresh():
                return self.snlm0e  # type: ignore[return-value]
            if force and self.snlm0e and (now - self.last_refresh_ts) < self.MIN_REFRESH_COOLDOWN:
                return self.snlm0e

            return await self._perform_controlled_handshake()

    async def _perform_controlled_handshake(self) -> str:
        """
        Performs a controlled traversal of https://gemini.google.com/app handling
        RotateCookiesPage and protecting against false resets on network failures.
        """
        client = await self.get_client()
        url = "https://gemini.google.com/app"
        max_hops = 4

        try:
            for _ in range(max_hops):
                resp = await client.get(url)

                if resp.status_code in (301, 302, 303, 307, 308):
                    if self._is_login_redirect(resp):
                        self.consecutive_auth_failures += 1
                        if self.consecutive_auth_failures >= 3:
                            self.state = SessionHealthState.QUARANTINED
                        raise MasterCookieRevokedError(
                            f"[{self.account_id}] Redirected to login page: {resp.headers.get('location')}"
                        )

                    # Permitted redirect (e.g., RotateCookiesPage or regional routing)
                    next_url = resp.headers.get("location", "")
                    if not next_url:
                        break
                    url = str(resp.url.join(next_url))
                    continue

                if resp.status_code >= 500 or resp.status_code == 429:
                    self.state = SessionHealthState.DEGRADED_NET
                    raise TransientSessionError(f"HTTP {resp.status_code} from Google during handshake")

                html = resp.text
                snlm0e_match = SNLM0E_REGEX.search(html)
                if snlm0e_match:
                    self.snlm0e = snlm0e_match.group(1)
                    bl_match = BL_REGEX.search(html)
                    fsid_match = FSID_REGEX.search(html)
                    if bl_match:
                        self.bl = bl_match.group(1)
                    if fsid_match:
                        self.fsid = fsid_match.group(1)

                    self.state = SessionHealthState.AUTHENTICATED
                    self.last_refresh_ts = time.monotonic()
                    self.consecutive_auth_failures = 0
                    return self.snlm0e

                # Received 200 OK, but SNlM0e was not present in response body
                break

            # If SNlM0e was not found in HTML, DO NOT delete cookies; signal stale token / quarantine
            self.consecutive_auth_failures += 1
            if self.consecutive_auth_failures >= 3:
                self.state = SessionHealthState.QUARANTINED
            else:
                self.state = SessionHealthState.TOKEN_STALE
            raise TokenStaleError(f"[{self.account_id}] Failed to extract SNlM0e (attempt #{self.consecutive_auth_failures})")

        except (httpx.TransportError, httpx.TimeoutException) as exc:
            # CRITICAL: on network failure, preserve existing SNlM0e and DO NOT reset authentication!
            self.state = SessionHealthState.DEGRADED_NET
            logger.warning(
                "[%s] Transient network failure (%s). Session preserved.",
                self.account_id,
                type(exc).__name__,
            )
            raise TransientSessionError(str(exc)) from exc

    async def heartbeat_rotate_cookies(self) -> bool:
        """
        Background rotation of SIDCC / __Secure-1PSIDTS and SNlM0e freshness check.
        Used by the background worker in main.py.
        """
        try:
            await self.ensure_authenticated(force=True)
            return True
        except TransientSessionError:
            # Network error during background ping is completely safe
            return False
        except (TokenStaleError, MasterCookieRevokedError) as exc:
            logger.warning("[%s] Background heartbeat warning: %s", self.account_id, exc)
            return False
```

---

### 2.3. Module `client.py`: Transparent Self-Healing Retry on Stale `SNlM0e` / `SIDCC`

In `client.py`, every request to `StreamGenerate` / `batchexecute` is wrapped in a two-stage pipeline: if Google rejects the request due to an expired `at` (`SNlM0e`) or stale `SIDCC` (HTTP `400/401/403`, redirect `302`, or empty response without a valid frame), the client automatically invokes `await session.ensure_authenticated(force=True)` and transparently retries the request.

```python
# Changes for client.py
import asyncio
import logging
from typing import Any, Dict
import httpx

from .session import (
    ImmortalGeminiSession,
    TransientSessionError,
    TokenStaleError,
    MasterCookieRevokedError,
)

logger = logging.getLogger(__name__)


class ResilientGeminiClient:
    """
    RPC execution wrapper in client.py with automatic SNlM0e self-healing.
    """
    def __init__(self, session: ImmortalGeminiSession):
        self.session = session

    def _is_auth_or_csrf_rejection(self, response: httpx.Response, body_text: str) -> bool:
        """Determines whether Google rejected the request due to an expired SNlM0e (at) or SIDCC."""
        if response.status_code in (401, 403, 302):
            return True
        # When SNlM0e expires, Google often returns 400 Bad Request or a short XSSI response without data
        if response.status_code == 400 and ("SNlM0e" in body_text or len(body_text) < 120):
            return True
        return False

    async def execute_with_self_healing(
        self,
        endpoint: str,
        payload_builder,
        max_retries: int = 2,
    ) -> httpx.Response:
        """
        Executes a POST request to the Gemini API.
        On SNlM0e expiration, performs a silent refresh and retries with the new token.
        """
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            force_refresh = attempt > 0
            try:
                at_token = await self.session.ensure_authenticated(force=force_refresh)
                http_client = await self.session.get_client()

                params, data = payload_builder(
                    at_token=at_token,
                    bl=self.session.bl,
                    fsid=self.session.fsid,
                )

                response = await http_client.post(endpoint, params=params, data=data)
                body_preview = response.text[:500] if response.status_code != 200 else ""

                if self._is_auth_or_csrf_rejection(response, body_preview):
                    if attempt < max_retries:
                        logger.info(
                            "[%s] Detected stale token (HTTP %s). Triggering silent refresh (attempt %d/%d)...",
                            self.session.account_id,
                            response.status_code,
                            attempt + 1,
                            max_retries,
                        )
                        continue
                    raise TokenStaleError(f"Repeated auth rejection after refresh: HTTP {response.status_code}")

                if response.status_code in (429, 502, 503, 504):
                    raise TransientSessionError(f"Transient HTTP status {response.status_code}")

                response.raise_for_status()
                return response

            except (httpx.TransportError, httpx.TimeoutException, TransientSessionError) as exc:
                last_exc = exc
                if attempt < max_retries:
                    backoff = 0.5 * (2 ** attempt)
                    logger.warning(
                        "[%s] Network error in client.py (%s). Retrying in %.1fs (session remains active).",
                        self.session.account_id,
                        exc,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                raise TransientSessionError(f"Connection retries exhausted: {exc}") from exc

            except (TokenStaleError, MasterCookieRevokedError) as exc:
                last_exc = exc
                if attempt < max_retries and isinstance(exc, TokenStaleError):
                    await asyncio.sleep(0.3)
                    continue
                raise

        raise RuntimeError(f"Failed to execute request: {last_exc}")
```

---

### 2.4. Modules `accounts.py` & `main.py`: Account Pool Isolation & Background `SessionKeeper`

In `accounts.py`, we eliminate irreversible marking of accounts as `expired` on a single failure. Even if an account enters `QUARANTINED` (after 3 consecutive confirmed redirects to `ServiceLogin`), its cookies in `vault.py` remain untouched, and the background worker in `main.py` periodically probes for recovery.

```python
# Changes for main.py (Lifespan Background Session Keeper)
import asyncio
import logging
import random
from contextlib import asynccontextmanager
from fastapi import FastAPI

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 900  # 15 minutes


async def session_keeper_loop(account_manager, stop_event: asyncio.Event) -> None:
    """
    Background session keeper coroutine for main.py.
    Maintains SNlM0e freshness and rotates __Secure-1PSIDTS / SIDCC 24/7.
    """
    logger.info("SessionKeeper: Background Gemini session keeper started.")
    while not stop_event.is_set():
        # Small jitter to avoid synchronized spikes
        jitter = random.uniform(5.0, 25.0)
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=HEARTBEAT_INTERVAL_SECONDS + jitter,
            )
            break
        except asyncio.TimeoutError:
            pass

        for session in account_manager.get_all_sessions():
            if stop_event.is_set():
                break
            ok = await session.heartbeat_rotate_cookies()
            logger.debug(
                "SessionKeeper: Heartbeat for %s completed (ok=%s, state=%s)",
                session.account_id,
                ok,
                session.state.value,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    account_manager = app.state.account_manager

    # Initial soft startup initialization (network error at startup does not crash the server)
    for session in account_manager.get_all_sessions():
        try:
            await session.ensure_authenticated(force=True)
        except Exception as exc:
            logger.warning(
                "Lifespan: Startup initialization for %s deferred (%s). Account remains in pool.",
                session.account_id,
                exc,
            )

    keeper_task = asyncio.create_task(session_keeper_loop(account_manager, stop_event))
    try:
        yield
    finally:
        stop_event.set()
        keeper_task.cancel()
        await asyncio.gather(keeper_task, return_exceptions=True)
```

---

## 3. Milestone M11 Change Checklist & Unit Tests

### File-by-File Change Checklist for M11

1. **`vault.py`:**
   * Add `CRITICAL_MASTER_COOKIES` and `ROTATABLE_SESSION_COOKIES` constants.
   * Integrate `merge_cookies_safely(..., allow_master_override=False)` across all passive cookie persistence paths.
   * Add atomic write via `.tmp` + `os.replace()` and `.bak` backup creation.
2. **`session.py`:**
   * Disable `follow_redirects=True` in `httpx.AsyncClient` in favor of the controlled `_perform_controlled_handshake()` loop.
   * Replace direct `client.cookies` reads with `_extract_safe_rotated_cookies(response)` filtering `Max-Age=0`, `1970`, and `ServiceLogin` redirects.
   * Force restoration of master cookies in `client.cookies` inside `_on_response_hook` if the server sent a deletion header.
   * Add `asyncio.Lock` (`_refresh_lock`) to `ensure_authenticated()` and separate exceptions into `TransientSessionError`, `TokenStaleError`, and `MasterCookieRevokedError`.
3. **`client.py`:**
   * Wrap `StreamGenerate` and `batchexecute` calls in `execute_with_self_healing()` with automatic `ensure_authenticated(force=True)` retry on HTTP `400/401/403/302`.
4. **`accounts.py` and `main.py`:**
   * Prevent transitioning an account to permanent `expired` on `TransientSessionError` or single `TokenStaleError` events.
   * Register `session_keeper_loop` in `lifespan` in `main.py` with a 15-minute interval.

---

### Unit Tests for Milestone M11 (`tests/test_m11_session_immortality.py`)

Below is the complete `pytest` suite verifying all 5 session protection scenarios.

```python
# tests/test_m11_session_immortality.py
import asyncio
import pytest
import httpx

# Imports of tested components
from backend.app.bridge.vault import SafeCookieVaultMixin, is_valid_cookie_value
from backend.app.bridge.session import (
    ImmortalGeminiSession,
    SessionHealthState,
    TransientSessionError,
)
from backend.app.bridge.client import ResilientGeminiClient


class DummyVault:
    def __init__(self, initial_cookies: dict[str, str]):
        self.saved_cookies = dict(initial_cookies)
        self.update_calls = 0

    async def update_account_cookies_atomic(
        self, account_id: str, incoming_cookies: dict[str, str], allow_master_override: bool = False
    ) -> dict[str, str]:
        self.update_calls += 1
        self.saved_cookies = SafeCookieVaultMixin.merge_cookies_safely(
            self.saved_cookies, incoming_cookies, allow_master_override=allow_master_override
        )
        return self.saved_cookies


MASTER_1PSID = "g.a000mQ_valid_ultra_master_cookie_value_1234567890_abcdef"
MASTER_3PSID = "g.a000mQ_valid_ultra_3psid_cookie_value_1234567890_abcdef"


@pytest.mark.asyncio
async def test_m11_vault_blocks_master_cookie_wipe_and_guest_poisoning():
    """
    Test 1: Verifies that passive cookie harvesting from a guest response or Max-Age=0
    NEVER overwrites __Secure-1PSID and __Secure-3PSID in the Vault.
    """
    existing = {
        "__Secure-1PSID": MASTER_1PSID,
        "__Secure-3PSID": MASTER_3PSID,
        "__Secure-1PSIDTS": "old_ts_value_111",
    }
    poisoned_incoming = {
        "__Secure-1PSID": "",  # attempt to wipe master cookie
        "__Secure-3PSID": "deleted",  # deletion marker
        "__Secure-1PSIDTS": "new_rotated_ts_value_222",
        "NID": "guest_nid_cookie_999",
    }

    merged = SafeCookieVaultMixin.merge_cookies_safely(
        existing_cookies=existing,
        incoming_cookies=poisoned_incoming,
        allow_master_override=False,
    )

    assert merged["__Secure-1PSID"] == MASTER_1PSID
    assert merged["__Secure-3PSID"] == MASTER_3PSID
    assert merged["__Secure-1PSIDTS"] == "new_rotated_ts_value_222"
    assert merged["NID"] == "guest_nid_cookie_999"


@pytest.mark.asyncio
async def test_m11_302_service_login_redirect_ignores_poisoned_set_cookie():
    """
    Test 2: If Google returns a 302 redirect to ServiceLogin with Set-Cookie: __Secure-1PSID=; Max-Age=0,
    the session must reject those headers and preserve the master cookie in memory and in the Vault.
    """
    initial = {"__Secure-1PSID": MASTER_1PSID, "__Secure-1PSIDTS": "valid_ts_12345"}
    vault = DummyVault(initial)
    session = ImmortalGeminiSession("ultra_acc_1", initial, vault)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=302,
            headers=[
                ("Location", "https://accounts.google.com/ServiceLogin?elo=1"),
                ("Set-Cookie", "__Secure-1PSID=; Max-Age=0; Path=/; Domain=.google.com"),
                ("Set-Cookie", "__Secure-1PSIDTS=deleted; Expires=Thu, 01 Jan 1970 00:00:00 GMT"),
            ],
        )

    client = await session.get_client()
    client._transport = httpx.MockTransport(mock_handler)

    with pytest.raises(Exception):
        await session.ensure_authenticated(force=True)

    # Verify master cookies remained intact in session, httpx jar, and Vault
    assert session._cookies["__Secure-1PSID"] == MASTER_1PSID
    assert client.cookies.get("__Secure-1PSID") == MASTER_1PSID
    assert vault.saved_cookies["__Secure-1PSID"] == MASTER_1PSID


@pytest.mark.asyncio
async def test_m11_transient_network_failure_preserves_session_and_token():
    """
    Test 3: Transient network errors (ReadTimeout, ConnectError, HTTP 503)
    do not reset the session to expired and preserve the previously obtained SNlM0e.
    """
    initial = {"__Secure-1PSID": MASTER_1PSID}
    vault = DummyVault(initial)
    session = ImmortalGeminiSession("ultra_acc_1", initial, vault)
    session.snlm0e = "existing_valid_snlm0e_token:12345"
    session.state = SessionHealthState.AUTHENTICATED

    def failing_network_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Proxy read timeout", request=request)

    client = await session.get_client()
    client._transport = httpx.MockTransport(failing_network_handler)

    with pytest.raises(TransientSessionError):
        await session.ensure_authenticated(force=True)

    assert session.state == SessionHealthState.DEGRADED_NET
    assert session.snlm0e == "existing_valid_snlm0e_token:12345"
    assert session.consecutive_auth_failures == 0


@pytest.mark.asyncio
async def test_m11_single_flight_concurrent_snlm0e_refresh():
    """
    Test 4: When 10 coroutines simultaneously access a stale session,
    exactly ONE network request is sent to Google for a new SNlM0e.
    """
    initial = {"__Secure-1PSID": MASTER_1PSID}
    vault = DummyVault(initial)
    session = ImmortalGeminiSession("ultra_acc_1", initial, vault)

    network_calls = 0

    async def slow_google_app_handler(request: httpx.Request) -> httpx.Response:
        nonlocal network_calls
        network_calls += 1
        await asyncio.sleep(0.05)
        html = '<html><script>WIZ_global_data = {"SNlM0e":"fresh_token_m11","cfb2h":"boq_assistant"};</script></html>'
        return httpx.Response(
            status_code=200,
            text=html,
            headers=[("Set-Cookie", "__Secure-1PSIDTS=rotated_ts_99999; Path=/; Domain=.google.com")],
        )

    client = await session.get_client()
    client._transport = httpx.MockTransport(slow_google_app_handler)

    results = await asyncio.gather(*(session.ensure_authenticated() for _ in range(10)))

    assert all(token == "fresh_token_m11" for token in results)
    assert network_calls == 1
    assert vault.saved_cookies["__Secure-1PSIDTS"] == "rotated_ts_99999"


@pytest.mark.asyncio
async def test_m11_client_self_healing_retry_on_stale_snlm0e():
    """
    Test 5: If StreamGenerate returns 401/403 due to an expired SNlM0e during operation,
    client.py automatically refreshes SNlM0e and transparently retries the request.
    """
    initial = {"__Secure-1PSID": MASTER_1PSID}
    vault = DummyVault(initial)
    session = ImmortalGeminiSession("ultra_acc_1", initial, vault)
    session.snlm0e = "stale_snlm0e_token"
    session.state = SessionHealthState.AUTHENTICATED
    session.last_refresh_ts = 1.0  # old timestamp

    post_attempts = 0

    def router_handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_attempts
        if request.method == "GET" and "gemini.google.com/app" in str(request.url):
            return httpx.Response(
                status_code=200,
                text='<script>{"SNlM0e":"regenerated_snlm0e_token_777"}</script>',
            )
        if request.method == "POST":
            post_attempts += 1
            body_str = request.content.decode("utf-8")
            if "stale_snlm0e_token" in body_str:
                return httpx.Response(status_code=403, text="CSRF / SNlM0e token expired")
            if "regenerated_snlm0e_token_777" in body_str:
                return httpx.Response(status_code=200, text=")]}'\n\n[[[\"wrb.fr\",null,\"OK\"]]]")
        return httpx.Response(status_code=500)

    http_client = await session.get_client()
    http_client._transport = httpx.MockTransport(router_handler)

    gemini_client = ResilientGeminiClient(session)
    resp = await gemini_client.execute_with_self_healing(
        endpoint="https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
        payload_builder=lambda at_token, bl, fsid: ({"bl": bl or ""}, {"at": at_token, "f.req": "[]"}),
    )

    assert resp.status_code == 200
    assert post_attempts == 2
    assert session.snlm0e == "regenerated_snlm0e_token_777"
```
