"""
backend/app/rag/vector_store.py
SQLite-vec (`vec0`) 512-dim float32 cosine vector store with NumPy fallback.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Protocol, Sequence, runtime_checkable

import numpy as np

from backend.app.storage.database import Database

logger = logging.getLogger("gemini_rag.vector_store")

VECTOR_DIM = 512


def load_sqlite_vec_extension(conn: Any) -> bool:
    """Load sqlite_vec C extension onto a sync sqlite3.Connection (or raw _conn)."""
    try:
        import sqlite_vec

        raw_conn = getattr(conn, "_conn", conn)
        if not isinstance(raw_conn, sqlite3.Connection):
            return False
        raw_conn.enable_load_extension(True)
        try:
            sqlite_vec.load(raw_conn)
        finally:
            raw_conn.enable_load_extension(False)
        return True
    except Exception as exc:
        logger.debug("sqlite_vec extension unavailable: %s", exc)
        return False


@runtime_checkable
class VectorStoreProtocol(Protocol):
    @property
    def sqlite_vec_loaded(self) -> bool: ...
    async def ensure_schema(self) -> None: ...
    async def upsert_vectors(self, items: Sequence[tuple[int, np.ndarray]]) -> None: ...
    async def delete_vectors(self, chunk_ids: Sequence[int]) -> None: ...
    async def clear_all(self) -> None: ...
    async def knn_search(self, query_vec: np.ndarray, k: int = 20) -> list[tuple[int, float]]: ...
    async def count(self) -> int: ...


class SqliteVecStore:
    """512-dim cosine vector store backed by `sqlite-vec` `vec0` virtual table."""

    def __init__(self, db: Database, dim: int = VECTOR_DIM) -> None:
        self.db = db
        self.dim = int(dim)
        self._schema_ready = False
        self._sqlite_vec_ok = False

    @property
    def sqlite_vec_loaded(self) -> bool:
        return bool(self._sqlite_vec_ok or getattr(self.db, "sqlite_vec_loaded", False))

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        conn = await self.db.connect()
        if getattr(self.db, "sqlite_vec_loaded", False):
            self._sqlite_vec_ok = True
        else:
            try:
                import sqlite_vec
                await conn.enable_load_extension(True)
                await conn._execute(sqlite_vec.load, conn._conn)
                await conn.enable_load_extension(False)
                self._sqlite_vec_ok = True
                self.db.sqlite_vec_loaded = True
            except Exception as exc:
                logger.warning("Falling back to NumPy vector table (sqlite-vec load failed): %s", exc)
                self._sqlite_vec_ok = False

        if self._sqlite_vec_ok:
            await self.db.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0("
                f"chunk_id INTEGER PRIMARY KEY, embedding float[{self.dim}] distance_metric=cosine)"
            )
        else:
            await self.db.execute(
                "CREATE TABLE IF NOT EXISTS vec_chunks_fallback ("
                "chunk_id INTEGER PRIMARY KEY, embedding BLOB NOT NULL)"
            )
        self._schema_ready = True

    def _to_f32_bytes(self, vec: np.ndarray) -> bytes:
        arr = np.asarray(vec, dtype=np.float32).reshape(-1)
        if arr.shape[0] != self.dim:
            if arr.shape[0] > self.dim:
                arr = arr[: self.dim]
            else:
                arr = np.pad(arr, (0, self.dim - arr.shape[0]))
        norm = float(np.linalg.norm(arr))
        if norm > 1e-12:
            arr = arr / norm
        return arr.astype(np.float32, copy=False).tobytes()

    async def upsert_vectors(self, items: Sequence[tuple[int, np.ndarray]]) -> None:
        if not items:
            return
        await self.ensure_schema()
        async with self.db.write_transaction() as conn:
            if self._sqlite_vec_ok:
                for cid, vec in items:
                    b = self._to_f32_bytes(vec)
                    await conn.execute("DELETE FROM vec_chunks WHERE chunk_id = ?", (int(cid),))
                    await conn.execute(
                        "INSERT INTO vec_chunks(chunk_id, embedding) VALUES (?, ?)", (int(cid), b)
                    )
            else:
                for cid, vec in items:
                    b = self._to_f32_bytes(vec)
                    await conn.execute(
                        "INSERT OR REPLACE INTO vec_chunks_fallback(chunk_id, embedding) VALUES (?, ?)",
                        (int(cid), b),
                    )

    async def delete_vectors(self, chunk_ids: Sequence[int]) -> None:
        if not chunk_ids:
            return
        await self.ensure_schema()
        tbl = "vec_chunks" if self._sqlite_vec_ok else "vec_chunks_fallback"
        placeholders = ",".join("?" for _ in chunk_ids)
        await self.db.execute(f"DELETE FROM {tbl} WHERE chunk_id IN ({placeholders})", tuple(int(x) for x in chunk_ids))

    async def clear_all(self) -> None:
        await self.ensure_schema()
        tbl = "vec_chunks" if self._sqlite_vec_ok else "vec_chunks_fallback"
        await self.db.execute(f"DELETE FROM {tbl}")

    async def count(self) -> int:
        await self.ensure_schema()
        tbl = "vec_chunks" if self._sqlite_vec_ok else "vec_chunks_fallback"
        val = await self.db.fetch_val(f"SELECT COUNT(*) FROM {tbl}")
        return int(val or 0)

    async def knn_search(self, query_vec: np.ndarray, k: int = 20) -> list[tuple[int, float]]:
        await self.ensure_schema()
        if k <= 0:
            return []
        q_bytes = self._to_f32_bytes(query_vec)
        if self._sqlite_vec_ok:
            rows = await self.db.fetch_all(
                "SELECT chunk_id, distance FROM vec_chunks WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (q_bytes, int(k)),
            )
            return [(int(r["chunk_id"]), float(1.0 - float(r["distance"]))) for r in rows]

        rows = await self.db.fetch_all("SELECT chunk_id, embedding FROM vec_chunks_fallback")
        if not rows:
            return []
        q_arr = np.frombuffer(q_bytes, dtype=np.float32)
        cids = [int(r["chunk_id"]) for r in rows]
        mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        sims = mat @ q_arr
        top_idx = np.argsort(-sims)[: int(k)]
        return [(cids[int(i)], float(sims[int(i)])) for i in top_idx]
