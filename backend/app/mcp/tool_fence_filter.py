"""
backend/app/mcp/tool_fence_filter.py
Streaming tool fence filter FSM preventing tool_call blocks from leaking to UI.
"""

from __future__ import annotations

import re

TRIGGER_FENCE = "```tool_call"
STATE_TEXT = 0
STATE_MAYBE_FENCE = 1
STATE_BUFFER_CALL = 2


class ToolFenceFilter:
    def __init__(self) -> None:
        self.state: int = STATE_TEXT
        self.pending_text: str = ""
        self.tool_buffer: str = ""

    def reset(self) -> None:
        self.state = STATE_TEXT
        self.pending_text = ""
        self.tool_buffer = ""

    def feed(self, delta: str) -> str:
        if not delta:
            return ""

        if self.state == STATE_BUFFER_CALL:
            self.tool_buffer += delta
            # Look for closing ``` fence
            m_close = re.search(r"\n```(?:\n|$)", self.tool_buffer)
            if m_close:
                after = self.tool_buffer[m_close.end():]
                self.tool_buffer = ""
                self.state = STATE_TEXT
                if after:
                    return self.feed(after)
            return ""

        self.pending_text += delta

        # Check if full TRIGGER_FENCE is already inside pending_text
        if TRIGGER_FENCE in self.pending_text:
            idx = self.pending_text.index(TRIGGER_FENCE)
            emitted = self.pending_text[:idx]
            after = self.pending_text[idx + len(TRIGGER_FENCE):]
            self.pending_text = ""
            self.tool_buffer = TRIGGER_FENCE + after
            self.state = STATE_BUFFER_CALL
            # Check if closing fence is already in after
            m_close = re.search(r"\n```(?:\n|$)", self.tool_buffer)
            if m_close:
                rem = self.tool_buffer[m_close.end():]
                self.tool_buffer = ""
                self.state = STATE_TEXT
                if rem:
                    return emitted + self.feed(rem)
            return emitted

        # Check if pending_text ends with a non-empty prefix of TRIGGER_FENCE
        match_len = 0
        max_check = min(len(self.pending_text), len(TRIGGER_FENCE) - 1)
        for i in range(max_check, 0, -1):
            suffix = self.pending_text[-i:]
            if TRIGGER_FENCE.startswith(suffix):
                match_len = i
                break

        if match_len > 0:
            emitted = self.pending_text[:-match_len]
            self.pending_text = self.pending_text[-match_len:]
            self.state = STATE_MAYBE_FENCE
            return emitted
        else:
            emitted = self.pending_text
            self.pending_text = ""
            self.state = STATE_TEXT
            return emitted

    def flush(self) -> str:
        if self.state == STATE_BUFFER_CALL:
            self.tool_buffer = ""
            self.state = STATE_TEXT
            return ""
        res = self.pending_text
        self.pending_text = ""
        self.state = STATE_TEXT
        return res


def strip_tool_call_fences(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"```tool_call\s*.*?\n```", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"```tool_call.*$", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()
