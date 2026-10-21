# tests/e2e/tier1_features/test_f10_to_f13_models_mock.py
"""Tier 1: Tests for Features F10 to F13 (Models, Gems, Multimodal, Mock)."""

import unittest
import json
from tests.e2e.harness import (
    HeaderSynthesizer,
    BatchexecuteEncoder,
    StreamGenerateEncoder,
    MultimodalUploadHandler,
    MockGeminiServer,
    StreamingFrameParser,
)
from backend.app.bridge.uploader import GeminiUploader
from backend.app.bridge.models import HeaderBuilder

class TestF10DynamicModelSelection(unittest.TestCase):
    """F10: Dynamic Model Selection."""

    def test_f10_01_flash_model_configuration(self):
        header = HeaderSynthesizer.synthesize_jspb_header(
            model_id="gemini-2.0-flash", model_number=1, thinking_flag=1
        )
        parsed = json.loads(header)
        self.assertEqual(parsed[4], "gemini-2.0-flash")
        self.assertEqual(parsed[14], 1)
        self.assertEqual(parsed[15], 1)

    def test_f10_02_pro_model_configuration(self):
        header = HeaderSynthesizer.synthesize_jspb_header(
            model_id="gemini-2.5-pro", model_number=2, thinking_flag=1
        )
        parsed = json.loads(header)
        self.assertEqual(parsed[4], "gemini-2.5-pro")
        self.assertEqual(parsed[14], 2)

    def test_f10_03_thinking_model_configuration(self):
        header = HeaderSynthesizer.synthesize_jspb_header(
            model_id="gemini-2.0-flash-thinking", model_number=1, thinking_flag=2
        )
        parsed = json.loads(header)
        self.assertEqual(parsed[4], "gemini-2.0-flash-thinking")
        self.assertEqual(parsed[15], 2)

    def test_f10_04_model_number_index_mapping(self):
        _, body = StreamGenerateEncoder.encode(
            prompt="Test", access_token="tok", model_number=3
        )
        from urllib.parse import unquote
        self.assertIn(",3,", unquote(body))

    def test_f10_05_capacity_tail_tier_setting(self):
        header_pro = HeaderSynthesizer.synthesize_jspb_header(capacity_tail=15)
        parsed = json.loads(header_pro)
        self.assertEqual(parsed[11], 15)


class TestF11CustomGemsIntegration(unittest.TestCase):
    """F11: Custom Gems Integration."""

    def test_f11_01_inject_gem_id_into_inner_req_list_19(self):
        _, body = StreamGenerateEncoder.encode(
            prompt="Hello Gem", access_token="tok", gem_id="gem-custom-arch-99"
        )
        from urllib.parse import parse_qs
        parsed = parse_qs(body)
        f_req = json.loads(parsed["f.req"][0])
        inner = json.loads(f_req[1])
        self.assertEqual(inner[19], "gem-custom-arch-99")

    def test_f11_02_parse_cngdbe_list_gems_response(self):
        gems_data = [
            ["gem_01", ["Code Assistant", "Helps with programming"], ["System prompt for coding"]],
            ["gem_02", ["Math Tutor", "Explains calculus"], ["System prompt for math"]],
        ]
        raw = f")]}}'\n[[[\"wrb.fr\",\"CNgdBe\",{json.dumps(json.dumps(gems_data))}]]]"
        decoded = BatchexecuteEncoder.decode_response(raw)
        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0][0][0], "gem_01")
        self.assertEqual(decoded[0][0][1][0], "Code Assistant")

    def test_f11_03_encode_omh3zd_create_gem_payload(self):
        create_payload = [["Gem Name", "Gem Desc", "System Instructions", None, None, None, None, None, 0, None, 1]]
        url, body, _ = BatchexecuteEncoder.encode_request(
            rpc_id="oMH3Zd", payload_data=create_payload, access_token="tok"
        )
        self.assertIn("rpcids=oMH3Zd", url)
        self.assertIn("oMH3Zd", body)

    def test_f11_04_encode_khv0vd_update_gem_payload(self):
        update_payload = ["gem_01", ["New Name", "New Desc", "New Instructions"]]
        url, body, _ = BatchexecuteEncoder.encode_request(
            rpc_id="kHv0Vd", payload_data=update_payload, access_token="tok"
        )
        self.assertIn("rpcids=kHv0Vd", url)
        self.assertIn("kHv0Vd", body)

    def test_f11_05_encode_uxcsjb_delete_gem_payload(self):
        delete_payload = ["gem_01"]
        url, body, _ = BatchexecuteEncoder.encode_request(
            rpc_id="UXcSJb", payload_data=delete_payload, access_token="tok"
        )
        self.assertIn("rpcids=UXcSJb", url)
        self.assertIn("UXcSJb", body)


class TestF12MultimodalUploadFlow(unittest.TestCase):
    """F12: Multimodal Upload Flow."""

    def test_f12_01_format_upload_resource_uri(self):
        uri = MultimodalUploadHandler.format_upload_response("image.png", "hash12345")
        self.assertEqual(uri, "/contrib_service/ttl_1d/hash12345_image.png")

    def test_f12_02_format_attachment_payload_inner_req(self):
        uri = "/contrib_service/ttl_1d/abc_spec.pdf"
        payload = MultimodalUploadHandler.format_attachment_payload(uri, "spec.pdf")
        self.assertEqual(payload, [[[uri], "spec.pdf"]])

    def test_f12_03_detect_mime_type_image_png(self):
        mime = GeminiUploader.detect_mime_type("diagram.png")
        self.assertEqual(mime, "image/png")
        # Fallback for unknown extensionless binary
        fallback_mime = GeminiUploader.detect_mime_type("binary_payload_blob")
        self.assertEqual(fallback_mime, "application/octet-stream")

    def test_f12_04_detect_mime_type_pdf_document(self):
        mime = GeminiUploader.detect_mime_type("manual.pdf")
        self.assertEqual(mime, "application/pdf")
        attachment_payload = GeminiUploader.format_chat_turn_attachment("/contrib_service/ttl_1d/manual.pdf", "manual.pdf")
        self.assertEqual(attachment_payload, [[["/contrib_service/ttl_1d/manual.pdf"], "manual.pdf"]])

    def test_f12_05_push_id_header_format(self):
        uploader = GeminiUploader()
        self.assertEqual(uploader.DEFAULT_PUSH_ID, "feeds/mcudyrk2a4khkz")
        headers = {"Push-ID": uploader.DEFAULT_PUSH_ID, "User-Agent": HeaderBuilder.DEFAULT_USER_AGENT}
        self.assertEqual(headers["Push-ID"], "feeds/mcudyrk2a4khkz")
        self.assertTrue(headers["Push-ID"].startswith("feeds/"))
        self.assertIn("Mozilla", headers["User-Agent"])


class TestF13DeterministicMockServer(unittest.TestCase):
    """F13: Deterministic Mock Server."""

    def setUp(self):
        self.mock = MockGeminiServer()

    def test_f13_01_mock_get_app_returns_wiz_data(self):
        html = self.mock.handle_get_app({})
        self.assertIn("window.WIZ_global_data", html)
        self.assertIn("SNlM0e", html)
        self.assertIn("cfb2h", html)

    def test_f13_02_mock_rotate_cookies_returns_set_cookie(self):
        status, headers, body = self.mock.handle_rotate_cookies({"__Secure-1PSID": "valid_token"})
        self.assertEqual(status, 200)
        self.assertIn("Set-Cookie", headers)
        self.assertIn("__Secure-1PSIDTS=", headers["Set-Cookie"])

    def test_f13_03_mock_batchexecute_otaq7b(self):
        resp = self.mock.handle_batchexecute("otAQ7b", [])
        decoded = BatchexecuteEncoder.decode_response(resp)
        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0][0], 1)

    def test_f13_04_mock_stream_generate_emits_valid_frames(self):
        stream = self.mock.handle_stream_generate("Hello", model_id="gemini-2.0-flash")
        frames = list(StreamingFrameParser.parse_stream(stream))
        self.assertEqual(len(frames), 3)
        self.assertEqual(frames[0]["text"], "Hello! ")
        self.assertEqual(frames[-1]["text"], "Hello! I am Gemini Local. Ready to assist!")

    def test_f13_05_mock_stream_generate_done_signal(self):
        stream = self.mock.handle_stream_generate("Hello", model_id="gemini-2.0-flash")
        frames = list(StreamingFrameParser.parse_stream(stream))
        self.assertFalse(frames[0]["is_done"])
        self.assertTrue(frames[-1]["is_done"])


if __name__ == "__main__":
    unittest.main()
