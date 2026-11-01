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
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any
from collections.abc import AsyncIterator
import uuid
import threading
import httpx


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
    resolve_model,
)
from backend.app.bridge.session import SessionContext
from backend.app.bridge.streaming_parser import (
    StreamChunk,
    StreamingDeltaAccumulator,
    StreamingFrameParser,
)

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
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=20.0, read=self.timeout, write=60.0),
                cookies=self.ctx.cookies,
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

        logger.debug(
            "Passively harvested %d cookies from %s: %s",
            len(harvested),
            response.url,
            list(harvested.keys()),
        )

        if hasattr(self.ctx, "cookies") and isinstance(self.ctx.cookies, dict):
            self.ctx.cookies.update(harvested)
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
        inner[68] = 1  # Execution mode

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

        resp = await client.post(url, params=params, data=data, headers=headers)
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
                if "pro" in policy_str or "advanced" in policy_str or "ultra" in policy_str:
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
                            is_pro_model = "pro" in disp.lower() or "argon" in disp.lower()
                            thinking_f = known.thinking_flag if known else (2 if is_pro_model else 1)
                            is_thinking = True
                        else:
                            # Mock server structure: item[3] is model_number, item[4] is thinking_flag
                            model_num = int(item[3]) if len(item) > 3 and isinstance(item[3], int) else 1
                            thinking_f = int(item[4]) if len(item) > 4 and isinstance(item[4], int) else (2 if "thinking" in m_id else 1)
                            is_thinking = thinking_f == 2

                        discovered.append(
                            ModelInfo(
                                id=m_id,
                                display_name=disp if disp != "Auto" else f"Auto ({desc})" if desc else disp,
                                description=desc,
                                model_number=model_num,
                                thinking_flag=thinking_f,
                                capabilities=ModelCapabilities(supports_thinking=is_thinking),
                                is_default=(idx == 0),
                                mode_tag=known.mode_tag if known else ("Argon4p0" if "argon" in disp.lower() else None),
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
            eff_feature_mode = 40 if eff_feature_mode.lower() in ("task", "tasks", "40", "spark") else 0

        acc_key = self._get_account_key()
        if eff_feature_mode == 40 and self.is_feature_mode_40_unsupported(acc_key):
            logger.info("Account %s cached as feature_mode=40 incompatible; using standard stream envelope", acc_key)
            eff_feature_mode = 0

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
        client = await self._get_client()

        is_container_request = (eff_feature_mode == 40)
        container_fallback_occurred = False
        max_attempts = MAX_TOTAL_ATTEMPTS if is_container_request else 3
        for attempt in range(max_attempts):
            params["_reqid"] = str(self._next_reqid())
            frame_parser = StreamingFrameParser()
            accumulator = StreamingDeltaAccumulator()

            effective_limit = MAX_TOTAL_ATTEMPTS if (is_container_request or container_fallback_occurred) else max_attempts

            try:
                async with client.stream("POST", url, params=params, data=data, headers=headers) as resp:
                    if resp.status_code in (401, 403):
                        raise AuthenticationError(f"Stream rejected: HTTP {resp.status_code}")
                    if resp.status_code >= 500:
                        raise TemporaryBackendOverloadError(f"HTTP {resp.status_code}")
                    if resp.status_code != 200:
                        raise GeminiBridgeError(f"Stream failed with HTTP {resp.status_code}")

                    async for raw_bytes in resp.aiter_bytes():
                        frames = frame_parser.feed(raw_bytes)
                        for frame in frames:
                            chunks = accumulator.process_frame(frame)
                            for chunk in chunks:
                                yield chunk
                if accumulator.full_raw_text.strip() == "" and inner_req[49] == 40 and attempt < effective_limit - 1:
                    logger.warning("Task container (feature_mode=40) produced empty text; falling back to standard stream envelope")
                    container_fallback_occurred = True
                    inner_req[49] = None
                    inner_str = json.dumps(inner_req, separators=(",", ":"))
                    data["f.req"] = json.dumps([None, inner_str], separators=(",", ":"))
                    continue

                # Successful stream completion: if task container was executed, close circuit
                if inner_req[49] == 40:
                    self.mark_feature_mode_40_supported(acc_key)
                return
            except (TemporaryBackendOverloadError, httpx.TransportError) as e:
                if attempt >= effective_limit - 1:
                    raise
                wait_time = (2 ** attempt) + random.uniform(0.1, 0.5)
                logger.warning("Stream error %s; retrying in %.2fs (attempt %d/%d)", e, wait_time, attempt + 1, effective_limit)
                await asyncio.sleep(wait_time)
            except (UsageLimitExceededError, ModelInconsistentError, ModelHeaderInvalidError, AuthenticationError):
                raise
            except GeminiBridgeError as e:
                if (any(code in str(e) for code in ("1061", "1173")) or getattr(e, "code", None) in (1061, 1173)) and inner_req[49] == 40 and attempt < effective_limit - 1:
                    logger.warning("Task container (feature_mode=40) returned %s; falling back to standard stream envelope", e)
                    container_fallback_occurred = True
                    if any(c in str(e) for c in ("1173", "1061")) or getattr(e, "code", None) in (1173, 1061):
                        self.mark_feature_mode_40_unsupported(acc_key)
                    inner_req[49] = None
                    inner_str = json.dumps(inner_req, separators=(",", ":"))
                    data["f.req"] = json.dumps([None, inner_str], separators=(",", ":"))
                    continue
                raise
