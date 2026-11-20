"""
backend/app/rag/reranker.py
Top-K candidate reranker supporting local ONNX `BAAI/bge-reranker-v2-m3` when present,
with fast deterministic Multilingual Subword + Code-Symbol + Proximity `CrossOverlapReranker`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Sequence

from backend.app.rag.embedder import extract_subword_tokens

logger = logging.getLogger("gemini_rag.reranker")

BGE_RERANKER_ID = "BAAI/bge-reranker-v2-m3"


def _resolve_local_reranker_dir(explicit_dir: str | Path | None = None) -> Path | None:
    cands: list[Path] = []
    if explicit_dir:
        cands.append(Path(explicit_dir))
    env_dir = os.environ.get("GEMINI_RAG_RERANKER_DIR", "").strip()
    if env_dir:
        cands.append(Path(env_dir))
    for cand in cands:
        if cand.is_dir() and (cand / "tokenizer.json").exists() and any(cand.rglob("*.onnx")):
            return cand

    auto_dl = os.environ.get("GEMINI_RAG_AUTO_DOWNLOAD", "").strip() == "1"
    try:
        from huggingface_hub import snapshot_download
        snap = Path(snapshot_download(repo_id=BGE_RERANKER_ID, local_files_only=not auto_dl))
        if (snap / "tokenizer.json").exists() and any(snap.rglob("*.onnx")):
            return snap
    except Exception:
        pass
    return None


def _char_trigrams(text: str) -> set[str]:
    clean = text.lower().replace("ё", "е")
    if len(clean) < 3:
        return {clean} if clean else set()
    limit = min(len(clean), 320)
    return {clean[i : i + 3] for i in range(limit - 2)}


class CrossOverlapReranker:
    """Fast CPU cross-scorer combining stem recall, split code symbols, trigrams, and RRF prior."""

    def score_candidates(self, query: str, candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        if not candidates:
            return []
        q_tokens, q_stems = extract_subword_tokens(query)
        q_tok_set = set(q_tokens)
        q_stem_set = set(q_stems)
        q_tri = _char_trigrams(query)

        scored: list[dict[str, Any]] = []
        for cand in candidates:
            doc_text = f"{cand.get('source_title', '')} {cand.get('content', '')}"
            d_tokens, d_stems = extract_subword_tokens(doc_text)
            d_tok_set = set(d_tokens)
            d_stem_set = set(d_stems)

            tok_recall = (len(q_tok_set & d_tok_set) / len(q_tok_set)) if q_tok_set else 0.0
            stem_recall = (len(q_stem_set & d_stem_set) / len(q_stem_set)) if q_stem_set else 0.0

            d_tri = _char_trigrams(doc_text)
            tri_overlap = (len(q_tri & d_tri) / len(q_tri)) if q_tri else 0.0

            bigram_hits = 0
            if len(q_stems) >= 2 and len(d_stems) >= 2:
                d_bigrams = {(d_stems[i], d_stems[i + 1]) for i in range(len(d_stems) - 1)}
                q_bigrams = [(q_stems[i], q_stems[i + 1]) for i in range(len(q_stems) - 1)]
                bigram_hits = sum(1 for bg in q_bigrams if bg in d_bigrams)
                prox_score = bigram_hits / len(q_bigrams)
            else:
                prox_score = 0.0

            rrf_prior = float(cand.get("rrf_score", cand.get("score", 0.0)))
            combined = (
                0.42 * stem_recall
                + 0.25 * tok_recall
                + 0.15 * tri_overlap
                + 0.10 * prox_score
                + 0.08 * min(1.0, rrf_prior * 30.0)
            )
            item = dict(cand)
            item["rerank_score"] = round(float(combined), 6)
            item["score"] = round(float(combined), 6)
            scored.append(item)

        scored.sort(key=lambda x: (x["score"], x.get("rrf_score", 0.0)), reverse=True)
        return scored


class Reranker:
    """Unified Reranker delegating to local ONNX BGE cross-encoder when cached or CrossOverlapReranker."""

    def __init__(self, *, model_dir: str | Path | None = None) -> None:
        self.onnx_dir = _resolve_local_reranker_dir(model_dir)
        self._fallback = CrossOverlapReranker()
        self._ort_session: Any = None
        self._tokenizer: Any = None
        self.backend_name = "onnx_bge_reranker" if self.onnx_dir is not None else "cross_overlap_v1"

    def rerank(self, query: str, candidates: Sequence[dict[str, Any]], *, top_k: int = 8) -> list[dict[str, Any]]:
        if not candidates:
            return []
        pool = list(candidates[:20])
        if self.onnx_dir is not None:
            try:
                import numpy as np
                import onnxruntime as ort
                from tokenizers import Tokenizer

                if self._ort_session is None:
                    onnx_path = next(self.onnx_dir.rglob("*.onnx"))
                    self._ort_session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
                    self._tokenizer = Tokenizer.from_file(str(self.onnx_dir / "tokenizer.json"))
                    self._tokenizer.enable_padding(pad_id=0)
                    self._tokenizer.enable_truncation(max_length=512)
                pairs = [(query, f"{c.get('source_title', '')}\n{c.get('content', '')}") for c in pool]
                enc = self._tokenizer.encode_batch(pairs)
                ids = np.array([e.ids for e in enc], dtype=np.int64)
                mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
                logits = np.asarray(self._ort_session.run(None, {"input_ids": ids, "attention_mask": mask})[0]).reshape(-1)
                probs = 1.0 / (1.0 + np.exp(-logits))
                out = []
                for c, sc in zip(pool, probs):
                    it = dict(c)
                    it["rerank_score"] = round(float(sc), 6)
                    it["score"] = round(float(sc), 6)
                    out.append(it)
                out.sort(key=lambda x: x["score"], reverse=True)
                return out[:top_k]
            except Exception as err:
                logger.debug("ONNX reranker fallback to CrossOverlapReranker: %s", err)
        return self._fallback.score_candidates(query, pool)[:top_k]
