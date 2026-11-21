"""
backend/app/services/media_postprocessor.py
Localization and metadata accumulation for generated images, citations, and rich media.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any
from urllib.parse import quote

logger = logging.getLogger("gemini_backend.media_postprocessor")

_FIFE_INLINE_URL_RE = re.compile(
    r"https://(?:[a-zA-Z0-9-]+\.)*(?:googleusercontent\.com/(?:rd-)?gg[^\s)\"'>]*|fife\.usercontent\.google\.com/[^\s)\"'>]+)"
)
_FOLLOW_UP_TAG_RE = re.compile(r"<FollowUp\b[^>]*/?>(?:[\s\S]*?</FollowUp>)?", re.IGNORECASE)


def strip_protocol_markup(text: str) -> str:
    """Strip transient Google UI directive tags such as <FollowUp ...> from model output."""
    if not text or ("followup" not in text.lower()):
        return text
    cleaned = _FOLLOW_UP_TAG_RE.sub("", text)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def build_image_proxy_url(raw_url: str) -> str:
    """Wrap a cross-site Google FIFE image URL behind our same-origin authenticated proxy."""
    return f"/api/images/proxy?url={quote(raw_url, safe='')}"


def needs_image_localization(url: Any) -> bool:
    """Check if image URL requires local downloading (googleusercontent / fife)."""
    if not isinstance(url, str) or url.startswith(("/files/", "/api/images/proxy")):
        return False
    return (
        "googleusercontent.com/gg" in url
        or "googleusercontent.com/rd-gg" in url
        or "fife.usercontent.google.com/" in url
    )


# Backward-compatible alias
_needs_image_localization = needs_image_localization


def filter_unlocalized_images_meta(metadata: dict[str, Any]) -> dict[str, Any]:
    """Strip out unlocalized images from metadata prior to local caching."""
    safe_meta = dict(metadata)
    for key in ("images", "generated_images"):
        if key in safe_meta and isinstance(safe_meta[key], list):
            safe_list = [
                img
                for img in safe_meta[key]
                if not (isinstance(img, dict) and needs_image_localization(img.get("url")))
            ]
            if safe_list:
                safe_meta[key] = safe_list
            else:
                safe_meta.pop(key, None)
    return safe_meta


_filter_unlocalized_images_meta = filter_unlocalized_images_meta


async def localize_generated_images(
    images_list: list[dict[str, Any]],
    active_cli: Any,
    cid: str | None,
    rid: str | None,
    rcid: str | None,
    data_dir: Any = None,
) -> bool:
    """Download remote Google gg/gg-dl images, save locally, update in-place to /files/<id>.<ext>."""
    if not images_list:
        return False

    from backend.app.bridge.image_downloader import GeminiImageDownloader
    from backend.app.openai.image_store import save_image

    downloader = GeminiImageDownloader(client=active_cli)
    modified = False

    for img in images_list:
        if not isinstance(img, dict):
            continue
        raw_url = img.get("url")
        if not isinstance(raw_url, str):
            continue
        if not needs_image_localization(raw_url):
            continue

        meta = {
            "url": raw_url,
            "image_id": img.get("image_id", ""),
            "placeholder": img.get("placeholder", ""),
            "cid": cid or "",
            "rid": rid or "",
            "rcid": rcid or "",
        }
        try:
            img_bytes, mime = await downloader.download_image(meta)
            if img_bytes:
                saved_name = save_image(img_bytes, mime or "image/png", data_dir=data_dir)
                if data_dir is not None:
                    from pathlib import Path
                    from backend.app.security.path_sandbox import validate_safe_path
                    validate_safe_path(Path(data_dir) / saved_name, allowed_roots=[Path(data_dir)])
                img["original_url"] = raw_url
                img["url"] = f"/files/{saved_name}"
                modified = True
                continue
        except Exception as dl_err:
            logger.warning(
                "Failed localizing image from %s (%s: %s); falling back to same-origin proxy",
                raw_url[:60],
                type(dl_err).__name__,
                dl_err,
            )

        # Fail-safe CORP protection: never expose raw cross-site FIFE URL directly to <img src>
        img["original_url"] = raw_url
        img["url"] = build_image_proxy_url(raw_url)
        modified = True

    return modified


def rewrite_fife_urls_in_text(text: str, images_list: list[dict[str, Any]] | None = None) -> str:
    """
    Rewrites any raw Google FIFE / gg-dl URLs embedded in Markdown text to their
    localized `/files/<id>.<ext>` path (if available) or same-origin `/api/images/proxy?url=...`.
    """
    if not text:
        return text
    out = text
    for img in images_list or []:
        if not isinstance(img, dict):
            continue
        orig = img.get("original_url")
        local = img.get("url")
        if isinstance(orig, str) and orig and isinstance(local, str) and local and orig in out:
            out = out.replace(orig, local)
    return _FIFE_INLINE_URL_RE.sub(lambda m: build_image_proxy_url(m.group(0)), out)


_localize_generated_images = localize_generated_images


@dataclass
class MediaAccumulator:
    """Accumulates rich media, citations, grounding, and tool execution state during streaming."""
    citations: list[Any] = field(default_factory=list)
    images: list[Any] = field(default_factory=list)
    grounding: dict[str, Any] = field(default_factory=dict)
    youtube: list[Any] = field(default_factory=list)
    code_executions: list[Any] = field(default_factory=list)

    def handle_chunk(self, chunk_type: str, metadata: dict[str, Any]) -> dict[str, Any] | None:
        """Process incoming chunk metadata, update accumulators, return safe UI event data if any."""
        if chunk_type == "citation" and metadata:
            self.citations = metadata.get("citations", self.citations)
            return {"type": "citation", "metadata": metadata}
        if chunk_type == "grounding" and metadata:
            if "generated_images" in metadata and not self.images:
                self.images = metadata["generated_images"]
            safe_grounding = filter_unlocalized_images_meta(metadata)
            if safe_grounding:
                self.grounding = safe_grounding
                return {"type": "grounding", "metadata": safe_grounding}
        if chunk_type == "media" and metadata:
            if "images" in metadata:
                self.images = metadata["images"]
            if "youtube" in metadata:
                self.youtube = metadata["youtube"]
            if "code_executions" in metadata:
                self.code_executions = metadata["code_executions"]
            safe_media = filter_unlocalized_images_meta(metadata)
            if safe_media:
                return {"type": "media", "metadata": safe_media}
        return None

    def build_rich_metadata(
        self,
        *,
        fulfilled_by_id: str,
        done_cid: str | None = None,
        done_rid: str | None = None,
        done_rcid: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Construct database-persisted rich metadata dictionary."""
        rich_meta: dict[str, Any] = {
            "citations": self.citations,
            "grounding": self.grounding,
            "images": self.images,
            "youtube": self.youtube,
            "fulfilled_by_account_id": fulfilled_by_id,
        }
        if self.code_executions:
            rich_meta["code_executions"] = self.code_executions
        if tool_calls:
            rich_meta["tool_calls"] = tool_calls
        if done_cid:
            rich_meta["cid"] = done_cid
        if done_rid:
            rich_meta["rid"] = done_rid
        if done_rcid:
            rich_meta["rcid"] = done_rcid
        return rich_meta
