# tests/e2e/harness/ui_sim_harness.py
"""UI Virtualization, Incremental Markdown, KaTeX, and Performance Observer harness."""

from __future__ import annotations
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

class VirtualListSimulator:
    """Simulates dynamic-height viewport virtualization and DOM bounding."""

    def __init__(self, viewport_height: int = 800, overscan: int = 2):
        self.viewport_height = viewport_height
        self.overscan = overscan
        self.item_heights: List[int] = []
        self.scroll_top: int = 0
        self.is_pinned_to_bottom: bool = True
        self.nodes_per_message: int = 12
        self.chrome_nodes: int = 32

    def set_items(self, heights: List[int]):
        self.item_heights = list(heights)

    def total_height(self) -> int:
        return sum(self.item_heights)

    def scroll_to(self, new_scroll_top: int):
        total = self.total_height()
        max_scroll = max(0, total - self.viewport_height)
        clamped = max(0, min(new_scroll_top, max_scroll))

        # Check if user scrolled up away from bottom (>30px)
        distance_from_bottom = total - (clamped + self.viewport_height)
        if distance_from_bottom > 30:
            self.is_pinned_to_bottom = False
        else:
            self.is_pinned_to_bottom = True

        self.scroll_top = clamped

    def add_message(self, height: int):
        self.item_heights.append(height)
        if self.is_pinned_to_bottom:
            self.scroll_to(max(0, self.total_height() - self.viewport_height))

    def update_tail_message_height(self, new_height: int):
        if self.item_heights:
            self.item_heights[-1] = new_height
            if self.is_pinned_to_bottom:
                self.scroll_to(max(0, self.total_height() - self.viewport_height))

    def compute_visible_window(self) -> Tuple[int, int, List[int]]:
        """Returns (start_idx, end_idx, mounted_indices)."""
        if not self.item_heights:
            return 0, 0, []

        offsets = []
        accum = 0
        for h in self.item_heights:
            offsets.append(accum)
            accum += h

        view_start = self.scroll_top
        view_end = self.scroll_top + self.viewport_height

        # Binary search or scan for first item intersecting viewport
        first_visible = 0
        for i, off in enumerate(offsets):
            if off + self.item_heights[i] > view_start:
                first_visible = i
                break

        last_visible = first_visible
        for i in range(first_visible, len(self.item_heights)):
            if offsets[i] <= view_end:
                last_visible = i
            else:
                break

        start_mounted = max(0, first_visible - self.overscan)
        end_mounted = min(len(self.item_heights) - 1, last_visible + self.overscan)
        mounted_indices = list(range(start_mounted, end_mounted + 1))

        return start_mounted, end_mounted, mounted_indices

    def count_active_dom_nodes(self) -> int:
        _, _, mounted = self.compute_visible_window()
        return len(mounted) * self.nodes_per_message + self.chrome_nodes


class IncrementalMarkdownParser:
    """Decomposes streaming text buffer into completed immutable blocks and mutable tail block."""

    BLOCK_SPLIT_REGEX = re.compile(r"\n\n+")

    @classmethod
    def parse_stream(cls, text_buffer: str) -> Dict[str, Any]:
        if not text_buffer:
            return {"immutable_blocks": [], "tail_block": "", "is_in_code_block": False}

        # Check if inside an unclosed code block fence ```
        fence_count = text_buffer.count("```")
        is_in_code_block = (fence_count % 2) != 0

        if is_in_code_block:
            last_fence_idx = text_buffer.rfind("```")
            immutable_text = text_buffer[:last_fence_idx].strip()
            tail_text = text_buffer[last_fence_idx:]
        else:
            stripped = text_buffer.rstrip()
            if stripped.endswith("```") or "\n\n" not in text_buffer:
                if stripped.endswith("```"):
                    immutable_text = text_buffer.strip()
                    tail_text = ""
                else:
                    immutable_text = ""
                    tail_text = text_buffer
            else:
                last_fence = text_buffer.rfind("```")
                last_break = text_buffer.rfind("\n\n")
                if last_fence != -1 and last_fence > last_break:
                    # After closing fence
                    immutable_text = text_buffer[:last_fence + 3].strip()
                    tail_text = text_buffer[last_fence + 3:].strip()
                elif last_break != -1:
                    immutable_text = text_buffer[:last_break].strip()
                    tail_text = text_buffer[last_break + 2:]
                else:
                    immutable_text = ""
                    tail_text = text_buffer

        typed_blocks = []
        if immutable_text:
            # Tokenize code blocks vs text blocks
            code_pattern = re.compile(r"```([a-zA-Z0-9_-]*)\r?\n(.*?)```", re.DOTALL)
            last_idx = 0
            for match in code_pattern.finditer(immutable_text):
                before_text = immutable_text[last_idx:match.start()].strip()
                if before_text:
                    for p in cls.BLOCK_SPLIT_REGEX.split(before_text):
                        p = p.strip()
                        if p:
                            b_type = "heading" if p.startswith("#") else "paragraph"
                            typed_blocks.append({"type": b_type, "content": p})

                lang = match.group(1).strip() or "text"
                code_content = match.group(2).removesuffix("\n").removesuffix("\r")
                typed_blocks.append({"type": "code", "language": lang, "content": code_content})
                last_idx = match.end()

            after_text = immutable_text[last_idx:].strip()
            if after_text:
                for p in cls.BLOCK_SPLIT_REGEX.split(after_text):
                    p = p.strip()
                    if p:
                        b_type = "heading" if p.startswith("#") else "paragraph"
                        typed_blocks.append({"type": b_type, "content": p})

        return {
            "immutable_blocks": typed_blocks,
            "tail_block": tail_text,
            "is_in_code_block": is_in_code_block,
        }


class KaTeXMathExtractor:
    """Extracts inline and display LaTeX math expressions."""

    BLOCK_MATH_REGEX = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
    # Matches $math$ but avoids currency like $100 or $50.00
    INLINE_MATH_REGEX = re.compile(r"(?<!\$)\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\$)")

    @classmethod
    def extract_math(cls, text: str) -> Dict[str, List[str]]:
        block_formulas = cls.BLOCK_MATH_REGEX.findall(text)
        # Strip blocks before finding inlines
        cleaned = cls.BLOCK_MATH_REGEX.sub(" ", text)
        inline_formulas = cls.INLINE_MATH_REGEX.findall(cleaned)
        return {
            "block_formulas": [f.strip() for f in block_formulas],
            "inline_formulas": [f.strip() for f in inline_formulas],
        }


class PromptInputSimulator:
    """Simulates prompt textarea expansion, history recall, and attachment tray."""

    def __init__(self, max_rows: int = 8):
        self.text: str = ""
        self.history: List[str] = []
        self.history_index: int = -1
        self.max_rows = max_rows
        self.attachments: List[Dict[str, Any]] = []

    def set_text(self, text: str):
        self.text = text

    def calculate_rows(self) -> int:
        lines = self.text.count("\n") + 1
        return max(1, min(self.max_rows, lines))

    def submit(self) -> str:
        val = self.text.strip()
        if val:
            self.history.append(val)
            self.history_index = len(self.history)
            self.text = ""
            self.attachments = []
        return val

    def recall_up(self) -> str:
        if not self.history:
            return self.text
        if self.history_index == -1:
            self.history_index = len(self.history) - 1
        elif self.history_index > 0:
            self.history_index -= 1
        self.text = self.history[self.history_index]
        return self.text

    def recall_down(self) -> str:
        if not self.history or self.history_index == -1:
            return self.text
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self.text = self.history[self.history_index]
        else:
            self.history_index = len(self.history)
            self.text = ""
        return self.text

    def add_attachment(self, name: str, size: int, mime: str):
        self.attachments.append({"name": name, "size": size, "mime": mime})

    def remove_attachment(self, index: int):
        if 0 <= index < len(self.attachments):
            self.attachments.pop(index)


class LongTaskPerformanceObserver:
    """Simulates performance observer recording task durations and asserting 0 Long Tasks (>50ms)."""

    def __init__(self, threshold_ms: float = 50.0):
        self.threshold_ms = threshold_ms
        self.long_tasks: List[Dict[str, Any]] = []
        self.total_tasks: int = 0

    def record_task(self, name: str, duration_ms: float):
        self.total_tasks += 1
        if duration_ms > self.threshold_ms:
            self.long_tasks.append({"name": name, "duration_ms": duration_ms})

    def long_task_count(self) -> int:
        return len(self.long_tasks)

    def is_performant(self) -> bool:
        return len(self.long_tasks) == 0
