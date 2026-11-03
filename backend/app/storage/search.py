"""
backend/app/storage/search.py
Full-text search engine with query sanitization, BM25 ranking,
single-flight request coalescing, and LRU/TTL caching with mutation invalidation.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import logging
import re
import time
from pydantic import BaseModel, ConfigDict, Field

from backend.app.storage.database import Database

logger = logging.getLogger("gemini_storage.search")


def sanitize_fts5_query(raw_query: str) -> str:
    """Sanitizes raw user search input into a secure, crash-proof SQLite FTS5 MATCH expression."""
    if not raw_query or not raw_query.strip():
        return ""

    raw = raw_query.strip()
    tokens = re.findall(r"[\w]+", raw, re.UNICODE)
    if not tokens:
        return ""

    parts = []
    for i, token in enumerate(tokens):
        is_last = i == len(tokens) - 1
        clean = token.replace('"', '""')
        if is_last and not raw.endswith(" ") and len(clean) >= 2:
            parts.append(f'"{clean}"*')
        else:
            parts.append(f'"{clean}"')

    return " ".join(parts)


class SearchResult(BaseModel):
    """Structured search result with highlighted preview and metadata."""
    message_id: int
    conversation_id: str
    conversation_title: str
    role: str
    snippet: str
    rank: float
    created_at: int
    matched_field: str = Field(default="content", description="'content' or 'thoughts'")

    model_config = ConfigDict(extra="ignore")

    @property
    def highlighted_snippet(self) -> str:
        """Compatibility alias for snippet."""
        return self.snippet


class SearchEngine:
    """
    Executes sanitized FTS5 queries with BM25 ranking, snippet generation,
    single-flight request coalescing, and mutation-invalidated LRU/TTL caching.
    """

    def __init__(
        self,
        db: Database,
        cache_size: int = 512,
        cache_ttl: float = 60.0,
    ) -> None:
        self.db = db
        self.cache_size = cache_size
        self.cache_ttl = cache_ttl
        self._generation: int = 0
        self._cache: OrderedDict[str, tuple[float, list[SearchResult]]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

        # Subscribe to database mutation events for zero-lag cache invalidation
        if hasattr(self.db, "register_mutation_listener"):
            self.db.register_mutation_listener(self.invalidate_cache)

    def invalidate_cache(self) -> None:
        """Clears all cached search results and advances mutation epoch."""
        self._generation += 1
        self._cache.clear()

    async def search(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
        conversation_id: str | None = None,
    ) -> list[SearchResult]:
        """
        Searches chat history across content and thinking reasoning traces.
        Coalesces concurrent identical in-flight queries and serves cached results when valid.
        """
        sanitized = sanitize_fts5_query(query)
        if not sanitized:
            return []

        cache_key = f"{sanitized}|{limit}|{offset}|{conversation_id or ''}"

        # 1. Single-Flight Coalescing & Cache Check
        async with self._lock:
            if cache_key in self._cache:
                ts, cached_res = self._cache[cache_key]
                if time.time() - ts <= self.cache_ttl:
                    self._cache.move_to_end(cache_key)
                    return cached_res
                del self._cache[cache_key]

            if cache_key in self._inflight:
                # Another coroutine is already executing this exact query; await its result
                future = self._inflight[cache_key]
                is_leader = False
            else:
                # This coroutine becomes the leader responsible for querying the database
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._inflight[cache_key] = future
                is_leader = True

            start_gen = self._generation

        if not is_leader:
            return await future

        # 2. Database FTS5 Query Execution (Leader)
        sql = """
        SELECT 
            m.id AS message_id,
            m.conversation_id,
            c.title AS conversation_title,
            m.role,
            m.created_at,
            snippet(messages_fts, 0, '<mark class="search-highlight">', '</mark>', '...', 32) AS content_snippet,
            snippet(messages_fts, 1, '<mark class="search-highlight">', '</mark>', '...', 32) AS thoughts_snippet,
            bm25(messages_fts) AS rank
        FROM messages_fts f
        JOIN messages m ON m.id = f.rowid
        JOIN conversations c ON c.id = m.conversation_id
        WHERE messages_fts MATCH :query
          AND (:conversation_id IS NULL OR m.conversation_id = :conversation_id)
        ORDER BY rank ASC
        LIMIT :limit OFFSET :offset;
        """

        params = {
            "query": sanitized,
            "limit": limit,
            "offset": offset,
            "conversation_id": conversation_id,
        }

        try:
            rows = await self.db.fetch_all(sql, params)
            results: list[SearchResult] = []
            for r in rows:
                content_snip = r.get("content_snippet") or ""
                thoughts_snip = r.get("thoughts_snippet") or ""

                if "<mark" in content_snip:
                    chosen_snippet = content_snip
                    matched_field = "content"
                elif "<mark" in thoughts_snip:
                    chosen_snippet = thoughts_snip
                    matched_field = "thoughts"
                else:
                    chosen_snippet = content_snip or thoughts_snip
                    matched_field = "content"

                results.append(
                    SearchResult(
                        message_id=r["message_id"],
                        conversation_id=r["conversation_id"],
                        conversation_title=r["conversation_title"],
                        role=r["role"],
                        snippet=chosen_snippet,
                        rank=float(r["rank"]),
                        created_at=int(r["created_at"]),
                        matched_field=matched_field,
                    )
                )

            # Store in cache & resolve in-flight waiters
            async with self._lock:
                # Only populate cache if no mutations occurred while the query was in-flight
                if self._generation == start_gen:
                    if len(self._cache) >= self.cache_size:
                        self._cache.popitem(last=False)
                    self._cache[cache_key] = (time.time(), results)
                if cache_key in self._inflight:
                    del self._inflight[cache_key]

            future.set_result(results)
            return results

        except Exception as e:
            logger.error("FTS5 search error for query '%s' (sanitized: '%s'): %s", query, sanitized, e)
            async with self._lock:
                if cache_key in self._inflight:
                    del self._inflight[cache_key]
            future.set_exception(e)
            return []
