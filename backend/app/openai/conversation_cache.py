"""
backend/app/openai/conversation_cache.py
Stateless-to-stateful OpenAI & Anthropic conversation prefix cache using SHA-256 message prefix matching
with two-tier storage: in-memory bounded LRU + persistent SQLite WAL backing store.

OpenAI/Anthropic-compatible clients resend the full `messages[]` array on every turn and never
carry Gemini's `(cid, rid, rcid)` session identifiers. By hashing normalized message
prefixes (`messages[:n]`) from longest to shortest, the gateway automatically resumes
existing Gemini Web conversations and sends only the new delta turn (`messages[n:]`),
even across Uvicorn server restarts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger("gemini_openai.conversation_cache")

MAX_CONVERSATIONS = 2000
DEFAULT_CACHE_DB_PATH = Path("backend/data/conversation_cache.db")


def _normalize_content(content: Any) -> Any:
    """Normalizes message content (str, list of parts, or None) into deterministic JSON-serializable data."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        norm_parts = []
        for part in content:
            if hasattr(part, "model_dump"):
                d = part.model_dump(exclude_none=True)
            elif isinstance(part, dict):
                d = {k: v for k, v in part.items() if v is not None}
            else:
                norm_parts.append(str(part))
                continue
            if d.get("type") == "thinking":
                continue
            norm_parts.append(d)
        return norm_parts
    if hasattr(content, "model_dump"):
        return content.model_dump(exclude_none=True)
    return str(content)


def _normalize_message(msg: Any) -> Dict[str, Any]:
    """Converts a Pydantic ChatCompletionMessageParam or dict into a canonical dict for hashing."""
    if hasattr(msg, "model_dump"):
        raw = msg.model_dump(exclude_none=True)
    elif isinstance(msg, dict):
        raw = dict(msg)
    else:
        raw = {
            "role": getattr(msg, "role", "user"),
            "content": getattr(msg, "content", ""),
        }

    norm: Dict[str, Any] = {
        "role": str(raw.get("role", "user")),
        "content": _normalize_content(raw.get("content")),
    }
    if raw.get("name"):
        norm["name"] = str(raw["name"])
    if raw.get("tool_calls") and isinstance(raw["tool_calls"], list):
        norm["tool_calls"] = [
            {
                "type": str(tc.get("type", "function")),
                "function": {
                    "name": str(tc.get("function", {}).get("name", "")),
                    "arguments": str(tc.get("function", {}).get("arguments", "")),
                },
            }
            for tc in raw["tool_calls"]
            if isinstance(tc, dict)
        ]
    return norm


def history_key(messages: Sequence[Any]) -> str:
    """Computes a deterministic SHA-256 hex digest for a sequence of OpenAI/Anthropic messages."""
    normalized = [_normalize_message(m) for m in messages]
    raw_json = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()


class ConversationCache:
    """
    Thread-safe bounded LRU cache mapping SHA-256 message prefix hashes to Gemini (cid, rid, rcid),
    optionally backed by a persistent SQLite WAL table so sessions survive server restarts.
    """

    def __init__(
        self,
        max_size: int = MAX_CONVERSATIONS,
        db_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self.max_size = max_size
        self._cache: OrderedDict[str, Dict[str, str]] = OrderedDict()
        self._lock = threading.Lock()
        self.db_path: Optional[Path] = Path(db_path) if db_path else None
        if self.db_path is not None:
            self._init_db()

    def _init_db(self) -> None:
        if self.db_path is None:
            return
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(self.db_path), timeout=5.0) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_prefix_cache (
                        prefix_hash TEXT PRIMARY KEY,
                        cid TEXT NOT NULL,
                        rid TEXT NOT NULL,
                        rcid TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                conn.commit()
        except Exception as e:
            logger.warning("Failed initializing SQLite conversation cache at %s: %s", self.db_path, e)

    def _db_lookup(self, candidate_keys: List[str]) -> Dict[str, Dict[str, str]]:
        if self.db_path is None or not candidate_keys:
            return {}
        try:
            placeholders = ",".join("?" for _ in candidate_keys)
            with sqlite3.connect(str(self.db_path), timeout=5.0) as conn:
                rows = conn.execute(
                    f"SELECT prefix_hash, cid, rid, rcid FROM conversation_prefix_cache WHERE prefix_hash IN ({placeholders})",
                    candidate_keys,
                ).fetchall()
            return {
                str(r[0]): {"cid": str(r[1]), "rid": str(r[2]), "rcid": str(r[3])}
                for r in rows
                if r and r[1]
            }
        except Exception as e:
            logger.debug("SQLite conversation cache lookup error: %s", e)
            return {}

    def _db_upsert(self, key: str, entry: Dict[str, str]) -> None:
        if self.db_path is None:
            return
        try:
            now = time.time()
            with sqlite3.connect(str(self.db_path), timeout=5.0) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO conversation_prefix_cache (prefix_hash, cid, rid, rcid, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (key, entry["cid"], entry["rid"], entry["rcid"], now),
                )
                count_row = conn.execute("SELECT COUNT(*) FROM conversation_prefix_cache").fetchone()
                total_rows = int(count_row[0]) if count_row else 0
                if total_rows > self.max_size:
                    excess = total_rows - self.max_size
                    conn.execute(
                        """
                        DELETE FROM conversation_prefix_cache
                        WHERE prefix_hash IN (
                            SELECT prefix_hash FROM conversation_prefix_cache
                            ORDER BY updated_at ASC
                            LIMIT ?
                        )
                        """,
                        (excess,),
                    )
                conn.commit()
        except Exception as e:
            logger.debug("SQLite conversation cache upsert error: %s", e)

    def _db_clear(self) -> None:
        if self.db_path is None or not self.db_path.exists():
            return
        try:
            with sqlite3.connect(str(self.db_path), timeout=5.0) as conn:
                conn.execute("DELETE FROM conversation_prefix_cache")
                conn.commit()
        except Exception as e:
            logger.debug("SQLite conversation cache clear error: %s", e)

    def find(self, messages: Sequence[Any]) -> Tuple[int, Optional[Dict[str, str]]]:
        """
        Searches for the longest prefix `messages[:n]` (for n from len(messages)-1 down to 1)
        that matches a cached Gemini conversation state in RAM or SQLite.

        Returns:
            (matched_prefix_length, {"cid": ..., "rid": ..., "rcid": ...}) or (0, None).
        """
        total = len(messages)
        if total < 2:
            return 0, None

        prefix_keys: List[Tuple[int, str]] = [
            (n, history_key(messages[:n])) for n in range(total - 1, 0, -1)
        ]

        with self._lock:
            for n, key in prefix_keys:
                chat = self._cache.get(key)
                if chat is not None:
                    self._cache.move_to_end(key)
                    return n, dict(chat)

            if self.db_path is not None:
                db_matches = self._db_lookup([k for _, k in prefix_keys])
                if db_matches:
                    for n, key in prefix_keys:
                        if key in db_matches:
                            entry = db_matches[key]
                            self._cache[key] = entry
                            self._cache.move_to_end(key)
                            while len(self._cache) > self.max_size:
                                self._cache.popitem(last=False)
                            return n, dict(entry)

        return 0, None

    def remember(
        self,
        messages: Sequence[Any],
        reply: Union[str, Dict[str, Any], Any],
        chat: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """
        Records `messages + [assistant_reply]` mapped to `chat` (`{"cid": ..., "rid": ..., "rcid": ...}`).
        Returns the SHA-256 key if stored, or None if `chat` lacks a valid `cid`.
        """
        if not chat or not isinstance(chat, dict) or not chat.get("cid"):
            return None

        if isinstance(reply, str):
            reply_msg: Dict[str, Any] = {"role": "assistant", "content": reply}
        elif hasattr(reply, "model_dump"):
            reply_msg = reply.model_dump(exclude_none=True)
        elif isinstance(reply, dict):
            reply_msg = dict(reply)
        else:
            reply_msg = {"role": "assistant", "content": str(reply)}

        full_transcript = list(messages) + [reply_msg]
        key = history_key(full_transcript)
        entry = {
            "cid": str(chat.get("cid") or ""),
            "rid": str(chat.get("rid") or ""),
            "rcid": str(chat.get("rcid") or ""),
        }

        with self._lock:
            self._cache[key] = entry
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_size:
                self._cache.popitem(last=False)
            self._db_upsert(key, entry)

        return key

    def clear(self) -> None:
        """Clears all cached conversation states in RAM and SQLite."""
        with self._lock:
            self._cache.clear()
            self._db_clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


# Module-level singleton and convenience functions
default_cache = ConversationCache(max_size=MAX_CONVERSATIONS, db_path=DEFAULT_CACHE_DB_PATH)


def find(messages: Sequence[Any]) -> Tuple[int, Optional[Dict[str, str]]]:
    return default_cache.find(messages)


def remember(
    messages: Sequence[Any],
    reply: Union[str, Dict[str, Any], Any],
    chat: Optional[Dict[str, Any]],
) -> Optional[str]:
    return default_cache.remember(messages, reply, chat)


def clear() -> None:
    default_cache.clear()
