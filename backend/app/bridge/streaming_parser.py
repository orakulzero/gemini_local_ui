"""
backend/app/bridge/streaming_parser.py
Two-stage streaming decoder:
1. StreamingFrameParser: Anti-XSSI prefix strip, UTF-16 code unit length-prefixed envelopes, TCP chunk reassembly.
2. StreamingDeltaAccumulator: Thought extraction, cumulative text diffing, flicker escape stripping, StreamChunk emission.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass, field
import difflib
import json
import logging
import re
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Tuple, Union
from urllib.parse import urlparse

from backend.app.bridge.exceptions import map_google_error_code
from backend.app.bridge.models import extract_answered_by_label

logger = logging.getLogger("gemini_bridge.streaming_parser")

_FLICKER_ESC_RE = re.compile(r"(?:\\+[`*_~])+\S*$")
_YOUTUBE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:youtube\.com/(?:watch\?(?:.*?&)?v=|shorts/)|youtu\.be/)([\w-]{11})"
)

MAX_FRAME_CODE_UNITS: int = 16 * 1024 * 1024  # 16M UTF-16 code units (32 MB)


@dataclass
class StreamChunk:
    """
    Normalized stream chunk emitted by Stage 2.
    Matches the PROJECT.md interface contract:
    {type: 'text'|'thought'|'grounding'|'citation'|'done', delta: str, metadata: dict}
    """
    type: str  # "text" | "thought" | "grounding" | "citation" | "done"
    delta: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class StreamingFrameParser:
    """
    Stage 1 Streaming Parser:
    Consumes raw text/bytes chunks from Google Gemini StreamGenerate or batchexecute,
    strips anti-XSSI prefixes, respects UTF-16 code unit length-prefixed envelope boundaries,
    and yields parsed outer JSON frame lists.
    """

    XSSI_PREFIX = ")]}'"
    MAX_FRAME_CODE_UNITS: int = MAX_FRAME_CODE_UNITS

    def __init__(self, utf8_errors: str = "replace") -> None:
        self._utf8_errors = utf8_errors
        self.reset()

    def reset(self) -> None:
        """Resets the parser buffer and incremental UTF-8 decoder."""
        self._buffer: str = ""
        self._xssi_stripped: bool = False
        self._utf8_decoder = codecs.getincrementaldecoder("utf-8")(errors=self._utf8_errors)

    def feed(self, chunk: Union[str, bytes]) -> List[List[Any]]:
        """
        Feeds a chunk of text or bytes into the parser buffer and returns
        any fully completed JSON envelope frames.
        """
        if isinstance(chunk, bytes):
            chunk_str = self._utf8_decoder.decode(chunk, final=False)
        else:
            chunk_str = chunk

        self._buffer += chunk_str
        completed_frames: List[List[Any]] = []

        # 1. Strip Anti-XSSI prefix if not already stripped
        if not self._xssi_stripped:
            clean_buf = self._buffer.lstrip()
            if clean_buf.startswith(self.XSSI_PREFIX):
                prefix_idx = self._buffer.find(self.XSSI_PREFIX)
                after_prefix = prefix_idx + len(self.XSSI_PREFIX)
                while after_prefix < len(self._buffer) and self._buffer[after_prefix] in ("\r", "\n"):
                    after_prefix += 1
                self._buffer = self._buffer[after_prefix:]
                self._xssi_stripped = True
            elif len(clean_buf) < len(self.XSSI_PREFIX):
                # Buffer might be a partial prefix, wait for more data
                return completed_frames
            else:
                self._xssi_stripped = True

        # 2. Extract length-prefixed envelopes
        while True:
            self._buffer = self._buffer.lstrip("\r\n")
            if not self._buffer:
                break

            newline_pos = self._buffer.find("\n")
            if newline_pos == -1:
                # Still receiving the length header; wait for more data
                if len(self._buffer) > self.MAX_FRAME_CODE_UNITS:
                    logger.error("Parser buffer exceeded MAX_FRAME_CODE_UNITS without newline; resetting buffer")
                    self._buffer = ""
                break

            len_str = self._buffer[:newline_pos].strip()
            if not len_str:
                self._buffer = self._buffer[newline_pos + 1:]
                continue

            try:
                expected_code_units = int(len_str)
                if expected_code_units <= 0:
                    raise ValueError(f"Non-positive frame length: {expected_code_units}")
                if expected_code_units > self.MAX_FRAME_CODE_UNITS:
                    raise ValueError(
                        f"Frame length exceeds maximum allowed code units "
                        f"({expected_code_units} > {self.MAX_FRAME_CODE_UNITS})"
                    )
            except ValueError as err:
                logger.warning("Invalid frame length header encountered: %r (%s)", len_str, err)
                self._buffer = self._buffer[newline_pos + 1:]
                continue

            remainder = self._buffer[newline_pos + 1:]

            # Fast length pre-check: 1 Python character is at most 2 UTF-16 code units (surrogate pair).
            # Avoid expensive full-string UTF-16 encoding when chunk is clearly incomplete.
            if len(remainder) * 2 < max(0, expected_code_units - 2):
                break

            # Encode remainder to UTF-16LE to measure UTF-16 code units (1 code unit = 2 bytes)
            remainder_u16 = remainder.encode("utf-16le", errors="surrogatepass")

            # Real Google batchexecute / StreamGenerate wire format counts the leading '\n'
            # before the length integer and trailing '\n' after the JSON array:
            # expected_code_units == utf16_len(json) + 2, so remainder holds (expected_code_units - 2)
            # code units of JSON followed by '\n' ((expected_code_units - 1) code units total).
            real_wire_needed_bytes = max(2, (expected_code_units - 1) * 2)
            exact_needed_bytes = expected_code_units * 2

            if expected_code_units > 2 and len(remainder_u16) >= real_wire_needed_bytes:
                json_bytes_len = (expected_code_units - 2) * 2
                trailing_unit = remainder_u16[json_bytes_len:real_wire_needed_bytes]
                if trailing_unit in (b"\n\x00", b"\r\x00"):
                    candidate_str = remainder_u16[:json_bytes_len].decode("utf-16le", errors="surrogatepass")
                    try:
                        frame_data = json.loads(candidate_str)
                        self._buffer = remainder_u16[real_wire_needed_bytes:].decode(
                            "utf-16le", errors="surrogatepass"
                        )
                        if isinstance(frame_data, list):
                            completed_frames.append(frame_data)
                        continue
                    except json.JSONDecodeError:
                        pass

            if len(remainder_u16) < exact_needed_bytes:
                # Incomplete frame; wait for next network chunk
                break

            # Extract exact slice in UTF-16 bytes
            payload_u16 = remainder_u16[:exact_needed_bytes]
            remainder_u16_tail = remainder_u16[exact_needed_bytes:]

            payload_str = payload_u16.decode("utf-16le", errors="surrogatepass")
            self._buffer = remainder_u16_tail.decode("utf-16le", errors="surrogatepass")

            try:
                frame_data = json.loads(payload_str)
                if isinstance(frame_data, list):
                    completed_frames.append(frame_data)
                else:
                    logger.debug("Skipping non-list frame envelope: %s", type(frame_data))
            except json.JSONDecodeError as err:
                logger.warning("Failed to decode JSON frame payload: %s; payload preview: %r", err, payload_str[:120])
                continue

        return completed_frames

    async def afeed(self, chunk_stream: AsyncIterator[Union[str, bytes]]) -> AsyncIterator[List[Any]]:
        """Asynchronously consumes string/bytes chunks and yields JSON envelope frames."""
        async for chunk in chunk_stream:
            for frame in self.feed(chunk):
                yield frame


def extract_task_thoughts_and_immersive(
    candidate: List[Any], raw_thoughts: str, raw_text: str
) -> Tuple[str, str]:
    """
    Extracts Tasks / Spark mode (featureMode=40) agency steps & plans from candidate[12] ("56", "58")
    and immersive Markdown documents from candidate[30][0][4].
    """
    extra_thought_parts: List[str] = []

    # 1. Extract Task Plan ("56") and Agency Progress Steps ("58") from candidate[12]
    if len(candidate) > 12 and isinstance(candidate[12], dict):
        rich = candidate[12]
        plan_node = rich.get("56")
        if isinstance(plan_node, list) and len(plan_node) >= 2 and isinstance(plan_node[1], list):
            plan_title = str(plan_node[0]) if plan_node[0] else "Task Plan"
            plan_lines = [f"### 📋 {plan_title}"]
            for step in plan_node[1]:
                if isinstance(step, list) and len(step) >= 2:
                    s_num = step[0]
                    s_title = step[1] or ""
                    s_desc = f" — {step[2]}" if len(step) > 2 and step[2] else ""
                    plan_lines.append(f"{s_num}. **{s_title}**{s_desc}")
            if len(plan_lines) > 1:
                extra_thought_parts.append("\n".join(plan_lines))

        agency_node = rich.get("58")
        if (
            isinstance(agency_node, list)
            and len(agency_node) > 1
            and isinstance(agency_node[1], list)
            and len(agency_node[1]) > 4
            and isinstance(agency_node[1][4], list)
            and len(agency_node[1][4]) > 2
            and isinstance(agency_node[1][4][2], list)
        ):
            step_lines = ["### ⚡ Task Execution"]
            for s_entry in agency_node[1][4][2]:
                if isinstance(s_entry, list) and len(s_entry) > 4 and isinstance(s_entry[4], list):
                    s_info = s_entry[4]
                    if len(s_info) >= 2 and s_info[1]:
                        step_lines.append(f"- {s_info[1]}")
            if len(step_lines) > 1:
                extra_thought_parts.append("\n".join(step_lines))

    if extra_thought_parts:
        prefix_block = "\n\n".join(extra_thought_parts)
        if raw_thoughts:
            raw_thoughts = f"{prefix_block}\n\n---\n\n{raw_thoughts}"
        else:
            raw_thoughts = prefix_block

    # 2. Extract Immersive / Task Markdown report from candidate[30]
    if len(candidate) > 30 and isinstance(candidate[30], list) and len(candidate[30]) > 0:
        immersive_docs: List[str] = []
        for imm in candidate[30]:
            if isinstance(imm, list) and len(imm) > 4 and isinstance(imm[4], str) and imm[4].strip():
                imm_title = str(imm[2]).strip() if len(imm) > 2 and imm[2] else ""
                imm_body = imm[4].strip()
                if imm_title and not imm_body.startswith("#"):
                    immersive_docs.append(f"# {imm_title}\n\n{imm_body}")
                else:
                    immersive_docs.append(imm_body)
        if immersive_docs:
            cleaned_base = re.sub(
                r"https?://googleusercontent\.com/immersive_entry_chip/\d+\s*", "", raw_text or ""
            ).strip()
            joined_docs = "\n\n---\n\n".join(immersive_docs)
            raw_text = f"{cleaned_base}\n\n{joined_docs}".strip() if cleaned_base else joined_docs

    return raw_thoughts, raw_text


def is_high_surrogate(char: str) -> bool:
    """Returns True if the single character is a UTF-16 high surrogate (0xD800..0xDBFF)."""
    return len(char) == 1 and 0xD800 <= ord(char) <= 0xDBFF


def is_low_surrogate(char: str) -> bool:
    """Returns True if the single character is a UTF-16 low surrogate (0xDC00..0xDFFF)."""
    return len(char) == 1 and 0xDC00 <= ord(char) <= 0xDFFF


def sanitize_surrogate_text(text: str) -> str:
    """
    Sanitizes any unpaired UTF-16 surrogates in text by replacing them with \uFFFD.
    Valid surrogate pairs are recombined into 32-bit Unicode code points.
    Guarantees text can be safely encoded to standard UTF-8 without UnicodeEncodeError.
    """
    if not text:
        return text
    try:
        return text.encode("utf-16le", errors="surrogatepass").decode("utf-16le", errors="replace")
    except Exception:
        return text


def ensure_utf8_clean(text: str) -> str:
    """Ensures no surrogate code points exist that would fail UTF-8 encoding."""
    if not text:
        return text
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        return text.encode("utf-16le", errors="surrogatepass").decode("utf-16le", errors="replace")


def longest_common_prefix_length(s1: str, s2: str) -> int:
    """Returns character length of the longest common prefix of two strings."""
    min_len = min(len(s1), len(s2))
    for i in range(min_len):
        if s1[i] != s2[i]:
            return i
    return min_len


def sanitize_and_buffer_surrogates(text: str, is_final: bool) -> str:
    """
    Sanitizes UTF-16 surrogate pairs and buffers trailing incomplete high surrogates.
    1. Recombines adjacent surrogate pairs (high + low) into 32-bit Unicode code points.
    2. When not is_final, trims a trailing isolated high surrogate so it remains
       buffered until the matching low surrogate arrives in subsequent chunks.
    3. Replaces any unpaired high surrogates followed by non-low surrogates, or isolated
       low surrogates, with U+FFFD ('\uFFFD') using surrogatepass encoding, preventing
       fatal UnicodeEncodeError downstream.
    """
    if not text:
        return text

    if not is_final and is_high_surrogate(text[-1]):
        text_to_clean = text[:-1]
    else:
        text_to_clean = text

    clean = sanitize_surrogate_text(text_to_clean)

    if is_final:
        clean = ensure_utf8_clean(clean)

    return clean


class StreamingDeltaAccumulator:
    """
    Stage 2 Streaming Accumulator:
    Takes outer JSON frames from Stage 1, parses double-serialized inner JSON payloads,
    tracks cumulative text and thoughts, calculates net deltas, strips transient flicker escapes,
    extracts citations/grounding, and emits structured StreamChunk items.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """
        Resets all accumulator state, text buffers, metadata, and emission latches.
        Ensures the accumulator can be reused cleanly for a new streaming turn.
        """
        self.last_sent_text: str = ""
        self.last_sent_thoughts: str = ""
        self.full_raw_text: str = ""
        self.full_raw_thoughts: str = ""
        self.cid: Optional[str] = None
        self.rid: Optional[str] = None
        self.rcid: Optional[str] = None
        self.continuation_token: Optional[str] = None
        self.is_completed: bool = False
        self._done_emitted: bool = False
        self.citations: List[Any] = []
        self.images: List[Any] = []
        self.grounding_sources: List[Dict[str, Any]] = []
        self.grounding_queries: List[str] = []
        self.youtube_videos: List[Dict[str, Any]] = []
        self._seen_youtube_ids: set[str] = set()
        self.answered_by: Optional[str] = None
        self.speculative_tail_buffer: int = 12
        self._extracted_code_executions: List[Dict[str, Any]] = []
        self._extracted_draft_candidates: List[Dict[str, Any]] = []

    def _normalize_citations(self, raw: Any) -> List[Any]:
        if not raw or not isinstance(raw, list):
            return []
        result = []
        for idx, item in enumerate(raw):
            if isinstance(item, dict):
                result.append(item)
            elif isinstance(item, list):
                c_idx = item[0] if len(item) > 0 and isinstance(item[0], int) else (idx + 1)
                c_url = str(item[1]) if len(item) > 1 and item[1] else ""
                c_title = str(item[2]) if len(item) > 2 and item[2] else ""
                c_domain = str(item[3]) if len(item) > 3 and isinstance(item[3], str) and not item[3].isdigit() else ""
                if not c_domain and c_url:
                    try:
                        c_domain = urlparse(c_url).netloc
                    except Exception:
                        c_domain = ""
                c_snippet = str(item[4]) if len(item) > 4 and item[4] else None
                result.append({
                    "index": c_idx,
                    "url": c_url,
                    "title": c_title,
                    "domain": c_domain,
                    "snippet": c_snippet,
                })
        return result

    def _normalize_images(self, raw: Any) -> List[Any]:
        if not raw or not isinstance(raw, list):
            return []
        # Unwrap nested outer list if candidate[12][7] is [ [gen1, gen2, ...] ]
        items = raw
        if (
            len(raw) == 1
            and isinstance(raw[0], list)
            and len(raw[0]) > 0
            and isinstance(raw[0][0], list)
            and not isinstance(raw[0][0], str)
        ):
            items = raw[0]

        result = []
        for idx, item in enumerate(items):
            if isinstance(item, dict):
                result.append(item)
            elif isinstance(item, list):
                # Check for real nested Google gg-dl image structure: item[0][3][3]
                nested_url = None
                try:
                    if (
                        len(item) > 0
                        and isinstance(item[0], list)
                        and len(item[0]) > 3
                        and isinstance(item[0][3], list)
                        and len(item[0][3]) > 3
                        and isinstance(item[0][3][3], str)
                    ):
                        nested_url = item[0][3][3]
                except Exception:
                    nested_url = None

                if nested_url:
                    image_id = (
                        item[1][0]
                        if len(item) > 1 and isinstance(item[1], list) and len(item[1]) > 0 and isinstance(item[1][0], str)
                        else f"http://googleusercontent.com/image_generation_content/{idx}"
                    )
                    result.append({
                        "url": nested_url,
                        "image_id": image_id,
                        "alt": str(item[0][3][0]) if len(item[0][3]) > 0 and item[0][3][0] else "",
                        "width": None,
                        "height": None,
                    })
                else:
                    url = item[0] if len(item) > 0 and item[0] else ""
                    alt = item[1] if len(item) > 1 and item[1] else ""
                    width = item[2] if len(item) > 2 and isinstance(item[2], int) else None
                    height = item[3] if len(item) > 3 and isinstance(item[3], int) else None
                    result.append({
                        "url": url,
                        "alt": alt,
                        "width": width,
                        "height": height,
                    })
        return result

    @property
    def done_emitted(self) -> bool:
        """Returns True if the terminal 'done' chunk has already been emitted for this stream."""
        return self._done_emitted

    def _clean_intermediate_text(self, text: str) -> str:
        """Strips volatile trailing escapes and unclosed code fences during generation."""
        if not text:
            return ""
        # Tail-only optimization: _FLICKER_ESC_RE is anchored at end ($), only scan the tail 64 chars
        if len(text) <= 64:
            cleaned = _FLICKER_ESC_RE.sub("", text)
        else:
            tail = text[-64:]
            tail_cleaned = _FLICKER_ESC_RE.sub("", tail)
            cleaned = text[:-64] + tail_cleaned

        if cleaned.endswith("\n```"):
            cleaned = cleaned[:-4]
        return cleaned

    def _compute_delta(
        self,
        new_val: str,
        last_sent: str,
        is_final: bool,
        tail_buffer_size: int = 0,
    ) -> Tuple[str, str]:
        """
        Computes incremental delta between last sent value and newly arrived cumulative value.
        Buffers incomplete high surrogates at delta boundaries until low surrogate arrives.
        Applies Speculative Tail Buffering (withholding last N characters when not is_final)
        and Longest Common Prefix (LCP) alignment to eliminate Markdown code fence jumping.
        Returns: (delta_to_emit, updated_last_sent)
        """
        target = new_val if is_final else self._clean_intermediate_text(new_val)
        target = sanitize_and_buffer_surrogates(target, is_final=is_final)

        if not target:
            return "", last_sent

        # Speculative tail buffering: when streaming and volatile Markdown constructs or retroactive edits exist.
        # Check only the tail window (last 64 characters) to avoid permanently withholding tokens if backticks exist earlier.
        tail_window = target[-64:] if len(target) > 64 else target
        has_volatile_tail = bool(
            "`" in tail_window[-16:]
            or ("\\" in tail_window and _FLICKER_ESC_RE.search(tail_window))
            or (last_sent and not target.startswith(last_sent))
        )
        if not is_final and tail_buffer_size > 0 and has_volatile_tail:
            if len(target) <= tail_buffer_size:
                candidate = ""
            else:
                candidate = target[:-tail_buffer_size]
        else:
            candidate = target

        if not candidate or candidate == last_sent:
            return "", last_sent

        # Fast path: strictly prefix-aligned
        if candidate.startswith(last_sent):
            delta = candidate[len(last_sent):]
            return ensure_utf8_clean(delta), candidate

        # Fallback: retroactive server mutation or fence adjustment (LCP resolution)
        lcp_len = longest_common_prefix_length(last_sent, candidate)

        # If candidate grew past last_sent despite slight drift at tail, emit the new suffix
        if len(candidate) > len(last_sent):
            delta = candidate[len(last_sent):]
            return ensure_utf8_clean(delta), candidate

        # If candidate contracted or didn't grow beyond last_sent, hold back until it grows
        return "", last_sent


    def process_frame(self, frame: List[Any]) -> List[StreamChunk]:
        """
        Processes a single Stage 1 frame envelope and returns any resulting StreamChunks.
        """
        chunks: List[StreamChunk] = []

        entries = frame
        while len(entries) == 1 and isinstance(entries[0], list) and len(entries[0]) > 0 and isinstance(entries[0][0], list):
            entries = entries[0]

        for entry in entries:
            if not isinstance(entry, list) or len(entry) < 3:
                continue

            envelope_type, rpcid, inner_json_str = entry[0], entry[1], entry[2]

            # Check for error in envelope entry[5]
            if len(entry) > 5 and entry[5] is not None:
                self._check_envelope_error(entry[5])

            if not inner_json_str or not isinstance(inner_json_str, str):
                continue

            try:
                part_json = json.loads(inner_json_str)
            except json.JSONDecodeError:
                logger.warning("Failed to decode inner_json_str in envelope")
                continue

            if not isinstance(part_json, list):
                continue

            # 0. Check for error payload inside part_json[5]
            if len(part_json) > 5 and part_json[5] is not None:
                self._check_envelope_error(part_json[5])

            # 0b. Extract stamped model label ("4 Argon", "3.8 Flash", etc.)
            stamped_label = extract_answered_by_label(part_json)
            if stamped_label:
                self.answered_by = stamped_label

            # 1. Extract Conversation Metadata (inner_json[1])
            if len(part_json) > 1 and isinstance(part_json[1], list):
                meta = part_json[1]
                if len(meta) > 0 and meta[0]:
                    self.cid = str(meta[0])
                if len(meta) > 1 and meta[1]:
                    self.rid = str(meta[1])
                if len(meta) > 2 and meta[2]:
                    self.rcid = str(meta[2])

            # 2. Extract Continuation Token (inner_json[25])
            if len(part_json) > 25 and isinstance(part_json[25], str):
                self.continuation_token = part_json[25]

            # 3. Extract Candidates (inner_json[4])
            if len(part_json) > 4 and isinstance(part_json[4], list) and len(part_json[4]) > 0:
                candidates_list = part_json[4]
                self._extracted_draft_candidates = [
                    {
                        "rcid": str(c[0] or ""),
                        "text": str(c[1][0] if len(c) > 1 and isinstance(c[1], list) and len(c[1]) > 0 else ""),
                        "index": idx,
                    }
                    for idx, c in enumerate(candidates_list)
                    if isinstance(c, list) and len(c) > 0
                ]
                candidate = candidates_list[0]
                if isinstance(candidate, list):
                    if len(candidate) > 0 and candidate[0]:
                        self.rcid = str(candidate[0])

                    # 3a. Check Turn Status: candidate[8][0] == 2 indicates completion
                    if len(candidate) > 8 and isinstance(candidate[8], list) and len(candidate[8]) > 0:
                        if candidate[8][0] == 2:
                            self.is_completed = True

                    # 3b. Extract Reasoning / Thoughts: candidate[37][0][0] + candidate[12] ("56"/"58" Task steps)
                    raw_thoughts = ""
                    if len(candidate) > 37 and isinstance(candidate[37], list) and len(candidate[37]) > 0:
                        thought_block = candidate[37][0]
                        if isinstance(thought_block, list) and len(thought_block) > 0 and isinstance(thought_block[0], str):
                            raw_thoughts = thought_block[0]

                    raw_text = ""
                    if len(candidate) > 1 and isinstance(candidate[1], list) and len(candidate[1]) > 0:
                        if isinstance(candidate[1][0], str):
                            raw_text = candidate[1][0]

                    raw_thoughts, raw_text = extract_task_thoughts_and_immersive(candidate, raw_thoughts, raw_text)

                    if raw_thoughts:
                        self.full_raw_thoughts = raw_thoughts
                        if not self._done_emitted:
                            thought_delta, self.last_sent_thoughts = self._compute_delta(
                                raw_thoughts, self.last_sent_thoughts, self.is_completed, tail_buffer_size=0
                            )
                            if thought_delta:
                                chunks.append(StreamChunk(type="thought", delta=thought_delta))

                    # 3c. Extract Generated Cumulative Text: candidate[1][0] + candidate[30] immersive document
                    new_youtube = False
                    if raw_text:
                        self.full_raw_text = raw_text
                        if not self._done_emitted:
                            text_delta, self.last_sent_text = self._compute_delta(
                                raw_text, self.last_sent_text, self.is_completed, tail_buffer_size=self.speculative_tail_buffer
                            )
                            if text_delta:
                                chunks.append(StreamChunk(type="text", delta=text_delta))

                        # Check for YouTube links in newly arrived text slice
                        yt_scan_start = max(0, len(self.last_sent_text) - 64)
                        for m in _YOUTUBE_RE.finditer(raw_text[yt_scan_start:]):
                            vid = m.group(1)
                            if vid not in self._seen_youtube_ids:
                                self._seen_youtube_ids.add(vid)
                                self.youtube_videos.append({
                                    "video_id": vid,
                                    "title": f"YouTube Video ({vid})",
                                    "url": f"https://www.youtube.com/watch?v={vid}",
                                    "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                                    "channel": None,
                                })
                                new_youtube = True

                    # 3d. Extract Rich Content: candidate[12] (can be dict or list)
                    new_images = False
                    if len(candidate) > 12 and candidate[12] is not None:
                        rich = candidate[12]

                        # Extract Citations (Field 43)
                        raw_cit = None
                        if isinstance(rich, dict) and "43" in rich and rich["43"]:
                            raw_cit = rich["43"]
                        elif isinstance(rich, list) and len(rich) > 43 and rich[43]:
                            raw_cit = rich[43]

                        if raw_cit is not None:
                            normalized_cit = self._normalize_citations(raw_cit)
                            if normalized_cit != self.citations:
                                self.citations = normalized_cit
                                chunks.append(
                                    StreamChunk(
                                        type="citation",
                                        delta="",
                                        metadata={"citations": self.citations},
                                    )
                                )

                        # Extract Images (Field 7)
                        raw_img = None
                        if isinstance(rich, dict) and "7" in rich and rich["7"]:
                            raw_img = rich["7"]
                        elif isinstance(rich, list) and len(rich) > 7 and rich[7]:
                            raw_img = rich[7]

                        if raw_img is not None:
                            normalized_img = self._normalize_images(raw_img)
                            if normalized_img != self.images:
                                self.images = normalized_img
                                new_images = True
                                chunks.append(
                                    StreamChunk(
                                        type="grounding",
                                        delta="",
                                        metadata={"generated_images": self.images},
                                    )
                                )

                        # Extract Grounding Queries & Sources (Fields 16, 23, 27)
                        if isinstance(rich, dict):
                            g_queries: List[str] = list(self.grounding_queries)
                            g_sources: List[Dict[str, Any]] = list(self.grounding_sources)
                            g_changed = False
                            for fk in ("16", "23", "27"):
                                if fk in rich and rich[fk]:
                                    fval = rich[fk]
                                    if isinstance(fval, list):
                                        for v in fval:
                                            if isinstance(v, str) and v not in g_queries:
                                                g_queries.append(v)
                                                g_changed = True
                                            elif isinstance(v, list) and len(v) > 0:
                                                if isinstance(v[0], str) and v[0].startswith("http"):
                                                    src_dict = {
                                                        "url": v[0],
                                                        "title": v[1] if len(v) > 1 and v[1] else "",
                                                        "snippet": v[2] if len(v) > 2 and v[2] else "",
                                                    }
                                                    if src_dict not in g_sources:
                                                        g_sources.append(src_dict)
                                                        g_changed = True
                                                elif isinstance(v[0], str) and v[0] not in g_queries:
                                                    g_queries.append(v[0])
                                                    g_changed = True
                            if g_changed:
                                self.grounding_queries = g_queries
                                self.grounding_sources = g_sources
                                chunks.append(
                                    StreamChunk(
                                        type="grounding",
                                        delta="",
                                        metadata={
                                            "generated_images": self.images,
                                            "grounding_sources": self.grounding_sources,
                                            "queries": self.grounding_queries,
                                        },
                                    )
                                )

                        # Extract Structured YouTube from rich (field 34 or key "youtube")
                        if isinstance(rich, dict):
                            yt_raw = rich.get("youtube") or rich.get("34")
                            if yt_raw and isinstance(yt_raw, list):
                                for y in yt_raw:
                                    vid = None
                                    yt_title = ""
                                    yt_url = ""
                                    if isinstance(y, dict):
                                        vid = y.get("video_id") or y.get("id")
                                        yt_title = y.get("title", "")
                                        yt_url = y.get("url", "")
                                    elif isinstance(y, list) and len(y) > 0:
                                        vid = str(y[0])
                                        yt_title = str(y[1]) if len(y) > 1 and y[1] else ""
                                        yt_url = str(y[2]) if len(y) > 2 and y[2] else f"https://www.youtube.com/watch?v={vid}"
                                    if vid and vid not in self._seen_youtube_ids:
                                        self._seen_youtube_ids.add(vid)
                                        self.youtube_videos.append({
                                            "video_id": vid,
                                            "title": yt_title or f"YouTube Video ({vid})",
                                            "url": yt_url or f"https://www.youtube.com/watch?v={vid}",
                                            "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                                            "channel": None,
                                        })
                                        new_youtube = True

                        # Extract Cloud Python Code Execution (REQ-CAP-01)
                        if isinstance(rich, dict):
                            exec_code = rich.get("executable_code")
                            exec_res = rich.get("code_execution_result")
                            if isinstance(exec_code, dict) or isinstance(exec_res, dict):
                                ec_dict = exec_code if isinstance(exec_code, dict) else {}
                                er_dict = exec_res if isinstance(exec_res, dict) else {}
                                exec_entry = {
                                    "language": str(ec_dict.get("language", "python")),
                                    "code": str(ec_dict.get("code", "")),
                                    "stdout": str(er_dict.get("output", er_dict.get("stdout", ""))),
                                    "stderr": str(er_dict.get("stderr", "")),
                                    "outcome": str(er_dict.get("outcome", "OUTCOME_OK")),
                                }
                                if exec_entry not in self._extracted_code_executions:
                                    self._extracted_code_executions.append(exec_entry)
                            raw_exec_list = rich.get("code_executions")
                            if isinstance(raw_exec_list, list):
                                for item in raw_exec_list:
                                    if isinstance(item, dict) and item not in self._extracted_code_executions:
                                        self._extracted_code_executions.append(item)
                        elif isinstance(rich, list):
                            for item in rich:
                                if isinstance(item, dict) and ("executable_code" in item or "code" in item):
                                    ec_dict = (
                                        item.get("executable_code", item)
                                        if isinstance(item.get("executable_code", item), dict)
                                        else {}
                                    )
                                    er_dict = (
                                        item.get("code_execution_result", item)
                                        if isinstance(item.get("code_execution_result", item), dict)
                                        else {}
                                    )
                                    exec_entry = {
                                        "language": str(ec_dict.get("language", "python")),
                                        "code": str(ec_dict.get("code", "")),
                                        "stdout": str(er_dict.get("output", er_dict.get("stdout", ""))),
                                        "stderr": str(er_dict.get("stderr", "")),
                                        "outcome": str(er_dict.get("outcome", "OUTCOME_OK")),
                                    }
                                    if exec_entry not in self._extracted_code_executions:
                                        self._extracted_code_executions.append(exec_entry)

                    # Emit media chunk if new images or YouTube videos were found
                    if new_images or new_youtube:
                        chunks.append(
                            StreamChunk(
                                type="media",
                                delta="",
                                metadata={
                                    "images": self.images,
                                    "youtube": self.youtube_videos,
                                },
                            )
                        )

        # 4. If completed, emit final trailing text delta and 'done' chunk exactly once
        if self.is_completed and not self._done_emitted:
            final_delta, self.last_sent_text = self._compute_delta(
                self.full_raw_text, self.last_sent_text, is_final=True, tail_buffer_size=0
            )
            if final_delta:
                chunks.append(StreamChunk(type="text", delta=final_delta))

            self._done_emitted = True
            done_meta: Dict[str, Any] = {
                "cid": self.cid,
                "rid": self.rid,
                "rcid": self.rcid,
                "answered_by": self.answered_by,
                "continuation_token": self.continuation_token,
                "full_text": self.full_raw_text,
                "full_thoughts": self.full_raw_thoughts,
                "citations": self.citations,
                "images": self.images,
                "grounding_sources": self.grounding_sources,
                "grounding_queries": self.grounding_queries,
                "grounding": {
                    "sources": self.grounding_sources,
                    "queries": self.grounding_queries,
                },
                "youtube": self.youtube_videos,
            }
            if self._extracted_code_executions:
                done_meta["code_executions"] = list(self._extracted_code_executions)
            if len(self._extracted_draft_candidates) >= 2:
                done_meta["draft_candidates"] = list(self._extracted_draft_candidates)
            chunks.append(
                StreamChunk(
                    type="done",
                    delta="",
                    metadata=done_meta,
                )
            )

        return chunks

    def _check_envelope_error(self, err_node: Any) -> None:
        """Parses Google RPC error codes from error payload node."""
        try:
            # Check nested structure: err_node[2][0][1][0] or err_node[0][1][0]
            code = None
            msg = None
            if isinstance(err_node, list):
                if len(err_node) > 2 and isinstance(err_node[2], list) and len(err_node[2]) > 0:
                    inner = err_node[2][0]
                    if isinstance(inner, list) and len(inner) > 1 and isinstance(inner[1], list):
                        code = inner[1][0]
                        if len(inner[1]) > 1:
                            msg = str(inner[1][1])
                elif len(err_node) > 0 and isinstance(err_node[0], (int, float)):
                    code = int(err_node[0])
                    if len(err_node) > 1 and isinstance(err_node[1], str):
                        msg = err_node[1]

            if code is not None and isinstance(code, int):
                logger.error("ENVELOPE ERROR DETECTED: code=%s, msg=%s, raw_err_node=%s", code, msg, err_node)
                map_google_error_code(code, msg)
        except (IndexError, TypeError, KeyError):
            pass
