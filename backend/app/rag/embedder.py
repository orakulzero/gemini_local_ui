"""
backend/app/rag/embedder.py
512-dim L2-normalized RAG Embedder with Win32 Job-guarded `max_workers=1` spawn pool,
local-only ONNX Qwen3-Embedding-0.6B support, and deterministic Multilingual Subword +
CamelCase/SnakeCase + Char-Ngram feature-hashing fallback (`subword_ngram_512`).
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
import hashlib
import hmac
import logging
import multiprocessing
import os
from pathlib import Path
import re
import sys
from typing import Any, Sequence
import zlib

import numpy as np

from backend.app.mcp.win32_job import Win32JobGuard

logger = logging.getLogger("gemini_rag.embedder")

EMBEDDING_DIM = 512
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
QWEN3_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
QWEN3_QUERY_PREFIX = (
    "Instruct: Given a technical search query, retrieve relevant passages that answer the query\nQuery: "
)

_CAMEL_RE_1 = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_RE_2 = re.compile(r"([A-Z]+)([A-Z][a-z])")
_WORD_RE = re.compile(r"[a-zA-Z0-9\u0400-\u04FF]+")

_RU_SUFFIXES = (
    "ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ием", "иям", "иях",
    "ах", "ях", "ов", "ев", "ей", "ий", "ый", "ой", "ая", "яя", "ое", "ее", "ые",
    "ие", "ам", "ям", "ом", "ем", "ую", "юю", "ть", "ти", "ет", "ит", "ут", "ют",
    "ат", "ят", "ся", "сь", "ия", "ии", "ию", "ешь", "ите", "ать", "ять", "ость",
    "а", "я", "у", "ю", "ы", "и", "е", "о",
)
_EN_SUFFIXES = ("ingly", "ation", "tions", "ments", "ing", "tion", "ions", "ment", "ed", "es", "er", "ly", "s")

_WORKER_ONNX_SESSION: Any = None
_WORKER_TOKENIZER: Any = None


def _apply_worker_below_normal_priority() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        except Exception:
            pass


def _worker_get_pid() -> int:
    _apply_worker_below_normal_priority()
    return os.getpid()


def _stem_token(tok: str) -> str:
    t = tok.lower().replace("ё", "е")
    if len(t) < 5:
        return t
    if "\u0400" <= t[0] <= "\u04ff":
        for suf in _RU_SUFFIXES:
            if t.endswith(suf) and len(t) - len(suf) >= 4:
                return t[: -len(suf)]
        return t
    for suf in _EN_SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            return t[: -len(suf)]
    return t


def extract_subword_tokens(text: str) -> tuple[list[str], list[str]]:
    """Return (raw_split_tokens, morphological_stems) splitting camelCase & snake_case."""
    if not text:
        return [], []
    expanded = _CAMEL_RE_2.sub(r"\1 \2", _CAMEL_RE_1.sub(r"\1 \2", text))
    expanded = expanded.replace("_", " ").replace("-", " ").replace(".", " ").replace("/", " ")
    raw_words = [w.lower().replace("ё", "е") for w in _WORD_RE.findall(expanded) if len(w) >= 2]
    whole_idents = [
        w.lower().replace("ё", "е")
        for w in _WORD_RE.findall(text)
        if len(w) >= 4 and ("_" in text or any(c.isupper() for c in w))
    ]
    all_tokens = raw_words + whole_idents
    stems = [_stem_token(t) for t in raw_words]
    return all_tokens, stems


def _hash_add(vec: np.ndarray, feat: str, weight: float, dim_mask: int, sign_bit: int) -> None:
    h = zlib.crc32(feat.encode("utf-8"))
    idx = h & dim_mask
    sign = 1.0 if (h & sign_bit) else -1.0
    vec[idx] += sign * weight


def compute_subword_ngram_embeddings(texts: Sequence[str], dim: int = EMBEDDING_DIM) -> np.ndarray:
    """Deterministic 512-dim float32 L2-normalized multilingual + code subword embedder."""
    out = np.zeros((len(texts), dim), dtype=np.float32)
    dim_mask = dim - 1
    sign_bit = dim
    for row_idx, raw_text in enumerate(texts):
        vec = out[row_idx]
        tokens, stems = extract_subword_tokens(raw_text)
        for tok in tokens:
            _hash_add(vec, f"t:{tok}", 2.2, dim_mask, sign_bit)
            padded = f"<{tok}>"
            plen = len(padded)
            for n in (3, 4):
                if plen >= n:
                    for i in range(min(24, plen - n + 1)):
                        _hash_add(vec, padded[i : i + n], 0.85, dim_mask, sign_bit)
        for st in stems:
            _hash_add(vec, f"s:{st}", 2.8, dim_mask, sign_bit)
        for i in range(len(stems) - 1):
            _hash_add(vec, f"b:{stems[i]}_{stems[i + 1]}", 1.5, dim_mask, sign_bit)
        norm = float(np.linalg.norm(vec))
        if norm > 1e-12:
            vec /= norm
    return out


def _find_primary_onnx_file(model_dir: Path) -> Path | None:
    if not model_dir.is_dir():
        return None
    for rel in (Path("model.onnx"), Path("onnx") / "model.onnx"):
        cand = model_dir / rel
        if cand.is_file():
            return cand
    found = sorted(p for p in model_dir.rglob("*.onnx") if p.is_file())
    return found[0] if found else None


def verify_onnx_weights_sha256(model_dir: Path, expected_sha256: str | None = None) -> bool:
    """Verify SHA-256 of primary `.onnx` file against expected hash or GEMINI_RAG_ONNX_SHA256."""
    root = Path(model_dir)
    onnx_file = _find_primary_onnx_file(root)
    if onnx_file is None:
        return False
    expected = (
        expected_sha256
        if expected_sha256 is not None
        else os.environ.get("GEMINI_RAG_ONNX_SHA256", "")
    ).strip().lower()
    if not expected:
        return True
    digest = hashlib.sha256()
    try:
        with open(onnx_file, "rb") as f:
            while chunk := f.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        return False
    return hmac.compare_digest(digest.hexdigest().lower(), expected)


def _resolve_local_onnx_dir(explicit_dir: str | Path | None = None) -> Path | None:
    """Locate local Qwen3-Embedding-0.6B ONNX directory without unannounced downloads."""
    candidates: list[Path] = []
    if explicit_dir:
        candidates.append(Path(explicit_dir))
    env_dir = os.environ.get("GEMINI_RAG_MODEL_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    for cand in candidates:
        if cand.is_dir() and (cand / "tokenizer.json").exists() and _find_primary_onnx_file(cand) is not None:
            if not verify_onnx_weights_sha256(cand):
                logger.error("ONNX SHA-256 mismatch for %s; failing closed.", cand)
                return None
            return cand

    auto_dl = os.environ.get("GEMINI_RAG_AUTO_DOWNLOAD", "").strip() == "1"
    try:
        from huggingface_hub import snapshot_download
        snap = Path(
            snapshot_download(repo_id=QWEN3_MODEL_ID, local_files_only=not auto_dl)
        )
        if (snap / "tokenizer.json").exists() and _find_primary_onnx_file(snap) is not None:
            if not verify_onnx_weights_sha256(snap):
                logger.error("ONNX SHA-256 mismatch for cached snapshot %s; failing closed.", snap)
                return None
            return snap
    except Exception:
        pass
    return None


def _worker_embed_batch(texts: list[str], dim: int, onnx_dir: str | None) -> np.ndarray:
    global _WORKER_ONNX_SESSION, _WORKER_TOKENIZER
    _apply_worker_below_normal_priority()
    if onnx_dir:
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            model_root = Path(onnx_dir)
            if _WORKER_ONNX_SESSION is None:
                onnx_path = _find_primary_onnx_file(model_root)
                if onnx_path is None:
                    raise FileNotFoundError(f"No .onnx file in {model_root}")
                sess_opts = ort.SessionOptions()
                sess_opts.intra_op_num_threads = max(1, min(4, os.cpu_count() or 2))
                providers = ["CPUExecutionProvider"]
                if os.environ.get("GEMINI_RAG_PREFER_DML", "").strip() == "1":
                    try:
                        if "DmlExecutionProvider" in ort.get_available_providers():
                            providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
                    except Exception:
                        pass
                _WORKER_ONNX_SESSION = ort.InferenceSession(
                    str(onnx_path), sess_options=sess_opts, providers=providers
                )
                _WORKER_TOKENIZER = Tokenizer.from_file(str(model_root / "tokenizer.json"))
                _WORKER_TOKENIZER.enable_padding(pad_id=0, pad_token="<|endoftext|>")
                _WORKER_TOKENIZER.enable_truncation(max_length=512)

            encodings = _WORKER_TOKENIZER.encode_batch(texts)
            input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
            attn_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
            feeds: dict[str, Any] = {"input_ids": input_ids, "attention_mask": attn_mask}
            in_names = {i.name for i in _WORKER_ONNX_SESSION.get_inputs()}
            if "position_ids" in in_names:
                feeds["position_ids"] = np.cumsum(attn_mask, axis=1) - 1
            outputs = _WORKER_ONNX_SESSION.run(None, feeds)
            hidden = np.asarray(outputs[0], dtype=np.float32)
            if hidden.ndim == 3:
                seq_lens = np.maximum(attn_mask.sum(axis=1) - 1, 0)
                pooled = hidden[np.arange(hidden.shape[0]), seq_lens]
            else:
                pooled = hidden
            sliced = pooled[:, :dim].astype(np.float32, copy=False)
            norms = np.linalg.norm(sliced, axis=1, keepdims=True)
            norms = np.where(norms > 1e-12, norms, 1.0)
            return (sliced / norms).astype(np.float32)
        except Exception as err:
            logger.warning("ONNX worker inference failed, falling back to subword_ngram_512: %s", err)
    return compute_subword_ngram_embeddings(texts, dim=dim)


class LocalEmbedder:
    """512d Embedder managing a lazy Win32 Job-guarded `max_workers=1` spawn worker pool."""

    def __init__(
        self,
        *,
        mode: str = "auto",
        model_dir: str | Path | None = None,
        dim: int = EMBEDDING_DIM,
    ) -> None:
        self.dim = int(dim)
        self.mode = mode.lower().strip()
        self.worker_pid: int | None = None
        self.win32_job_bound: bool = False
        self._executor: ProcessPoolExecutor | None = None
        self._job_guard: Win32JobGuard | None = None
        self._pool_lock = asyncio.Lock()

        if self.mode == "disabled":
            self.onnx_dir: Path | None = None
            self.weights_available: bool = False
            self.is_available: bool = False
            self.backend_name: str = "disabled"
        else:
            self.onnx_dir = None if self.mode == "subword" else _resolve_local_onnx_dir(model_dir)
            self.weights_available = self.onnx_dir is not None
            self.is_available = True
            self.backend_name = "onnx_qwen3_512" if self.onnx_dir is not None else "subword_ngram_512"

    async def warmup(self) -> dict[str, Any]:
        """Ensure the `max_workers=1` spawn worker is alive and bound to a Win32 Job Object."""
        if not self.is_available:
            return {"worker_pid": None, "win32_job_bound": False, "backend": self.backend_name}
        async with self._pool_lock:
            if self._executor is None:
                ctx = multiprocessing.get_context("spawn")
                self._executor = ProcessPoolExecutor(max_workers=1, mp_context=ctx)
                self._job_guard = Win32JobGuard()
                loop = asyncio.get_running_loop()
                pid = await loop.run_in_executor(self._executor, _worker_get_pid)
                self.worker_pid = int(pid)
                self.win32_job_bound = bool(self._job_guard.assign_pid(self.worker_pid))
        return {
            "worker_pid": self.worker_pid,
            "win32_job_bound": self.win32_job_bound,
            "backend": self.backend_name,
        }

    async def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        if not self.is_available:
            raise RuntimeError("Embedder is disabled")
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        items = list(texts)
        if self.onnx_dir is not None or len(items) >= 64:
            await self.warmup()
            assert self._executor is not None
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self._executor,
                _worker_embed_batch,
                items,
                self.dim,
                str(self.onnx_dir) if self.onnx_dir else None,
            )
        return compute_subword_ngram_embeddings(items, dim=self.dim)

    async def embed_query(self, query: str) -> np.ndarray:
        if not self.is_available:
            raise RuntimeError("Embedder is disabled")
        q = query.strip()
        if self.onnx_dir is not None:
            formatted = q if q.startswith("Instruct:") else f"{QWEN3_QUERY_PREFIX}{q}"
            mat = await self.embed_documents([formatted])
            return mat[0]
        if q.startswith(QWEN3_QUERY_PREFIX):
            q = q[len(QWEN3_QUERY_PREFIX) :].strip()
        return compute_subword_ngram_embeddings([q], dim=self.dim)[0]

    def shutdown(self) -> None:
        if self._executor is not None:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            self._executor = None
        if self._job_guard is not None:
            self._job_guard.close()
            self._job_guard = None
        self.worker_pid = None
        self.win32_job_bound = False


RAGEmbedder = LocalEmbedder
