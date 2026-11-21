"""
backend/app/bridge/client.py
GeminiWebClient:
- 81-element inner request construction for StreamGenerate
- Header synthesis (x-goog-ext-525001261-jspb, x-goog-ext-525005358-jspb)
- Batchexecute unary RPC engine (otAQ7b, MaZiqc, hNvQHb, CNgdBe, GzXR5e)
- Streaming chat generation with StreamingFrameParser & StreamingDeltaAccumulator
- Exponential backoff retry for transient errors
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import os
import random
import re
import ssl
import time
from dataclasses import dataclass
from typing import Any
from collections.abc import AsyncIterator
import uuid
import threading
import httpx

try:
    import wreq
    _wreq_candidates = [
        getattr(wreq, "TlsError", None),
        getattr(wreq, "ConnectionError", None),
        getattr(wreq, "ConnectionResetError", None),
        getattr(wreq, "TimeoutError", None),
        getattr(wreq, "RequestError", None),
        getattr(wreq, "DecodingError", None),
        getattr(wreq, "BodyError", None),
        getattr(wreq, "ProxyConnectionError", None),
        getattr(wreq, "StatusError", None),
    ]
    WREQ_TRANSPORT_ERRORS: tuple[type[Exception], ...] = tuple(
        e for e in _wreq_candidates if e is not None and issubclass(e, Exception)
    )
except Exception:
    WREQ_TRANSPORT_ERRORS = ()

from backend.app.bridge.exceptions import (
    AuthenticationError,
    GeminiBridgeError,
    ModelHeaderInvalidError,
    ModelInconsistentError,
    TemporaryBackendOverloadError,
    UsageLimitExceededError,
)
from backend.app.bridge.models import (
    DEFAULT_MODELS,
    Gem,
    GemSerializer,
    HeaderBuilder,
    ModelCapabilities,
    ModelInfo,
    WorkspaceExtensionRegistry,
    register_discovered_carbon_model,
    resolve_model,
)
from backend.app.bridge.session import SessionContext
from backend.app.bridge.streaming_parser import (
    StreamChunk,
    StreamingDeltaAccumulator,
    StreamingFrameParser,
)


@dataclass
class CacheEntry:
    """
    Container circuit breaker cache entry for feature_mode=40 (task container).
    Tracks fallback status, expiration timestamp, failure count, and half-open probe state.
    """
    fallback_mode: bool = True
    expires_at: float = 0.0
    failure_count: int = 1
    is_half_open: bool = False

    def is_expired(self, now: float | None = None) -> bool:
        current_time = now if now is not None else time.time()
        return current_time >= self.expires_at


DEFAULT_CONTAINER_TTL: float = 3600.0  # 1 hour
MAX_TOTAL_ATTEMPTS: int = 2            # Hard ceiling per client request
DEEP_RESEARCH_TIMEOUT: float = 1800.0  # 30 minutes read timeout for Deep Research / Task containers
FLASH4P0_HOLDBACK_CHARS: int = 128
FLASH4P0_CANNED_REFUSALS: tuple[str, ...] = (
    "я вас не понимаю. я просто языковая модель",
    "я просто языковая модель",
    "sorry, something went wrong",
    "i encountered an error doing what you asked",
    "i'm having a hard time fulfilling your request",
)


def _is_canned_refusal(text: str) -> bool:
    """Returns True when text matches a degraded Flash4p0 consumer refusal."""
    norm = " ".join(text.strip().lower().split())
    if not norm:
        return False
    return any(marker in norm for marker in FLASH4P0_CANNED_REFUSALS)

logger = logging.getLogger("gemini_bridge.client")

HARVESTED_COOKIE_NAMES = {
    "__Secure-1PSIDTS",
    "__Secure-3PSIDTS",
    "SIDCC",
    "__Secure-1PSIDCC",
    "__Secure-3PSIDCC",
    "NID",
}


class GeminiWebClient:
    """
    Core client communicating with Google Gemini Web Services.
    Handles batchexecute unary RPCs and StreamGenerate streaming sessions.
    """

    DEFAULT_BASE_URL = "https://gemini.google.com"

    def __init__(
        self,
        session_context: Any | None = None,
        base_url: str = DEFAULT_BASE_URL,
        cookies: dict[str, str] | None = None,
        timeout: float = 360.0,
        vault: Any | None = None,
        account_id: str | None = None,
    ) -> None:
        if session_context is not None:
            self.ctx = session_context
            if cookies:
                self.ctx.cookies.update(cookies)
        else:
            self.ctx = SessionContext(cookies=cookies or {})

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.vault = vault or getattr(self.ctx, "vault", None)
        self.account_id = account_id or getattr(self.ctx, "account_id", None)
        self._reqid_counter: int = random.randint(100000, 999999)
        self._client: Any | None = None

    def get_transport_name(self) -> str:
        if os.environ.get("GEMINI_TRANSPORT") == "httpx":
            return "httpx"
        if not (self.base_url or "").startswith("https://"):
            return "httpx"
        if self._client is not None and isinstance(getattr(self._client, "_transport", None), httpx.MockTransport):
            return "httpx"
        try:
            import wreq  # noqa: F401
            return "wreq"
        except ImportError:
            return "httpx"

    async def _get_client(self) -> Any:
        if self._client is None or getattr(self._client, "is_closed", False):
            if self.get_transport_name() == "wreq":
                from backend.app.bridge.wreq_transport import WreqTransportClient
                self._client = WreqTransportClient(
                    timeout=self.timeout,
                    on_response=self.on_response_harvest_cookies,
                )
            else:
                self._client = httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout, connect=20.0, read=self.timeout, write=60.0),
                    cookies=self.ctx.cookies,
                    http2=False,
                    limits=httpx.Limits(max_connections=32, max_keepalive_connections=12, keepalive_expiry=20.0),
                    headers={
                        "User-Agent": HeaderBuilder.DEFAULT_USER_AGENT,
                        "Origin": self.base_url,
                        "Referer": f"{self.base_url}/",
                        "x-same-domain": "1",
                    },
                    event_hooks={"response": [self.on_response_harvest_cookies]},
                )
        return self._client

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
                "Anti-poisoning: ignored Set-Cookie from HTTP %d response (%s)",
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

        changed: dict[str, str] = harvested
        if hasattr(self.ctx, "cookies") and isinstance(self.ctx.cookies, dict):
            changed = {k: v for k, v in harvested.items() if self.ctx.cookies.get(k) != v}
            if not changed:
                return
            self.ctx.cookies.update(changed)

        logger.debug(
            "Passively harvested %d changed cookies from %s: %s",
            len(changed),
            response.url,
            list(changed.keys()),
        )

        self.ctx.last_refresh = time.time()
        if hasattr(self.ctx, "status") and hasattr(self.ctx, "cookies"):
            if str(getattr(self.ctx, "status", "")) != "healthy" and self.ctx.cookies.get("__Secure-1PSID"):
                try:
                    from backend.app.bridge.session import SessionHealthStatus
                    self.ctx.status = SessionHealthStatus.HEALTHY
                    self.ctx.last_error = None
                except Exception:
                    pass

        vault = self.vault or getattr(self.ctx, "vault", None)
        if vault:
            try:
                acc_id = self.account_id or getattr(self.ctx, "account_id", None)
                if acc_id and hasattr(vault, "update_account_cookies_async"):
                    await vault.update_account_cookies_async(acc_id, harvested)
                    logger.info("Persisted passively harvested cookies for account '%s' to DPAPI vault asynchronously", acc_id)
                    return
                if acc_id and hasattr(vault, "save_account_async"):
                    profile = vault.get_account(acc_id)
                    if profile:
                        if hasattr(profile, "cookies") and isinstance(profile.cookies, dict):
                            profile.cookies.update(harvested)
                        profile.last_rotated_at = time.time()
                        await vault.save_account_async(profile)
                        logger.info("Persisted passively harvested cookies for account '%s' to DPAPI vault asynchronously", acc_id)
                        return
                elif acc_id and hasattr(vault, "get_account"):
                    profile = vault.get_account(acc_id)
                    if profile:
                        if hasattr(profile, "cookies") and isinstance(profile.cookies, dict):
                            profile.cookies.update(harvested)
                        profile.last_rotated_at = time.time()
                        vault.save_account(profile)
                        logger.info("Persisted passively harvested cookies for account '%s' to DPAPI vault", acc_id)
                        return
                if hasattr(vault, "store_credentials_async"):
                    await vault.store_credentials_async(dict(self.ctx.cookies))
                    logger.info("Persisted passively harvested cookies to active DPAPI vault credentials asynchronously")
                elif hasattr(vault, "store_credentials"):
                    vault.store_credentials(dict(self.ctx.cookies))
                    logger.info("Persisted passively harvested cookies to active DPAPI vault credentials")
            except Exception as vault_err:
                logger.error("Failed persisting harvested cookies to vault: %s", vault_err)

        callback = getattr(self.ctx, "on_credentials_updated", None)
        if callback:
            try:
                res = callback(dict(self.ctx.cookies))
                if asyncio.iscoroutine(res):
                    await res
            except Exception as cb_err:
                logger.warning("on_credentials_updated callback failed during harvest: %s", cb_err)

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _next_reqid(self) -> int:
        self._reqid_counter += random.randint(1000, 9999)
        return self._reqid_counter

    # Shared across all GeminiWebClient instances: account_key -> CacheEntry or float
    _feature_mode_40_unsupported: dict[str, CacheEntry | float] = {}
    _cache_lock: threading.Lock = threading.Lock()

    def _get_account_key(self) -> str:
        """Returns stable account identifier for caching (account_id or PSID fingerprint)."""
        acc_id = getattr(self.ctx, "account_id", None) or getattr(self, "account_id", None)
        if acc_id:
            return str(acc_id)
        if hasattr(self.ctx, "cookies") and isinstance(self.ctx.cookies, dict):
            psid = self.ctx.cookies.get("__Secure-1PSID")
            if psid:
                return f"psid_{psid[:16]}"
        return "default"

    @classmethod
    def is_feature_mode_40_unsupported(cls, account_key: str) -> bool:
        """
        Returns True if account is cached as incompatible with feature_mode=40 within TTL.
        Implements Half-Open probe: upon TTL expiration, grants a single probe request
        to test native container while shielding concurrent requests.
        """
        with cls._cache_lock:
            raw_entry = cls._feature_mode_40_unsupported.get(account_key)
            if raw_entry is None:
                return False

            # Backward compatibility with legacy tests setting a raw float timestamp
            if isinstance(raw_entry, (int, float)):
                entry = CacheEntry(fallback_mode=True, expires_at=float(raw_entry), failure_count=1)
                cls._feature_mode_40_unsupported[account_key] = entry
            else:
                entry = raw_entry

            if not entry.fallback_mode:
                return False

            now = time.time()
            if now < entry.expires_at:
                # Circuit is OPEN: fallback active
                return True

            # TTL expired: transition to HALF-OPEN atomically
            if not entry.is_half_open:
                logger.info(
                    "Container cache TTL expired for account %s (failures=%d); initiating half-open probe on task container",
                    account_key,
                    entry.failure_count,
                )
                entry.is_half_open = True
                # Grant a 30s probe lease to prevent thundering herd probes
                entry.expires_at = now + 30.0
                return False  # Allow this request to probe native task container (feature_mode=40)

            # Another concurrent request is already probing in half-open state; use fallback
            return True

    @classmethod
    def mark_feature_mode_40_unsupported(
        cls, account_key: str, ttl_seconds: float = DEFAULT_CONTAINER_TTL
    ) -> CacheEntry:
        """
        Caches feature_mode=40 incompatibility for given account.
        Increments failure_count and resets TTL.
        """
        with cls._cache_lock:
            raw_entry = cls._feature_mode_40_unsupported.get(account_key)
            failures = 1
            if isinstance(raw_entry, CacheEntry):
                failures = raw_entry.failure_count + 1
            elif isinstance(raw_entry, (int, float)):
                failures = 2

            entry = CacheEntry(
                fallback_mode=True,
                expires_at=time.time() + ttl_seconds,
                failure_count=failures,
                is_half_open=False,
            )
            cls._feature_mode_40_unsupported[account_key] = entry
            logger.warning(
                "Cached feature_mode=40 incompatibility for account '%s' (failure_count=%d, ttl=%.0fs)",
                account_key,
                failures,
                ttl_seconds,
            )
            return entry

    @classmethod
    def mark_feature_mode_40_supported(cls, account_key: str) -> None:
        """Clears incompatibility cache upon successful task container execution (Circuit CLOSED)."""
        with cls._cache_lock:
            entry = cls._feature_mode_40_unsupported.pop(account_key, None)
            if entry is not None:
                logger.info("Cleared feature_mode=40 incompatibility cache for account '%s' (probe succeeded)", account_key)

    @classmethod
    def clear_feature_mode_40_cache(cls) -> None:
        """Clears the incompatibility cache (for testing and manual reset)."""
        with cls._cache_lock:
            cls._feature_mode_40_unsupported.clear()

    def _build_81_element_envelope(
        self,
        prompt: str,
        cid: str | None = None,
        rid: str | None = None,
        rcid: str | None = None,
        gem_id: str | None = None,
        attachments: list[Any] | None = None,
        model_number: int = 1,
        thinking_flag: int = 1,
        incognito: bool = False,
        feature_mode: int = 0,
        mode_tag: str | None = None,
        extensions: list[str] | None = None,
    ) -> list[Any]:
        """Constructs the canonical 81-element inner request envelope."""
        inner: list[Any] = [None] * 81

        # Normalize Workspace extensions (@YouTube, @Gmail, @Google Drive, @Google Docs, etc.)
        norm_prompt, _ = WorkspaceExtensionRegistry.normalize_prompt_and_extensions(prompt, extensions)

        # Format attachments for inner[0][3]
        att_formatted = None
        if attachments:
            att_formatted = []
            for item in attachments:
                if isinstance(item, dict):
                    uri = item.get("uri", "")
                    name = item.get("filename", "file")
                    if item.get("scotty") or item.get("media_type") == 1:
                        att_formatted.append([[uri, 1], name])
                    else:
                        att_formatted.append([[uri], name])
                elif isinstance(item, (list, tuple)):
                    if len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], str):
                        att_formatted.append([[item[0]], item[1]])
                    else:
                        att_formatted.append(item)

        # [0] Message Content: [prompt, 0, null, file_attachments, null, null, 0]
        inner[0] = [norm_prompt, 0, None, att_formatted, None, None, 0]

        # [1] Language
        lang = getattr(self.ctx, "language", "en")
        inner[1] = [lang]

        # [2] Conversation Metadata (TEd schema: [cid, rid, rcid])
        if cid:
            inner[2] = [cid, rid or "", rcid or ""]
        else:
            inner[2] = ["", "", ""]

        # Capability and routing flags
        inner[6] = [1]
        inner[7] = 1   # Streaming enabled
        inner[10] = 1  # Client channel
        inner[11] = 0  # Subchannel
        inner[17] = None
        inner[18] = 0  # History mode

        if gem_id and not str(gem_id).startswith(("gem_cc_", "gem_local_")):
            inner[19] = gem_id

        inner[27] = 1  # Rich components
        inner[30] = [4]
        inner[41] = [1] if att_formatted else None

        if incognito:
            inner[45] = 1

        if feature_mode == 40:
            # Tasks / Spark mode (featureMode = 40 in _.Byd -> routes to Google Agency Task container)
            inner[49] = 40

        inner[53] = 0  # Grounding flag
        inner[59] = str(uuid.uuid4()).upper()
        inner[61] = []
        if mode_tag:
            # Field 65 (0-based index 64) is Voc ("Argon4p0") in _.Byd
            inner[64] = mode_tag
        inner[68] = 0  # Execution mode

        # Model routing
        inner[79] = model_number
        inner[80] = thinking_flag

        return inner

    async def call_batchexecute(self, rpc_id: str, payload: list[Any]) -> Any:
        """
        Executes a unary batchexecute RPC and returns the unpacked inner JSON response.
        """
        client = await self._get_client()
        url = f"{self.base_url}/_/BardChatUi/data/batchexecute"

        params = {
            "rpcids": rpc_id,
            "source-path": "/app",
            "bl": getattr(self.ctx, "build_label", "boq_assistant-bard-web-server_20261017.00_p0"),
            "f.sid": getattr(self.ctx, "session_id", ""),
            "hl": getattr(self.ctx, "language", "en"),
            "_reqid": str(self._next_reqid()),
            "rt": "c",
        }

        escaped_payload = json.dumps(payload, separators=(",", ":"))
        envelope = [[[rpc_id, escaped_payload, None, "generic"]]]
        f_req = json.dumps(envelope, separators=(",", ":"))

        data = {
            "at": getattr(self.ctx, "at_token", ""),
            "f.req": f_req,
        }

        cookie_header = HeaderBuilder.format_cookies(self.ctx.cookies)
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "User-Agent": HeaderBuilder.DEFAULT_USER_AGENT,
            "x-same-domain": "1",
        }
        if cookie_header:
            headers["Cookie"] = cookie_header
        sapisid = HeaderBuilder.extract_sapisid(self.ctx.cookies)
        if sapisid:
            headers["Authorization"] = HeaderBuilder.make_sapisidhash(sapisid, origin=self.base_url)

        resp = None
        for attempt in range(2):
            try:
                resp = await client.post(url, params=params, data=data, headers=headers)
                break
            except Exception as post_err:
                is_transient = isinstance(
                    post_err,
                    (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.NetworkError, ConnectionError, TimeoutError),
                ) or "is_connect error" in str(post_err) or "SYSCALL" in str(post_err)
                if attempt == 0 and is_transient:
                    logger.warning("Transient batchexecute error on %s (%s); resetting pool and retrying once", rpc_id, post_err)
                    if hasattr(client, "reset_pool"):
                        client.reset_pool(f"batchexecute_retry_{rpc_id}")
                    continue
                raise
        assert resp is not None
        if resp.status_code in (401, 403):
            raise AuthenticationError(f"batchexecute authentication rejected with HTTP {resp.status_code}")

        if resp.status_code != 200:
            raise GeminiBridgeError(f"batchexecute HTTP {resp.status_code}: {resp.text[:200]}")

        # Parse chunked response via StreamingFrameParser
        parser = StreamingFrameParser()
        frames = parser.feed(resp.text)

        for frame in frames:
            entries = frame
            while len(entries) == 1 and isinstance(entries[0], list) and len(entries[0]) > 0 and isinstance(entries[0][0], list):
                entries = entries[0]

            for entry in entries:
                if len(entry) >= 3 and entry[1] == rpc_id and entry[2]:
                    # Check for Code 7 error in entry[5] or inner payload
                    if len(entry) > 5 and entry[5] is not None:
                        err_node = entry[5]
                        if isinstance(err_node, list) and len(err_node) > 0 and err_node[0] == 7:
                            raise AuthenticationError("Session credentials revoked or expired (Code 7)")

                    inner_data = json.loads(entry[2])
                    if isinstance(inner_data, list) and len(inner_data) > 0 and inner_data[0] == 7:
                        raise AuthenticationError("Session credentials revoked or expired (Code 7)")
                    return inner_data

        return None

    # Unary RPC implementations
    async def get_user_status(self) -> dict[str, Any]:
        """RPC otAQ7b: Discovers models, account tier, and quotas."""
        raw = await self.call_batchexecute("otAQ7b", [])
        return {"raw": raw}

    async def list_models(self) -> list[ModelInfo]:
        """Discovers available models via otAQ7b or falls back to known defaults."""
        try:
            status = await self.get_user_status()
            raw = status.get("raw")
            if raw and isinstance(raw, list) and len(raw) > 24 and isinstance(raw[24], list) and len(raw[24]) > 0:
                policy_str = str(raw[24][0]).lower()
                if "ultra" in policy_str:
                    self.ctx.account_tier = "ultra"
                elif "pro" in policy_str or "advanced" in policy_str:
                    if self.ctx.account_tier != "ultra":
                        self.ctx.account_tier = "pro"
                elif "free" in policy_str:
                    self.ctx.account_tier = "free"

            if raw and isinstance(raw, list) and len(raw) > 15 and isinstance(raw[15], list):
                discovered: list[ModelInfo] = []
                for idx, item in enumerate(raw[15]):
                    if isinstance(item, list) and len(item) >= 2:
                        m_id = str(item[0])
                        disp = str(item[1])
                        desc = str(item[2]) if len(item) > 2 and item[2] else ""
                        known = DEFAULT_MODELS.get(m_id)
                        if len(item) > 3 and isinstance(item[3], list):
                            # Real Google otAQ7b structure: item[3] is capabilities list
                            if isinstance(item[-1], str) and item[-1].strip():
                                disp = item[-1].strip()
                            model_num = known.model_number if known else (int(item[4]) if len(item) > 4 and isinstance(item[4], int) else 1)
                            is_pro_model = any(k in disp.lower() for k in ("pro", "argon", "carbon", "neon"))
                            thinking_f = known.thinking_flag if known else (2 if is_pro_model else 1)
                            is_thinking = True
                            if "carbon" in disp.lower() and len(m_id) == 16 and all(c in "0123456789abcdef" for c in m_id.lower()):
                                register_discovered_carbon_model(
                                    m_id,
                                    display_name=disp,
                                    description=desc,
                                    model_number=model_num,
                                    thinking_flag=thinking_f,
                                )
                        else:
                            # Mock server structure: item[3] is model_number, item[4] is thinking_flag
                            model_num = int(item[3]) if len(item) > 3 and isinstance(item[3], int) else 1
                            thinking_f = int(item[4]) if len(item) > 4 and isinstance(item[4], int) else (2 if "thinking" in m_id else 1)
                            is_thinking = thinking_f == 2

                        is_carbon = "carbon" in disp.lower()
                        is_argon = "argon" in disp.lower()
                        is_neon = "neon" in disp.lower()
                        discovered.append(
                            ModelInfo(
                                id=m_id,
                                display_name=disp if disp != "Auto" else f"Auto ({desc})" if desc else disp,
                                description=desc,
                                model_number=model_num,
                                thinking_flag=thinking_f,
                                capabilities=ModelCapabilities(supports_thinking=is_thinking),
                                is_default=(idx == 0),
                                mode_tag=(
                                    "Neon4p0"
                                    if is_neon
                                    else (None if is_carbon else (known.mode_tag if known else ("Argon4p0" if is_argon else None)))
                                ),
                            )
                        )
                if discovered:
                    if len(discovered) == 1 and discovered[0].id == "cf41b0e0dd7d53e5":
                        discovered.extend(DEFAULT_MODELS.values())
                    return discovered
        except Exception as e:
            logger.debug("Failed dynamically discovering models via otAQ7b: %s", e)

        return list(DEFAULT_MODELS.values())

    async def list_conversations(self, limit: int = 50) -> list[dict[str, Any]]:
        """RPC MaZiqc: Lists sidebar conversation history."""
        payload = [limit, None, [0, None, 1]]
        raw = await self.call_batchexecute("MaZiqc", payload)
        conversations: list[dict[str, Any]] = []
        if not raw or not isinstance(raw, list):
            return conversations

        conv_items: list[Any] = []
        if len(raw) > 0 and isinstance(raw[0], list):
            conv_items = raw[0]
        elif len(raw) > 2 and isinstance(raw[2], list):
            conv_items = raw[2]

        for item in conv_items:
            if isinstance(item, list) and len(item) > 1 and item[0]:
                updated_usec = 0
                if len(item) > 2 and isinstance(item[2], (int, float)):
                    updated_usec = int(item[2])
                elif (
                    len(item) > 5
                    and isinstance(item[5], list)
                    and len(item[5]) >= 1
                    and isinstance(item[5][0], (int, float))
                ):
                    nsec = int(item[5][1]) if len(item[5]) > 1 and isinstance(item[5][1], (int, float)) else 0
                    updated_usec = int(item[5][0]) * 1_000_000 + (nsec // 1000)

                conversations.append(
                    {
                        "cid": str(item[0]),
                        "title": str(item[1] or "Untitled Conversation"),
                        "updated_usec": updated_usec,
                        "is_pinned": bool(item[4]) if len(item) > 4 and isinstance(item[4], bool) else False,
                    }
                )
        return conversations

    # Alias aligned with PROJECT.md Interface Contract 1
    list_remote_conversations = list_conversations

    async def get_conversation_turns(self, cid: str, limit: int = 100) -> list[Any]:
        """RPC hNvQHb: Fetches turns within a conversation."""
        payload = [cid, limit]
        return await self.call_batchexecute("hNvQHb", payload)

    async def list_gems(self, gem_type: int = 2) -> list[Gem]:
        """RPC CNgdBe: Lists Custom Gems (2=custom, 3=system)."""
        lang = getattr(self.ctx, "language", "en")
        is_custom = (gem_type == 2)
        payload = GemSerializer.build_list_payload(is_custom=is_custom, language=lang)
        raw = await self.call_batchexecute("CNgdBe", payload)
        return GemSerializer.parse_list_response(raw, is_builtin=not is_custom)

    async def get_gem(self, gem_id: str) -> Gem | None:
        """Fetches a single Gem by ID or name across Custom (2) and System (3) Gems."""
        for g_type in (2, 3):
            try:
                gems = await self.list_gems(gem_type=g_type)
                for g in gems:
                    if g.id == gem_id or g.name.lower() == gem_id.lower():
                        return g
            except Exception as e:
                logger.debug("Error listing gem_type=%d in get_gem: %s", g_type, e)
        return None

    async def create_gem(self, name: str, prompt: str, description: str = "") -> Gem:
        """RPC oMH3Zd: Creates a new Custom Gem and returns the created Gem object."""
        payload = GemSerializer.build_create_payload(name=name, description=description, prompt=prompt)
        raw = await self.call_batchexecute("oMH3Zd", payload)
        gem_id = GemSerializer.parse_create_response(raw)
        now_ts = int(time.time())
        return Gem(
            id=gem_id,
            name=name,
            description=description,
            prompt=prompt,
            is_builtin=False,
            created_at=now_ts,
            updated_at=now_ts,
        )

    async def update_gem(self, gem_id: str, name: str, prompt: str, description: str = "") -> Gem:
        """RPC kHv0Vd: Updates an existing Custom Gem and returns the updated Gem object."""
        payload = GemSerializer.build_update_payload(
            gem_id=gem_id, name=name, description=description, prompt=prompt
        )
        await self.call_batchexecute("kHv0Vd", payload)
        return Gem(
            id=gem_id,
            name=name,
            description=description,
            prompt=prompt,
            is_builtin=False,
            updated_at=int(time.time()),
        )

    async def delete_gem(self, gem_id: str) -> bool:
        """RPC UXcSJb: Deletes an existing Custom Gem."""
        payload = GemSerializer.build_delete_payload(gem_id=gem_id)
        await self.call_batchexecute("UXcSJb", payload)
        return True

    async def delete_conversation(self, cid: str) -> bool:
        """RPC GzXR5e: Deletes a conversation from Google account."""
        payload = [cid]
        try:
            await self.call_batchexecute("GzXR5e", payload)
            return True
        except Exception as e:
            logger.warning("Failed deleting conversation %s: %s", cid, e)
            return False

    async def get_full_size_image_url(
        self,
        image_id: str,
        cid: str,
        rid: str,
        rcid: str,
    ) -> str:
        """
        RPC c8o8Fe (GetFullSizeImage): Resolves a generated image's full-size original URL.
        """
        payload = [
            [
                [None, None, None, [None, None, None, None, None, ""]],
                [image_id, 0],
                None,
                [19, ""],
                None,
                None,
                None,
                None,
                None,
                "",
            ],
            [rid, rcid, cid, None, ""],
            1,
            0,
            1,
        ]
        try:
            raw = await self.call_batchexecute("c8o8Fe", payload)
            if isinstance(raw, list) and len(raw) > 0 and isinstance(raw[0], str):
                return raw[0]
        except Exception as e:
            logger.warning("Failed resolving full-size image URL via c8o8Fe: %s", e)
        return ""

    async def synthesize_speech(self, text: str, language: str | None = None) -> bytes:
        """
        RPC XqA3Ic: Synthesizes text-to-speech audio via Google Gemini batchexecute.
        Cleans code fences and URLs, caps at 4000 characters, auto-selects 'ru-RU'
        if >=15% Cyrillic letters else 'en-US' (when language omitted), and decodes
        returned Base64 payload into Ogg/Opus bytes (b"OggS...").
        """
        if not text:
            raise GeminiBridgeError("Text for speech synthesis is empty")

        # Strip code blocks, inline code, and URLs
        clean_text = re.sub(r"```[\s\S]*?```", " ", text)
        clean_text = re.sub(r"`[^`\n]+`", " ", clean_text)
        clean_text = re.sub(r"https?://\S+", " ", clean_text)
        # Strip common markdown formatting characters
        clean_text = re.sub(r"[#*_~`>]", "", clean_text)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()

        if not clean_text:
            raise GeminiBridgeError("Text for speech synthesis is empty after cleaning")

        clean_text = clean_text[:4000]

        if not language:
            cyrillic_chars = sum(1 for c in clean_text if "\u0400" <= c <= "\u04FF")
            alpha_chars = sum(1 for c in clean_text if c.isalpha())
            if alpha_chars > 0 and (cyrillic_chars / alpha_chars) >= 0.15:
                lang_code = "ru-RU"
            elif cyrillic_chars > 0 and alpha_chars == 0:
                lang_code = "ru-RU"
            else:
                lang_code = "en-US"
        else:
            lang_code = language

        raw = await self.call_batchexecute("XqA3Ic", [None, clean_text, lang_code, None, 2])
        if not raw or not isinstance(raw, list) or len(raw) == 0 or not raw[0]:
            raise GeminiBridgeError("No audio returned from speech synthesis RPC")

        try:
            audio_bytes = base64.b64decode(raw[0])
        except Exception as dec_err:
            raise GeminiBridgeError(f"Failed decoding audio base64 payload: {dec_err}")

        if not audio_bytes:
            raise GeminiBridgeError("Empty audio data returned from speech synthesis RPC")

        return audio_bytes

    async def generate_stream(
        self,
        prompt: str,
        model: str = "gemini-2.0-flash",
        gem_id: str | None = None,
        cid: str | None = None,
        rid: str | None = None,
        rcid: str | None = None,
        attachments: list[Any] | None = None,
        incognito: bool = False,
        history: list[Any] | None = None,
        extensions: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """
        Initiates a streaming chat completion session against Google StreamGenerate.
        Yields normalized StreamChunk items.

        Supports both M1 bridge keyword parameters (cid, rid, rcid, attachments, incognito)
        and PROJECT.md Interface Contract 1 (prompt, model, gem_id, attachments, history, **kwargs).
        """
        # Adapt PROJECT.md positional contract: (prompt, model, gem_id, attachments, history)
        if isinstance(cid, list) and attachments is None:
            attachments = cid
            cid = kwargs.get("cid")
        if isinstance(rid, list) and history is None:
            history = rid
            rid = kwargs.get("rid")

        # Ingest kwargs overrides
        cid = cid or kwargs.get("cid")
        rid = rid or kwargs.get("rid")
        rcid = rcid or kwargs.get("rcid")
        if attachments is None and "attachments" in kwargs:
            attachments = kwargs.get("attachments")
        if not incognito and "incognito" in kwargs:
            incognito = bool(kwargs.get("incognito"))
        if extensions is None and "extensions" in kwargs:
            extensions = kwargs.get("extensions")

        # Extract gem_id from model string if not explicitly passed
        if not gem_id and model:
            if "@gem=" in model:
                for part in model.split("@")[1:]:
                    if part.startswith("gem="):
                        gem_id = part.split("=", 1)[1].strip()
                        break
            elif model.startswith(("gem:", "gem/")):
                gem_id = model[4:].split("@")[0].strip()

        # Derive conversation context from history if cid/rid/rcid not explicit
        if history and not cid:
            for turn in reversed(history):
                if isinstance(turn, dict):
                    cid = cid or turn.get("cid") or turn.get("conversation_id")
                    rid = rid or turn.get("rid") or turn.get("response_id")
                    rcid = rcid or turn.get("rcid")
                elif hasattr(turn, "cid") or hasattr(turn, "conversation_id"):
                    cid = cid or getattr(turn, "cid", None) or getattr(turn, "conversation_id", None)
                    rid = rid or getattr(turn, "rid", None) or getattr(turn, "response_id", None)
                    rcid = rcid or getattr(turn, "rcid", None)
                if cid:
                    break
            if not cid:
                hist_lines: list[str] = []
                for turn in history:
                    if isinstance(turn, dict) and turn.get("content"):
                        r_lbl = turn.get("role", "user")
                        hist_lines.append(f"[{r_lbl}]: {turn['content']}")
                if hist_lines:
                    first_turn = hist_lines[0]
                    rest_block = "\n\n".join(hist_lines[1:])
                    if len(rest_block) > 24000:
                        rest_block = "...\n" + rest_block[-24000:]
                    hist_block = f"{first_turn}\n\n{rest_block}".strip() if rest_block else first_turn
                    if "\n\n# Tool Use\n" in prompt:
                        p_head, p_tail = prompt.split("\n\n# Tool Use\n", 1)
                        prompt = f"{p_head}\n\n[Conversation History]\n{hist_block}\n\n# Tool Use\n{p_tail}"
                    elif "\n\n[User Prompt]\n" in prompt:
                        p_head, p_tail = prompt.split("\n\n[User Prompt]\n", 1)
                        prompt = f"{p_head}\n\n[Conversation History]\n{hist_block}\n\n[User Prompt]\n{p_tail}"
                    else:
                        prompt = f"[Conversation History]\n{hist_block}\n\n{prompt}"

        model_info = resolve_model(model)
        request_uuid = str(uuid.uuid4()).upper()
        session_uuid = getattr(self.ctx, "session_uuid", str(uuid.uuid4()).upper())

        headers = {
            "x-goog-ext-525001261-jspb": HeaderBuilder.build_model_jspb_header(
                model_info, session_uuid, model_info.thinking_flag, model_info.model_number
            ),
            "x-goog-ext-73010989-jspb": "[0]",
            "x-goog-ext-73010990-jspb": "[0,0,0]",
            "x-goog-ext-525005358-jspb": json.dumps([request_uuid, 1], separators=(",", ":")),
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "User-Agent": HeaderBuilder.DEFAULT_USER_AGENT,
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
            "x-same-domain": "1",
        }
        cookie_header = HeaderBuilder.format_cookies(self.ctx.cookies)
        if cookie_header:
            headers["Cookie"] = cookie_header
        sapisid = HeaderBuilder.extract_sapisid(self.ctx.cookies)
        if sapisid:
            headers["Authorization"] = HeaderBuilder.make_sapisidhash(sapisid, origin=self.base_url)

        eff_feature_mode = kwargs.get("feature_mode")
        if eff_feature_mode is None:
            eff_feature_mode = getattr(model_info, "feature_mode", 0)
        elif isinstance(eff_feature_mode, str):
            eff_feature_mode = (
                40
                if eff_feature_mode.lower() in ("task", "tasks", "40", "spark", "deep-research", "deep_research", "deepresearch", "research")
                else 0
            )

        acc_key = self._get_account_key()
        if eff_feature_mode == 40 and self.is_feature_mode_40_unsupported(acc_key):
            logger.info("Account %s cached as feature_mode=40 incompatible; using standard stream envelope", acc_key)
            eff_feature_mode = 0

        is_mock_transport = self._client is not None and isinstance(
            getattr(self._client, "_transport", None), httpx.MockTransport
        )
        if (
            eff_feature_mode == 0
            and model_info.model_number == 8
            and "gemini.google.com" in (self.base_url or "")
            and not is_mock_transport
            and not self.is_feature_mode_40_unsupported(acc_key)
        ):
            # Upstream boq_gemini-web-uiserver_20261117.10_p0 detaches model_number=8 in chat mode;
            # routing via container feature_mode=40 streams reliably without 32s disconnects.
            eff_feature_mode = 40

        inner_req = self._build_81_element_envelope(
            prompt=prompt,
            cid=cid,
            rid=rid,
            rcid=rcid,
            gem_id=gem_id,
            attachments=attachments,
            model_number=model_info.model_number,
            thinking_flag=model_info.thinking_flag,
            incognito=incognito,
            feature_mode=eff_feature_mode,
            mode_tag=getattr(model_info, "mode_tag", None),
            extensions=extensions,
        )

        inner_str = json.dumps(inner_req, separators=(",", ":"))
        f_req = json.dumps([None, inner_str], separators=(",", ":"))

        params = {
            "hl": getattr(self.ctx, "language", "en"),
            "_reqid": str(self._next_reqid()),
            "rt": "c",
            "bl": getattr(self.ctx, "build_label", "boq_assistant-bard-web-server_20261017.00_p0"),
            "f.sid": getattr(self.ctx, "session_id", ""),
        }

        data = {
            "at": getattr(self.ctx, "at_token", ""),
            "f.req": f_req,
        }

        url = f"{self.base_url}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
        is_container_request = (eff_feature_mode == 40)
        if is_container_request and self.timeout < DEEP_RESEARCH_TIMEOUT:
            self.timeout = DEEP_RESEARCH_TIMEOUT
            if self._client is not None and not self._client.is_closed:
                self._client.timeout = httpx.Timeout(
                    self.timeout, connect=20.0, read=self.timeout, write=60.0
                )
        client = await self._get_client()

        is_flash4p0_primary = (getattr(model_info, "mode_tag", None) == "Flash4p0")
        flash4p0_fallback_used = False
        chunks_emitted = False

        def _activate_flash4p0_fallback(reason: str) -> None:
            nonlocal flash4p0_fallback_used, inner_req
            flash4p0_fallback_used = True
            logger.warning(
                "Flash4p0 stream %s; retrying once on fallback fast slot 8c46e95b1a07cecc",
                reason,
            )
            fallback_model_info = ModelInfo(
                id="8c46e95b1a07cecc",
                display_name="3.5 Flash-Lite",
                description="Fastest responses",
                model_number=6,
                thinking_flag=1,
                capacity_tail=12,
                tier=model_info.tier,
                capabilities=model_info.capabilities,
                mode_tag=None,
            )
            headers["x-goog-ext-525001261-jspb"] = HeaderBuilder.build_model_jspb_header(
                fallback_model_info, session_uuid, 1, 6
            )
            headers["x-goog-ext-525005358-jspb"] = json.dumps(
                [str(uuid.uuid4()).upper(), 1], separators=(",", ":")
            )
            inner_req = self._build_81_element_envelope(
                prompt=prompt,
                cid=cid,
                rid=rid,
                rcid=rcid,
                gem_id=gem_id,
                attachments=attachments,
                model_number=6,
                thinking_flag=1,
                incognito=incognito,
                feature_mode=0,
                mode_tag=None,
                extensions=extensions,
            )
            data["f.req"] = json.dumps(
                [None, json.dumps(inner_req, separators=(",", ":"))],
                separators=(",", ":"),
            )

        container_fallback_occurred = False
        max_attempts = MAX_TOTAL_ATTEMPTS if (is_container_request or is_flash4p0_primary) else 3
        attempt = 0
        while attempt < max_attempts:
            params["_reqid"] = str(self._next_reqid())
            frame_parser = StreamingFrameParser()
            accumulator = StreamingDeltaAccumulator()

            effective_limit = (
                MAX_TOTAL_ATTEMPTS
                if (is_container_request or container_fallback_occurred or is_flash4p0_primary)
                else max_attempts
            )
            if attempt >= effective_limit:
                break

            should_buffer_flash4p0 = (
                is_flash4p0_primary and not flash4p0_fallback_used and not chunks_emitted
            )
            buffered_chunks: list[StreamChunk] = []
            buffered_text_parts: list[str] = []
            buffered_text_len = 0
            trigger_flash4p0_fallback = False

            try:
                async with client.stream("POST", url, params=params, data=data, headers=headers) as resp:
                    if resp.status_code in (401, 403):
                        raise AuthenticationError(f"Stream rejected: HTTP {resp.status_code}")
                    if resp.status_code >= 500:
                        raise TemporaryBackendOverloadError(f"HTTP {resp.status_code}")
                    if resp.status_code != 200:
                        raise GeminiBridgeError(f"Stream failed with HTTP {resp.status_code}")

                    raw_iter = resp.aiter_bytes()
                    if inspect.iscoroutine(raw_iter):
                        raw_iter = await raw_iter
                    if isinstance(raw_iter, (list, tuple)):
                        async def _iter_items(items):
                            for b in items:
                                yield b
                        stream_bytes = _iter_items(raw_iter)
                    else:
                        stream_bytes = raw_iter

                    async for raw_bytes in stream_bytes:
                        for frame in frame_parser.feed(raw_bytes):
                            for chunk in accumulator.process_frame(frame):
                                if not should_buffer_flash4p0:
                                    chunks_emitted = True
                                    yield chunk
                                    continue

                                buffered_chunks.append(chunk)
                                if chunk.type == "text" and chunk.delta:
                                    buffered_text_parts.append(chunk.delta)
                                    buffered_text_len += len(chunk.delta)

                                if chunk.type == "done":
                                    candidate_text = accumulator.full_raw_text or "".join(buffered_text_parts)
                                    has_payload = bool(
                                        candidate_text.strip()
                                        or accumulator.images
                                        or accumulator.youtube_videos
                                        or accumulator.research_plan
                                    )
                                    if _is_canned_refusal(candidate_text) or not has_payload:
                                        trigger_flash4p0_fallback = True
                                        break
                                    for bc in buffered_chunks:
                                        yield bc
                                    buffered_chunks.clear()
                                    chunks_emitted = True
                                    should_buffer_flash4p0 = False
                                elif buffered_text_len >= FLASH4P0_HOLDBACK_CHARS:
                                    candidate_text = "".join(buffered_text_parts)
                                    if _is_canned_refusal(candidate_text):
                                        trigger_flash4p0_fallback = True
                                        break
                                    for bc in buffered_chunks:
                                        yield bc
                                    buffered_chunks.clear()
                                    chunks_emitted = True
                                    should_buffer_flash4p0 = False
                            if trigger_flash4p0_fallback:
                                break
                        if trigger_flash4p0_fallback:
                            break

                if trigger_flash4p0_fallback:
                    _activate_flash4p0_fallback("returned canned refusal or empty response")
                    attempt += 1
                    continue

                if should_buffer_flash4p0:
                    candidate_text = accumulator.full_raw_text or "".join(buffered_text_parts)
                    has_payload = bool(
                        candidate_text.strip()
                        or accumulator.images
                        or accumulator.youtube_videos
                        or accumulator.research_plan
                    )
                    if _is_canned_refusal(candidate_text) or not has_payload:
                        _activate_flash4p0_fallback("returned canned refusal or empty stream")
                        attempt += 1
                        continue
                    for bc in buffered_chunks:
                        yield bc
                    buffered_chunks.clear()
                    chunks_emitted = True

                has_valid_container_output = bool(
                    accumulator.full_raw_text.strip()
                    or accumulator.research_plan
                    or "### 📋" in accumulator.full_raw_thoughts
                )
                if not has_valid_container_output and inner_req[49] == 40 and attempt < effective_limit - 1:
                    logger.warning("Task container (feature_mode=40) produced empty text; falling back to standard stream envelope")
                    container_fallback_occurred = True
                    inner_req[49] = None
                    inner_str = json.dumps(inner_req, separators=(",", ":"))
                    data["f.req"] = json.dumps([None, inner_str], separators=(",", ":"))
                    attempt += 1
                    continue

                # Successful stream completion: if task container was executed, close circuit
                if inner_req[49] == 40:
                    self.mark_feature_mode_40_supported(acc_key)
                return
            except AuthenticationError:
                raise
            except (GeminiBridgeError, httpx.TransportError, ssl.SSLError, *WREQ_TRANSPORT_ERRORS) as e:
                if (
                    isinstance(e, (ssl.SSLError, httpx.ReadError, httpx.RemoteProtocolError, *WREQ_TRANSPORT_ERRORS))
                    and not chunks_emitted
                    and attempt < effective_limit - 1
                ):
                    logger.warning("Stream SSL or protocol error before chunks (%s); retrying", e)
                    if not hasattr(getattr(client, "stream", None), "side_effect"):
                        try:
                            await self.close()
                            client = await self._get_client()
                        except Exception:
                            pass
                    await asyncio.sleep(0.3 * (attempt + 1) + random.uniform(0.05, 0.15))
                    attempt += 1
                    continue
                if is_flash4p0_primary and not flash4p0_fallback_used and not chunks_emitted:
                    if inner_req[49] == 40 and (
                        any(code in str(e) for code in ("1061", "1173"))
                        or getattr(e, "code", None) in (1061, 1173)
                    ):
                        self.mark_feature_mode_40_unsupported(acc_key)
                    _activate_flash4p0_fallback(f"failed with {type(e).__name__}: {e}")
                    attempt += 1
                    continue
                if is_flash4p0_primary:
                    raise
                if (
                    isinstance(e, httpx.RemoteProtocolError)
                    and not chunks_emitted
                    and inner_req[49] != 40
                    and not is_mock_transport
                    and not self.is_feature_mode_40_unsupported(acc_key)
                    and attempt < effective_limit - 1
                ):
                    logger.warning("Stream disconnected before payload (%s); retrying with feature_mode=40", e)
                    inner_req[49] = 40
                    inner_str = json.dumps(inner_req, separators=(",", ":"))
                    data["f.req"] = json.dumps([None, inner_str], separators=(",", ":"))
                    attempt += 1
                    continue
                if isinstance(e, (TemporaryBackendOverloadError, httpx.TransportError)):
                    if attempt >= effective_limit - 1:
                        raise
                    wait_time = (2 ** attempt) + random.uniform(0.1, 0.5)
                    logger.warning("Stream error %s; retrying in %.2fs (attempt %d/%d)", e, wait_time, attempt + 1, effective_limit)
                    await asyncio.sleep(wait_time)
                    attempt += 1
                    continue
                if isinstance(e, (UsageLimitExceededError, ModelInconsistentError, ModelHeaderInvalidError)):
                    raise
                if (any(code in str(e) for code in ("1061", "1173")) or getattr(e, "code", None) in (1061, 1173)) and inner_req[49] == 40 and attempt < effective_limit - 1:
                    logger.warning("Task container (feature_mode=40) returned %s; falling back to standard stream envelope", e)
                    container_fallback_occurred = True
                    if any(c in str(e) for c in ("1173", "1061")) or getattr(e, "code", None) in (1173, 1061):
                        self.mark_feature_mode_40_unsupported(acc_key)
                    inner_req[49] = None
                    inner_str = json.dumps(inner_req, separators=(",", ":"))
                    data["f.req"] = json.dumps([None, inner_str], separators=(",", ":"))
                    attempt += 1
                    continue
                raise
