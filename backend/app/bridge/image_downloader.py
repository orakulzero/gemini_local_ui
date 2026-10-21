"""
backend/app/bridge/image_downloader.py
Authenticated FIFE / gg-dl image downloader for Gemini Web Client:
- Strips foreign service cookies (OSID, LSID, __Secure-OSID, __Host-1PLSID, __Host-3PLSID)
  that cause HTTP 403 on work.fife.usercontent.google.com
- Resolves full-size original URLs via batchexecute RPC `c8o8Fe` (GetFullSizeImage)
- Follows the `=d-I?alr=yes` text-redirect chain to fetch raw image bytes
- Resolves inline `http://googleusercontent.com/image_generation_content/<idx>` placeholders
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import httpx

from backend.app.bridge.models import HeaderBuilder

logger = logging.getLogger("gemini_bridge.image_downloader")

FOREIGN_SERVICE_COOKIES = {
    "OSID",
    "__Secure-OSID",
    "LSID",
    "__Host-1PLSID",
    "__Host-3PLSID",
}

GET_FULL_SIZE_IMAGE_RPC = "c8o8Fe"

_PLACEHOLDER_RE = re.compile(
    r"https?://[a-zA-Z0-9.-]*googleusercontent\.com/image_generation_content/(\d+)|/image_generation_content/(\d+)"
)
_REAL_GUSER_URL_RE = re.compile(
    r'https?://[a-zA-Z0-9.-]*googleusercontent\.com/[^\s"\'\\\}\],]+'
)


def filter_fife_cookies(cookies: Optional[Dict[str, str]]) -> Dict[str, str]:
    """
    Removes foreign Google service cookies (Gmail OSID, Accounts LSID) that cause
    HTTP 403 Forbidden on work.fife.usercontent.google.com during image downloads.
    """
    if not cookies or not isinstance(cookies, dict):
        return {}
    return {
        k: v
        for k, v in cookies.items()
        if k and v and k not in FOREIGN_SERVICE_COOKIES
    }


def resolve_image_placeholders(text: str, raw_or_urls: Union[str, List[str]]) -> str:
    """
    Replaces `http://googleusercontent.com/image_generation_content/<idx>` placeholders
    in response text with actual `googleusercontent.com` URLs extracted from the frame.
    """
    if not text:
        return text

    real_urls: List[str] = []
    if isinstance(raw_or_urls, str):
        for url in _REAL_GUSER_URL_RE.findall(raw_or_urls):
            if "image_generation_content" not in url and url not in real_urls:
                real_urls.append(url)
    elif isinstance(raw_or_urls, list):
        for item in raw_or_urls:
            u = item.get("url", "") if isinstance(item, dict) else str(item)
            if u and "image_generation_content" not in u and u not in real_urls:
                real_urls.append(u)

    if not real_urls:
        return text

    def _replace(match: re.Match[str]) -> str:
        idx_str = match.group(1) or match.group(2)
        idx = int(idx_str)
        if idx < len(real_urls):
            return real_urls[idx]
        if len(real_urls) == 1:
            return real_urls[0]
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_replace, text)


def _nested(obj: Any, path: List[Union[int, str]], default: Any = None) -> Any:
    cur = obj
    for key in path:
        try:
            cur = cur[key]
        except (KeyError, IndexError, TypeError):
            return default
    return cur


def parse_generated_images_from_raw(raw: str) -> List[Dict[str, str]]:
    """
    Extracts generated image descriptors `{cid, rid, rcid, image_id, url}`
    from raw StreamGenerate wrb.fr lines.
    """
    results: List[Dict[str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for line in raw.splitlines():
        if '"wrb.fr"' not in line or "gg-dl" not in line:
            continue
        try:
            inner = json.loads(json.loads(line)[0][2])
        except (json.JSONDecodeError, IndexError, TypeError):
            continue
        cid = _nested(inner, [1, 0], "") or ""
        rid = _nested(inner, [1, 1], "") or ""
        for cand in _nested(inner, [4], []) or []:
            rcid = _nested(cand, [0], "") or ""
            gen_list = (_nested(cand, [12, 7, 0], []) or []) + (
                _nested(cand, [12, 0, "8", 0], []) or []
            )
            for img_idx, gen in enumerate(gen_list):
                url = _nested(gen, [0, 3, 3])
                if not url or not isinstance(url, str):
                    continue
                image_id = (
                    _nested(gen, [1, 0])
                    or f"http://googleusercontent.com/image_generation_content/{img_idx}"
                )
                key = (rcid, url)
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    {
                        "cid": cid,
                        "rid": rid,
                        "rcid": rcid,
                        "image_id": str(image_id),
                        "url": url,
                    }
                )
    return results


class GeminiImageDownloader:
    """
    Downloads Gemini-generated images by resolving full-size URLs via `c8o8Fe`
    and following the `=d-I?alr=yes` text-redirect chain with filtered FIFE cookies.
    """

    def __init__(
        self,
        session_context: Any = None,
        client: Any = None,
        timeout: float = 60.0,
    ) -> None:
        self.ctx = session_context or (getattr(client, "ctx", None) if client else None)
        self.bridge_client = client
        self.timeout = timeout

    def get_clean_cookies(self) -> Dict[str, str]:
        raw = getattr(self.ctx, "cookies", {}) if self.ctx else {}
        return filter_fife_cookies(raw)

    async def get_full_size_url(self, meta: Dict[str, str]) -> str:
        """Calls `c8o8Fe` (GetFullSizeImage) RPC via `bridge_client` if available."""
        if not self.bridge_client or not hasattr(self.bridge_client, "get_full_size_image_url"):
            return ""
        image_id = meta.get("image_id", "")
        cid = meta.get("cid", "")
        rid = meta.get("rid", "")
        rcid = meta.get("rcid", "")
        if not (image_id and cid and rid and rcid):
            return ""
        return await self.bridge_client.get_full_size_image_url(
            image_id=image_id,
            cid=cid,
            rid=rid,
            rcid=rcid,
        )

    async def follow_alr_chain(
        self,
        start_url: str,
        max_hops: int = 6,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> Tuple[Optional[bytes], Optional[str]]:
        """
        Follows the `=d-I?alr=yes` text-redirect chain where intermediate HTTP 200
        responses have `Content-Type: text/plain` containing the next signed URL,
        until raw `image/*` bytes are returned.
        """
        cookies = self.get_clean_cookies()
        headers = {
            "Origin": "https://gemini.google.com",
            "Referer": "https://gemini.google.com/",
            "User-Agent": HeaderBuilder.DEFAULT_USER_AGENT,
        }
        cookie_str = HeaderBuilder.format_cookies(cookies)
        if cookie_str:
            headers["Cookie"] = cookie_str

        owns_client = http_client is None
        client = http_client or httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(self.timeout, connect=15.0),
            cookies=cookies,
        )
        try:
            cur_url = start_url
            for _ in range(max_hops):
                resp = await client.get(cur_url, headers=headers)
                ct = (resp.headers.get("content-type") or "").lower()
                if resp.status_code == 200 and ct.startswith("image/"):
                    mime = ct.split(";")[0].strip()
                    return resp.content, mime
                if resp.status_code == 200 and ct.startswith("text/"):
                    next_url = resp.text.strip()
                    if next_url.startswith(("http://", "https://")):
                        cur_url = next_url
                        continue
                logger.warning(
                    "Image download chain stopped at %s: HTTP %d content-type=%r",
                    cur_url[:80],
                    resp.status_code,
                    ct[:40],
                )
                return None, None
            return None, None
        finally:
            if owns_client:
                await client.aclose()

    async def download_image(
        self,
        meta: Dict[str, str],
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> Tuple[Optional[bytes], Optional[str]]:
        """
        Downloads a generated image:
        1. Attempts full-size resolution via `c8o8Fe` RPC + `=d-I?alr=yes` chain.
        2. Falls back to `meta['url'] + '=s2048-rj'` if full-size RPC is unavailable.
        """
        original_url = await self.get_full_size_url(meta)
        if original_url:
            alr_url = original_url if "?alr=yes" in original_url else f"{original_url}=d-I?alr=yes"
            data, mime = await self.follow_alr_chain(alr_url, http_client=http_client)
            if data:
                return data, mime

        preview_url = meta.get("url", "")
        if not preview_url:
            return None, None
        fallback_url = (
            preview_url
            if preview_url.endswith(("-rj", "?alr=yes"))
            else f"{preview_url}=s2048-rj"
        )
        return await self.follow_alr_chain(fallback_url, http_client=http_client)
