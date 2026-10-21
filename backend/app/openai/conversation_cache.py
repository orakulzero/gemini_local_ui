"""
backend/app/openai/conversation_cache.py
Stateless-to-stateful OpenAI conversation prefix cache using SHA-256 message prefix matching.

OpenAI-compatible clients resend the full `messages[]` array on every turn and never
carry Gemini's `(cid, rid, rcid)` session identifiers. By hashing normalized message
prefixes (`messages[:n]`) from longest to shortest, the gateway automatically resumes
existing Gemini Web conversations and sends only the new delta turn (`messages[n:]`).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

MAX_CONVERSATIONS = 2000


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
                norm_parts.append(part.model_dump(exclude_none=True))
            elif isinstance(part, dict):
                norm_parts.append({k: v for k, v in part.items() if v is not None})
            else:
                norm_parts.append(str(part))
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
    """Computes a deterministic SHA-256 hex digest for a sequence of OpenAI messages."""
    normalized = [_normalize_message(m) for m in messages]
    raw_json = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()


class ConversationCache:
    """Thread-safe bounded LRU cache mapping SHA-256 message prefix hashes to Gemini (cid, rid, rcid)."""

    def __init__(self, max_size: int = MAX_CONVERSATIONS) -> None:
        self.max_size = max_size
        self._cache: OrderedDict[str, Dict[str, str]] = OrderedDict()
        self._lock = threading.Lock()

    def find(self, messages: Sequence[Any]) -> Tuple[int, Optional[Dict[str, str]]]:
        """
        Searches for the longest prefix `messages[:n]` (for n from len(messages)-1 down to 1)
        that matches a cached Gemini conversation state.

        Returns:
            (matched_prefix_length, {"cid": ..., "rid": ..., "rcid": ...}) or (0, None).
        """
        total = len(messages)
        if total < 2:
            return 0, None

        with self._lock:
            for n in range(total - 1, 0, -1):
                key = history_key(messages[:n])
                chat = self._cache.get(key)
                if chat is not None:
                    self._cache.move_to_end(key)
                    return n, dict(chat)
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

        return key

    def clear(self) -> None:
        """Clears all cached conversation states."""
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


# Module-level singleton and convenience functions
default_cache = ConversationCache(max_size=MAX_CONVERSATIONS)


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
