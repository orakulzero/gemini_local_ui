"""
backend/app/bridge/uploader.py
Multimodal content-push resumable upload client (Scotty 2-step + multipart fallback),
magic-byte MIME detector, and attachment formatters.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from typing import Any
import httpx

from backend.app.bridge.exceptions import AuthenticationError, GeminiBridgeError
from backend.app.bridge.models import HeaderBuilder

logger = logging.getLogger("gemini_bridge.uploader")

FOREIGN_SERVICE_COOKIES = {
    "OSID",
    "__Secure-OSID",
    "LSID",
    "__Host-1PLSID",
    "__Host-3PLSID",
}


class GeminiUploader:
    """
    Multimodal File Uploader for Gemini Web Client.
    Uploads images, PDFs, audio, video, and code files to content-push.googleapis.com
    using Google's Scotty 2-step resumable upload protocol (with multipart fallback for mock servers),
    and returns upload resource URIs formatted for StreamGenerate envelopes.
    """

    DEFAULT_UPLOAD_URL = "https://content-push.googleapis.com"
    DEFAULT_PUSH_ID = "feeds/mcudyrk2a4khkz"
    DEFAULT_PCTX = "CgcSBWjK7pYx"

    def __init__(
        self,
        session_context: Any = None,
        base_url: str | None = None,
        upload_url: str | None = None,
        cookies: dict[str, str] | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.ctx = session_context
        # Use mock base_url only in test environments; default to content-push.googleapis.com in production
        if upload_url:
            target_url = upload_url
        elif base_url and ("127.0.0.1" in base_url or "localhost" in base_url or "testserver" in base_url):
            target_url = base_url
        else:
            target_url = self.DEFAULT_UPLOAD_URL
        self.upload_url = target_url.rstrip("/")
        raw_cookies = cookies or (getattr(session_context, "cookies", {}) if session_context else {})
        self._cookies = {
            k: v for k, v in (raw_cookies or {}).items() if k not in FOREIGN_SERVICE_COOKIES
        }
        self.timeout = timeout

    @staticmethod
    def detect_image_mime(image_bytes: bytes, fallback: str = "image/png") -> str:
        """Infers raster image MIME type directly from file magic bytes."""
        if not isinstance(image_bytes, bytes) or not image_bytes:
            return fallback
        if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if image_bytes.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if image_bytes.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if len(image_bytes) >= 12 and image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
            return "image/webp"
        if image_bytes.startswith(b"BM"):
            return "image/bmp"
        if image_bytes.startswith((b"II*\x00", b"MM\x00*")):
            return "image/tiff"
        if len(image_bytes) >= 12 and image_bytes[4:8] == b"ftyp":
            brand = image_bytes[8:12]
            if brand in (b"avif", b"avis"):
                return "image/avif"
            if brand in (b"heic", b"heix", b"hevc", b"hevx"):
                return "image/heic"
        return fallback

    @classmethod
    def detect_mime_type(
        cls,
        filename: str,
        fallback: str = "application/octet-stream",
        data: bytes | None = None,
    ) -> str:
        """Determines MIME type using magic bytes (if data is an image) or file extension."""
        if data:
            magic_mime = cls.detect_image_mime(data, fallback="")
            if magic_mime:
                return magic_mime
        mime, _ = mimetypes.guess_type(filename)
        return mime or fallback

    @staticmethod
    def format_chat_turn_attachment(resource_uri: str, filename: str) -> list[Any]:
        """
        Formats a single file attachment for insertion into inner_req_list[0][3].
        Structure: [[[uri], filename]]
        """
        return [[[resource_uri], filename]]

    @staticmethod
    def format_scotty_attachment(resource_uri: str, filename: str = "image.png") -> list[Any]:
        """
        Formats a Scotty resumable upload reference with media flag 1:
        Structure: [[[uri, 1], filename]]
        """
        return [[[resource_uri, 1], filename]]

    @staticmethod
    def format_chat_turn_attachments(
        attachments: list[tuple[str, str]] | list[dict[str, Any]]
    ) -> list[Any]:
        """
        Formats multiple file attachments.
        Accepts list of (resource_uri, filename) tuples or list of {"uri": "...", "filename": "..."} dicts.
        """
        formatted: list[Any] = []
        for item in attachments:
            if isinstance(item, tuple):
                uri, name = item
                formatted.append([[[uri], name]])
            elif isinstance(item, dict):
                uri = item.get("uri", "")
                name = item.get("filename", "file")
                if item.get("scotty") or item.get("media_type") == 1:
                    formatted.append([[[uri, 1], name]])
                else:
                    formatted.append([[[uri], name]])
        return formatted

    def _get_clean_cookies(self) -> dict[str, str]:
        raw = self._cookies or (getattr(self.ctx, "cookies", {}) if self.ctx else {})
        return {k: v for k, v in (raw or {}).items() if k not in FOREIGN_SERVICE_COOKIES}

    async def upload_file_resumable(
        self,
        data: bytes,
        filename: str = "image.png",
        mime_type: str | None = None,
        push_id: str | None = None,
        pctx: str | None = None,
    ) -> str:
        """
        Uploads binary data using Google's 2-step Scotty resumable upload protocol:
        1. POST /upload/ with X-Goog-Upload-Command: start -> extracts X-Goog-Upload-URL
        2. POST <X-Goog-Upload-URL> with X-Goog-Upload-Command: upload, finalize -> returns resource URI
        """
        effective_mime = mime_type or self.detect_mime_type(filename, data=data)
        ctx_push_id = getattr(self.ctx, "push_id", None) if self.ctx else None
        effective_push_id = push_id or ctx_push_id or self.DEFAULT_PUSH_ID
        ctx_pctx = getattr(self.ctx, "pctx", None) if self.ctx else None
        effective_pctx = pctx or ctx_pctx or self.DEFAULT_PCTX

        cookies = self._get_clean_cookies()
        start_headers: dict[str, str] = {
            "Push-ID": effective_push_id,
            "X-Tenant-Id": "bard-storage",
            "X-Client-Pctx": effective_pctx,
            "X-Goog-Upload-Header-Content-Length": str(len(data)),
            "X-Goog-Upload-Header-Content-Type": effective_mime,
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "Origin": "https://gemini.google.com",
            "Referer": "https://gemini.google.com/",
            "User-Agent": HeaderBuilder.DEFAULT_USER_AGENT,
        }
        cookie_str = HeaderBuilder.format_cookies(cookies)
        if cookie_str:
            start_headers["Cookie"] = cookie_str
        sapisid = HeaderBuilder.extract_sapisid(cookies)
        if sapisid:
            start_headers["Authorization"] = HeaderBuilder.make_sapisidhash(sapisid)

        start_url = (
            f"{self.upload_url}/upload/"
            if not self.upload_url.endswith(("/upload", "/upload/"))
            else self.upload_url
        )

        async with httpx.AsyncClient(
            http2=False,
            timeout=httpx.Timeout(self.timeout, connect=15.0),
            cookies=cookies,
        ) as client:
            resp1 = await client.post(start_url, headers=start_headers, content=b"")
            if resp1.status_code in (401, 403):
                raise AuthenticationError(f"Scotty upload initiation rejected: HTTP {resp1.status_code}")
            if resp1.status_code == 413:
                raise GeminiBridgeError("File size exceeds Google Gemini upload limit", code=413)
            if resp1.status_code != 200:
                raise GeminiBridgeError(f"Scotty upload start failed with HTTP {resp1.status_code}: {resp1.text[:200]}")

            upload_session_url = (
                resp1.headers.get("X-Goog-Upload-URL")
                or resp1.headers.get("x-goog-upload-url")
            )
            if not upload_session_url:
                # If server directly returned a resource URI in step 1 (e.g. simplified mock)
                direct_ref = resp1.text.strip()
                if direct_ref.startswith("/"):
                    return direct_ref
                raise GeminiBridgeError("Missing X-Goog-Upload-URL header in Scotty resumable start response")

            finalize_headers = {
                "X-Goog-Upload-Command": "upload, finalize",
                "X-Goog-Upload-Offset": "0",
                "Content-Type": "application/octet-stream",
                "User-Agent": HeaderBuilder.DEFAULT_USER_AGENT,
            }
            resp2 = await client.post(upload_session_url, headers=finalize_headers, content=data)
            if resp2.status_code in (401, 403):
                raise AuthenticationError(f"Scotty upload finalize rejected: HTTP {resp2.status_code}")
            if resp2.status_code == 413:
                raise GeminiBridgeError("File size exceeds Google Gemini upload limit", code=413)
            if resp2.status_code != 200:
                raise GeminiBridgeError(f"Scotty upload finalize failed with HTTP {resp2.status_code}: {resp2.text[:200]}")

            file_ref = resp2.text.strip()
            if not file_ref or not file_ref.startswith("/"):
                raise GeminiBridgeError(f"Invalid Scotty file reference returned: {file_ref[:100]}")
            return file_ref

    async def upload_file(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        push_id: str | None = None,
        use_resumable: bool | None = None,
    ) -> str:
        """
        Uploads binary file data and returns the Google resource URI string.
        Uses 2-step Scotty resumable upload when `use_resumable=True`,
        and supports single-step multipart upload (with automatic Scotty step-2 completion if
        the server returns `X-Goog-Upload-URL`).
        """
        if use_resumable is True:
            return await self.upload_file_resumable(
                data=data,
                filename=filename,
                mime_type=mime_type,
                push_id=push_id,
            )

        effective_mime = mime_type or self.detect_mime_type(filename, data=data)
        ctx_push_id = getattr(self.ctx, "push_id", None) if self.ctx else None
        effective_push_id = push_id or ctx_push_id or self.DEFAULT_PUSH_ID
        cookies = self._get_clean_cookies()

        headers: dict[str, str] = {
            "Push-ID": effective_push_id,
            "Origin": "https://gemini.google.com",
            "Referer": "https://gemini.google.com/",
            "User-Agent": HeaderBuilder.DEFAULT_USER_AGENT,
        }
        sapisid = HeaderBuilder.extract_sapisid(cookies)
        if sapisid:
            headers["Authorization"] = HeaderBuilder.make_sapisidhash(sapisid)

        files = {
            "file": (filename, data, effective_mime)
        }

        url = f"{self.upload_url}/upload" if not self.upload_url.endswith("/upload") else self.upload_url

        last_err = None
        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(
                    http2=False,
                    timeout=httpx.Timeout(self.timeout, connect=15.0),
                    cookies=cookies,
                ) as client:
                    resp = await client.post(url, headers=headers, files=files)

                    if resp.status_code == 200:
                        upload_session_url = (
                            resp.headers.get("X-Goog-Upload-URL")
                            or resp.headers.get("x-goog-upload-url")
                        )
                        if upload_session_url:
                            finalize_headers = {
                                "X-Goog-Upload-Command": "upload, finalize",
                                "X-Goog-Upload-Offset": "0",
                                "Content-Type": "application/octet-stream",
                                "User-Agent": HeaderBuilder.DEFAULT_USER_AGENT,
                            }
                            resp2 = await client.post(upload_session_url, headers=finalize_headers, content=data)
                            if resp2.status_code != 200:
                                raise GeminiBridgeError(f"Upload finalize failed with HTTP {resp2.status_code}")
                            return resp2.text.strip()

                        resource_uri = resp.text.strip()
                        if not resource_uri.startswith("/contrib_service/"):
                            logger.warning("Unexpected upload response format: %r", resource_uri[:100])
                        return resource_uri
                    if resp.status_code in (401, 403):
                        raise AuthenticationError(f"Upload rejected: HTTP {resp.status_code}")
                    if resp.status_code == 413:
                        raise GeminiBridgeError("File size exceeds Google Gemini upload limit", code=413)
                    raise GeminiBridgeError(f"Upload failed with HTTP {resp.status_code}: {resp.text[:200]}")
            except (AuthenticationError, GeminiBridgeError):
                raise
            except Exception as e:
                last_err = e
                logger.warning("Upload attempt %d failed: %s. Retrying...", attempt, e)
                if attempt < 3:
                    await asyncio.sleep(0.5 * attempt)

        raise GeminiBridgeError(f"Upload failed after 3 attempts: {last_err}")


# Alias for compatibility with test imports
ResumableUploader = GeminiUploader
