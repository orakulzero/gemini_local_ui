"""
backend/app/openai/image_store.py
Local storage and Markdown URL rewriting for Gemini-generated images (`GET /files/{name}`).

Gemini Web returns generated images via authenticated `lh3.googleusercontent.com/gg-dl/...`
URLs that require Google cookies or `c8o8Fe` RPC resolution and cannot be rendered
directly in standard OpenAI clients (OpenWebUI, LobeChat, LibreChat). This module stores
downloaded image bytes locally as `<uuid32>.(png|jpg|webp|gif)` and rewrites `gg-dl`
references into unauthenticated local `/files/<name>` Markdown links.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp|gif)$")

EXT_BY_MIME: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}

MIME_BY_EXT: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

GG_DL_URL_RE = re.compile(r"https://lh3\.googleusercontent\.com/gg(?:-dl)?/[^\s)\"'>]+")

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _resolve_images_dir(data_dir: str | Path | None = None) -> Path:
    base = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    return base / "images"


def save_image(
    data: bytes,
    mime: str = "image/png",
    data_dir: str | Path | None = None,
) -> str:
    """
    Saves raw image bytes under `<data_dir>/images/<uuid32>.<ext>` and returns the filename.
    """
    clean_mime = (mime or "image/png").split(";")[0].strip().lower()
    ext = EXT_BY_MIME.get(clean_mime, "png")
    name = f"{uuid.uuid4().hex}.{ext}"
    folder = _resolve_images_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return name


def read_image(
    name: str,
    data_dir: str | Path | None = None,
) -> tuple[bytes, str] | None:
    """
    Reads a stored image by filename after strict `NAME_RE` validation to prevent path traversal.
    Returns `(bytes, mime_type)` or `None` if invalid/missing.
    """
    if not name or not isinstance(name, str) or not NAME_RE.match(name):
        return None
    path = _resolve_images_dir(data_dir) / name
    if not path.is_file():
        return None
    mime = MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")
    return path.read_bytes(), mime


def link_images(
    text: str,
    images: list[dict[str, Any]],
    base_url: str = "",
) -> str:
    """
    Replaces raw `https://lh3.googleusercontent.com/gg-dl/...` links in `text` (or appends
    Markdown image tags `[![title](url)](url)`) pointing to `<base_url>/files/<file>`.
    """
    if not images:
        return text or ""

    prefix = base_url.rstrip("/") if base_url else ""
    body = text or ""

    # Collect local URLs for all images that have a saved `file` name (or explicit `local_url`)
    local_entries: list[tuple[str, str, str]] = []
    for item in images:
        if not isinstance(item, dict):
            continue
        file_name = item.get("file")
        local_url = item.get("local_url")
        if not local_url and file_name:
            local_url = f"{prefix}/files/{file_name}"
        if not local_url:
            continue
        orig_url = str(item.get("url") or "")
        title = str(item.get("title") or item.get("alt") or "image").strip("[]") or "image"
        local_entries.append((orig_url, local_url, title))

    if not local_entries:
        return body

    # 1. Replace exact original URL occurrences first
    replaced_indices: set[int] = set()
    for idx, (orig_url, local_url, title) in enumerate(local_entries):
        if orig_url and orig_url in body:
            # If already inside markdown image syntax `![](orig_url)`, just swap the URL
            if f"]({orig_url})" in body:
                body = body.replace(f"]({orig_url})", f"]({local_url})")
            else:
                body = body.replace(orig_url, f"![{title}]({local_url})")
            replaced_indices.add(idx)

    # 2. Replace any remaining generic gg-dl URLs in text sequentially
    remaining_entries = [
        (idx, entry) for idx, entry in enumerate(local_entries) if idx not in replaced_indices
    ]
    if remaining_entries:
        rem_iter = iter(remaining_entries)

        def _sub_gg_dl(match: re.Match[str]) -> str:
            try:
                idx, (_, local_url, title) = next(rem_iter)
                replaced_indices.add(idx)
                return f"![{title}]({local_url})"
            except StopIteration:
                return match.group(0)

        # Check if gg-dl url is bare or inside markdown link
        for idx, (_, local_url, title) in list(remaining_entries):
            m = GG_DL_URL_RE.search(body)
            if not m:
                break
            matched_url = m.group(0)
            if f"]({matched_url})" in body:
                body = body.replace(f"]({matched_url})", f"]({local_url})", 1)
            else:
                body = body.replace(matched_url, f"![{title}]({local_url})", 1)
            replaced_indices.add(idx)

    # 3. Append any images not yet referenced in the text
    appended_blocks: list[str] = []
    for idx, (_, local_url, title) in enumerate(local_entries):
        if idx not in replaced_indices and local_url not in body:
            appended_blocks.append(f"![{title}]({local_url})")

    if appended_blocks:
        body = body.rstrip()
        sep = "\n\n" if body else ""
        body = f"{body}{sep}" + "\n\n".join(appended_blocks)

    return body
