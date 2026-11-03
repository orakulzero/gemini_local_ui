"""
backend/app/storage/repository.py
Data access repository for Conversations, Messages, Attachments,
DAG Tree Branching, and Remote Google Gemini Synchronization.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any
from collections.abc import AsyncIterator
import uuid

from backend.app.storage.database import AsyncRLock, Database

logger = logging.getLogger("gemini_storage.repository")


@dataclass
class Attachment:
    id: str
    message_id: int
    filename: str
    mime_type: str
    size_bytes: int
    storage_path: str = ""
    uri: str = ""
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))

    @property
    def resource_uri(self) -> str:
        return self.uri

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "storage_path": self.storage_path,
            "uri": self.uri,
            "resource_uri": self.uri,
            "created_at": self.created_at,
        }


@dataclass
class Message:
    id: int
    conversation_id: str
    role: str  # 'user' | 'assistant' | 'system'
    content: str
    parent_id: int | None = None
    thoughts: str | None = None
    model: str = "gemini-2.5-pro"
    token_count: int | None = None
    is_error: bool = False
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = field(default_factory=lambda: int(time.time() * 1000))
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[Attachment] = field(default_factory=list)

    # Dynamic branch navigation properties computed at query time
    sibling_index: int | None = None  # 1-indexed (e.g. 2)
    sibling_count: int | None = None  # Total siblings (e.g. 3)
    sibling_ids: list[int] | None = None
    children_ids: list[int] | None = None

    @property
    def parent_message_id(self) -> int | None:
        return self.parent_id

    @property
    def thinking_content(self) -> str | None:
        return self.thoughts

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "parent_id": self.parent_id,
            "parent_message_id": self.parent_id,
            "role": self.role,
            "content": self.content,
            "thoughts": self.thoughts,
            "thinking_content": self.thoughts,
            "model": self.model,
            "token_count": self.token_count,
            "is_error": self.is_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "attachments": [att.to_dict() for att in self.attachments],
            "sibling_index": self.sibling_index,
            "sibling_count": self.sibling_count,
            "sibling_ids": self.sibling_ids or [],
            "children_ids": self.children_ids or [],
        }


@dataclass
class Conversation:
    id: str
    title: str
    model: str = "gemini-2.5-pro"
    remote_conversation_id: str | None = None
    gem_id: str | None = None
    system_prompt: str | None = None
    active_leaf_message_id: int | None = None
    is_pinned: bool = False
    is_archived: bool = False
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = field(default_factory=lambda: int(time.time() * 1000))
    metadata: dict[str, Any] = field(default_factory=dict)
    message_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "model": self.model,
            "remote_conversation_id": self.remote_conversation_id,
            "gem_id": self.gem_id,
            "system_prompt": self.system_prompt,
            "active_leaf_message_id": self.active_leaf_message_id,
            "is_pinned": self.is_pinned,
            "is_archived": self.is_archived,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "message_count": self.message_count,
        }


class ConversationRepository:
    """
    Data access repository for Conversations, Messages, Attachments,
    DAG Tree Branching, and Remote Google Gemini Synchronization.
    """

    def __init__(self, db: Database | sqlite3.Connection | str | Path) -> None:
        if isinstance(db, Database):
            self.db = db
            self._external_conn: sqlite3.Connection | None = None
        elif isinstance(db, sqlite3.Connection):
            self.db = Database(db_path=":memory:")
            self._external_conn = db
        else:
            self.db = Database(db_path=db)
            self._external_conn = None
        self._write_lock = AsyncRLock()

    @asynccontextmanager
    async def write_transaction(self) -> AsyncIterator[Any]:
        """Context manager acquiring repository write lock and database write transaction."""
        async with self._write_lock:
            async with self.db.write_transaction() as conn:
                yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        """Backwards-compatible alias for write_transaction."""
        async with self.write_transaction() as conn:
            yield conn


    # Helper parsing methods
    @staticmethod
    def _parse_int_id(val: Any) -> int | None:
        if val is None:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    def _row_to_conversation(self, row: dict[str, Any], msg_count: int = 0) -> Conversation:
        meta = json.loads(row["metadata_json"]) if row.get("metadata_json") else {}
        return Conversation(
            id=row["id"],
            title=row["title"],
            model=row.get("model", "gemini-2.5-pro"),
            remote_conversation_id=row.get("remote_conversation_id"),
            gem_id=row.get("gem_id"),
            system_prompt=row.get("system_prompt"),
            active_leaf_message_id=self._parse_int_id(row.get("active_leaf_message_id")),
            is_pinned=bool(row.get("is_pinned", 0)),
            is_archived=bool(row.get("is_archived", 0)),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            metadata=meta,
            message_count=msg_count,
        )

    def _row_to_message(self, row: dict[str, Any]) -> Message:
        meta = json.loads(row["metadata_json"]) if row.get("metadata_json") else {}
        return Message(
            id=int(row["id"]),
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            parent_id=self._parse_int_id(row.get("parent_id")),
            thoughts=row.get("thoughts"),
            model=row.get("model", "gemini-2.5-pro"),
            token_count=row.get("token_count"),
            is_error=bool(row.get("is_error", 0)),
            created_at=int(row["created_at"]),
            updated_at=int(row.get("updated_at") or row["created_at"]),
            metadata=meta,
        )

    def _row_to_attachment(self, row: dict[str, Any]) -> Attachment:
        return Attachment(
            id=row["id"],
            message_id=int(row["message_id"]),
            filename=row["filename"],
            mime_type=row["mime_type"],
            size_bytes=int(row["size_bytes"]),
            storage_path=row.get("storage_path", ""),
            uri=row.get("uri", ""),
            created_at=int(row.get("created_at") or 0),
        )

    # =========================================================================
    # Conversation CRUD
    # =========================================================================

    async def create_conversation(
        self,
        title: str = "New Conversation",
        model: str = "gemini-2.5-pro",
        gem_id: str | None = None,
        system_prompt: str | None = None,
        remote_conversation_id: str | None = None,
        conversation_id: str | None = None,
        id: str | None = None,
    ) -> Conversation:
        conv_id = conversation_id or id or f"conv_{uuid.uuid4().hex[:12]}"
        now = int(time.time() * 1000)
        conv = Conversation(
            id=conv_id,
            title=title,
            model=model,
            gem_id=gem_id,
            system_prompt=system_prompt,
            remote_conversation_id=remote_conversation_id,
            created_at=now,
            updated_at=now,
        )

        sql = """
        INSERT INTO conversations (
            id, title, model, remote_conversation_id, gem_id, system_prompt,
            active_leaf_message_id, is_pinned, is_archived, created_at, updated_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            conv.id,
            conv.title,
            conv.model,
            conv.remote_conversation_id,
            conv.gem_id,
            conv.system_prompt,
            conv.active_leaf_message_id,
            1 if conv.is_pinned else 0,
            1 if conv.is_archived else 0,
            conv.created_at,
            conv.updated_at,
            json.dumps(conv.metadata) if conv.metadata else None,
        )
        async with self.write_transaction() as conn:
            await conn.execute(sql, params)
        return conv

    async def get_conversation(self, conv_id: str) -> Conversation | None:
        sql = """
        SELECT c.*, COUNT(m.id) as msg_count
        FROM conversations c
        LEFT JOIN messages m ON m.conversation_id = c.id
        WHERE c.id = ?
        GROUP BY c.id;
        """
        row = await self.db.fetch_one(sql, (conv_id,))
        if not row:
            return None
        return self._row_to_conversation(row, msg_count=row["msg_count"])

    async def get_conversation_by_remote_id(self, remote_id: str) -> Conversation | None:
        sql = "SELECT * FROM conversations WHERE remote_conversation_id = ?;"
        row = await self.db.fetch_one(sql, (remote_id,))
        if not row:
            return None
        return self._row_to_conversation(row)

    async def list_conversations(
        self,
        page: int = 1,
        limit: int = 20,
        archived: bool = False,
        pinned_first: bool = True,
        search_query: str | None = None,
    ) -> tuple[list[Conversation], int]:
        offset = (page - 1) * limit
        where_clauses = ["c.is_archived = ?"]
        params: list[Any] = [1 if archived else 0]

        if search_query:
            where_clauses.append("c.title LIKE ?")
            params.append(f"%{search_query}%")

        where_sql = " AND ".join(where_clauses)
        order_sql = "c.is_pinned DESC, c.updated_at DESC" if pinned_first else "c.updated_at DESC"

        count_row = await self.db.fetch_one(
            f"SELECT COUNT(*) as total FROM conversations c WHERE {where_sql};",
            params,
        )
        total = count_row["total"] if count_row else 0

        query = f"""
            SELECT c.*, COUNT(m.id) as msg_count
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            WHERE {where_sql}
            GROUP BY c.id
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?;
        """
        rows = await self.db.fetch_all(query, params + [limit, offset])
        conversations = [self._row_to_conversation(r, msg_count=r["msg_count"]) for r in rows]
        return conversations, total

    async def search_conversations(
        self,
        query: str,
        limit: int = 20,
    ) -> list[Conversation]:
        """Searches conversations matching query string."""
        convs, _ = await self.list_conversations(search_query=query, limit=limit)
        return convs

    async def delete_conversation(self, conv_id: str) -> bool:
        async with self.write_transaction() as conn:
            cursor = await conn.execute("DELETE FROM conversations WHERE id = ?;", (conv_id,))
            return cursor.rowcount > 0

    async def update_conversation(
        self,
        conv_id: str | None = None,
        conversation_id: str | None = None,
        **fields: Any,
    ) -> Conversation | None:
        target_id = conv_id or conversation_id or fields.pop("id", None)
        if not target_id:
            raise ValueError("Missing conv_id or conversation_id")

        allowed = {
            "title", "model", "gem_id", "system_prompt", "active_leaf_message_id",
            "is_pinned", "is_archived", "updated_at", "remote_conversation_id",
        }
        set_items = []
        params = []
        for k, v in fields.items():
            if k in allowed:
                set_items.append(f"{k} = ?")
                if k in ("is_pinned", "is_archived"):
                    params.append(1 if v else 0)
                else:
                    params.append(v)

        if not set_items:
            return await self.get_conversation(target_id)

        set_sql = ", ".join(set_items)
        params.append(target_id)

        async with self.write_transaction() as conn:
            await conn.execute(f"UPDATE conversations SET {set_sql} WHERE id = ?;", params)
        return await self.get_conversation(target_id)

    async def archive_conversation(self, conv_id: str, is_archived: bool = True) -> Conversation | None:
        now = int(time.time() * 1000)
        return await self.update_conversation(conv_id, is_archived=is_archived, updated_at=now)

    async def set_active_leaf(self, conv_id: str, leaf_message_id: int) -> None:
        now = int(time.time() * 1000)
        async with self.write_transaction() as conn:
            await conn.execute(
                "UPDATE conversations SET active_leaf_message_id = ?, updated_at = ? WHERE id = ?;",
                (leaf_message_id, now, conv_id),
            )

    # =========================================================================
    # Message Insertion & DAG Linking
    # =========================================================================

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        parent_id: int | None = None,
        thoughts: str | None = None,
        model: str = "gemini-2.5-pro",
        token_count: int | None = None,
        is_error: bool = False,
        created_at: int | None = None,
        metadata: dict[str, Any] | None = None,
        parent_message_id: int | None = None,
        thinking_content: str | None = None,
    ) -> Message:
        """Inserts message, links parent_id in DAG, and advances active leaf pointer."""
        effective_parent = parent_id if parent_id is not None else parent_message_id
        effective_thoughts = thoughts if thoughts is not None else thinking_content
        now = created_at or int(time.time() * 1000)
        meta_json = json.dumps(metadata) if metadata else None

        sql = """
        INSERT INTO messages (
            conversation_id, parent_id, role, content, thoughts,
            model, token_count, is_error, created_at, updated_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            conversation_id,
            effective_parent,
            role,
            content,
            effective_thoughts,
            model,
            token_count,
            1 if is_error else 0,
            now,
            now,
            meta_json,
        )
        async with self.write_transaction() as conn:
            cursor = await conn.execute(sql, params)
            msg_id = cursor.lastrowid
            assert msg_id is not None
            self.db._notify_mutations()

            # Update conversation active leaf and updated_at
            await conn.execute(
                "UPDATE conversations SET active_leaf_message_id = ?, updated_at = ? WHERE id = ?;",
                (msg_id, now, conversation_id),
            )


        return Message(
            id=msg_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            parent_id=effective_parent,
            thoughts=effective_thoughts,
            model=model,
            token_count=token_count,
            is_error=is_error,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )

    async def create_message(self, **kwargs: Any) -> Message:
        """Alias for add_message supporting diverse naming styles."""
        # Handle custom ID if passed (e.g., from external turn synchronization)
        custom_id = kwargs.pop("id", None)
        conv_id = kwargs.pop("conversation_id")
        role = kwargs.pop("role")
        content = kwargs.pop("content")
        parent_id = kwargs.pop("parent_id", kwargs.pop("parent_message_id", None))
        thoughts = kwargs.pop("thoughts", kwargs.pop("thinking_content", None))
        model = kwargs.pop("model", "gemini-2.5-pro")
        token_count = kwargs.pop("token_count", None)
        is_error = kwargs.pop("is_error", False)
        created_at = kwargs.pop("created_at", None)
        metadata = kwargs.pop("metadata", kwargs.pop("metadata_json", None))

        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}

        if custom_id is not None and isinstance(custom_id, int):
            now = created_at or int(time.time() * 1000)
            meta_json = json.dumps(metadata) if metadata else None
            sql = """
            INSERT OR REPLACE INTO messages (
                id, conversation_id, parent_id, role, content, thoughts,
                model, token_count, is_error, created_at, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """
            async with self.write_transaction() as conn:
                await conn.execute(
                    sql,
                    (
                        custom_id,
                        conv_id,
                        parent_id,
                        role,
                        content,
                        thoughts,
                        model,
                        token_count,
                        1 if is_error else 0,
                        now,
                        now,
                        meta_json,
                    ),
                )
                await conn.execute(
                    "UPDATE conversations SET active_leaf_message_id = ?, updated_at = ? WHERE id = ?;",
                    (custom_id, now, conv_id),
                )
            return Message(
                id=custom_id,
                conversation_id=conv_id,
                role=role,
                content=content,
                parent_id=parent_id,
                thoughts=thoughts,
                model=model,
                token_count=token_count,
                is_error=is_error,
                created_at=now,
                updated_at=now,
                metadata=metadata or {},
            )

        return await self.add_message(
            conversation_id=conv_id,
            role=role,
            content=content,
            parent_id=parent_id,
            thoughts=thoughts,
            model=model,
            token_count=token_count,
            is_error=is_error,
            created_at=created_at,
            metadata=metadata,
        )

    async def get_message(self, message_id: int | str) -> Message | None:
        int_id = self._parse_int_id(message_id)
        if int_id is None:
            return None
        row = await self.db.fetch_one("SELECT * FROM messages WHERE id = ?;", (int_id,))
        if not row:
            return None
        msg = self._row_to_message(row)
        att_rows = await self.db.fetch_all("SELECT * FROM attachments WHERE message_id = ?;", (int_id,))
        msg.attachments = [self._row_to_attachment(ar) for ar in att_rows]
        return msg

    async def get_all_messages(self, conversation_id: str) -> list[Message]:
        """Retrieves all messages in conversation and annotates with siblings and attachments."""
        rows = await self.db.fetch_all(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC;",
            (conversation_id,),
        )
        messages = [self._row_to_message(r) for r in rows]
        await self._annotate_siblings_and_attachments(conversation_id, messages)
        return messages

    # =========================================================================
    # DAG Branching & Sibling Resolution
    # =========================================================================

    async def get_conversation_thread(
        self,
        conversation_id: str,
        leaf_message_id: int | str | None = None,
    ) -> list[Message]:
        """Reconstructs the ordered list of messages in thread (alias for get_active_thread)."""
        return await self.get_active_thread(conversation_id, leaf_message_id)

    async def get_active_thread(
        self,
        conversation_id: str,
        leaf_message_id: int | str | None = None,
    ) -> list[Message]:
        """
        Reconstructs the ordered list of messages forming the active conversation thread
        from root to leaf via a recursive CTE.
        """
        target_leaf_id = self._parse_int_id(leaf_message_id)

        # 1. Resolve target leaf ID if not explicitly specified
        if target_leaf_id is None:
            conv_row = await self.db.fetch_one(
                "SELECT active_leaf_message_id FROM conversations WHERE id = ?;",
                (conversation_id,),
            )
            if conv_row and conv_row["active_leaf_message_id"]:
                target_leaf_id = self._parse_int_id(conv_row["active_leaf_message_id"])

        # 2. Fallback to latest message if leaf_id still not resolved
        if target_leaf_id is None:
            latest_row = await self.db.fetch_one(
                "SELECT id FROM messages WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 1;",
                (conversation_id,),
            )
            if not latest_row:
                return []
            target_leaf_id = int(latest_row["id"])

        # 3. Traverse upwards from leaf to root using recursive CTE with cycle protection
        cte_sql = """
        WITH RECURSIVE thread_cte(id, parent_id, depth) AS (
            SELECT id, parent_id, 0
            FROM messages
            WHERE id = :leaf_id AND conversation_id = :conv_id
            UNION ALL
            SELECT m.id, m.parent_id, t.depth + 1
            FROM messages m
            JOIN thread_cte t ON m.id = t.parent_id
            WHERE t.depth < 1000 AND m.conversation_id = :conv_id
        )
        SELECT m.*
        FROM thread_cte t
        JOIN messages m ON m.id = t.id
        ORDER BY t.depth DESC;
        """
        rows = await self.db.fetch_all(cte_sql, {"leaf_id": target_leaf_id, "conv_id": conversation_id})
        messages = [self._row_to_message(r) for r in rows]

        # 4. Annotate messages with sibling metrics and attachments
        await self._annotate_siblings_and_attachments(conversation_id, messages)
        return messages

    async def _annotate_siblings_and_attachments(
        self, conversation_id: str, messages: list[Message]
    ) -> None:
        """Annotates each message with sibling indices, total siblings, and attachments."""
        if not messages:
            return

        all_rows = await self.db.fetch_all(
            "SELECT id, parent_id, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at ASC;",
            (conversation_id,),
        )

        parent_to_children: dict[int | None, list[int]] = {}
        for r in all_rows:
            p_id = self._parse_int_id(r["parent_id"])
            m_id = int(r["id"])
            parent_to_children.setdefault(p_id, []).append(m_id)

        msg_ids = [m.id for m in messages]
        placeholders = ",".join(["?"] * len(msg_ids))
        att_rows = await self.db.fetch_all(
            f"SELECT * FROM attachments WHERE message_id IN ({placeholders});",
            msg_ids,
        )
        att_by_msg: dict[int, list[Attachment]] = {}
        for ar in att_rows:
            att = self._row_to_attachment(ar)
            att_by_msg.setdefault(att.message_id, []).append(att)

        for m in messages:
            siblings = parent_to_children.get(m.parent_id, [m.id])
            m.sibling_count = len(siblings)
            try:
                m.sibling_index = siblings.index(m.id) + 1
            except ValueError:
                m.sibling_index = 1
            m.sibling_ids = siblings
            m.children_ids = parent_to_children.get(m.id, [])
            m.attachments = att_by_msg.get(m.id, [])

    async def get_message_siblings(self, message_id: int | str) -> tuple[int, int]:
        """Returns (branch_index, total_branches) for message, 1-indexed."""
        int_id = self._parse_int_id(message_id)
        if int_id is None:
            return 1, 1

        msg_row = await self.db.fetch_one(
            "SELECT id, conversation_id, parent_id FROM messages WHERE id = ?;",
            (int_id,),
        )
        if not msg_row:
            return 1, 1

        parent_id = self._parse_int_id(msg_row["parent_id"])
        conv_id = msg_row["conversation_id"]

        if parent_id is None:
            sibling_rows = await self.db.fetch_all(
                "SELECT id FROM messages WHERE conversation_id = ? AND parent_id IS NULL ORDER BY created_at ASC;",
                (conv_id,),
            )
        else:
            sibling_rows = await self.db.fetch_all(
                "SELECT id FROM messages WHERE conversation_id = ? AND parent_id = ? ORDER BY created_at ASC;",
                (conv_id, parent_id),
            )

        sibling_ids = [int(r["id"]) for r in sibling_rows]
        total = len(sibling_ids)
        try:
            index = sibling_ids.index(int_id) + 1
        except ValueError:
            index = 1
        return index, total

    async def fork_branch(
        self,
        conversation_id: str,
        parent_id: int | str | None,
        role: str,
        content: str,
        thoughts: str | None = None,
        model: str | None = None,
    ) -> tuple[Message, list[Message]]:
        """Creates a new branch starting from parent_id and returns (new_message, new_thread)."""
        int_parent_id = self._parse_int_id(parent_id)
        msg_model = model or "gemini-2.5-pro"
        new_msg = await self.add_message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            parent_id=int_parent_id,
            thoughts=thoughts,
            model=msg_model,
        )
        new_thread = await self.get_active_thread(conversation_id, leaf_message_id=new_msg.id)
        return new_msg, new_thread

    async def switch_active_branch(
        self,
        conversation_id: str,
        target_message_id: int | str,
    ) -> tuple[int, list[Message]]:
        """
        Switches active branch by resolving down the tree from target_message_id
        to the most recent descendant leaf.
        """
        int_target_id = self._parse_int_id(target_message_id)
        if int_target_id is None:
            raise ValueError(f"Invalid target_message_id: {target_message_id}")

        # Fetch child relationships for this conversation in a single fast query
        child_rows = await self.db.fetch_all(
            """
            SELECT id, parent_id 
            FROM messages 
            WHERE conversation_id = ? 
            ORDER BY created_at DESC;
            """,
            (conversation_id,),
        )
        if not any(r["id"] == int_target_id for r in child_rows):
            raise ValueError(f"Message {target_message_id} not found in conversation {conversation_id}")

        # Build parent -> latest child mapping
        parent_to_latest_child: dict[int, int] = {}
        for r in child_rows:
            p_id = r.get("parent_id")
            if p_id is not None and p_id not in parent_to_latest_child:
                parent_to_latest_child[int(p_id)] = int(r["id"])

        curr_id = int_target_id
        visited: set[int] = set()
        depth = 0
        while depth < 1000:
            visited.add(curr_id)
            next_id = parent_to_latest_child.get(curr_id)
            if next_id is None or next_id in visited:
                break
            curr_id = next_id
            depth += 1

        resolved_leaf_id = curr_id
        now = int(time.time() * 1000)
        async with self.write_transaction() as conn:
            await conn.execute(
                "UPDATE conversations SET active_leaf_message_id = ?, updated_at = ? WHERE id = ?;",
                (resolved_leaf_id, now, conversation_id),
            )

        thread = await self.get_active_thread(conversation_id, leaf_message_id=resolved_leaf_id)
        return resolved_leaf_id, thread

    async def create_branch_edit(
        self,
        conversation_id: str,
        target_message_id: int | str,
        new_content: str,
        model: str | None = None,
    ) -> tuple[Message, list[Message]]:
        """
        Edits an earlier message, creating a new sibling message sharing the same parent,
        updating the active leaf pointer.
        """
        int_target_id = self._parse_int_id(target_message_id)
        orig = await self.get_message(int_target_id)
        if not orig:
            raise ValueError(f"Target message {target_message_id} not found")

        msg_model = model or orig.model
        metadata = {"edited_from": int_target_id}

        new_msg = await self.add_message(
            conversation_id=conversation_id,
            role=orig.role,
            content=new_content,
            parent_id=orig.parent_id,
            model=msg_model,
            metadata=metadata,
        )

        new_thread = await self.get_active_thread(conversation_id, leaf_message_id=new_msg.id)
        return new_msg, new_thread

    # =========================================================================
    # Attachments
    # =========================================================================

    async def add_attachment(
        self,
        message_id: int | str,
        filename: str | None = None,
        file_name: str | None = None,
        mime_type: str = "application/octet-stream",
        size_bytes: int | None = None,
        file_size: int | None = None,
        storage_path: str = "",
        uri: str = "",
        remote_file_uri: str | None = None,
        attachment_id: str | None = None,
    ) -> Attachment:
        int_msg_id = self._parse_int_id(message_id)
        assert int_msg_id is not None
        att_id = attachment_id or f"att_{uuid.uuid4().hex[:12]}"
        att_filename = filename or file_name or "attachment"
        att_size = size_bytes if size_bytes is not None else (file_size or 0)
        att_uri = uri or remote_file_uri or ""
        now = int(time.time() * 1000)

        sql = """
        INSERT INTO attachments (id, message_id, filename, mime_type, size_bytes, storage_path, uri, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """
        async with self.write_transaction() as conn:
            await conn.execute(
                sql,
                (att_id, int_msg_id, att_filename, mime_type, att_size, storage_path, att_uri, now),
            )

        return Attachment(
            id=att_id,
            message_id=int_msg_id,
            filename=att_filename,
            mime_type=mime_type,
            size_bytes=att_size,
            storage_path=storage_path,
            uri=att_uri,
            created_at=now,
        )

    # =========================================================================
    # Remote Conversation Sync
    # =========================================================================

    async def reconcile_remote_conversations(
        self, remote_list: list[dict[str, Any]]
    ) -> dict[str, int]:
        """
        Reconciles remote conversation metadata from Google Gemini with the local database.
        Returns: { 'synced': total, 'new': count, 'updated': count, 'unchanged': count }
        """
        now = int(time.time() * 1000)
        new_count = 0
        updated_count = 0
        unchanged_count = 0

        existing_rows = await self.db.fetch_all(
            "SELECT id, remote_conversation_id, title, updated_at FROM conversations WHERE remote_conversation_id IS NOT NULL;"
        )
        existing_map = {r["remote_conversation_id"]: r for r in existing_rows}

        for item in remote_list:
            cid = item.get("cid")
            if not cid:
                continue
            title = item.get("title") or "Untitled Conversation"
            raw_ts = item.get("updated_usec", 0)
            ts_ms = raw_ts // 1000 if raw_ts > 100000000000000 else raw_ts
            is_pinned = bool(item.get("is_pinned", False))

            if cid not in existing_map:
                local_id = f"conv_{uuid.uuid4().hex[:12]}"
                await self.create_conversation(
                    title=title,
                    model="gemini-2.5-pro",
                    remote_conversation_id=cid,
                    conversation_id=local_id,
                )
                if ts_ms:
                    async with self.write_transaction() as conn:
                        await conn.execute(
                            "UPDATE conversations SET created_at = ?, updated_at = ?, is_pinned = ? WHERE id = ?;",
                            (ts_ms, ts_ms, 1 if is_pinned else 0, local_id),
                        )
                new_count += 1
            else:
                existing = existing_map[cid]
                needs_update = False
                if title != existing["title"] or (ts_ms and ts_ms > existing["updated_at"]):
                    needs_update = True

                if needs_update:
                    async with self.write_transaction() as conn:
                        await conn.execute(
                            """
                            UPDATE conversations 
                            SET title = ?, updated_at = max(updated_at, ?), is_pinned = ?
                            WHERE id = ?;
                            """,
                            (title, ts_ms or now, 1 if is_pinned else 0, existing["id"]),
                        )
                    updated_count += 1
                else:
                    unchanged_count += 1

        return {
            "synced": len(remote_list),
            "new": new_count,
            "updated": updated_count,
            "unchanged": unchanged_count,
        }

    async def hydrate_remote_turns(
        self,
        conversation_id: str,
        turns_payload: list[Any],
    ) -> int:
        """
        Parses Google Gemini turn payloads from RPC hNvQHb and populates local messages.
        Supports both:
        1) Real Google hNvQHb payload: [ [ [ [cid, rid], ..., [[user_text]], [[[rcid, [asst_text]]]], [sec, nsec] ], ... ], ... ]
        2) Mock server payload: [cid, [ [turn_id, role, parts, timestamp, rid, rcid], ... ]]
        """
        if not turns_payload or not isinstance(turns_payload, list):
            return 0

        conv_row = await self.db.fetch_one(
            "SELECT id, model FROM conversations WHERE id = ?;", (conversation_id,)
        )
        if not conv_row:
            raise ValueError(f"Conversation {conversation_id} not found")
        conv_model = conv_row["model"]

        existing_msgs = await self.db.fetch_all(
            "SELECT id, content, thoughts, metadata_json FROM messages WHERE conversation_id = ?;",
            (conversation_id,),
        )
        existing_turn_ids = set()
        turn_id_to_row: dict[str, dict[str, Any]] = {}
        for em in existing_msgs:
            if em.get("metadata_json"):
                try:
                    meta = json.loads(em["metadata_json"])
                    if "remote_turn_id" in meta:
                        rt_id = meta["remote_turn_id"]
                        existing_turn_ids.add(rt_id)
                        turn_id_to_row[rt_id] = em
                except Exception:
                    pass

        prev_msg_id: int | None = None
        latest_msg = await self.db.fetch_one(
            "SELECT id FROM messages WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 1;",
            (conversation_id,),
        )
        if latest_msg:
            prev_msg_id = int(latest_msg["id"])

        inserted_count = 0
        now = int(time.time() * 1000)

        # Case 1: Real Google hNvQHb format where turns_payload[0] is a list of turn pairs (newest first)
        if isinstance(turns_payload[0], list):
            for tp in reversed(turns_payload[0]):
                if not isinstance(tp, list) or len(tp) < 4:
                    continue
                id_block = tp[0] if isinstance(tp[0], list) else []
                rid = str(id_block[1]) if len(id_block) > 1 and id_block[1] else f"r_{uuid.uuid4().hex[:8]}"
                user_turn_id = f"{rid}_user"
                asst_turn_id = f"{rid}_asst"

                # Timestamp
                ts_ms = now
                if len(tp) > 4 and isinstance(tp[4], list) and len(tp[4]) >= 1 and isinstance(tp[4][0], (int, float)):
                    nsec = int(tp[4][1]) if len(tp[4]) > 1 and isinstance(tp[4][1], (int, float)) else 0
                    ts_ms = int(tp[4][0]) * 1000 + (nsec // 1_000_000)

                # Extract user text & model
                user_text = ""
                turn_model = conv_model
                if isinstance(tp[2], list) and len(tp[2]) > 0:
                    if isinstance(tp[2][0], list) and len(tp[2][0]) > 0 and tp[2][0][0]:
                        user_text = str(tp[2][0][0])
                    if len(tp[2]) > 4 and isinstance(tp[2][4], str) and tp[2][4]:
                        turn_model = tp[2][4]

                # Extract assistant text, thoughts, immersive documents & rcid
                asst_text = ""
                asst_thoughts = ""
                rcid = None
                if isinstance(tp[3], list) and len(tp[3]) > 0 and isinstance(tp[3][0], list) and len(tp[3][0]) > 0:
                    cand = tp[3][0][0]
                    if isinstance(cand, list) and len(cand) >= 2:
                        rcid = str(cand[0]) if cand[0] else None
                        if isinstance(cand[1], list) and len(cand[1]) > 0 and cand[1][0]:
                            asst_text = str(cand[1][0])
                        if len(cand) > 37 and isinstance(cand[37], list) and len(cand[37]) > 0:
                            tb = cand[37][0]
                            if isinstance(tb, list) and len(tb) > 0 and isinstance(tb[0], str):
                                asst_thoughts = tb[0]
                        from backend.app.bridge.streaming_parser import extract_task_thoughts_and_immersive
                        asst_thoughts, asst_text = extract_task_thoughts_and_immersive(cand, asst_thoughts, asst_text)

                if user_text and user_turn_id not in existing_turn_ids:
                    u_msg = await self.add_message(
                        conversation_id=conversation_id,
                        role="user",
                        content=user_text,
                        parent_id=prev_msg_id,
                        model=turn_model,
                        created_at=ts_ms,
                        metadata={"remote_turn_id": user_turn_id, "rid": rid},
                    )
                    prev_msg_id = u_msg.id
                    inserted_count += 1
                    existing_turn_ids.add(user_turn_id)
                elif user_turn_id in turn_id_to_row:
                    prev_msg_id = int(turn_id_to_row[user_turn_id]["id"])

                if asst_text and asst_turn_id not in existing_turn_ids:
                    a_msg = await self.add_message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=asst_text,
                        thoughts=asst_thoughts or None,
                        parent_id=prev_msg_id,
                        model=turn_model,
                        created_at=ts_ms + 1,
                        metadata={"remote_turn_id": asst_turn_id, "rid": rid, "rcid": rcid},
                    )
                    prev_msg_id = a_msg.id
                    inserted_count += 1
                    existing_turn_ids.add(asst_turn_id)
                elif asst_text and asst_turn_id in turn_id_to_row:
                    em = turn_id_to_row[asst_turn_id]
                    prev_msg_id = int(em["id"])
                    old_content = em.get("content") or ""
                    old_thoughts = em.get("thoughts") or ""
                    if (
                        "immersive_entry_chip" in old_content
                        or len(asst_text) > len(old_content) + 10
                        or (asst_thoughts and len(asst_thoughts) > len(old_thoughts) + 10)
                    ):
                        async with self.write_transaction() as conn:
                            await conn.execute(
                                "UPDATE messages SET content = ?, thoughts = ?, updated_at = ? WHERE id = ?;",
                                (asst_text, asst_thoughts or old_thoughts or None, now, int(em["id"])),
                            )
                        inserted_count += 1

            return inserted_count

        # Case 2: Mock server format [cid, [ [turn_id, role, parts, timestamp, rid, rcid], ... ]]
        if len(turns_payload) < 2 or not isinstance(turns_payload[1], list):
            return 0

        raw_turns = turns_payload[1]
        for turn in raw_turns:
            if not isinstance(turn, list) or len(turn) < 3:
                continue

            turn_id = str(turn[0])
            if turn_id in existing_turn_ids:
                for em in existing_msgs:
                    if em.get("metadata_json") and f'"remote_turn_id": "{turn_id}"' in em["metadata_json"]:
                        prev_msg_id = int(em["id"])
                        break
                continue

            raw_role = str(turn[1]).lower()
            role = "user" if "user" in raw_role else "assistant"
            raw_parts = turn[2]

            text_content = ""
            if isinstance(raw_parts, list):
                parts_acc = []
                for p in raw_parts:
                    if isinstance(p, list) and len(p) >= 2 and p[0] == "text":
                        parts_acc.append(str(p[1]))
                    elif isinstance(p, str):
                        parts_acc.append(p)
                text_content = "\n\n".join(parts_acc)

            ts = turn[3] if len(turn) > 3 and isinstance(turn[3], (int, float)) else now
            if ts > 100000000000000:
                ts = ts // 1000

            rid = turn[4] if len(turn) > 4 and isinstance(turn[4], str) else None
            rcid = turn[5] if len(turn) > 5 and isinstance(turn[5], str) else None

            metadata = {
                "remote_turn_id": turn_id,
                "rid": rid,
                "rcid": rcid,
            }

            new_msg = await self.add_message(
                conversation_id=conversation_id,
                role=role,
                content=text_content,
                parent_id=prev_msg_id,
                model=conv_model,
                created_at=int(ts),
                metadata=metadata,
            )
            prev_msg_id = new_msg.id
            inserted_count += 1
            existing_turn_ids.add(turn_id)

        return inserted_count

    async def log_usage_telemetry(
        self,
        account_id: str,
        model_id: str,
        model_name: str,
        feature_mode: str = "chat",
        reasoning_level: int = 1,
        request_timestamp: int | None = None,
        prompt_tokens_est: int = 0,
        completion_tokens_est: int = 0,
        thinking_tokens_est: int = 0,
        status_code: int = 200,
        error_code: str | None = None,
        duration_ms: int = 0,
        was_failover: int = 0,
        failover_from_account: str | None = None,
    ) -> None:
        """Records token and request counts to usage_telemetry in the database."""
        await self.db.record_telemetry(
            account_id=account_id,
            model_id=model_id,
            model_name=model_name,
            feature_mode=feature_mode,
            reasoning_level=reasoning_level,
            request_timestamp=request_timestamp,
            prompt_tokens_est=prompt_tokens_est,
            completion_tokens_est=completion_tokens_est,
            thinking_tokens_est=thinking_tokens_est,
            status_code=status_code,
            error_code=error_code,
            duration_ms=duration_ms,
            was_failover=was_failover,
            failover_from_account=failover_from_account,
        )
