# tests/e2e/tier2_boundaries/test_tier2_openai_proxy.py
"""Tier 2: Boundary & Corner Cases for Features F28 to F31 (OpenAI Proxy)."""

import unittest
import json
from tests.e2e.harness import (
    OpenAIModelRegistry,
    OpenAIChatAdapter,
    OpenAISSEChunkEncoder,
    OpenAIMultimodalParser,
)

class TestTier2OpenAIProxy(unittest.TestCase):
    """Boundary & Corner Cases for F28 to F31."""

    # --- F28: OpenAI API /v1/models ---
    def test_t2_f28_01_resolve_unknown_model_returns_same(self):
        resolved = OpenAIModelRegistry.resolve_model("unknown-custom-model-999")
        self.assertEqual(resolved, "unknown-custom-model-999")

    def test_t2_f28_02_resolve_case_variations(self):
        resolved = OpenAIModelRegistry.resolve_model("gpt-4o")
        self.assertEqual(resolved, "gemini-2.5-pro")

    def test_t2_f28_03_models_response_required_keys(self):
        resp = OpenAIModelRegistry.get_models_response()
        for item in resp["data"]:
            self.assertIn("id", item)
            self.assertIn("object", item)
            self.assertIn("owned_by", item)
            self.assertEqual(item["object"], "model")

    def test_t2_f28_04_models_permission_array_empty(self):
        resp = OpenAIModelRegistry.get_models_response()
        for item in resp["data"]:
            self.assertEqual(item["permission"], [])

    def test_t2_f28_05_resolve_empty_string_model(self):
        resolved = OpenAIModelRegistry.resolve_model("")
        self.assertEqual(resolved, "")

    # --- F29: OpenAI /v1/chat/completions Stream ---
    def test_t2_f29_01_sse_empty_delta_text(self):
        chunk = OpenAISSEChunkEncoder.encode_content_chunk("id1", "model", "")
        parsed = json.loads(chunk[6:].strip())
        self.assertEqual(parsed["choices"][0]["delta"]["content"], "")

    def test_t2_f29_02_sse_multiline_delta_newlines(self):
        chunk = OpenAISSEChunkEncoder.encode_content_chunk("id1", "model", "Line 1\nLine 2\n")
        parsed = json.loads(chunk[6:].strip())
        self.assertEqual(parsed["choices"][0]["delta"]["content"], "Line 1\nLine 2\n")

    def test_t2_f29_03_sse_chunk_with_emojis(self):
        chunk = OpenAISSEChunkEncoder.encode_content_chunk("id1", "model", "✨ 🚀 🤖")
        parsed = json.loads(chunk[6:].strip())
        self.assertEqual(parsed["choices"][0]["delta"]["content"], "✨ 🚀 🤖")

    def test_t2_f29_04_stream_request_missing_model_defaults(self):
        req = {"messages": [{"role": "user", "content": "hi"}], "stream": True}
        parsed = OpenAIChatAdapter.parse_request(req)
        self.assertEqual(parsed["model"], "gemini-2.0-flash")

    def test_t2_f29_05_stream_finish_chunk_length_reason(self):
        chunk = OpenAISSEChunkEncoder.encode_finish_chunk("id1", "model", reason="length")
        parsed = json.loads(chunk[6:].strip())
        self.assertEqual(parsed["choices"][0]["finish_reason"], "length")

    # --- F30: OpenAI /v1/chat/completions Non-stream ---
    def test_t2_f30_01_multiple_developer_and_system_prompts_merged(self):
        req = {
            "messages": [
                {"role": "system", "content": "System directive 1"},
                {"role": "developer", "content": "Developer directive 2"},
                {"role": "user", "content": "Prompt"},
            ]
        }
        parsed = OpenAIChatAdapter.parse_request(req)
        self.assertIn("System directive 1", parsed["system_instruction"])
        self.assertIn("Developer directive 2", parsed["system_instruction"])

    def test_t2_f30_02_temperature_zero_deterministic(self):
        req = {"temperature": 0.0, "messages": []}
        parsed = OpenAIChatAdapter.parse_request(req)
        self.assertEqual(parsed["temperature"], 0.0)

    def test_t2_f30_03_temperature_max_two_point_zero(self):
        req = {"temperature": 2.0, "messages": []}
        parsed = OpenAIChatAdapter.parse_request(req)
        self.assertEqual(parsed["temperature"], 2.0)

    def test_t2_f30_04_empty_messages_array_handled(self):
        req = {"messages": []}
        parsed = OpenAIChatAdapter.parse_request(req)
        self.assertEqual(parsed["turns"], [])

    def test_t2_f30_05_non_stream_usage_tokens_sum(self):
        resp = OpenAIChatAdapter.format_non_stream_response(
            "Text", "model", prompt_tokens=150, completion_tokens=350
        )
        self.assertEqual(resp["usage"]["total_tokens"], 500)

    # --- F31: OpenAI Multimodal Adapter ---
    def test_t2_f31_01_missing_image_url_dict(self):
        part = {"type": "image_url"}
        valid, _, _ = OpenAIMultimodalParser.parse_image_part(part)
        self.assertFalse(valid)

    def test_t2_f31_02_empty_url_string(self):
        part = {"type": "image_url", "image_url": {"url": ""}}
        valid, _, _ = OpenAIMultimodalParser.parse_image_part(part)
        self.assertFalse(valid)

    def test_t2_f31_03_data_uri_non_base64(self):
        part = {"type": "image_url", "image_url": {"url": "data:image/png;utf8,plain_text"}}
        valid, _, _ = OpenAIMultimodalParser.parse_image_part(part)
        self.assertFalse(valid)

    def test_t2_f31_04_webp_image_data_uri(self):
        part = {
            "type": "image_url",
            "image_url": {"url": "data:image/webp;base64,UklGRkAAAABXRUJQVlA4IDQAAADwAQCdASoBAAEAAQAcJaACdLoAAP7/2QAA"},
        }
        valid, mime, data = OpenAIMultimodalParser.parse_image_part(part)
        self.assertTrue(valid)
        self.assertEqual(mime, "image/webp")

    def test_t2_f31_05_gif_image_data_uri(self):
        part = {
            "type": "image_url",
            "image_url": {"url": "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"},
        }
        valid, mime, data = OpenAIMultimodalParser.parse_image_part(part)
        self.assertTrue(valid)
        self.assertEqual(mime, "image/gif")


if __name__ == "__main__":
    unittest.main()
