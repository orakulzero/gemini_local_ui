"""
backend/app/rag/chunker.py
Code-fence, symbol, and Markdown-aware document chunker plus redacted brain digest collector.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
from pathlib import Path
import re
from typing import Any

from backend.app.observability.telemetry import redact_sensitive_processor

logger = logging.getLogger("gemini_rag.chunker")

_INLINE_SECRET_RE = re.compile(
    r"(?i)((?:cookie|token|secret|password|authorization|psid|sapisid|snlm0e|dpapi|api_key)"
    r"[\w.-]*\s*[:=]\s*)([^\s,;\"']+)"
)
_BLOCK_SPLIT_RE = re.compile(
    r"(?m)(?=^```|^#{1,4}\s+|^(?:async\s+def|def|class|pub\s+fn|fn|export\s+(?:async\s+)?(?:function|class|interface|type))\s+)"
)


@dataclass
class ChunkPiece:
    chunk_index: int
    content: str
    content_sha256: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_semantic_units(text: str) -> list[str]:
    """Split text while keeping fenced code blocks intact when under budget."""
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []

    units: list[str] = []
    fence_parts = re.split(r"(```[\s\S]*?```)", normalized)
    for part in fence_parts:
        part_s = part.strip()
        if not part_s:
            continue
        if part_s.startswith("```") and part_s.endswith("```"):
            units.append(part_s)
            continue
        blocks = _BLOCK_SPLIT_RE.split(part_s)
        for blk in blocks:
            for para in re.split(r"\n{2,}", blk.strip()):
                p = para.strip()
                if p:
                    units.append(p)
    return units


def chunk_document(
    text: str,
    *,
    max_chars: int = 1200,
    overlap_chars: int = 180,
    source_type: str = "workspace_file",
) -> list[ChunkPiece]:
    """Chunk Markdown, prose, or source code into bounded overlapping pieces."""
    max_c = max(128, int(max_chars))
    ov_c = max(0, min(int(overlap_chars), max_c // 2))
    units = _split_semantic_units(text)
    if not units:
        return []

    raw_chunks: list[str] = []
    buf = ""
    for unit in units:
        if len(unit) > max_c:
            if buf:
                raw_chunks.append(buf)
                buf = ""
            step = max(1, max_c - ov_c)
            start = 0
            while start < len(unit):
                seg = unit[start : start + max_c].strip()
                if seg:
                    raw_chunks.append(seg)
                if start + max_c >= len(unit):
                    break
                start += step
            continue

        candidate = f"{buf}\n\n{unit}" if buf else unit
        if len(candidate) <= max_c:
            buf = candidate
        else:
            if buf:
                raw_chunks.append(buf)
                tail = buf[-ov_c:].strip() if ov_c > 0 else ""
                merged = f"{tail}\n\n{unit}".strip() if tail else unit
                buf = merged if len(merged) <= max_c else unit
            else:
                buf = unit

    if buf:
        raw_chunks.append(buf)

    pieces: list[ChunkPiece] = []
    for idx, chunk_str in enumerate(raw_chunks):
        pieces.append(
            ChunkPiece(
                chunk_index=idx,
                content=chunk_str,
                content_sha256=_sha256_text(chunk_str),
                metadata={"source_type": source_type, "char_len": len(chunk_str)},
            )
        )
    return pieces


def sanitize_digest_text(text: str) -> str:
    """Scrub secret key-value patterns using observability redaction rules."""
    scrubbed = _INLINE_SECRET_RE.sub(r"\1[REDACTED]", text)
    probe = redact_sensitive_processor(None, "ingest", {"content": scrubbed})
    return str(probe.get("content", scrubbed))


def collect_brain_digest_documents(
    brain_root: Path | None = None,
    *,
    max_files: int = 64,
    max_bytes_per_file: int = 65536,
) -> list[dict[str, Any]]:
    """Bounded scan of `.md` digest artifacts in brain_root, skipping transcripts & binaries."""
    root = Path(brain_root) if brain_root is not None else (Path.home() / ".gemini" / "antigravity" / "brain")
    if not root.exists() or not root.is_dir():
        return []

    skip_dirs = {".system_generated", "logs", "scratch", "node_modules", ".git", "__pycache__"}
    docs: list[dict[str, Any]] = []

    for path in sorted(root.rglob("*.md")):
        if len(docs) >= max_files:
            break
        rel_parts = set(path.relative_to(root).parts)
        if rel_parts & skip_dirs:
            continue
        lname = path.name.lower()
        if "transcript" in lname or lname.endswith(".resolved.md"):
            continue
        try:
            st = path.stat()
            if st.st_size <= 0 or st.st_size > max_bytes_per_file:
                continue
            raw = path.read_text(encoding="utf-8", errors="ignore").strip()
            if not raw or "\x00" in raw:
                continue
            clean = sanitize_digest_text(raw)
            rel_id = path.relative_to(root).as_posix()
            first_line = clean.splitlines()[0].lstrip("# ").strip() if clean else path.stem
            docs.append({
                "source_type": "brain_digest",
                "source_id": f"brain://{rel_id}",
                "source_title": first_line[:120] or path.stem,
                "content": clean,
            })
        except Exception as err:
            logger.debug("Skipping brain digest %s: %s", path, err)
    return docs
