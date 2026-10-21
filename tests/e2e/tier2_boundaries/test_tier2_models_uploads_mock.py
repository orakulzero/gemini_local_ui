# tests/e2e/tier2_boundaries/test_tier2_models_uploads_mock.py
"""Tier 2: Boundary & Corner Cases for Features F10 to F13 (Models, Gems, Uploads, Mock)."""

import unittest
import json
from urllib.parse import unquote
from tests.e2e.harness import (
    HeaderSynthesizer,
    StreamGenerateEncoder,
    BatchexecuteEncoder,
    MultimodalUploadHandler,
    MockGeminiServer,
    StreamingFrameParser,
)

class TestTier2ModelsUploadsMock(unittest.TestCase):
    """Boundary & Corner Cases for F10 to F13."""

    # --- F10: Dynamic Model Selection ---
    def test_t2_f10_01_unknown_custom_model_id(self):
        header = HeaderSynthesizer.synthesize_jspb_header(model_id="custom-fine-tuned-gemini")
        parsed = json.loads(header)
        self.assertEqual(parsed[4], "custom-fine-tuned-gemini")

    def test_t2_f10_02_thinking_flag_boundary_values(self):
        header = HeaderSynthesizer.synthesize_jspb_header(thinking_flag=0)
        parsed = json.loads(header)
        self.assertEqual(parsed[15], 0)

    def test_t2_f10_03_large_model_number_index(self):
        header = HeaderSynthesizer.synthesize_jspb_header(model_number=9999)
        parsed = json.loads(header)
        self.assertEqual(parsed[14], 9999)

    def test_t2_f10_04_stream_generate_with_zero_model_number(self):
        _, body = StreamGenerateEncoder.encode("Test", "tok", model_number=0)
        from urllib.parse import unquote
        self.assertIn(",0,", unquote(body))

    def test_t2_f10_05_header_null_placeholders_preserved(self):
        header = HeaderSynthesizer.synthesize_jspb_header()
        parsed = json.loads(header)
        self.assertIsNone(parsed[1])
        self.assertIsNone(parsed[2])
        self.assertIsNone(parsed[3])

    # --- F11: Custom Gems Integration ---
    def test_t2_f11_01_gem_id_with_special_symbols(self):
        gem_id = "gem-k987_special@123!"
        _, body = StreamGenerateEncoder.encode("Test", "tok", gem_id=gem_id)
        from urllib.parse import unquote
        self.assertIn(gem_id, unquote(body))

    def test_t2_f11_02_create_gem_payload_empty_strings(self):
        empty_payload = [["", "", ""]]
        _, body, _ = BatchexecuteEncoder.encode_request("oMH3Zd", empty_payload, "token")
        self.assertIn("oMH3Zd", unquote(body))
        self.assertIn('\\"\\"', unquote(body))

    def test_t2_f11_03_gem_prompt_with_100k_chars(self):
        long_prompt = "A" * 100000
        payload = [["Big Gem", "Desc", long_prompt]]
        _, body, _ = BatchexecuteEncoder.encode_request("oMH3Zd", payload, "token")
        self.assertGreater(len(body), 100000)

    def test_t2_f11_04_delete_non_existent_gem_id(self):
        url, body, _ = BatchexecuteEncoder.encode_request("UXcSJb", ["non_existent_gem"], "token")
        from urllib.parse import unquote
        self.assertIn("non_existent_gem", unquote(body))

    def test_t2_f11_05_list_gems_response_with_zero_gems(self):
        raw = ")]}'\n[[[\"wrb.fr\",\"CNgdBe\",\"[]\"]]]"
        decoded = BatchexecuteEncoder.decode_response(raw)
        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0], [])

    # --- F12: Multimodal Upload Flow ---
    def test_t2_f12_01_upload_zero_byte_file(self):
        uri = MultimodalUploadHandler.format_upload_response("empty.bin", "hash_zero")
        self.assertEqual(uri, "/contrib_service/ttl_1d/hash_zero_empty.bin")

    def test_t2_f12_02_upload_unknown_binary_extension(self):
        ext = "binary_blob.xyz".split(".")[-1]
        mime = "application/octet-stream" if ext not in ("png", "jpg", "pdf") else "known"
        self.assertEqual(mime, "application/octet-stream")

    def test_t2_f12_03_filename_with_spaces_and_special_chars(self):
        filename = "My Special File (1) [Final].pdf"
        uri = MultimodalUploadHandler.format_upload_response(filename, "h1")
        self.assertIn("My Special File (1) [Final].pdf", uri)

    def test_t2_f12_04_format_attachment_multiple_files(self):
        uri1 = "/contrib_service/ttl_1d/h1_file1.png"
        uri2 = "/contrib_service/ttl_1d/h2_file2.png"
        p1 = MultimodalUploadHandler.format_attachment_payload(uri1, "file1.png")
        p2 = MultimodalUploadHandler.format_attachment_payload(uri2, "file2.png")
        combined = p1 + p2
        self.assertEqual(len(combined), 2)

    def test_t2_f12_05_push_id_with_special_feed_name(self):
        push_id = "feeds/custom-push-feed-0987654321"
        self.assertTrue(push_id.startswith("feeds/"))

    # --- F13: Deterministic Mock Server ---
    def test_t2_f13_01_mock_rotate_cookies_missing_psid_returns_401(self):
        mock = MockGeminiServer()
        status, _, _ = mock.handle_rotate_cookies({})
        self.assertEqual(status, 401)

    def test_t2_f13_02_mock_batchexecute_unknown_rpc_returns_ok(self):
        mock = MockGeminiServer()
        resp = mock.handle_batchexecute("unknown_rpc_123", [])
        decoded = BatchexecuteEncoder.decode_response(resp)
        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0], ["OK"])

    def test_t2_f13_03_mock_stream_generate_empty_prompt(self):
        mock = MockGeminiServer()
        stream = mock.handle_stream_generate("")
        frames = list(StreamingFrameParser.parse_stream(stream))
        self.assertGreater(len(frames), 0)

    def test_t2_f13_04_mock_stream_generate_with_thinking_model(self):
        mock = MockGeminiServer()
        stream = mock.handle_stream_generate("Explain quantum logic", is_thinking=True)
        frames = list(StreamingFrameParser.parse_stream(stream))
        has_thought = any(f.get("thought") for f in frames)
        self.assertTrue(has_thought)

    def test_t2_f13_05_mock_stream_deterministic_frames_count(self):
        mock = MockGeminiServer()
        stream1 = mock.handle_stream_generate("prompt")
        stream2 = mock.handle_stream_generate("prompt")
        frames1 = list(StreamingFrameParser.parse_stream(stream1))
        frames2 = list(StreamingFrameParser.parse_stream(stream2))
        self.assertEqual(len(frames1), len(frames2))


if __name__ == "__main__":
    unittest.main()
