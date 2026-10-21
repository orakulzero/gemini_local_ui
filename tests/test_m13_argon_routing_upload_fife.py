"""
Milestone M13 (Step 2): Argon Hard Routing, Scotty Resumable Upload & FIFE Image Downloader Verification Suite.

Tests:
1. HeaderBuilder.make_sapisidhash, extract_sapisid, and Authorization header injection
2. Compact 9-element & 25-element x-goog-ext-525001261-jspb header for Gemini 4 Argon (95a4c0abcba24364)
3. extract_answered_by_label & StreamingDeltaAccumulator 'answered_by' stamp in 'done' chunk metadata
4. Magic-byte image MIME detection (PNG, JPEG, GIF, WEBP, BMP, TIFF, AVIF, HEIC)
5. 2-step Scotty resumable upload protocol (start -> X-Goog-Upload-URL -> upload, finalize) + foreign cookie stripping
6. FIFE image downloader: c8o8Fe (GetFullSizeImage) RPC + =d-I?alr=yes text-redirect chain + placeholder resolution
7. POST /api/images/download endpoint (base64 and raw modes)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.image_downloader import (
    GeminiImageDownloader,
    filter_fife_cookies,
    parse_generated_images_from_raw,
    resolve_image_placeholders,
)
from backend.app.bridge.models import (
    HeaderBuilder,
    extract_answered_by_label,
    resolve_model,
)
from backend.app.bridge.session import SessionContext, SessionManager
from backend.app.bridge.streaming_parser import (
    StreamingDeltaAccumulator,
    StreamingFrameParser,
)
from backend.app.bridge.uploader import GeminiUploader
from backend.app.storage.vault import CredentialVault


def test_sapisidhash_and_header_injection():
    """Verifies SAPISIDHASH computation and injection into stream, batch, and upload headers."""
    sapisid = "W344P6oDJS752Exo/AXFxXhuC1vuSRHU4-"
    ts = 1792500000
    expected_sha1 = hashlib.sha1(f"{ts} {sapisid} https://gemini.google.com".encode("utf-8")).hexdigest()
    auth_val = HeaderBuilder.make_sapisidhash(sapisid, timestamp=ts)
    assert auth_val == f"SAPISIDHASH {ts}_{expected_sha1}"

    ctx = SessionContext(
        cookies={
            "__Secure-1PSID": "g.a000ValidPsid",
            "__Secure-1PAPISID": sapisid,
        }
    )
    stream_h = HeaderBuilder.build_stream_headers(ctx, "gemini-4-argon")
    batch_h = HeaderBuilder.build_batch_headers(ctx)
    upload_h = HeaderBuilder.build_upload_headers(ctx)

    assert stream_h["Authorization"].startswith("SAPISIDHASH ")
    assert batch_h["Authorization"].startswith("SAPISIDHASH ")
    assert upload_h["Authorization"].startswith("SAPISIDHASH ")


def test_argon_jspb_headers_compact_and_full():
    """Verifies both compact 9-element and full 25-element x-goog-ext-525001261-jspb headers for Gemini 4 Argon."""
    compact = json.loads(HeaderBuilder.build_compact_model_jspb_header("gemini-4-argon"))
    assert compact == [1, None, None, None, "95a4c0abcba24364", None, None, 0, [4]]

    full = json.loads(HeaderBuilder.build_model_jspb_header("gemini-4-argon", "UUID-TEST-1234"))
    assert full[0] == 1
    assert full[4] == "95a4c0abcba24364"
    assert full[14] == 8
    assert full[15] == 2
    assert full[24] == "Argon4p0"


def test_answered_by_extraction_ignores_reply_text_and_stamps_done_chunk():
    """Verifies that extract_answered_by_label reads candidate metadata while ignoring reply text."""
    # Candidate mentions "3.8 Flash" in reply text [1], but metadata [20] is stamped "4 Argon"
    inner_payload = [
        None,
        ["c_123", "r_456"],
        None,
        None,
        [
            [
                "rc_789",
                ["I am comparing 3.8 Flash and 3.5 Flash-Lite in this text."],
                None,
                None,
                None,
                None,
                None,
                None,
                [2],
                None,
                ["meta", "4 Argon"],
            ]
        ],
    ]
    assert extract_answered_by_label(inner_payload) == "4 Argon"

    acc = StreamingDeltaAccumulator()
    frame = [[["wrb.fr", None, json.dumps(inner_payload)]]]
    chunks = acc.process_frame(frame)
    done_chunks = [c for c in chunks if c.type == "done"]
    assert len(done_chunks) == 1
    assert done_chunks[0].metadata["answered_by"] == "4 Argon"


def test_magic_byte_image_mime_detection():
    """Verifies GeminiUploader.detect_image_mime across PNG, JPEG, GIF, WEBP, BMP, TIFF, AVIF, HEIC."""
    assert GeminiUploader.detect_image_mime(b"\x89PNG\r\n\x1a\n\x00\x00") == "image/png"
    assert GeminiUploader.detect_image_mime(b"\xff\xd8\xff\xe0\x00\x10JFIF") == "image/jpeg"
    assert GeminiUploader.detect_image_mime(b"GIF89a\x01\x00\x01\x00") == "image/gif"
    assert GeminiUploader.detect_image_mime(b"RIFF\x24\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert GeminiUploader.detect_image_mime(b"BM\x36\x00\x00\x00") == "image/bmp"
    assert GeminiUploader.detect_image_mime(b"II*\x00\x08\x00\x00\x00") == "image/tiff"
    assert GeminiUploader.detect_image_mime(b"\x00\x00\x00\x1cftypavif\x00\x00") == "image/avif"
    assert GeminiUploader.detect_image_mime(b"\x00\x00\x00\x18ftypheic\x00\x00") == "image/heic"

    # Extension mismatch overridden by magic bytes
    assert GeminiUploader.detect_mime_type("unknown.bin", data=b"\x89PNG\r\n\x1a\n1234") == "image/png"


@pytest.mark.asyncio
async def test_scotty_resumable_two_step_upload_and_foreign_cookie_strip():
    """Verifies 2-step Scotty resumable upload (start -> X-Goog-Upload-URL -> upload, finalize)."""
    ctx = SessionContext(
        cookies={
            "__Secure-1PSID": "g.a000ValidPsid",
            "SAPISID": "W344P6oDJS752Exo/AXFxXhuC1vuSRHU4-",
            "OSID": "ForeignGmailOsidMustBeStripped",
            "LSID": "ForeignLsidMustBeStripped",
        },
        push_id="feeds/custom_push_id_99",
    )
    uploader = GeminiUploader(session_context=ctx, upload_url="https://content-push.googleapis.com")

    # Verify foreign cookies were stripped on init
    assert "OSID" not in uploader._get_clean_cookies()
    assert "LSID" not in uploader._get_clean_cookies()
    assert "__Secure-1PSID" in uploader._get_clean_cookies()

    captured_calls = []

    async def fake_post(self_client, url, **kwargs):
        captured_calls.append((url, kwargs.get("headers", {}), kwargs.get("content", b"")))
        req = httpx.Request("POST", url)
        if len(captured_calls) == 1:
            # Step 1 response with X-Goog-Upload-URL
            return httpx.Response(
                status_code=200,
                headers={"X-Goog-Upload-URL": "https://content-push.googleapis.com/upload/?upload_id=scotty_123"},
                request=req,
            )
        # Step 2 finalize response with resource URI
        return httpx.Response(
            status_code=200,
            text="/contrib_service/ttl_1d/scotty_uploaded_resource_999",
            request=req,
        )

    png_bytes = b"\x89PNG\r\n\x1a\nFAKE_PNG_DATA"
    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        uri = await uploader.upload_file(png_bytes, "diagram.png", use_resumable=True)

    assert uri == "/contrib_service/ttl_1d/scotty_uploaded_resource_999"
    assert len(captured_calls) == 2

    # Check Step 1 headers
    step1_url, step1_headers, _ = captured_calls[0]
    assert step1_headers["X-Goog-Upload-Protocol"] == "resumable"
    assert step1_headers["X-Goog-Upload-Command"] == "start"
    assert step1_headers["X-Goog-Upload-Header-Content-Type"] == "image/png"
    assert step1_headers["X-Goog-Upload-Header-Content-Length"] == str(len(png_bytes))
    assert step1_headers["X-Tenant-Id"] == "bard-storage"
    assert step1_headers["Push-ID"] == "feeds/custom_push_id_99"
    assert step1_headers["Authorization"].startswith("SAPISIDHASH ")
    assert "OSID=" not in step1_headers.get("Cookie", "")

    # Check Step 2 headers
    step2_url, step2_headers, step2_body = captured_calls[1]
    assert "upload_id=scotty_123" in step2_url
    assert step2_headers["X-Goog-Upload-Command"] == "upload, finalize"
    assert step2_headers["X-Goog-Upload-Offset"] == "0"
    assert step2_body == png_bytes


@pytest.mark.asyncio
async def test_fife_image_downloader_c8o8fe_and_alr_redirect_chain():
    """Verifies c8o8Fe full-size resolution, =d-I?alr=yes text redirect following, and placeholder replacement."""
    raw_text = "Here is your image: http://googleusercontent.com/image_generation_content/0"
    resolved = resolve_image_placeholders(
        raw_text,
        ["https://lh3.googleusercontent.com/gg-dl/real_signed_image_abc"],
    )
    assert resolved == "Here is your image: https://lh3.googleusercontent.com/gg-dl/real_signed_image_abc"

    ctx = SessionContext(
        cookies={
            "__Secure-1PSID": "g.a000ValidPsid",
            "OSID": "PoisonGmailOsid",
            "__Secure-OSID": "PoisonSecureOsid",
        }
    )
    mock_bridge = AsyncMock()
    mock_bridge.ctx = ctx
    mock_bridge.get_full_size_image_url = AsyncMock(
        return_value="https://lh3.googleusercontent.com/gg-dl/full_size_img_xyz"
    )

    downloader = GeminiImageDownloader(session_context=ctx, client=mock_bridge)
    clean_cookies = downloader.get_clean_cookies()
    assert "OSID" not in clean_cookies
    assert "__Secure-OSID" not in clean_cookies
    assert clean_cookies["__Secure-1PSID"] == "g.a000ValidPsid"

    visited_urls = []

    async def fake_get(self_client, url, **kwargs):
        visited_urls.append((url, kwargs.get("headers", {})))
        req = httpx.Request("GET", url)
        if len(visited_urls) == 1:
            # First hop returns text/plain redirect URL (Google FIFE alr=yes behavior)
            return httpx.Response(
                status_code=200,
                headers={"content-type": "text/plain; charset=utf-8"},
                text="https://work.fife.usercontent.google.com/rd-gg-dl/final_signed_binary_url",
                request=req,
            )
        # Second hop returns actual image/png bytes
        return httpx.Response(
            status_code=200,
            headers={"content-type": "image/png"},
            content=b"\x89PNG\r\n\x1a\nFINAL_IMAGE_BYTES",
            request=req,
        )

    meta = {
        "url": "https://lh3.googleusercontent.com/gg-dl/preview_img",
        "image_id": "img_id_001",
        "cid": "c_111",
        "rid": "r_222",
        "rcid": "rc_333",
    }

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        img_bytes, mime = await downloader.download_image(meta)

    assert img_bytes == b"\x89PNG\r\n\x1a\nFINAL_IMAGE_BYTES"
    assert mime == "image/png"
    assert len(visited_urls) == 2
    assert visited_urls[0][0] == "https://lh3.googleusercontent.com/gg-dl/full_size_img_xyz=d-I?alr=yes"
    assert visited_urls[1][0] == "https://work.fife.usercontent.google.com/rd-gg-dl/final_signed_binary_url"
    assert "OSID" not in visited_urls[0][1].get("Cookie", "")


def test_api_images_download_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verifies POST /api/images/download endpoint returns base64 and raw image payloads."""
    vault_file = tmp_path / "vault.enc"
    test_vault = CredentialVault(vault_path=vault_file)
    test_sm = SessionManager(
        base_url="http://127.0.0.1:19999",
        vault=test_vault,
        rotation_interval_seconds=3600.0,
    )
    monkeypatch.setattr(main_module, "vault", test_vault)
    monkeypatch.setattr(main_module, "session_manager", test_sm)
    monkeypatch.setattr(main_module.app.state, "vault", test_vault, raising=False)
    monkeypatch.setattr(main_module.app.state, "session_manager", test_sm, raising=False)

    async def fake_download_image(self_dl, meta, http_client=None):
        return b"\x89PNG\r\n\x1a\nENDPOINT_IMAGE", "image/png"

    with patch.object(GeminiImageDownloader, "download_image", new=fake_download_image):
        with TestClient(main_module.app) as client:
            main_module.vault = test_vault
            main_module.session_manager = test_sm

            resp_b64 = client.post(
                "/api/images/download",
                json={"url": "https://lh3.googleusercontent.com/gg-dl/test", "format": "base64"},
            )
            assert resp_b64.status_code == 200
            body = resp_b64.json()
            assert body["ok"] is True
            assert body["mime_type"] == "image/png"
            assert body["size_bytes"] == len(b"\x89PNG\r\n\x1a\nENDPOINT_IMAGE")

            resp_raw = client.post(
                "/api/images/download",
                json={"url": "https://lh3.googleusercontent.com/gg-dl/test", "format": "raw"},
            )
            assert resp_raw.status_code == 200
            assert resp_raw.content == b"\x89PNG\r\n\x1a\nENDPOINT_IMAGE"
