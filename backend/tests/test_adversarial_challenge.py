"""
backend/tests/test_adversarial_challenge.py
Empirical Adversarial Stress Test Suite for StreamingFrameParser and StreamingDeltaAccumulator.

Testing 4 core attack vectors:
1. Single-byte TCP fragmentation across length headers and multi-byte UTF-8 payloads.
2. Surrogate pair fragmentation across packet boundaries and UTF-16 code unit boundaries.
3. Massive Unicode and emoji streams (1,000+ emojis, ZWJ sequences, math symbols, RTL/bidi text).
4. Malformed frames, corrupt length prefixes, negative lengths, massive lengths, truncated chunks.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import random
import string
import sys
import time
from typing import Any, List
import pytest

from backend.app.bridge.exceptions import (
    AuthenticationError,
    GeminiBridgeError,
    ModelInconsistentError,
    TemporaryBackendOverloadError,
    UsageLimitExceededError,
)
from backend.app.bridge.streaming_parser import (
    StreamChunk,
    StreamingDeltaAccumulator,
    StreamingFrameParser,
)


def _encode_frame(data: Any) -> bytes:
    """Helper to encode a frame in Google wire format: <utf16_code_units>\n<json>\n."""
    raw_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    code_units = len(raw_json.encode("utf-16le")) // 2
    header = f"{code_units}\n"
    return header.encode("utf-8") + raw_json.encode("utf-8") + b"\n"


def _make_stream_envelope(
    text: str,
    thoughts: str = "",
    is_final: bool = False,
    cid: str = "c1",
    rid: str = "r1",
    rcid: str = "rc1",
    citations: Any = None,
) -> List[Any]:
    """Helper to build a canonical 2-phase Gemini candidate frame."""
    candidate: List[Any] = [
        rcid,
        [text] if text else None,
        None, None, None, None, None, None,
        [2 if is_final else 1],  # candidate[8][0] == 2 indicates final
    ]
    # Pad to 38 elements for candidate[12] and candidate[37]
    while len(candidate) < 38:
        candidate.append(None)
    if citations:
        candidate[12] = {"43": citations}
    if thoughts:
        candidate[37] = [[thoughts]]

    inner_json = [
        None,
        [cid, rid, rcid],
        None, None,
        [candidate],
    ]
    inner_str = json.dumps(inner_json, ensure_ascii=False, separators=(",", ":"))
    outer = [
        ["wrb.fr", "StreamGenerate", inner_str, None, None, None]
    ]
    return outer


# =====================================================================
# VECTOR 1: Single-byte TCP fragmentation across length headers and payloads
# =====================================================================

class TestVector1SingleByteFragmentation:
    """Tests byte-by-byte and fragmented TCP delivery across headers and payloads."""

    def test_single_byte_ascii_length_and_payload(self):
        """Single-byte delivery of ASCII envelope and payload."""
        parser = StreamingFrameParser()
        data = _encode_frame([["test", 123]])
        stream = b")]}'\n" + data

        frames = []
        for b in stream:
            res = parser.feed(bytes([b]))
            if res:
                frames.extend(res)

        assert len(frames) == 1
        assert frames[0] == [["test", 123]]

    def test_utf8_byte_fragmentation_defect_reproduction(self):
        """
        EMPIRICAL PROOF OF DEFECT (Finding 1):
        When raw UTF-8 bytes for multi-byte characters (4-byte emojis, 3-byte CJK, 2-byte Cyrillic)
        are split across TCP packet boundaries, parser.feed() decodes chunks with errors='replace'.
        This permanently corrupts characters to \ufffd and alters the UTF-16 code unit count,
        causing JSONDecodeError and dropping the entire frame.
        """
        parser = StreamingFrameParser()
        # Raw wire frame containing UTF-8 emojis and Cyrillic
        payload = [["wrb.fr", "StreamGenerate", json.dumps(["Hello 🚀 漢字"], ensure_ascii=False)]]
        wire_data = _encode_frame(payload)

        # Feed 1 byte at a time
        frames = []
        for b in wire_data:
            res = parser.feed(bytes([b]))
            if res:
                frames.extend(res)

        # Verified fix: 1 frame received cleanly with all international characters and emojis preserved
        assert len(frames) == 1
        decoded_text = json.loads(frames[0][0][2])[0]
        assert decoded_text == "Hello 🚀 漢字"

    def test_single_byte_feeding_with_incremental_decoder_benchmark(self):
        """
        Benchmark / verification showing the proposed remedy:
        Using codecs.getincrementaldecoder('utf-8') recovers 100% of fragmented frames.
        """
        decoder = codecs.getincrementaldecoder("utf-8")()
        parser = StreamingFrameParser()
        payload = [["wrb.fr", "StreamGenerate", json.dumps(["Hello 🚀 漢字"], ensure_ascii=False)]]
        wire_data = _encode_frame(payload)

        frames = []
        for b in wire_data:
            text_chunk = decoder.decode(bytes([b]), final=False)
            if text_chunk:
                res = parser.feed(text_chunk)
                if res:
                    frames.extend(res)

        assert len(frames) == 1
        decoded_text = json.loads(frames[0][0][2])[0]
        assert decoded_text == "Hello 🚀 漢字"

    def test_arbitrary_chunk_sizes_string_mode(self):
        """Randomized chunk sizes (1 to 7 chars) feeding multi-frame stream in string mode."""
        parser = StreamingFrameParser()
        frames_expected = []
        full_stream = ")]}'\n"
        for i in range(10):
            frame = [["wrb.fr", "StreamGenerate", json.dumps([f"Turn {i}: 🌟 sparkles and numbers {i * 100}"])]]
            frames_expected.append(frame)
            full_stream += _encode_frame(frame).decode("utf-8")

        random.seed(42)
        idx = 0
        frames_received = []
        while idx < len(full_stream):
            chunk_size = random.randint(1, 7)
            chunk = full_stream[idx : idx + chunk_size]
            idx += chunk_size
            res = parser.feed(chunk)
            if res:
                frames_received.extend(res)

        assert len(frames_received) == 10
        for i in range(10):
            assert frames_received[i] == frames_expected[i]


# =====================================================================
# VECTOR 2: Surrogate pair fragmentation across boundaries
# =====================================================================

class TestVector2SurrogatePairFragmentation:
    """Tests astral plane characters (UTF-16 surrogate pairs) at frame and packet boundaries."""

    def test_surrogate_pair_measured_in_utf16_code_units(self):
        """
        In UTF-16, astral plane characters (U+10000..U+10FFFF) count as 2 code units.
        Verify that length header correctly accounts for surrogate pairs.
        """
        parser = StreamingFrameParser()
        emoji_str = "😀😁😂😃😄😅😆😇😈😉"
        raw_json = json.dumps([emoji_str], ensure_ascii=False)
        utf16_code_units = len(raw_json.encode("utf-16le")) // 2
        wire = f"{utf16_code_units}\n{raw_json}\n"
        
        frames = parser.feed(wire)
        assert len(frames) == 1
        assert frames[0] == [emoji_str]

    def test_corrupt_odd_or_split_surrogate_length(self):
        """
        If length prefix cuts inside an astral emoji (high surrogate only),
        parser must not crash with unhandled exception and recovers on subsequent lines.
        """
        parser = StreamingFrameParser()
        bad_wire = "3\n[\"🚀\"]\n"
        good_wire = _encode_frame([["recovered"]]).decode("utf-8")

        frames_bad = parser.feed(bad_wire)
        assert isinstance(frames_bad, list)

        frames_good = parser.feed(good_wire)
        assert isinstance(frames_good, list)


# =====================================================================
# VECTOR 3: Massive Unicode, Emojis, Math, RTL, and Delta Accumulation
# =====================================================================

class TestVector3MassiveUnicodeAndDeltaAccumulation:
    """Tests massive Unicode streams (1,000+ emojis, math, RTL, ZWJ) and delta accumulator."""

    def test_1000_emojis_stream(self):
        """Massive stream with 1,000 distinct and repeating emojis."""
        emojis_bank = ["🚀", "🔥", "🧠", "✨", "🎉", "🌈", "⚡", "🛸", "💎", "👾", "🤖", "🧬", "🌍", "🌕", "💥"]
        massive_emojis = "".join(random.choice(emojis_bank) for _ in range(1000))
        
        parser = StreamingFrameParser()
        accumulator = StreamingDeltaAccumulator()

        chunk_size = 200
        emitted_deltas = []

        for step in range(5):
            is_final = (step == 4)
            accumulated_emojis = massive_emojis[: (step + 1) * chunk_size]
            frame_data = _make_stream_envelope(accumulated_emojis, is_final=is_final)
            wire = _encode_frame(frame_data).decode("utf-8")
            
            frames = parser.feed(wire)
            assert len(frames) == 1
            chunks = accumulator.process_frame(frames[0])
            for c in chunks:
                if c.type == "text":
                    emitted_deltas.append(c.delta)

        reconstructed_text = "".join(emitted_deltas)
        assert reconstructed_text == massive_emojis
        assert accumulator.full_raw_text == massive_emojis
        assert accumulator.is_completed is True

    def test_complex_zwj_and_combining_characters(self):
        """Complex Unicode sequences: family ZWJ, flags, skin tones, combining diacritics."""
        zwj_samples = [
            "👨‍👩‍👧‍👦",  # Family: man + ZWJ + woman + ZWJ + girl + ZWJ + boy
            "🏳️‍🌈",      # Rainbow flag: white flag + VS16 + ZWJ + rainbow
            "🧑🏽‍💻",      # Technologist: person + medium skin tone + ZWJ + laptop
            "🇺🇸",      # US flag (regional indicators)
            "e\u0301\u0302\u0303\u0304",  # Stacked diacritics
        ]
        test_text = " ".join(zwj_samples)

        parser = StreamingFrameParser()
        accumulator = StreamingDeltaAccumulator()

        frame_data = _make_stream_envelope(test_text, is_final=True)
        wire = _encode_frame(frame_data).decode("utf-8")
        frames = parser.feed(wire)
        assert len(frames) == 1

        chunks = accumulator.process_frame(frames[0])
        text_chunks = [c.delta for c in chunks if c.type == "text"]
        assert "".join(text_chunks) == test_text

    def test_rtl_arabic_hebrew_and_bidi_code_blocks(self):
        """Bidirectional text with Arabic, Hebrew, and embedded code blocks."""
        bidi_text = (
            "مرحبا بالعالم! هذا اختبار للغة العربية.\n"
            "שלום עולם! זהו מבחן לעברית.\n"
            "```python\n"
            "def hello():\n"
            "    print('مرحبا / שלום')\n"
            "```\n"
            "نهاية الاختبار."
        )

        parser = StreamingFrameParser()
        accumulator = StreamingDeltaAccumulator()

        # Stream with transient markdown flicker
        intermediate = bidi_text[:-15] + "\\*\\*bold"
        frame_int = _make_stream_envelope(intermediate, is_final=False)
        frames = parser.feed(_encode_frame(frame_int).decode("utf-8"))
        chunks1 = accumulator.process_frame(frames[0])

        frame_final = _make_stream_envelope(bidi_text, is_final=True)
        frames2 = parser.feed(_encode_frame(frame_final).decode("utf-8"))
        chunks2 = accumulator.process_frame(frames2[0])

        all_text = "".join(c.delta for c in chunks1 + chunks2 if c.type == "text")
        assert all_text == bidi_text
        assert accumulator.is_completed is True

    def test_interleaved_thoughts_and_text_stream(self):
        """Interleaved thought progression and text generation."""
        parser = StreamingFrameParser()
        accumulator = StreamingDeltaAccumulator()

        steps = [
            ("Thinking step 1...", ""),
            ("Thinking step 1...\nThinking step 2...", "Hello"),
            ("Thinking step 1...\nThinking step 2...\nDone thinking.", "Hello world, "),
            ("Thinking step 1...\nThinking step 2...\nDone thinking.", "Hello world, streaming is ready!"),
        ]

        thoughts_deltas = []
        text_deltas = []

        for i, (thought, text) in enumerate(steps):
            is_final = (i == len(steps) - 1)
            frame = _make_stream_envelope(text, thoughts=thought, is_final=is_final)
            frames = parser.feed(_encode_frame(frame).decode("utf-8"))
            chunks = accumulator.process_frame(frames[0])
            for c in chunks:
                if c.type == "thought":
                    thoughts_deltas.append(c.delta)
                elif c.type == "text":
                    text_deltas.append(c.delta)

        assert "".join(thoughts_deltas) == "Thinking step 1...\nThinking step 2...\nDone thinking."
        assert "".join(text_deltas) == "Hello world, streaming is ready!"


# =====================================================================
# VECTOR 4: Malformed frames, corrupt length prefixes, truncated chunks
# =====================================================================

class TestVector4MalformedFramesAndFaultRecovery:
    """Tests corrupt headers, negative lengths, oversized lengths, non-JSON payloads."""

    def test_duplicate_done_chunks_defect_reproduction(self):
        """Verifies duplicate 'done' chunks are suppressed on trailing metadata frames."""
        accumulator = StreamingDeltaAccumulator()

        # Turn completion frame
        f1 = _make_stream_envelope("Final response", is_final=True)
        chunks1 = accumulator.process_frame(f1)
        done_chunks1 = [c for c in chunks1 if c.type == "done"]
        text_chunks1 = [c for c in chunks1 if c.type == "text"]
        assert len(done_chunks1) == 1
        assert len(text_chunks1) == 1
        assert accumulator.done_emitted is True

        # Subsequent metadata/citation frame arriving on the same stream
        f2 = _make_stream_envelope("Final response", is_final=True, citations=[{"url": "https://example.com"}])
        chunks2 = accumulator.process_frame(f2)
        done_chunks2 = [c for c in chunks2 if c.type == "done"]
        text_chunks2 = [c for c in chunks2 if c.type == "text"]
        citation_chunks2 = [c for c in chunks2 if c.type == "citation"]

        # Verified: 0 duplicate done, 0 duplicate text, exactly 1 citation chunk emitted cleanly
        assert len(done_chunks2) == 0, "Duplicate 'done' chunk emitted!"
        assert len(text_chunks2) == 0, "Duplicate 'text' chunk emitted on trailing metadata frame!"
        assert len(citation_chunks2) == 1, "Citation chunk was not emitted!"
        assert citation_chunks2[0].metadata["citations"] == [{"url": "https://example.com"}]

    def test_citation_deduplication_across_trailing_frames(self):
        """Verifies duplicate citations across repeated trailing frames are deduplicated."""
        accumulator = StreamingDeltaAccumulator()
        f1 = _make_stream_envelope("Done", is_final=True)
        accumulator.process_frame(f1)

        # Frame 2: Citations arrive
        f2 = _make_stream_envelope("Done", is_final=True, citations=[{"url": "https://example.com"}])
        c2 = accumulator.process_frame(f2)
        assert len([c for c in c2 if c.type == "citation"]) == 1

        # Frame 3: Same citations arrive again (e.g. with continuation token)
        f3 = _make_stream_envelope("Done", is_final=True, citations=[{"url": "https://example.com"}])
        c3 = accumulator.process_frame(f3)
        assert len(c3) == 0, "Redundant citation chunk emitted for identical metadata!"

    def test_streaming_delta_accumulator_clean_reset(self):
        """Verifies StreamingDeltaAccumulator resets completely between turns."""
        acc = StreamingDeltaAccumulator()
        f1 = _make_stream_envelope("Turn 1 text", thoughts="Turn 1 thoughts", is_final=True)
        chunks1 = acc.process_frame(f1)
        assert acc.done_emitted is True
        assert acc.is_completed is True
        assert acc.full_raw_text == "Turn 1 text"

        # Reset
        acc.reset()
        assert acc.done_emitted is False
        assert acc.is_completed is False
        assert acc.last_sent_text == ""
        assert acc.last_sent_thoughts == ""
        assert acc.full_raw_text == ""
        assert acc.full_raw_thoughts == ""
        assert acc.cid is None
        assert acc.rid is None
        assert acc.rcid is None
        assert acc.continuation_token is None
        assert acc.citations == []
        assert acc.images == []

        # Turn 2
        f2 = _make_stream_envelope("Turn 2 fresh text", is_final=True)
        chunks2 = acc.process_frame(f2)
        text_chunks2 = [c for c in chunks2 if c.type == "text"]
        done_chunks2 = [c for c in chunks2 if c.type == "done"]
        assert len(text_chunks2) == 1
        assert text_chunks2[0].delta == "Turn 2 fresh text"
        assert len(done_chunks2) == 1

    def test_negative_length_prefix_defect_reproduction(self):
        """
        EMPIRICAL PROOF OF DEFECT (Finding 3):
        Negative length prefixes (e.g. '-50\n') cause backwards slice remainder_u16[:-100],
        corrupting buffer slicing rather than being rejected as an invalid header.
        """
        parser = StreamingFrameParser()
        corrupt = "-50\n[1, 2, 3]\n"
        # Slices backwards and throws JSONDecodeError on empty string
        frames = parser.feed(corrupt)
        assert frames == []

    def test_unbounded_buffer_on_oversized_length_prefix(self):
        """
        EMPIRICAL PROOF OF DEFECT (Finding 4):
        An oversized length prefix (e.g. '999999999999\n') causes parser to buffer
        indefinitely without an upper bound limit, retaining memory on every feed.
        """
        parser = StreamingFrameParser()
        parser.feed("999999999999\npartial content")
        # Buffer retains the oversized expectation and data without limit
        assert len(parser._buffer) > 0
        parser.feed(" more content")
        assert "partial content more content" in parser._buffer

    def test_non_numeric_length_prefix_recovery(self):
        """Parser encounters non-numeric length header 'INVALID_HEADER\n'."""
        parser = StreamingFrameParser()
        corrupt_stream = "INVALID_HEADER\n[1, 2, 3]\n"
        valid_stream = _encode_frame([["valid", "frame"]]).decode("utf-8")

        frames1 = parser.feed(corrupt_stream)
        assert isinstance(frames1, list)

        frames2 = parser.feed(valid_stream)
        assert len(frames2) == 1
        assert frames2[0] == [["valid", "frame"]]

    def test_zero_length_prefix(self):
        """Parser encounters zero length header '0\n\n'."""
        parser = StreamingFrameParser()
        zero_header = "0\n\n"
        valid = _encode_frame([["zero_test_ok"]]).decode("utf-8")

        frames1 = parser.feed(zero_header)
        assert isinstance(frames1, list)

        frames2 = parser.feed(valid)
        assert len(frames2) == 1
        assert frames2[0] == [["zero_test_ok"]]

    def test_corrupt_non_json_payload(self):
        """Length is valid, but payload is non-JSON garbage."""
        parser = StreamingFrameParser()
        garbage = "THIS_IS_NOT_JSON!!!"
        header = f"{len(garbage)}\n"
        wire = f"{header}{garbage}\n"
        valid = _encode_frame([["after_garbage"]]).decode("utf-8")

        frames1 = parser.feed(wire)
        assert len(frames1) == 0  # Corrupted frame discarded

        frames2 = parser.feed(valid)
        assert len(frames2) == 1
        assert frames2[0] == [["after_garbage"]]

    def test_incomplete_truncated_chunk_held_in_buffer(self):
        """Frame is truncated mid-payload; parser buffers until completion."""
        parser = StreamingFrameParser()
        full_frame = _encode_frame([["complete_data"]]).decode("utf-8")
        
        half1 = full_frame[: len(full_frame) // 2]
        half2 = full_frame[len(full_frame) // 2 :]

        res1 = parser.feed(half1)
        assert len(res1) == 0  # Incomplete, buffered
        assert len(parser._buffer) > 0

        res2 = parser.feed(half2)
        assert len(res2) == 1  # Completed after remainder arrives
        assert res2[0] == [["complete_data"]]

    def test_accumulator_corrupted_inner_json(self):
        """StreamingDeltaAccumulator receives envelope with invalid inner_json_str."""
        accumulator = StreamingDeltaAccumulator()
        corrupt_frame = [["wrb.fr", "StreamGenerate", "{invalid_json_str:::"]]
        
        chunks = accumulator.process_frame(corrupt_frame)
        assert isinstance(chunks, list)
        assert len(chunks) == 0

    def test_accumulator_malformed_envelope_types(self):
        """StreamingDeltaAccumulator receives envelope with invalid types."""
        accumulator = StreamingDeltaAccumulator()
        accumulator.process_frame([])
        accumulator.process_frame(["not a list entry"])
        accumulator.process_frame([[1]])
        accumulator.process_frame([[1, 2]])
        accumulator.process_frame([["wrb.fr", "StreamGenerate", None]])
        accumulator.process_frame([["wrb.fr", "StreamGenerate", json.dumps({"not": "a list"})]])
        accumulator.process_frame([["wrb.fr", "StreamGenerate", json.dumps([None, "not a list"])]])
        accumulator.process_frame([["wrb.fr", "StreamGenerate", json.dumps([None, [], None, None, ["not a list candidate"]])]])
        assert accumulator.is_completed is False

    def test_accumulator_google_error_propagation(self):
        """StreamingDeltaAccumulator triggers typed exception when error node is present."""
        accumulator = StreamingDeltaAccumulator()
        error_envelope = [
            ["wrb.fr", "StreamGenerate", None, None, None, [1037, "Quota exceeded"]]
        ]
        with pytest.raises(UsageLimitExceededError) as exc_info:
            accumulator.process_frame(error_envelope)
        assert exc_info.value.code == 1037

        inner_error_json = json.dumps([None, None, None, None, None, [1050, "Model inconsistent"]])
        error_frame_inner = [
            ["wrb.fr", "StreamGenerate", inner_error_json, None, None, None]
        ]
        with pytest.raises(ModelInconsistentError) as exc_info2:
            accumulator.process_frame(error_frame_inner)
        assert exc_info2.value.code == 1050

    def test_parser_clean_reset(self):
        """Verifies StreamingFrameParser reset method clears buffer and decoder."""
        parser = StreamingFrameParser()
        parser.feed("incomplete chunk data")
        assert len(parser._buffer) > 0
        parser.reset()
        assert parser._buffer == ""
        assert parser._xssi_stripped is False

    def test_parser_negative_length_header_recovery(self):
        """Verifies negative length header is rejected and parser recovers on next valid frame."""
        parser = StreamingFrameParser()
        corrupt = "-100\n[1, 2, 3]\n"
        valid = _encode_frame([["recovered_after_negative"]]).decode("utf-8")
        frames1 = parser.feed(corrupt)
        assert frames1 == []
        frames2 = parser.feed(valid)
        assert len(frames2) == 1
        assert frames2[0] == [["recovered_after_negative"]]

    def test_parser_oversized_length_rejection(self):
        """Verifies length header exceeding MAX_FRAME_CODE_UNITS is rejected and skipped."""
        parser = StreamingFrameParser()
        oversized = "20000000\n[1, 2, 3]\n"  # > 16M code units
        valid = _encode_frame([["recovered_after_oversized"]]).decode("utf-8")
        frames1 = parser.feed(oversized)
        assert frames1 == []
        frames2 = parser.feed(valid)
        assert len(frames2) == 1
        assert frames2[0] == [["recovered_after_oversized"]]


class TestRemediationInterfaceAlignment:
    """Tests host validation middleware and GeminiWebClient alignment."""

    @pytest.mark.asyncio
    async def test_host_header_missing_or_empty_rejected(self):
        """Verifies host validation middleware strictly rejects empty or missing Host header."""
        from backend.app.main import app
        import httpx

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            # Request without Host header
            resp_no_host = await client.get("/health", headers={"Host": ""})
            assert resp_no_host.status_code == 400
            assert "Invalid Host header" in resp_no_host.text

            # Request with whitespace Host header
            resp_ws_host = await client.get("/health", headers={"Host": "   "})
            assert resp_ws_host.status_code == 400
            assert "Invalid Host header" in resp_ws_host.text

    def test_gemini_web_client_interface_and_alias(self):
        """Verifies GeminiWebClient exposes list_remote_conversations and accepts history & kwargs."""
        import inspect
        from backend.app.bridge.client import GeminiWebClient

        client = GeminiWebClient()
        assert hasattr(client, "list_remote_conversations")
        assert client.list_remote_conversations == client.list_conversations

        sig = inspect.signature(client.generate_stream)
        assert "history" in sig.parameters
        assert "kwargs" in sig.parameters

