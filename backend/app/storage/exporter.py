"""
backend/app/storage/exporter.py
High-fidelity Markdown and structured JSON thread exporters.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Dict, List, Optional

from backend.app.storage.repository import Conversation, Message


class ThreadExporter:
    """Thread exporter producing clean Markdown and structured JSON transcripts."""

    @staticmethod
    def _format_iso8601(epoch_ms: int) -> str:
        dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _format_display_time(epoch_ms: int) -> str:
        dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")

    @classmethod
    def balance_code_fences(cls, text: str) -> str:
        """
        Scans text for triple-backtick markdown fences and ensures balanced closure.
        Prevents broken layouts when generation is aborted mid-code block.
        """
        fence_matches = re.findall(r"^```", text, flags=re.MULTILINE)
        if len(fence_matches) % 2 != 0:
            return text + "\n```\n"
        return text

    @classmethod
    def export_to_markdown(
        cls,
        conversation: Conversation,
        thread: List[Message],
        exported_at: Optional[int] = None,
    ) -> str:
        """Generates clean GitHub-flavored Markdown with YAML frontmatter."""
        export_ts = exported_at or int(datetime.now(timezone.utc).timestamp() * 1000)
        lines: List[str] = []

        # 1. YAML Frontmatter
        lines.append("---")
        safe_title = conversation.title.replace('"', '\\"')
        lines.append(f'title: "{safe_title}"')
        lines.append(f'id: "{conversation.id}"')
        if conversation.remote_conversation_id:
            lines.append(f'remote_id: "{conversation.remote_conversation_id}"')
        lines.append(f'model: "{conversation.model}"')
        if conversation.gem_id:
            lines.append(f'gem_id: "{conversation.gem_id}"')
        lines.append(f'created_at: "{cls._format_iso8601(conversation.created_at)}"')
        lines.append(f'exported_at: "{cls._format_iso8601(export_ts)}"')
        if conversation.active_leaf_message_id is not None:
            lines.append(f'active_leaf_id: "{conversation.active_leaf_message_id}"')
        lines.append(f"total_messages: {len(thread)}")
        lines.append("---\n")

        # 2. System Prompt (if present)
        if conversation.system_prompt:
            lines.append("> **System Prompt:**")
            for sp_line in conversation.system_prompt.strip().splitlines():
                lines.append(f"> {sp_line}")
            lines.append("\n---\n")

        # 3. Message Sequence
        for i, msg in enumerate(thread):
            # Role Header
            if msg.role == "user":
                role_label = "User"
            elif msg.role == "assistant":
                role_label = f"Gemini ({msg.model})"
            else:
                role_label = f"System ({msg.role})"

            lines.append(f"### {role_label}")
            lines.append(f"*{cls._format_display_time(msg.created_at)}*\n")

            # Reasoning / Thoughts Block
            thoughts_val = msg.thoughts or msg.thinking_content
            if thoughts_val and thoughts_val.strip():
                lines.append("<details>")
                lines.append("<summary>Thought Process</summary>\n")
                lines.append(thoughts_val.strip())
                lines.append("\n</details>\n")

            # Message Content (with code block balancing)
            sanitized_content = cls.balance_code_fences(msg.content.strip())
            lines.append(sanitized_content)

            # Attachments Manifest
            if msg.attachments:
                lines.append("\n**Attachments:**")
                for att in msg.attachments:
                    size_kb = att.size_bytes / 1024.0
                    lines.append(f"- `{att.filename}` ({att.mime_type}, {size_kb:.1f} KB)")

            # Divider between turns (except last)
            if i < len(thread) - 1:
                lines.append("\n---\n")

        return "\n".join(lines).strip() + "\n"

    @classmethod
    def export_markdown(
        cls,
        conversation: Conversation,
        thread: List[Message],
        exported_at: Optional[int] = None,
    ) -> str:
        """Alias for export_to_markdown."""
        return cls.export_to_markdown(conversation, thread, exported_at)

    @classmethod
    def export_to_json(
        cls,
        conversation: Conversation,
        active_thread: List[Message],
        all_messages: Optional[List[Message]] = None,
        exported_at: Optional[int] = None,
    ) -> str:
        """Generates lossless structured JSON export with DAG tree metadata."""
        export_ts = exported_at or int(datetime.now(timezone.utc).timestamp() * 1000)
        messages_pool = all_messages if all_messages is not None else active_thread

        # Build adjacency graph
        branch_tree: Dict[str, Dict[str, Any]] = {}
        for m in messages_pool:
            branch_tree[str(m.id)] = {
                "parent_id": m.parent_id,
                "children": m.children_ids or [],
                "sibling_index": m.sibling_index,
                "sibling_count": m.sibling_count,
            }

        payload: Dict[str, Any] = {
            "$schema": "https://gemini-local.internal/schemas/v1/conversation-export.json",
            "version": "1.0",
            "exported_at": cls._format_iso8601(export_ts),
            "conversation": conversation.to_dict(),
            "active_branch_ids": [m.id for m in active_thread],
            "active_branch": [m.to_dict() for m in active_thread],
            "messages": [m.to_dict() for m in messages_pool],
            "branch_tree": branch_tree,
        }

        return json.dumps(payload, indent=2, ensure_ascii=False)

    @classmethod
    def export_json(
        cls,
        conversation: Conversation,
        active_thread: List[Message],
        all_messages: Optional[List[Message]] = None,
        exported_at: Optional[int] = None,
    ) -> str:
        """Alias for export_to_json."""
        return cls.export_to_json(conversation, active_thread, all_messages, exported_at)
