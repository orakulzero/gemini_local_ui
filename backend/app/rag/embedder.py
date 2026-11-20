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
import time
from typing import Any, Sequence
from urllib.parse import urlparse
import zlib

import httpx
import numpy as np

from backend.app.mcp.win32_job import Win32JobGuard

logger = logging.getLogger("gemini_rag.embedder")

EMBEDDING_DIM = 512
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
QWEN3_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
QWEN3_ONNX_REPO_ID = "onnx-community/Qwen3-Embedding-0.6B-ONNX"
QWEN3_REMOTE_BACKEND_ID = "remote_qwen3_512"
QWEN3_QUERY_PREFIX = (
    "Instruct: Given a technical search query, retrieve relevant passages that answer the query\nQuery: "
)


class RemoteEmbedderUnavailableError(RuntimeError):
    """Raised when the configured remote embedding endpoint cannot be reached."""


def _normalize_remote_embed_url(raw_url: str) -> str:
    """Validate and normalize an OpenAI-compatible `/v1/embeddings` HTTP endpoint URL."""
    cleaned = raw_url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Invalid GEMINI_RAG_REMOTE_EMBED_URL: {raw_url!r}")
    path = parsed.path.rstrip("/")
    if not path:
        return f"{parsed.scheme}://{parsed.netloc}/v1/embeddings"
    if path == "/v1":
        return f"{parsed.scheme}://{parsed.netloc}/v1/embeddings"
    return cleaned


def _normalize_l2_rows(raw_matrix: np.ndarray, dim: int) -> np.ndarray:
    """Apply Matryoshka truncation/padding to `dim` and L2-normalize each float32 row."""
    arr = np.asarray(raw_matrix, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.shape[1] > dim:
        arr = arr[:, :dim]
    elif arr.shape[1] < dim:
        arr = np.pad(arr, ((0, 0), (0, dim - arr.shape[1])))
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.where(norms > 1e-12, norms, 1.0)
    return (arr / norms).astype(np.float32, copy=False)


def get_default_onnx_model_dir() -> Path:
    """Return canonical per-user local weights directory for Qwen3-Embedding-0.6B ONNX."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "gemini_local_ui" / "models" / "qwen3-embedding-0.6b-onnx"

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
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            k32.SetPriorityClass.restype = ctypes.c_int
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
    for rel in (
        Path("onnx") / "model_int8.onnx",
        Path("onnx") / "model_quantized.onnx",
        Path("model_int8.onnx"),
        Path("model_quantized.onnx"),
        Path("model.onnx"),
        Path("onnx") / "model.onnx",
    ):
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
    candidates.append(get_default_onnx_model_dir())
    for cand in candidates:
        if cand.is_dir() and (cand / "tokenizer.json").exists() and _find_primary_onnx_file(cand) is not None:
            if not verify_onnx_weights_sha256(cand):
                logger.error("ONNX SHA-256 mismatch for %s; failing closed.", cand)
                return None
            return cand

    auto_dl = os.environ.get("GEMINI_RAG_AUTO_DOWNLOAD", "").strip() == "1"
    try:
        from huggingface_hub import snapshot_download
        for repo_id in (QWEN3_ONNX_REPO_ID, QWEN3_MODEL_ID):
            try:
                snap = Path(
                    snapshot_download(repo_id=repo_id, local_files_only=not auto_dl)
                )
            except Exception:
                continue
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
                sess_opts.intra_op_num_threads = max(1, min(8, os.cpu_count() or 4))
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
                pad_id = _WORKER_TOKENIZER.token_to_id("<|endoftext|>")
                if pad_id is None:
                    pad_id = 151643
                max_len = max(32, min(1024, int(os.environ.get("GEMINI_RAG_MAX_TOKENS", "192"))))
                _WORKER_TOKENIZER.enable_padding(pad_id=int(pad_id), pad_token="<|endoftext|>")
                _WORKER_TOKENIZER.enable_truncation(max_length=max_len)

            in_meta = list(_WORKER_ONNX_SESSION.get_inputs())
            in_names = {i.name for i in in_meta}
            kv_specs = [(i.name, i.shape) for i in in_meta if i.name.startswith("past_key_values.")]
            out_name = _WORKER_ONNX_SESSION.get_outputs()[0].name
            batch_size = max(1, min(32, int(os.environ.get("GEMINI_RAG_BATCH_SIZE", "8"))))

            # Length-bucketed micro-batching eliminates worst-case padding waste while restoring input order.
            order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
            result = np.zeros((len(texts), dim), dtype=np.float32)
            for start in range(0, len(order), batch_size):
                batch_indices = order[start : start + batch_size]
                sub = [texts[i] for i in batch_indices]
                encodings = _WORKER_TOKENIZER.encode_batch(sub)
                input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
                attn_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
                feeds: dict[str, Any] = {"input_ids": input_ids, "attention_mask": attn_mask}
                if "position_ids" in in_names:
                    feeds["position_ids"] = np.maximum(np.cumsum(attn_mask, axis=1) - 1, 0).astype(np.int64)
                bsz = input_ids.shape[0]
                for kv_name, kv_shape in kv_specs:
                    n_heads = int(kv_shape[1]) if len(kv_shape) > 1 and isinstance(kv_shape[1], int) else 8
                    head_dim = int(kv_shape[3]) if len(kv_shape) > 3 and isinstance(kv_shape[3], int) else 128
                    feeds[kv_name] = np.zeros((bsz, n_heads, 0, head_dim), dtype=np.float32)

                outputs = _WORKER_ONNX_SESSION.run([out_name], feeds)
                hidden = np.asarray(outputs[0], dtype=np.float32)
                if hidden.ndim == 3:
                    seq_lens = np.maximum(attn_mask.sum(axis=1) - 1, 0)
                    pooled = hidden[np.arange(hidden.shape[0]), seq_lens]
                else:
                    pooled = hidden
                sliced = pooled[:, :dim].astype(np.float32, copy=False)
                norms = np.linalg.norm(sliced, axis=1, keepdims=True)
                norms = np.where(norms > 1e-12, norms, 1.0)
                normed = (sliced / norms).astype(np.float32)
                for row_i, orig_idx in enumerate(batch_indices):
                    result[orig_idx] = normed[row_i]
            return result
        except Exception as err:
            logger.warning("ONNX worker inference failed, falling back to subword_ngram_512: %s", err)
    return compute_subword_ngram_embeddings(texts, dim=dim)


class LocalEmbedder:
    """512d Embedder supporting remote smart-server HTTP, local ONNX Qwen3, and subword fallback."""

    def __init__(
        self,
        *,
        mode: str = "auto",
        model_dir: str | Path | None = None,
        dim: int = EMBEDDING_DIM,
        remote_url: str | None = None,
        remote_model: str | None = None,
        thermal_telemetry_url: str | None = None,
    ) -> None:
        self.dim = int(dim)
        raw_mode = mode.lower().strip()
        if raw_mode == "auto" and model_dir is None and remote_url is None:
            env_mode = os.environ.get("GEMINI_RAG_EMBEDDER_MODE", "").lower().strip()
            if env_mode in ("subword", "disabled", "auto", "remote", "onnx"):
                raw_mode = env_mode
        self.mode = raw_mode
        self.worker_pid: int | None = None
        self.win32_job_bound: bool = False
        self._executor: ProcessPoolExecutor | None = None
        self._job_guard: Win32JobGuard | None = None
        self._pool_lock = asyncio.Lock()
        self._http_client: httpx.AsyncClient | None = None
        self._last_thermal_poll: float = 0.0

        self.remote_url: str | None = None
        self.remote_model: str = (
            remote_model or os.environ.get("GEMINI_RAG_REMOTE_EMBED_MODEL", "qwen3-embedding-0.6b")
        ).strip() or "qwen3-embedding-0.6b"
        self.remote_timeout: float = max(2.0, min(180.0, float(os.environ.get("GEMINI_RAG_REMOTE_EMBED_TIMEOUT", "60.0"))))
        self.remote_batch_size: int = max(1, min(32, int(os.environ.get("GEMINI_RAG_REMOTE_BATCH_SIZE", "4"))))
        self.remote_max_chars: int = max(256, min(16000, int(os.environ.get("GEMINI_RAG_REMOTE_MAX_CHARS", "900"))))
        raw_therm = (
            thermal_telemetry_url
            if thermal_telemetry_url is not None
            else os.environ.get("GEMINI_RAG_THERMAL_TELEMETRY_URL", "")
        )
        self.thermal_telemetry_url: str | None = raw_therm.strip() or None
        self.thermal_warn_pause_sec: float = max(0.0, float(os.environ.get("GEMINI_RAG_THERMAL_WARN_PAUSE_SEC", "1.5")))
        self.thermal_throttle_pause_sec: float = max(0.0, float(os.environ.get("GEMINI_RAG_THERMAL_THROTTLE_PAUSE_SEC", "5.0")))
        self.remote_reachable: bool | None = None
        self.last_remote_error: str | None = None
        self.last_thermal_state: dict[str, Any] | None = None

        raw_remote = (
            remote_url.strip()
            if remote_url is not None
            else (
                os.environ.get("GEMINI_RAG_REMOTE_EMBED_URL", "").strip()
                if model_dir is None and self.mode in ("auto", "remote")
                else ""
            )
        )

        if self.mode == "disabled":
            self.onnx_dir: Path | None = None
            self.weights_available: bool = False
            self.is_available: bool = False
            self.backend_name: str = "disabled"
        elif raw_remote and self.mode in ("auto", "remote"):
            self.remote_url = _normalize_remote_embed_url(raw_remote)
            self.onnx_dir = None
            self.weights_available = True
            self.is_available = True
            self.backend_name = (
                os.environ.get("GEMINI_RAG_REMOTE_EMBED_ID", "").strip() or QWEN3_REMOTE_BACKEND_ID
            )
        elif self.mode == "remote":
            self.onnx_dir = None
            self.weights_available = False
            self.is_available = False
            self.backend_name = "remote_unconfigured"
        else:
            self.onnx_dir = None if self.mode == "subword" else _resolve_local_onnx_dir(model_dir)
            self.weights_available = self.onnx_dir is not None
            self.is_available = True
            self.backend_name = "onnx_qwen3_512" if self.onnx_dir is not None else "subword_ngram_512"

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=self.remote_timeout, trust_env=False)
        return self._http_client

    async def _apply_thermal_guard(self) -> None:
        """Respect Realme GT Neo 3 thermal guard (41C warn / 44C throttle) during bulk indexing."""
        if not self.thermal_telemetry_url:
            return
        now = time.monotonic()
        if (now - self._last_thermal_poll) < 5.0:
            return
        self._last_thermal_poll = now
        try:
            client = self._get_http_client()
            resp = await client.get(self.thermal_telemetry_url, timeout=1.5)
            if resp.status_code != 200:
                return
            snap = resp.json()
            tg = snap.get("thermal_guard") or {}
            bat = snap.get("battery") or {}
            level = str(tg.get("level") or "unknown").lower()
            temp_c = bat.get("temperature_c")
            self.last_thermal_state = {
                "level": level,
                "temp_c": temp_c,
                "warn_c": tg.get("warn_c"),
                "throttle_c": tg.get("throttle_c"),
            }
            if level == "throttle" and self.thermal_throttle_pause_sec > 0:
                logger.warning(
                    "Smart-server thermal guard THROTTLE (%sC); pausing %.1fs before next batch.",
                    temp_c,
                    self.thermal_throttle_pause_sec,
                )
                await asyncio.sleep(self.thermal_throttle_pause_sec)
            elif level == "warn" and self.thermal_warn_pause_sec > 0:
                logger.info(
                    "Smart-server thermal guard WARN (%sC); pacing %.1fs.",
                    temp_c,
                    self.thermal_warn_pause_sec,
                )
                await asyncio.sleep(self.thermal_warn_pause_sec)
        except Exception:
            return

    async def _embed_remote_texts(self, texts: list[str], *, check_thermal: bool = True) -> np.ndarray:
        assert self.remote_url is not None
        client = self._get_http_client()
        result = np.zeros((len(texts), self.dim), dtype=np.float32)
        for start in range(0, len(texts), self.remote_batch_size):
            if check_thermal:
                await self._apply_thermal_guard()
            raw_sub = texts[start : start + self.remote_batch_size]
            sub = [(t[: self.remote_max_chars] if t and t.strip() else " ") for t in raw_sub]
            try:
                resp = None
                for attempt in range(2):
                    try:
                        resp = await client.post(
                            self.remote_url,
                            json={"model": self.remote_model, "input": sub},
                            timeout=self.remote_timeout,
                        )
                        break
                    except httpx.RemoteProtocolError:
                        if attempt == 0:
                            continue
                        raise
                assert resp is not None
                if resp.status_code != 200:
                    detail = resp.text[:240].strip()
                    raise RemoteEmbedderUnavailableError(
                        f"Remote embedder HTTP {resp.status_code}: {detail}"
                    )
                payload = resp.json()
                rows = payload.get("data")
                if not isinstance(rows, list) or len(rows) != len(sub):
                    raise RemoteEmbedderUnavailableError(
                        f"Remote embedder malformed response (expected {len(sub)} vectors)"
                    )
                ordered = sorted(rows, key=lambda item: int(item.get("index", 0)))
                mat = np.array([item["embedding"] for item in ordered], dtype=np.float32)
                normed = _normalize_l2_rows(mat, self.dim)
                result[start : start + len(sub)] = normed
                self.remote_reachable = True
                self.last_remote_error = None
            except RemoteEmbedderUnavailableError as exc:
                self.remote_reachable = False
                self.last_remote_error = str(exc)
                raise
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                err_desc = f"{type(exc).__name__}: {exc}" if str(exc).strip() else type(exc).__name__
                self.remote_reachable = False
                self.last_remote_error = err_desc
                raise RemoteEmbedderUnavailableError(f"Remote embedder unavailable: {err_desc}") from exc
        return result

    async def warmup(self) -> dict[str, Any]:
        """Ensure the `max_workers=1` spawn worker is alive (skipped in remote HTTP mode)."""
        if not self.is_available:
            return {"worker_pid": None, "win32_job_bound": False, "backend": self.backend_name}
        if self.remote_url is not None:
            return {
                "worker_pid": None,
                "win32_job_bound": False,
                "backend": self.backend_name,
                "remote_url": self.remote_url,
            }
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
        if self.remote_url is not None:
            return await self._embed_remote_texts(items, check_thermal=True)
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
        if self.remote_url is not None:
            formatted = q if q.startswith("Instruct:") else f"{QWEN3_QUERY_PREFIX}{q}"
            mat = await self._embed_remote_texts([formatted], check_thermal=False)
            return mat[0]
        if self.onnx_dir is not None:
            formatted = q if q.startswith("Instruct:") else f"{QWEN3_QUERY_PREFIX}{q}"
            mat = await self.embed_documents([formatted])
            return mat[0]
        if q.startswith(QWEN3_QUERY_PREFIX):
            q = q[len(QWEN3_QUERY_PREFIX) :].strip()
        return compute_subword_ngram_embeddings([q], dim=self.dim)[0]

    async def aclose(self) -> None:
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            self._http_client = None
        self.shutdown()

    def shutdown(self) -> None:
        if self._http_client is not None:
            client = self._http_client
            self._http_client = None
            try:
                loop = asyncio.get_running_loop()
                if not loop.is_closed():
                    loop.create_task(client.aclose())
            except RuntimeError:
                pass
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
