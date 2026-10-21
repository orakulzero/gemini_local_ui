# tests/e2e/tier1_features/test_f06_to_f09_rpc_streaming.py
"""Tier 1: Tests for Features F06 to F09 (RPC & Streaming)."""

import unittest
import json
from urllib.parse import parse_qs, unquote
from tests.e2e.harness import (
    BatchexecuteEncoder,
    StreamGenerateEncoder,
    StreamingFrameParser,
    StreamingDeltaAccumulator,
    ThoughtParser,
)

class TestF06UnaryBatchexecuteRPCs(unittest.TestCase):
    """F06: Unary Batchexecute RPCs."""

    def test_f06_01_encode_batchexecute_url_params(self):
        url, body, headers = BatchexecuteEncoder.encode_request(
            rpc_id="otAQ7b",
            payload_data=[],
            access_token="mock_at_123",
            session_id="mock_sid_456",
            build_label="mock_bl_789",
            req_id=12345,
        )
        self.assertIn("rpcids=otAQ7b", url)
        self.assertIn("bl=mock_bl_789", url)
        self.assertIn("f.sid=mock_sid_456", url)
        self.assertIn("_reqid=12345", url)
        self.assertIn("rt=c", url)

    def test_f06_02_encode_f_req_envelope_structure(self):
        url, body, headers = BatchexecuteEncoder.encode_request(
            rpc_id="MaZiqc",
            payload_data=[10, None, [1, None, 1]],
            access_token="test_token",
        )
        parsed_body = parse_qs(body)
        self.assertEqual(parsed_body["at"][0], "test_token")
        f_req = json.loads(parsed_body["f.req"][0])
        self.assertEqual(f_req[0][0][0], "MaZiqc")
        self.assertEqual(f_req[0][0][3], "generic")

    def test_f06_03_strip_anti_xssi_prefix(self):
        raw = ")]}'\n[[[\"wrb.fr\",\"otAQ7b\",\"[1,2,3]\"]]]"
        decoded = BatchexecuteEncoder.decode_response(raw)
        self.assertEqual(decoded, [[1, 2, 3]])

    def test_f06_04_decode_wrb_fr_response_payload(self):
        raw = "[[[\"wrb.fr\",\"MaZiqc\",\"{\\\"chats\\\":[\\\"c1\\\",\\\"c2\\\"]}\"]]]"
        decoded = BatchexecuteEncoder.decode_response(raw)
        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0]["chats"], ["c1", "c2"])

    def test_f06_05_parse_otaq7b_user_status_data(self):
        status_inner = json.dumps([1, [["gemini-2.0-flash", 1, 1], ["gemini-2.5-pro", 2, 1]], 15])
        raw = f")]}}'\n[[[\"wrb.fr\",\"otAQ7b\",{json.dumps(status_inner)}]]]"
        decoded = BatchexecuteEncoder.decode_response(raw)
        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0][0], 1)
        self.assertEqual(decoded[0][1][0][0], "gemini-2.0-flash")


class TestF07StreamGenerateRPCClient(unittest.TestCase):
    """F07: StreamGenerate RPC Client."""

    def test_f07_01_encode_81_element_inner_req_list(self):
        endpoint, body = StreamGenerateEncoder.encode(
            prompt="Hello Gemini",
            access_token="token_xyz",
            cid="c_123",
            rid="r_456",
            rcid="rc_789",
            gem_id="gem_001",
            model_number=2,
            thinking_flag=2,
            req_uuid="UUID-ABC-123",
        )
        self.assertIn("assistant.lamda.BardFrontendService/StreamGenerate", endpoint)
        parsed = parse_qs(body)
        f_req = json.loads(parsed["f.req"][0])
        inner_list = json.loads(f_req[1])
        self.assertEqual(len(inner_list), 81)
        self.assertEqual(inner_list[0][0], "Hello Gemini")
        self.assertEqual(inner_list[2][0], "c_123")
        self.assertEqual(inner_list[7], 1)  # streaming flag
        self.assertEqual(inner_list[19], "gem_001")
        self.assertEqual(inner_list[59], "UUID-ABC-123")
        self.assertEqual(inner_list[79], 2)
        self.assertEqual(inner_list[80], 2)

    def test_f07_02_utf16_code_units_calculation_ascii(self):
        s = "Hello World"
        self.assertEqual(StreamingFrameParser.utf16_code_units(s), 11)

    def test_f07_03_utf16_code_units_surrogate_pairs_emojis(self):
        # Emoji 🚀 (U+1F680) is 1 character in Python, but 2 code units in UTF-16!
        s = "Rocket 🚀"
        self.assertEqual(len(s), 8)  # Python len
        self.assertEqual(StreamingFrameParser.utf16_code_units(s), 9)  # UTF-16 code units

    def test_f07_04_stream_frame_length_prefixed_parsing(self):
        inner_obj = [None, ["c_99", "r_99"], None, None, [["rc_99", ["Streamed token!"]]]]
        frame = StreamingFrameParser.encode_frame(inner_obj)
        raw_stream = ")]}'\n" + frame
        frames = list(StreamingFrameParser.parse_stream(raw_stream))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["cid"], "c_99")
        self.assertEqual(frames[0]["text"], "Streamed token!")

    def test_f07_05_detect_turn_status_is_done(self):
        # Candidate status 2 indicates completion
        inner_obj = [None, ["c_1", "r_1"], None, None, [["rc_1", ["Final text"], None, None, None, None, None, None, [2]]]]
        frame = StreamingFrameParser.encode_frame(inner_obj)
        frames = list(StreamingFrameParser.parse_stream(frame))
        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0]["is_done"])


class TestF08StreamingDeltaAccumulator(unittest.TestCase):
    """F08: Streaming Delta Accumulator."""

    def test_f08_01_monotonic_prefix_delta_calculation(self):
        accum = StreamingDeltaAccumulator()
        d1 = accum.calculate_delta("Hello")
        d2 = accum.calculate_delta("Hello world")
        d3 = accum.calculate_delta("Hello world!")
        self.assertEqual(d1, "Hello")
        self.assertEqual(d2, " world")
        self.assertEqual(d3, "!")

    def test_f08_02_multiple_consecutive_deltas_sum(self):
        accum = StreamingDeltaAccumulator()
        chunks = ["A", "AB", "ABC", "ABCD", "ABCDE"]
        deltas = [accum.calculate_delta(c) for c in chunks]
        self.assertEqual("".join(deltas), "ABCDE")

    def test_f08_03_strip_transient_markdown_fence_suffix(self):
        accum = StreamingDeltaAccumulator()
        # Transient code fence flickering at tail
        d1 = accum.calculate_delta("Here is code:\n```")
        self.assertEqual(d1, "Here is code:")

    def test_f08_04_strip_transient_trailing_escape_characters(self):
        accum = StreamingDeltaAccumulator()
        # Escaped backslash before token completion
        d1 = accum.calculate_delta("Text with \\*")
        self.assertEqual(d1, "Text with ")

    def test_f08_05_difflib_fallback_on_mid_stream_replacement(self):
        accum = StreamingDeltaAccumulator()
        d1 = accum.calculate_delta("The color is red")
        # Non-monotonic correction: "The color is blue and bright"
        d2 = accum.calculate_delta("The color is blue and bright")
        self.assertTrue(len(d2) > 0)


class TestF09ThoughtReasoningParsing(unittest.TestCase):
    """F09: Thought / Reasoning Parsing."""

    def test_f09_01_extract_candidate_37_thinking_tokens(self):
        candidate: list = [None] * 38
        candidate[37] = [["Let me solve this problem step by step."]]
        thoughts = ThoughtParser.extract_thoughts(candidate)
        self.assertEqual(thoughts, "Let me solve this problem step by step.")

    def test_f09_02_handle_absent_thinking_tokens(self):
        candidate: list = [None] * 10
        thoughts = ThoughtParser.extract_thoughts(candidate)
        self.assertIsNone(thoughts)

    def test_f09_03_separate_thinking_from_main_content(self):
        candidate: list = [None] * 38
        candidate[0] = "rc_123"
        candidate[1] = ["The final answer is 42."]
        candidate[37] = [["Computing 6 times 7..."]]

        thoughts = ThoughtParser.extract_thoughts(candidate)
        main_text = candidate[1][0]
        self.assertEqual(thoughts, "Computing 6 times 7...")
        self.assertEqual(main_text, "The final answer is 42.")

    def test_f09_04_empty_thinking_array_handled_safely(self):
        candidate: list = [None] * 38
        candidate[37] = []
        thoughts = ThoughtParser.extract_thoughts(candidate)
        self.assertIsNone(thoughts)

    def test_f09_05_multiline_reasoning_token_extraction(self):
        multiline = "Step 1: Check hypotheses.\nStep 2: Derive equation.\nStep 3: Conclude."
        candidate: list = [None] * 38
        candidate[37] = [[multiline]]
        thoughts = ThoughtParser.extract_thoughts(candidate)
        self.assertEqual(thoughts, multiline)


if __name__ == "__main__":
    unittest.main()
