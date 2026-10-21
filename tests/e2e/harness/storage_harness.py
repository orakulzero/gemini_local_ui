# tests/e2e/harness/storage_harness.py
"""SQLite persistence, FTS5 full-text search, DAG branching, and export harness."""

from __future__ import annotations
import sqlite3
import time
import json
import re
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    remote_conversation_id TEXT,
    model TEXT NOT NULL,
    gem_id TEXT,
    system_prompt TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    is_pinned BOOLEAN NOT NULL DEFAULT 0,
    is_archived BOOLEAN NOT NULL DEFAULT 0,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    parent_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    thinking_content TEXT,
    model TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    token_count INTEGER,
    is_error BOOLEAN NOT NULL DEFAULT 0,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    resource_uri TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv_created ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_parent ON messages(parent_message_id);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    message_id UNINDEXED,
    conversation_id UNINDEXED,
    content,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS trg_messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(message_id, conversation_id, content)
    VALUES (new.id, new.conversation_id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS trg_messages_ad AFTER DELETE ON messages BEGIN
    DELETE FROM messages_fts WHERE message_id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_messages_au AFTER UPDATE OF content ON messages BEGIN
    UPDATE messages_fts SET content = new.content WHERE message_id = old.id;
END;
"""

class StorageEngine:
    """Manages SQLite connection, schema, transactions, and CRUD."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        cur = self.conn.cursor()
        cur.execute("PRAGMA journal_mode = WAL;")
        cur.execute("PRAGMA synchronous = NORMAL;")
        cur.execute("PRAGMA foreign_keys = ON;")
        cur.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def create_conversation(
        self,
        conv_id: str,
        title: str,
        model: str = "gemini-2.0-flash",
        remote_id: Optional[str] = None,
        gem_id: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> str:
        now = int(time.time() * 1000)
        self.conn.execute(
            """
            INSERT INTO conversations(id, title, remote_conversation_id, model, gem_id, system_prompt, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (conv_id, title, remote_id, model, gem_id, system_prompt, now, now),
        )
        self.conn.commit()
        return conv_id

    def add_message(
        self,
        msg_id: str,
        conversation_id: str,
        role: str,
        content: str,
        parent_id: Optional[str] = None,
        thinking_content: Optional[str] = None,
        model: str = "gemini-2.0-flash",
    ) -> str:
        now = int(time.time() * 1000)
        self.conn.execute(
            """
            INSERT INTO messages(id, conversation_id, parent_message_id, role, content, thinking_content, model, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (msg_id, conversation_id, parent_id, role, content, thinking_content, model, now, now),
        )
        self.conn.commit()
        return msg_id

    def get_conversation(self, conv_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        return dict(row) if row else None

    def get_messages(self, conv_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC", (conv_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_conversation(self, conv_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        self.conn.commit()
        return cur.rowcount > 0


class FTS5SearchEngine:
    """Sanitizes FTS5 queries, executes full-text searches, and produces snippets."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @staticmethod
    def sanitize_query(raw_query: str) -> str:
        if not raw_query or not raw_query.strip():
            return ""

        query = raw_query.strip()
        # Remove dangerous control characters
        query = re.sub(r"[\x00-\x1f\x7f]", "", query)

        # Count quotes
        if query.count('"') % 2 != 0:
            query = query.replace('"', "")

        tokens = query.split()
        safe_tokens = []
        for t in tokens:
            if t.upper() in ("AND", "OR", "NOT"):
                safe_tokens.append(t.upper())
            else:
                clean_t = re.sub(r'[^\w\s*"-]', "", t)
                if clean_t:
                    # If token ends with wildcard
                    if clean_t.endswith("*"):
                        base = clean_t[:-1].replace('"', "")
                        if base:
                            safe_tokens.append(f'"{base}"*')
                    else:
                        safe_tokens.append(f'"{clean_t.replace("\"", "")}"')

        return " ".join(safe_tokens)

    def search(self, query: str, limit: int = 20) -> Tuple[List[Dict[str, Any]], float]:
        sanitized = self.sanitize_query(query)
        if not sanitized:
            return [], 0.0

        start_time = time.perf_counter()
        sql = """
        SELECT
            m.id AS message_id,
            m.conversation_id,
            c.title AS conversation_title,
            m.role,
            m.content,
            snippet(messages_fts, 2, '<mark>', '</mark>', '...', 15) AS highlighted_snippet,
            bm25(messages_fts) AS rank_score
        FROM messages_fts
        JOIN messages m ON m.id = messages_fts.message_id
        JOIN conversations c ON c.id = m.conversation_id
        WHERE messages_fts MATCH ?
        ORDER BY rank_score ASC
        LIMIT ?;
        """
        try:
            rows = self.conn.execute(sql, (sanitized, limit)).fetchall()
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            results = [dict(r) for r in rows]
            return results, elapsed_ms
        except Exception:
            return [], 0.0


class ConversationTree:
    """Directed Acyclic Graph (DAG) for chat turn branching and sibling resolution."""

    def __init__(self):
        self.nodes: Dict[str, Dict[str, Any]] = {}

    def add_message(
        self,
        msg_id: str,
        parent_id: Optional[str],
        role: str,
        content: str,
        thinking_content: Optional[str] = None,
        model: str = "gemini-2.0-flash",
    ):
        self.nodes[msg_id] = {
            "id": msg_id,
            "parent_id": parent_id,
            "role": role,
            "content": content,
            "thinking_content": thinking_content,
            "model": model,
            "children": [],
        }
        if parent_id and parent_id in self.nodes:
            self.nodes[parent_id]["children"].append(msg_id)

    def get_linear_thread(self, leaf_id: str) -> List[Dict[str, Any]]:
        thread = []
        curr = leaf_id
        visited = set()
        while curr and curr not in visited:
            visited.add(curr)
            node = self.nodes.get(curr)
            if not node:
                break
            thread.append(node)
            curr = node["parent_id"]
        return list(reversed(thread))

    def get_sibling_info(self, msg_id: str) -> Dict[str, Any]:
        node = self.nodes.get(msg_id)
        if not node:
            return {"index": 1, "total": 1, "sibling_ids": [msg_id]}
        parent_id = node["parent_id"]
        if parent_id is None:
            siblings = [n["id"] for n in self.nodes.values() if n["parent_id"] is None]
        else:
            siblings = self.nodes[parent_id]["children"]

        if msg_id in siblings:
            idx = siblings.index(msg_id) + 1
        else:
            idx = 1
        return {"index": idx, "total": max(1, len(siblings)), "sibling_ids": siblings}


class ThreadExporter:
    """Exports conversation threads to Markdown with YAML frontmatter and JSON."""

    @staticmethod
    def export_markdown(conv: Dict[str, Any], messages: List[Dict[str, Any]]) -> str:
        created_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(conv.get("created_at", 0) / 1000))
        lines = [
            "---",
            f"id: \"{conv.get('id', '')}\"",
            f"title: \"{conv.get('title', '')}\"",
            f"model: \"{conv.get('model', '')}\"",
            f"created: \"{created_str}\"",
            "---",
            "",
            f"# {conv.get('title', 'Conversation')}",
            "",
        ]

        for msg in messages:
            role = msg.get("role", "user").capitalize()
            lines.append(f"### {role}")
            lines.append("")
            # Thinking block in details
            thinking = msg.get("thinking_content")
            if thinking:
                lines.append("<details>")
                lines.append("<summary>Thought Process</summary>")
                lines.append("")
                lines.append(thinking)
                lines.append("")
                lines.append("</details>")
                lines.append("")
            lines.append(msg.get("content", ""))
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def export_json(conv: Dict[str, Any], messages: List[Dict[str, Any]]) -> str:
        payload = {
            "version": "1.0",
            "conversation": {
                "id": conv.get("id"),
                "title": conv.get("title"),
                "model": conv.get("model"),
                "created_at": conv.get("created_at"),
            },
            "messages": [
                {
                    "id": m.get("id"),
                    "parent_id": m.get("parent_message_id"),
                    "role": m.get("role"),
                    "content": m.get("content"),
                    "thinking_content": m.get("thinking_content"),
                    "model": m.get("model"),
                    "created_at": m.get("created_at"),
                }
                for m in messages
            ],
        }
        return json.dumps(payload, indent=2)


class RemoteSyncEngine:
    """Synchronizes remote Gemini web conversations into local storage."""

    def __init__(self, storage: StorageEngine):
        self.storage = storage

    def sync_remote_conversations(self, remote_list: List[Dict[str, Any]]) -> int:
        count = 0
        for item in remote_list:
            cid = item.get("cid")
            title = item.get("title", "Synced Chat")
            existing = self.storage.conn.execute(
                "SELECT id FROM conversations WHERE remote_conversation_id = ?", (cid,)
            ).fetchone()
            if not existing:
                self.storage.create_conversation(
                    conv_id=f"local_{cid}",
                    title=title,
                    remote_id=cid,
                )
                count += 1
        return count
