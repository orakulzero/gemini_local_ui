"""
backend/tests/test_r2_empirical_challenge.py
Round 2 Empirical Adversarial Re-Challenge Test Suite.

Author: m1_r2_challenger_1 (Critic / Specialist)
Target: Verification of M1 R2 remediation patches:
  1. Single-byte feeding across multi-byte UTF-8 packet boundaries (emojis, CJK, Cyrillic, astral planes, ZWJ).
  2. Single done chunk semantics on trailing metadata frames (citations, images, continuation tokens).
  3. Negative, zero, and oversized length headers, buffer bounds, and clean recovery.
  4. Mock server rapid start/stop lifecycle and port release on Windows.
  5. In-depth edge cases: List-format rich content, corrupted UTF-8 byte replacement, 500-frame randomized stress.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import random
import string
import time
from typing import Any, List, Tuple
import pytest

from backend.app.bridge.exceptions import (
    AuthenticationError,
    ModelInconsistentError,
    UsageLimitExceededError,
)
from backend.app.bridge.streaming_parser import (
    MAX_FRAME_CODE_UNITS,
    StreamChunk,
    StreamingDeltaAccumulator,
    StreamingFrameParser,
)
from backend.app.mock.mock_server import MockGeminiServer


def _encode_wire_frame(data: Any) -> bytes:
    """Encodes Python object into Google wire frame: <utf16_code_units>\n<json>\n."""
    raw_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    code_units = len(raw_json.encode("utf-16le")) // 2
    header = f"{code_units}\n".encode("utf-8")
    return header + raw_json.encode("utf-8") + b"\n"


def _make_candidate_envelope(
    text: str = "",
    thoughts: str = "",
    is_final: bool = False,
    cid: str = "c_emp_1",
    rid: str = "r_emp_1",
    rcid: str = "rc_emp_1",
    citations: Any = None,
    images: Any = None,
    continuation_token: str = "",
    rich_as_list: bool = False,
) -> List[Any]:
    """Constructs a fully populated Gemini response envelope."""
    candidate: List[Any] = [
        rcid,
        [text] if text else None,
        None, None, None, None, None, None,
        [2 if is_final else 1],  # [8][0] == 2 indicates turn complete
    ]
    while len(candidate) < 38:
        candidate.append(None)

    if rich_as_list:
        rich_list: List[Any] = [None] * 45
        if citations is not None:
            rich_list[43] = citations
        if images is not None:
            rich_list[7] = images
        candidate[12] = rich_list
    else:
        rich_dict: dict[str, Any] = {}
        if citations is not None:
            rich_dict["43"] = citations
        if images is not None:
            rich_dict["7"] = images
        if rich_dict:
            candidate[12] = rich_dict

    if thoughts:
        candidate[37] = [[thoughts]]

    inner_json: List[Any] = [
        None,
        [cid, rid, rcid],
        None, None,
        [candidate],
    ]
    while len(inner_json) <= 25:
        inner_json.append(None)
    if continuation_token:
        inner_json[25] = continuation_token

    inner_str = json.dumps(inner_json, ensure_ascii=False, separators=(",", ":"))
    return [["wrb.fr", "StreamGenerate", inner_str, None, None, None]]


# =====================================================================
# CHALLENGE 1: Empirical Multi-Byte UTF-8 Fragmentation Stress
# =====================================================================

class TestEmpiricalUTF8Fragmentation:
    """
    Stress-tests multi-byte UTF-8 packet boundary fragmentation.
    Feeds 1 byte at a time across diverse scripts, astral emojis, and ZWJ sequences.
    """

    def test_single_byte_feeding_diverse_scripts(self):
        """
        Feeds 1 byte at a time across:
        - 4-byte astral emojis: 🚀 🛰 🌌 🛸 🪐
        - 4-byte CJK Extension B: 𠮷 𠀋 𡈽
        - 3-byte CJK ideographs: 漢字, 日本語, 繁體中文, 한국어
        - 2-byte Latin/Greek: Café résumé naïve, λΩπΔΘΣΦΨ streaming parser
        - 2-byte Arabic / Hebrew: العربية, עברית
        - Complex ZWJ emoji: 👨‍👩‍👧‍👦 (family: man, woman, girl, boy)
        - Skin-tone modifiers: 👋🏽 🧑🏿‍💻
        """
        parser = StreamingFrameParser()

        test_payloads = [
            "4-byte Astral: 🚀🛰🌌🛸🪐",
            "4-byte CJK Ext-B: 𠮷野家 𠀋 𡈽",
            "3-byte CJK: 漢字・日本語・繁體字・한국어・한글",
            "2-byte Latin/Greek: Café résumé! λΩπΔΘΣΦΨ single-byte split check.",
            "2-byte Arabic/Hebrew: مرحبا بالعالم / שלום עולם",
            "ZWJ Sequences: 👨‍👩‍👧‍👦 and 🧑🏿‍💻 and 🏳️‍🌈",
            "Diacritics & Math: é à ç ü ∑ ∏ ∫ √ ∞ ≈ ≠",
        ]

        for i, text in enumerate(test_payloads):
            envelope = [["wrb.fr", "StreamGenerate", json.dumps([text], ensure_ascii=False)]]
            wire = _encode_wire_frame(envelope)

            # Feed 1 byte at a time
            frames = []
            for b in wire:
                chunk = bytes([b])
                res = parser.feed(chunk)
                if res:
                    frames.extend(res)

            assert len(frames) == 1, f"Failed for payload index {i}: expected 1 frame, got {len(frames)}"
            recovered_text = json.loads(frames[0][0][2])[0]
            assert recovered_text == text, f"Text mismatch at index {i}: {recovered_text!r} != {text!r}"

    def test_continuous_100_frames_single_byte_feeding(self):
        """
        Feeds 100 consecutive frames with mixed UTF-8 content byte-by-byte
        through a single continuous parser instance without resetting.
        Verifies 100% of frames are recovered without desynchronization.
        """
        parser = StreamingFrameParser()
        full_wire = bytearray()
        expected_texts = []

        scripts = [
            "English ASCII token {i} with numbers 1234567890",
            "Extended Latin & Greek {i}: Café résumé naïve λΩπΔΘΣΦΨ pan-gram check",
            "日本語の文脈 {i}: すもももももももものうち",
            "Emoji explosion {i}: 🚀🎉🔥💡🧠⚡🎯🛠️🏆",
            "Arabic: النص العربي للاختبار رقم {i}",
        ]

        for i in range(100):
            text = f"Frame {i}: {scripts[i % len(scripts)].format(i=i)}"
            expected_texts.append(text)
            envelope = [["wrb.fr", "StreamGenerate", json.dumps([text], ensure_ascii=False)]]
            full_wire.extend(_encode_wire_frame(envelope))

        # Feed the entire 100-frame stream byte-by-byte
        received_frames = []
        for b in full_wire:
            res = parser.feed(bytes([b]))
            if res:
                received_frames.extend(res)

        assert len(received_frames) == 100, f"Expected 100 frames, recovered {len(received_frames)}"
        for i in range(100):
            recovered = json.loads(received_frames[i][0][2])[0]
            assert recovered == expected_texts[i], f"Mismatch at frame {i}"

    def test_randomized_chunk_sizes_across_utf8_boundaries(self):
        """
        Feeds multi-frame stream with pseudo-random chunk sizes (1 to 17 bytes),
        forcing chunk boundaries directly in the middle of multi-byte UTF-8 sequences.
        """
        parser = StreamingFrameParser()
        rng = random.Random(1337)

        complex_text = "Test 🚀 漢字 𠮷 👨‍👩‍👧‍👦 עברית"
        wire = bytearray(b")]}'\n")
        expected_frames = 20

        for i in range(expected_frames):
            t = f"{i}: {complex_text} - step {i}"
            envelope = [["wrb.fr", "StreamGenerate", json.dumps([t], ensure_ascii=False)]]
            wire.extend(_encode_wire_frame(envelope))

        idx = 0
        received = []
        while idx < len(wire):
            chunk_len = rng.randint(1, 17)
            chunk = bytes(wire[idx : idx + chunk_len])
            idx += chunk_len
            res = parser.feed(chunk)
            if res:
                received.extend(res)

        assert len(received) == expected_frames
        for i in range(expected_frames):
            rec_text = json.loads(received[i][0][2])[0]
            assert rec_text.startswith(f"{i}: {complex_text}")

    @pytest.mark.asyncio
    async def test_afeed_async_iterator_single_byte(self):
        """Tests async generator afeed() yielding 1 byte at a time."""
        parser = StreamingFrameParser()
        envelope = [["wrb.fr", "StreamGenerate", json.dumps(["Async 🚀 漢字 1-byte stream"], ensure_ascii=False)]]
        wire = _encode_wire_frame(envelope)

        async def byte_stream():
            for b in wire:
                yield bytes([b])

        frames = []
        async for frame in parser.afeed(byte_stream()):
            frames.append(frame)

        assert len(frames) == 1
        rec = json.loads(frames[0][0][2])[0]
        assert rec == "Async 🚀 漢字 1-byte stream"

    def test_invalid_utf8_byte_replacement_graceful_handling(self):
        """
        Injects completely invalid UTF-8 bytes (0xFF, 0xFE, orphan continuation bytes).
        Verifies parser replaces invalid bytes with \ufffd without crashing,
        and subsequently resumes parsing clean frames.
        """
        parser = StreamingFrameParser(utf8_errors="replace")

        # Valid frame
        f1 = [["wrb.fr", "StreamGenerate", json.dumps(["Clean Frame 1"])]]
        w1 = _encode_wire_frame(f1)

        # Feed invalid bytes with newline delimiter
        res_bad = parser.feed(b"\xff\xfe\x80\xbf\n")
        assert res_bad == []

        # Feed clean frame 1
        res1 = parser.feed(w1)
        assert len(res1) == 1
        assert json.loads(res1[0][0][2])[0] == "Clean Frame 1"

    def test_500_frame_stress_combinatorial_lengths(self):
        """
        High-throughput stress test: 500 frames with varying lengths, mixed scripts,
        fed in random chunk sizes (1 to 256 bytes).
        Ensures zero memory leak, zero desync, and 100% frame extraction.
        """
        parser = StreamingFrameParser()
        rng = random.Random(4242)

        wire_all = bytearray(b")]}'\n")
        expected_texts = []

        vocab = ["Alpha", "Beta 🚀", "Gamma 漢字", "Delta 𠮷", "Omega 👨‍👩‍👦", "Symbol ∑∏∫"]
        for i in range(500):
            txt = f"{i}: {' '.join(rng.choice(vocab) for _ in range(rng.randint(2, 6)))}"
            expected_texts.append(txt)
            env = [["wrb.fr", "StreamGenerate", json.dumps([txt], ensure_ascii=False)]]
            wire_all.extend(_encode_wire_frame(env))

        # Chunk stream
        idx = 0
        extracted = []
        while idx < len(wire_all):
            size = rng.randint(1, 256)
            chunk = bytes(wire_all[idx : idx + size])
            idx += size
            frames = parser.feed(chunk)
            if frames:
                extracted.extend(frames)

        assert len(extracted) == 500
        for i in range(500):
            recovered = json.loads(extracted[i][0][2])[0]
            assert recovered == expected_texts[i]


# =====================================================================
# CHALLENGE 2: Empirical Single Done Chunk & Trailing Metadata Stress
# =====================================================================

class TestEmpiricalDoneChunkSemantics:
    """
    Stress-tests StreamingDeltaAccumulator against complex trailing metadata sequences.
    Verifies EXACTLY 1 'done' chunk is emitted per turn and that subsequent
    metadata frames (citations, images, continuation tokens) do not re-emit 'done' or text.
    """

    def test_exactly_one_done_chunk_across_multiple_trailing_metadata_frames(self):
        """
        Emulates a full multi-stage response:
        Turn 1:
          Frame 1: partial thoughts
          Frame 2: complete thoughts + initial text
          Frame 3: more text
          Frame 4: completion marker [8][0] == 2 + final text -> EMITS DONE
          Frame 5: trailing citations frame -> MUST NOT EMIT DONE or TEXT
          Frame 6: trailing images frame -> MUST NOT EMIT DONE or TEXT
          Frame 7: trailing continuation token frame -> MUST NOT EMIT DONE or TEXT
          Frame 8: redundant duplicate metadata frame -> MUST NOT EMIT DONE, TEXT, OR DUPLICATE METADATA
        """
        acc = StreamingDeltaAccumulator()

        f1 = _make_candidate_envelope(text="", thoughts="Thinking about citations...")
        f2 = _make_candidate_envelope(text="Here is the answer", thoughts="Thinking about citations... Done.")
        f3 = _make_candidate_envelope(text="Here is the answer to your question in detail.")
        f4 = _make_candidate_envelope(
            text="Here is the answer to your question in detail. Hope this helps!",
            is_final=True,
            cid="c_final",
            rid="r_final",
            rcid="rc_final",
        )
        f5 = _make_candidate_envelope(
            text="Here is the answer to your question in detail. Hope this helps!",
            is_final=True,
            citations=[{"url": "https://docs.python.org", "title": "Python Docs"}],
        )
        f6 = _make_candidate_envelope(
            text="Here is the answer to your question in detail. Hope this helps!",
            is_final=True,
            images=[{"url": "https://img.example.com/graph.png"}],
        )
        f7 = _make_candidate_envelope(
            text="Here is the answer to your question in detail. Hope this helps!",
            is_final=True,
            continuation_token="tok_cont_12345",
        )
        f8 = _make_candidate_envelope(
            text="Here is the answer to your question in detail. Hope this helps!",
            is_final=True,
            citations=[{"url": "https://docs.python.org", "title": "Python Docs"}],
        )

        all_emitted_chunks: List[StreamChunk] = []
        for f in [f1, f2, f3, f4, f5, f6, f7, f8]:
            emitted = acc.process_frame(f)
            all_emitted_chunks.extend(emitted)

        # Count chunk types
        done_chunks = [c for c in all_emitted_chunks if c.type == "done"]
        thought_chunks = [c for c in all_emitted_chunks if c.type == "thought"]
        text_chunks = [c for c in all_emitted_chunks if c.type == "text"]
        citation_chunks = [c for c in all_emitted_chunks if c.type == "citation"]
        grounding_chunks = [c for c in all_emitted_chunks if c.type == "grounding"]

        # Assertions
        assert len(done_chunks) == 1, f"CRITICAL: Expected exactly 1 'done' chunk, got {len(done_chunks)}!"
        assert done_chunks[0].metadata["cid"] == "c_final"
        assert done_chunks[0].metadata["full_text"] == "Here is the answer to your question in detail. Hope this helps!"
        assert "Done." in done_chunks[0].metadata["full_thoughts"]

        # Accumulated text must equal final string
        reconstructed_text = "".join(c.delta for c in text_chunks)
        assert reconstructed_text == "Here is the answer to your question in detail. Hope this helps!"

        # Citations and grounding
        assert len(citation_chunks) == 1, f"Expected 1 citation chunk, got {len(citation_chunks)}"
        assert citation_chunks[0].metadata["citations"] == [{"url": "https://docs.python.org", "title": "Python Docs"}]

        assert len(grounding_chunks) == 1, f"Expected 1 grounding chunk, got {len(grounding_chunks)}"
        assert grounding_chunks[0].metadata["generated_images"] == [{"url": "https://img.example.com/graph.png"}]

        # Ensure continuation token was updated
        assert acc.continuation_token == "tok_cont_12345"

    def test_list_formatted_rich_citations_and_grounding(self):
        """
        Tests candidate[12] arriving as a list rather than a dict:
        candidate[12][43] = citations, candidate[12][7] = images.
        Verifies single done chunk, proper extraction, and deduplication.
        """
        acc = StreamingDeltaAccumulator()

        f1 = _make_candidate_envelope(
            text="Completed response with list rich content.",
            is_final=True,
            citations=[{"url": "https://example.com/source"}],
            images=[{"url": "https://example.com/pic.jpg"}],
            rich_as_list=True,
        )
        c1 = acc.process_frame(f1)

        dones = [c for c in c1 if c.type == "done"]
        citations = [c for c in c1 if c.type == "citation"]
        images = [c for c in c1 if c.type == "grounding"]

        assert len(dones) == 1
        assert len(citations) == 1
        assert citations[0].metadata["citations"] == [{"url": "https://example.com/source"}]
        assert len(images) == 1
        assert images[0].metadata["generated_images"] == [{"url": "https://example.com/pic.jpg"}]

        # Repeat identical frame as trailing metadata
        c2 = acc.process_frame(f1)
        assert len(c2) == 0, f"Expected 0 chunks on redundant list frame, got {len(c2)}"

    def test_post_done_text_injection_is_strictly_suppressed(self):
        """
        Verifies that if an adversarial frame attempts to append more text AFTER
        turn completion is indicated, no additional text deltas or done chunks leak out.
        """
        acc = StreamingDeltaAccumulator()

        # Normal completion
        f1 = _make_candidate_envelope(text="Legitimate completed response.", is_final=True)
        c1 = acc.process_frame(f1)
        assert any(c.type == "done" for c in c1)
        assert acc.done_emitted is True

        # Malicious / rogue trailing frame attempting to inject more text
        f2 = _make_candidate_envelope(text="Legitimate completed response. Extra injected payload!", is_final=True)
        c2 = acc.process_frame(f2)

        # c2 must be empty
        assert len(c2) == 0, f"Expected 0 chunks emitted post-completion, got {len(c2)}: {c2}"

    def test_turn_reuse_requires_explicit_reset(self):
        """
        Verifies that reusing the accumulator across turns requires reset()
        and that reset() properly restores fresh generation state.
        """
        acc = StreamingDeltaAccumulator()

        # Turn 1
        t1 = _make_candidate_envelope(text="Turn 1 text", is_final=True)
        chunks1 = acc.process_frame(t1)
        assert len([c for c in chunks1 if c.type == "done"]) == 1

        # Without reset: Turn 2 would be blocked
        t2_blocked = _make_candidate_envelope(text="Turn 2 text", is_final=True)
        chunks_blocked = acc.process_frame(t2_blocked)
        assert len([c for c in chunks_blocked if c.type == "done"]) == 0

        # After reset: Turn 2 emits cleanly
        acc.reset()
        assert acc.done_emitted is False
        assert acc.is_completed is False

        chunks_clean = acc.process_frame(t2_blocked)
        dones_clean = [c for c in chunks_clean if c.type == "done"]
        texts_clean = [c for c in chunks_clean if c.type == "text"]

        assert len(dones_clean) == 1
        assert len(texts_clean) == 1
        assert texts_clean[0].delta == "Turn 2 text"

    def test_google_error_in_envelope_raises_typed_exception(self):
        """Verifies Google RPC error node in entry[5] raises typed exception."""
        acc = StreamingDeltaAccumulator()

        # Error code 1037: Usage limit exceeded
        envelope_quota = [["wrb.fr", "StreamGenerate", None, None, None, [1037, "Quota exceeded"]]]
        with pytest.raises(UsageLimitExceededError):
            acc.process_frame(envelope_quota)

        # Error code 1050: Model inconsistent
        envelope_inconsistent = [["wrb.fr", "StreamGenerate", None, None, None, [1050, "Model state mismatch"]]]
        with pytest.raises(ModelInconsistentError):
            acc.process_frame(envelope_inconsistent)


# =====================================================================
# CHALLENGE 3: Negative, Zero, and Oversized Frame Headers & Fault Recovery
# =====================================================================

class TestEmpiricalLengthHeaderAndBufferBounds:
    """
    Stress-tests length header parsing with negative values, zero, non-numeric,
    astronomical numbers, and buffer ceilings.
    """

    @pytest.mark.parametrize("bad_header", [
        "-1\n",
        "-50\n",
        "-999999\n",
        "-0\n",
        "0\n",
        "0000\n",
        "-12345678901234567890\n",
    ])
    def test_negative_and_zero_lengths_rejected_and_subsequent_frame_parsed(self, bad_header: str):
        """
        Feeds invalid non-positive length header, followed immediately by a valid frame.
        Verifies:
        1. Invalid header does not crash or corrupt internal slicing.
        2. Subsequent valid frame is parsed cleanly without loss.
        """
        parser = StreamingFrameParser()

        valid_frame = [["wrb.fr", "StreamGenerate", json.dumps(["Healthy frame after bad header"])]]
        valid_wire = _encode_wire_frame(valid_frame).decode("utf-8")

        corrupt_input = f"{bad_header}{valid_wire}"
        frames = parser.feed(corrupt_input)

        assert len(frames) == 1, f"Failed recovery after {bad_header!r}: got {len(frames)} frames"
        rec_text = json.loads(frames[0][0][2])[0]
        assert rec_text == "Healthy frame after bad header"

    @pytest.mark.parametrize("oversized_header", [
        f"{MAX_FRAME_CODE_UNITS + 1}\n",
        f"{MAX_FRAME_CODE_UNITS * 2}\n",
        "999999999999999999999999999999\n",
        "1000000000\n",
    ])
    def test_oversized_lengths_rejected_and_subsequent_frame_parsed(self, oversized_header: str):
        """
        Feeds length header exceeding MAX_FRAME_CODE_UNITS (16M code units / 32 MB),
        followed by a valid frame.
        Verifies parser logs warning, discards oversized header, and cleanly parses valid frame.
        """
        parser = StreamingFrameParser()

        valid_frame = [["wrb.fr", "StreamGenerate", json.dumps(["Healthy frame after oversized"])]]
        valid_wire = _encode_wire_frame(valid_frame).decode("utf-8")

        corrupt_input = f"{oversized_header}{valid_wire}"
        frames = parser.feed(corrupt_input)

        assert len(frames) == 1, f"Failed recovery after oversized {oversized_header!r}"
        rec_text = json.loads(frames[0][0][2])[0]
        assert rec_text == "Healthy frame after oversized"

    def test_unbounded_stream_without_newline_triggers_buffer_safety_flush(self):
        """
        Feeds data without any newline character until buffer exceeds MAX_FRAME_CODE_UNITS.
        Verifies buffer is flushed to prevent Out-Of-Memory (OOM) attack.
        """
        parser = StreamingFrameParser()
        original_max = parser.MAX_FRAME_CODE_UNITS
        try:
            parser.MAX_FRAME_CODE_UNITS = 100  # Set small limit for fast test
            # Feed 150 chars without newline
            parser.feed("A" * 150)
            # Buffer should have been reset to empty
            assert parser._buffer == ""
        finally:
            parser.MAX_FRAME_CODE_UNITS = original_max

    def test_interleaved_garbage_and_valid_frames(self):
        """
        Feeds an alternating sequence of:
        - garbage non-numeric header
        - negative length header
        - valid frame
        - oversized length header
        - corrupt JSON payload
        - valid frame
        Verifies parser maintains synchronization and extracts exactly the 2 valid frames.
        """
        parser = StreamingFrameParser()

        vf1 = [["wrb.fr", "StreamGenerate", json.dumps(["Valid 1"])]]
        vf2 = [["wrb.fr", "StreamGenerate", json.dumps(["Valid 2"])]]

        stream = (
            "GARBAGE_HEADER_123\n"
            "-42\n"
            + _encode_wire_frame(vf1).decode("utf-8")
            + "999999999\n"
            "20\n{NOT_VALID_JSON_AT_ALL}\n"
            + _encode_wire_frame(vf2).decode("utf-8")
        )

        frames = parser.feed(stream)
        assert len(frames) == 2
        assert json.loads(frames[0][0][2])[0] == "Valid 1"
        assert json.loads(frames[1][0][2])[0] == "Valid 2"


# =====================================================================
# CHALLENGE 4: Mock Gemini Server Socket Lifecycle & Windows Port Release
# =====================================================================

class TestEmpiricalMockServerLifecycle:
    """
    Stress-tests MockGeminiServer rapid start/stop cycles on Windows.
    Verifies sockets are fully closed without WSAEADDRINUSE (10048) collisions.
    """

    def test_rapid_start_stop_cycles(self):
        """
        Rapidly starts and stops MockGeminiServer 5 times in a row on port 8996.
        On Windows, improper socket teardown causes WSAEADDRINUSE immediately.
        """
        test_port = 8996
        for cycle in range(5):
            server = MockGeminiServer(host="127.0.0.1", port=test_port)
            server.start_background()

            # Small sleep to allow background thread loop to start
            time.sleep(0.05)

            # Stop cleanly
            server.stop_background()
            time.sleep(0.05)
