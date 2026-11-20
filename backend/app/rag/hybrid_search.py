"""
backend/app/rag/hybrid_search.py
Hybrid RAG Orchestrator fusing SQLite FTS5 BM25 + `sqlite-vec` 512d cosine KNN via
Reciprocal Rank Fusion (RRF, k=60) + Reranker, with SHA-256 incremental indexing,
untrusted XML prompt-injection sanitization, and graceful FTS5 degradation.
"""

from __future__ import annotations

from collections import OrderedDict
import html
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Sequence

from backend.app.observability.telemetry import get_tracer
from backend.app.rag.chunker import chunk_document, collect_brain_digest_documents
from backend.app.rag.embedder import LocalEmbedder, RemoteEmbedderUnavailableError
from backend.app.rag.reranker import Reranker
from backend.app.rag.vector_store import SqliteVecStore, VectorStoreProtocol
from backend.app.storage.database import Database

logger = logging.getLogger("gemini_rag.hybrid_search")

_FTS_TOKEN_RE = re.compile(r"[a-zA-Z0-9_\u0400-\u04FF]+")
_TOOL_FENCE_RE = re.compile(r"(?:`{3,}|~{3,})\s*tool_call\b", re.IGNORECASE)
_TAG_ESCAPE_RE = re.compile(r"</?\s*(?:untrusted_rag_context|rag_passage)\b(?:[^>]*>)?", re.IGNORECASE)

_INDEXABLE_EXTS = {
    ".md", ".txt", ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".json", ".toml", ".sql", ".yaml", ".yml",
}
_SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", ".venv", "venv", ".pytest_cache"}


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[int]],
    k: int = 60,
) -> list[tuple[int, float]]:
    """Fuse ranked chunk_id lists via Reciprocal Rank Fusion: sum(1 / (k + rank_1based))."""
    scores: dict[int, float] = {}
    rk = max(1, int(k))
    for rlist in ranked_lists:
        seen_in_list: set[int] = set()
        for rank_idx, cid in enumerate(rlist):
            c_int = int(cid)
            if c_int in seen_in_list:
                continue
            seen_in_list.add(c_int)
            scores[c_int] = scores.get(c_int, 0.0) + (1.0 / (rk + rank_idx + 1))
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def _sanitize_passage_body(raw_body: str) -> str:
    """Neutralize XML breakout tags and embedded ```tool_call fences inside RAG passages."""
    if not raw_body:
        return ""
    s = _TAG_ESCAPE_RE.sub(lambda m: html.escape(m.group(0)), raw_body)
    s = _TOOL_FENCE_RE.sub("```code_block", s)
    return s


def format_rag_context_xml(hits: Sequence[Any]) -> str:
    """Wrap retrieved RAG passages in sanitized `<untrusted_rag_context>` XML envelope."""
    if not hits:
        return ""
    lines = ["<untrusted_rag_context>"]
    for idx, hit in enumerate(hits, start=1):
        h = hit if isinstance(hit, dict) else getattr(hit, "__dict__", {})
        src_type = html.escape(str(h.get("source_type", "unknown")), quote=True)
        src_id = html.escape(str(h.get("source_id", "")), quote=True)
        title = html.escape(str(h.get("source_title", "")), quote=True)
        score = float(h.get("score", 0.0))
        body = _sanitize_passage_body(str(h.get("content", "")))
        lines.append(
            f'  <rag_passage rank="{idx}" source_type="{src_type}" source_id="{src_id}" '
            f'title="{title}" score="{score:.4f}">\n{body}\n  </rag_passage>'
        )
    lines.append("</untrusted_rag_context>")
    return "\n".join(lines)


def _build_fts5_match_expr(query: str, *, use_or: bool = False) -> str:
    tokens = [t for t in _FTS_TOKEN_RE.findall(query) if len(t) >= 2]
    if not tokens:
        return ""
    quoted = [f'"{t.replace(chr(34), "")}"' for t in tokens[:16]]
    return (" OR " if use_or else " ").join(quoted)


def _collect_workspace_files(workspace_dir: Path, *, max_files: int = 1000, max_bytes: int = 131072) -> list[dict[str, Any]]:
    if not workspace_dir.exists() or not workspace_dir.is_dir():
        return []
    docs: list[dict[str, Any]] = []
    for path in sorted(workspace_dir.rglob("*")):
        if len(docs) >= max_files:
            break
        if not path.is_file() or path.suffix.lower() not in _INDEXABLE_EXTS:
            continue
        rel_parts = set(path.relative_to(workspace_dir).parts)
        if rel_parts & _SKIP_DIRS:
            continue
        try:
            st = path.stat()
            if st.st_size <= 0 or st.st_size > max_bytes:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
            if not text or "\x00" in text:
                continue
            rel_posix = path.relative_to(workspace_dir).as_posix()
            docs.append({
                "source_type": "workspace_file",
                "source_id": rel_posix,
                "source_title": path.name,
                "content": text,
            })
        except Exception:
            continue
    return docs


class HybridRAGService:
    """Local Hybrid RAG service combining FTS5 BM25 + `sqlite-vec` 512d + RRF + Reranker."""

    def __init__(
        self,
        db: Database,
        *,
        embedder: LocalEmbedder | None = None,
        reranker: Reranker | None = None,
        vector_store: VectorStoreProtocol | None = None,
        cache_size: int = 128,
        cache_ttl_seconds: float = 60.0,
    ) -> None:
        self.db = db
        self.embedder = embedder if embedder is not None else LocalEmbedder()
        self.reranker = reranker if reranker is not None else Reranker()
        self.vector_store: VectorStoreProtocol = (
            vector_store if vector_store is not None else SqliteVecStore(db)
        )
        self.cache_size = max(16, int(cache_size))
        self.cache_ttl_seconds = float(cache_ttl_seconds)
        self._cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self.db.register_mutation_listener(self.invalidate_cache)

    def invalidate_cache(self) -> None:
        self._cache.clear()

    async def _collect_conversation_docs(self) -> list[dict[str, Any]]:
        rows = await self.db.fetch_all(
            "SELECT m.id, m.conversation_id, m.role, m.content, c.title "
            "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.is_error = 0 AND LENGTH(TRIM(m.content)) >= 12 "
            "ORDER BY m.created_at DESC LIMIT 2000"
        )
        by_conv: dict[str, list[str]] = {}
        titles: dict[str, str] = {}
        for r in rows:
            cid = str(r["conversation_id"])
            titles[cid] = str(r.get("title") or cid)
            by_conv.setdefault(cid, []).append(f"[{r['role']}]: {r['content']}")
        return [
            {
                "source_type": "conversation",
                "source_id": cid,
                "source_title": titles[cid],
                "content": "\n\n".join(reversed(parts)),
            }
            for cid, parts in by_conv.items()
        ]

    async def index_sources(
        self,
        *,
        source_types: Sequence[str] | None = None,
        workspace_dir: str | Path | None = None,
        brain_root: str | Path | None = None,
        documents: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Incrementally chunk, SHA-256 deduplicate, embed, and persist RAG sources."""
        t0 = time.perf_counter()
        await self.vector_store.ensure_schema()
        if self.embedder.is_available:
            await self.vector_store.ensure_vector_space(
                embedder_id=self.embedder.backend_name,
                dim=self.embedder.dim,
                normalized=True,
            )
        types = list(source_types) if source_types is not None else ["conversation", "brain_digest"]
        raw_docs: list[dict[str, Any]] = list(documents or [])
        if "conversation" in types:
            raw_docs.extend(await self._collect_conversation_docs())
        if "workspace_file" in types and workspace_dir is not None:
            raw_docs.extend(_collect_workspace_files(Path(workspace_dir)))
        if "brain_digest" in types:
            raw_docs.extend(collect_brain_digest_documents(Path(brain_root) if brain_root else None))

        existing_rows = await self.db.fetch_all(
            "SELECT id, source_type, source_id, chunk_index, content_sha256, embedding_model FROM rag_chunks"
        )
        existing_map: dict[tuple[str, str, int], tuple[int, str, str]] = {
            (str(r["source_type"]), str(r["source_id"]), int(r["chunk_index"])): (
                int(r["id"]),
                str(r["content_sha256"]),
                str(r["embedding_model"] or ""),
            )
            for r in existing_rows
        }

        target_model = self.embedder.backend_name if self.embedder.is_available else "disabled"
        skipped_unchanged = 0
        pending_items: list[tuple[str, str, str, int, str, str, str]] = []
        for doc in raw_docs:
            stype = str(doc.get("source_type") or "workspace_file")
            sid = str(doc.get("source_id") or "")
            stitle = str(doc.get("source_title") or sid)
            body = str(doc.get("content") or "").strip()
            if not sid or not body:
                continue
            pieces = chunk_document(body, source_type=stype)
            for p in pieces:
                key = (stype, sid, p.chunk_index)
                prev = existing_map.get(key)
                if prev is not None and prev[1] == p.content_sha256 and (
                    not self.embedder.is_available or prev[2] == target_model
                ):
                    skipped_unchanged += 1
                    continue
                pending_items.append((
                    stype, sid, stitle, p.chunk_index, p.content, p.content_sha256, json.dumps(p.metadata)
                ))

        if getattr(self.embedder, "remote_url", None):
            wave_size = 32
        elif self.embedder.weights_available:
            wave_size = 64
        else:
            wave_size = max(len(pending_items), 1)
        now_ms = int(time.time() * 1000)
        indexed_count = 0
        for wave_start in range(0, len(pending_items), wave_size):
            wave = pending_items[wave_start : wave_start + wave_size]
            vectors = None
            if self.embedder.is_available:
                texts_to_embed = [f"{it[2]}\n{it[4]}" for it in wave]
                try:
                    vectors = await self.embedder.embed_documents(texts_to_embed)
                except RemoteEmbedderUnavailableError as err:
                    logger.warning(
                        "Remote embedder unreachable at wave %d/%d (%d chunks already committed): %s",
                        wave_start,
                        len(pending_items),
                        indexed_count,
                        err,
                    )
                    self.invalidate_cache()
                    return {
                        "ok": False,
                        "degraded": True,
                        "error": str(err),
                        "indexed_chunks": indexed_count,
                        "remaining_chunks": len(pending_items) - indexed_count,
                        "skipped_unchanged": skipped_unchanged,
                        "embedding_model": target_model,
                        "duration_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                    }

            vec_pairs: list[tuple[int, Any]] = []
            async with self.db.write_transaction() as conn:
                for idx, (stype, sid, stitle, cidx, content, sha, meta_json) in enumerate(wave):
                    prev = existing_map.get((stype, sid, cidx))
                    if prev is not None:
                        chunk_id = prev[0]
                        await conn.execute(
                            "UPDATE rag_chunks SET source_title=?, content=?, content_sha256=?, "
                            "metadata_json=?, embedding_model=?, updated_at=? WHERE id=?",
                            (stitle, content, sha, meta_json, target_model, now_ms, chunk_id),
                        )
                    else:
                        cur = await conn.execute(
                            "INSERT INTO rag_chunks (source_type, source_id, source_title, chunk_index, "
                            "content, content_sha256, metadata_json, embedding_model, created_at, updated_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (stype, sid, stitle, cidx, content, sha, meta_json, target_model, now_ms, now_ms),
                        )
                        chunk_id = int(cur.lastrowid)
                        existing_map[(stype, sid, cidx)] = (chunk_id, sha, target_model)
                    if vectors is not None:
                        vec_pairs.append((chunk_id, vectors[idx]))

            if vec_pairs:
                await self.vector_store.upsert_vectors(vec_pairs)
            indexed_count += len(wave)
        self.invalidate_cache()
        return {
            "ok": True,
            "indexed_chunks": indexed_count,
            "skipped_unchanged": skipped_unchanged,
            "embedding_model": target_model,
            "duration_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }

    def _check_vector_space_mismatch(self, meta: dict[str, Any]) -> bool:
        persisted_id = meta.get("embedder_id")
        if not persisted_id or not self.embedder.is_available:
            return False
        persisted_dim = meta.get("dim")
        return bool(
            persisted_id != self.embedder.backend_name
            or (persisted_dim is not None and int(persisted_dim) != int(self.embedder.dim))
        )

    async def get_status(self) -> dict[str, Any]:
        await self.vector_store.ensure_schema()
        meta = await self.vector_store.get_vector_space_meta()
        mismatch = self._check_vector_space_mismatch(meta)
        rows = await self.db.fetch_all(
            "SELECT source_type, COUNT(*) AS cnt FROM rag_chunks GROUP BY source_type"
        )
        by_type = {str(r["source_type"]): int(r["cnt"]) for r in rows}
        total = sum(by_type.values())
        vec_cnt = await self.vector_store.count()
        return {
            "total_chunks": total,
            "vector_chunks": vec_cnt,
            "by_source_type": by_type,
            "sqlite_vec_loaded": self.vector_store.sqlite_vec_loaded,
            "embedder_backend": self.embedder.backend_name,
            "embedder_available": self.embedder.is_available,
            "weights_available": self.embedder.weights_available,
            "remote_embed_url": getattr(self.embedder, "remote_url", None),
            "remote_reachable": getattr(self.embedder, "remote_reachable", None),
            "last_remote_error": getattr(self.embedder, "last_remote_error", None),
            "thermal_guard": getattr(self.embedder, "last_thermal_state", None),
            "reranker_backend": self.reranker.backend_name,
            "worker_pid": self.embedder.worker_pid,
            "win32_job_bound": self.embedder.win32_job_bound,
            "degraded": (not self.embedder.is_available) or mismatch or (getattr(self.embedder, "remote_reachable", None) is False),
            "vector_space": {
                **meta,
                "active_embedder_id": self.embedder.backend_name,
                "active_dim": self.embedder.dim,
                "mismatch": mismatch,
            },
        }

    async def status(self) -> dict[str, Any]:
        return await self.get_status()

    async def clear_index(self, *, source_type: str | None = None) -> dict[str, Any]:
        await self.vector_store.ensure_schema()
        if source_type:
            rows = await self.db.fetch_all("SELECT id FROM rag_chunks WHERE source_type = ?", (source_type,))
            cids = [int(r["id"]) for r in rows]
            if cids:
                await self.vector_store.delete_vectors(cids)
                await self.db.execute("DELETE FROM rag_chunks WHERE source_type = ?", (source_type,))
            deleted = len(cids)
        else:
            cnt = await self.db.fetch_val("SELECT COUNT(*) FROM rag_chunks")
            deleted = int(cnt or 0)
            await self.vector_store.clear_all()
            await self.db.execute("DELETE FROM rag_chunks")
        self.invalidate_cache()
        return {"ok": True, "deleted_chunks": deleted}

    async def _fts5_search(
        self,
        query: str,
        *,
        limit: int = 20,
        source_types: Sequence[str] | None = None,
        use_or: bool = False,
    ) -> list[tuple[int, float]]:
        match_expr = _build_fts5_match_expr(query, use_or=use_or)
        if not match_expr:
            return []
        sql = (
            "SELECT c.id AS chunk_id, -bm25(rag_chunks_fts, 1.0, 2.0) AS bm25_score "
            "FROM rag_chunks_fts JOIN rag_chunks c ON c.id = rag_chunks_fts.rowid "
            "WHERE rag_chunks_fts MATCH ?"
        )
        params: list[Any] = [match_expr]
        if source_types:
            ph = ",".join("?" for _ in source_types)
            sql += f" AND c.source_type IN ({ph})"
            params.extend(source_types)
        sql += " ORDER BY bm25(rag_chunks_fts, 1.0, 2.0) ASC LIMIT ?"
        params.append(int(limit))
        try:
            rows = await self.db.fetch_all(sql, tuple(params))
            return [(int(r["chunk_id"]), float(r["bm25_score"])) for r in rows]
        except Exception as err:
            logger.debug("FTS5 query skipped (%s): %s", match_expr, err)
            return []

    async def search(
        self,
        query: str,
        *,
        top_k: int = 8,
        mode: str = "hybrid",
        source_types: Sequence[str] | None = None,
        rerank: bool = True,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        q = (query or "").strip()
        req_mode = (mode or "hybrid").lower()
        k = max(1, min(int(top_k), 50))
        st_tuple = tuple(sorted(source_types)) if source_types else ()
        cache_key = f"{q}|{k}|{req_mode}|{st_tuple}|{bool(rerank)}|{self.embedder.backend_name}"

        now_m = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached is not None and (now_m - cached[0]) <= self.cache_ttl_seconds:
            self._cache.move_to_end(cache_key)
            hit_copy = dict(cached[1])
            hit_copy["duration_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
            return hit_copy

        tracer = get_tracer("gemini_local_ui.rag")
        with tracer.start_as_current_span("rag.hybrid.search") as span:
            span.set_attribute("rag.mode", req_mode)
            span.set_attribute("rag.top_k", k)
            await self.vector_store.ensure_schema()
            meta = await self.vector_store.get_vector_space_meta()
            vector_space_mismatch = self._check_vector_space_mismatch(meta)

            degraded = False
            transient_remote_error = False
            effective_mode = req_mode
            pool_k = max(20, k * 3)

            if req_mode in ("hybrid", "vector") and (not self.embedder.is_available or vector_space_mismatch):
                degraded = True
                effective_mode = "fts5_degraded"

            ranked_scores: dict[int, float] = {}
            rrf_map: dict[int, float] = {}

            q_vec: Any = None
            if effective_mode in ("vector", "hybrid"):
                try:
                    q_vec = await self.embedder.embed_query(q)
                except RemoteEmbedderUnavailableError as err:
                    logger.warning("Remote embedder offline during search; degrading gracefully to FTS5: %s", err)
                    degraded = True
                    transient_remote_error = True
                    effective_mode = "fts5_degraded"

            if effective_mode in ("fts5", "fts5_degraded"):
                fts_rows = await self._fts5_search(q, limit=pool_k, source_types=source_types, use_or=False)
                if not fts_rows and degraded:
                    fts_rows = await self._fts5_search(q, limit=pool_k, source_types=source_types, use_or=True)
                ordered_ids = [cid for cid, _ in fts_rows]
                ranked_scores = {cid: sc for cid, sc in fts_rows}
            elif effective_mode == "vector":
                vec_rows = await self.vector_store.knn_search(q_vec, k=pool_k)
                ordered_ids = [cid for cid, _ in vec_rows]
                ranked_scores = {cid: sc for cid, sc in vec_rows}
            else:
                fts_rows = await self._fts5_search(q, limit=pool_k, source_types=source_types, use_or=True)
                vec_rows = await self.vector_store.knn_search(q_vec, k=pool_k)
                fused = reciprocal_rank_fusion(
                    [[cid for cid, _ in fts_rows], [cid for cid, _ in vec_rows]], k=60
                )
                ordered_ids = [cid for cid, _ in fused[:pool_k]]
                rrf_map = {cid: sc for cid, sc in fused}
                ranked_scores = dict(rrf_map)

            results: list[dict[str, Any]] = []
            if ordered_ids:
                ph = ",".join("?" for _ in ordered_ids)
                sql = f"SELECT * FROM rag_chunks WHERE id IN ({ph})"
                sql_params: list[Any] = list(ordered_ids)
                if source_types:
                    st_ph = ",".join("?" for _ in source_types)
                    sql += f" AND source_type IN ({st_ph})"
                    sql_params.extend(source_types)
                chunk_rows = await self.db.fetch_all(sql, tuple(sql_params))
                row_by_id = {int(r["id"]): r for r in chunk_rows}
                for cid in ordered_ids:
                    r = row_by_id.get(cid)
                    if r is None:
                        continue
                    meta_obj = {}
                    if r.get("metadata_json"):
                        try:
                            meta_obj = json.loads(r["metadata_json"])
                        except Exception:
                            meta_obj = {}
                    sc = round(float(ranked_scores.get(cid, 0.0)), 6)
                    results.append({
                        "chunk_id": cid,
                        "source_type": str(r["source_type"]),
                        "source_id": str(r["source_id"]),
                        "source_title": str(r["source_title"] or ""),
                        "chunk_index": int(r["chunk_index"]),
                        "content": str(r["content"]),
                        "score": sc,
                        "rrf_score": round(float(rrf_map.get(cid, sc)), 6),
                        "metadata": meta_obj,
                    })

                if rerank and results and effective_mode in ("hybrid", "vector"):
                    results = self.reranker.rerank(q, results, top_k=k)
                else:
                    results = results[:k]

            xml_ctx = format_rag_context_xml(results)
            dur_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            span.set_attribute("rag.degraded", degraded)
            span.set_attribute("rag.hit_count", len(results))
            out = {
                "query": q,
                "mode": req_mode,
                "effective_mode": effective_mode,
                "degraded": degraded,
                "vector_space_mismatch": vector_space_mismatch,
                "duration_ms": dur_ms,
                "formatted_xml_context": xml_ctx,
                "results": results,
            }
            if not transient_remote_error:
                self._cache[cache_key] = (time.monotonic(), out)
                if len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
            return out
