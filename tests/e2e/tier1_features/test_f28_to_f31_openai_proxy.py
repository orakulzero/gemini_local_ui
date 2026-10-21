# tests/e2e/tier1_features/test_f28_to_f31_openai_proxy.py
"""Tier 1: Tests for Features F28 to F31 (OpenAI Proxy & Vision Adapter)."""

import unittest
import json
from tests.e2e.harness import (
    OpenAIModelRegistry,
    OpenAIChatAdapter,
    OpenAISSEChunkEncoder,
    OpenAIMultimodalParser,
)

class TestF28OpenAIAPIModels(unittest.TestCase):
    """F28: OpenAI API /v1/models."""

    def test_f28_01_models_endpoint_object_list(self):
        resp = OpenAIModelRegistry.get_models_response()
        self.assertEqual(resp["object"], "list")
        self.assertTrue(isinstance(resp["data"], list))

    def test_f28_02_models_list_contains_gemini_2_5_flash(self):
        resp = OpenAIModelRegistry.get_models_response()
        model_ids = [m["id"] for m in resp["data"]]
        self.assertIn("gemini-2.5-flash", model_ids)

    def test_f28_03_models_list_contains_gemini_2_5_pro(self):
        resp = OpenAIModelRegistry.get_models_response()
        model_ids = [m["id"] for m in resp["data"]]
        self.assertIn("gemini-2.5-pro", model_ids)

    def test_f28_04_alias_resolution_gpt_4o(self):
        resolved = OpenAIModelRegistry.resolve_model("gpt-4o")
        self.assertEqual(resolved, "gemini-2.5-pro")

    def test_f28_05_alias_resolution_gpt_3_5_turbo(self):
        resolved = OpenAIModelRegistry.resolve_model("gpt-3.5-turbo")
        self.assertEqual(resolved, "gemini-2.0-flash")


class TestF29OpenAIChatCompletionsStream(unittest.TestCase):
    """F29: OpenAI /v1/chat/completions Stream."""

    def test_f29_01_encode_initial_role_chunk(self):
        chunk = OpenAISSEChunkEncoder.encode_role_chunk("stream_123", "gemini-2.5-pro")
        self.assertTrue(chunk.startswith("data: "))
        self.assertTrue(chunk.endswith("\n\n"))
        parsed = json.loads(chunk[6:].strip())
        self.assertEqual(parsed["choices"][0]["delta"]["role"], "assistant")
        self.assertEqual(parsed["choices"][0]["delta"]["content"], "")

    def test_f29_02_encode_content_delta_chunk(self):
        chunk = OpenAISSEChunkEncoder.encode_content_chunk("stream_123", "gemini-2.5-pro", "Hello token")
        parsed = json.loads(chunk[6:].strip())
        self.assertEqual(parsed["choices"][0]["delta"]["content"], "Hello token")
        self.assertIsNone(parsed["choices"][0]["finish_reason"])

    def test_f29_03_encode_finish_chunk_stop(self):
        chunk = OpenAISSEChunkEncoder.encode_finish_chunk("stream_123", "gemini-2.5-pro", "stop")
        parsed = json.loads(chunk[6:].strip())
        self.assertEqual(parsed["choices"][0]["finish_reason"], "stop")

    def test_f29_04_encode_done_sentinel(self):
        done = OpenAISSEChunkEncoder.encode_done()
        self.assertEqual(done, "data: [DONE]\n\n")

    def test_f29_05_stream_true_flag_parsed_correctly(self):
        req = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        parsed = OpenAIChatAdapter.parse_request(req)
        self.assertTrue(parsed["stream"])


class TestF30OpenAIChatCompletionsNonStream(unittest.TestCase):
    """F30: OpenAI /v1/chat/completions Non-stream."""

    def test_f30_01_non_stream_response_object_type(self):
        resp = OpenAIChatAdapter.format_non_stream_response("Result text", "gemini-2.5-pro")
        self.assertEqual(resp["object"], "chat.completion")
        self.assertEqual(resp["model"], "gemini-2.5-pro")

    def test_f30_02_non_stream_choices_message_role_assistant(self):
        resp = OpenAIChatAdapter.format_non_stream_response("Full answer", "gemini-2.5-pro")
        choice = resp["choices"][0]
        self.assertEqual(choice["message"]["role"], "assistant")
        self.assertEqual(choice["message"]["content"], "Full answer")
        self.assertEqual(choice["finish_reason"], "stop")

    def test_f30_03_non_stream_usage_tokens_calculation(self):
        resp = OpenAIChatAdapter.format_non_stream_response(
            "Answer", "gemini-2.5-pro", prompt_tokens=25, completion_tokens=75
        )
        self.assertEqual(resp["usage"]["prompt_tokens"], 25)
        self.assertEqual(resp["usage"]["completion_tokens"], 75)
        self.assertEqual(resp["usage"]["total_tokens"], 100)

    def test_f30_04_system_message_parsed_to_instruction(self):
        req = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are a helpful coding assistant."},
                {"role": "user", "content": "Write quicksort."},
            ],
        }
        parsed = OpenAIChatAdapter.parse_request(req)
        self.assertEqual(parsed["system_instruction"], "You are a helpful coding assistant.")
        self.assertEqual(len(parsed["turns"]), 1)
        self.assertEqual(parsed["turns"][0]["content"], "Write quicksort.")

    def test_f30_05_generation_hyperparameters_extraction(self):
        req = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "test"}],
            "temperature": 0.2,
            "top_p": 0.8,
            "max_tokens": 2048,
            "stop": ["END"],
        }
        parsed = OpenAIChatAdapter.parse_request(req)
        self.assertEqual(parsed["temperature"], 0.2)
        self.assertEqual(parsed["top_p"], 0.8)
        self.assertEqual(parsed["max_tokens"], 2048)
        self.assertEqual(parsed["stop"], ["END"])


class TestF31OpenAIMultimodalAdapter(unittest.TestCase):
    """F31: OpenAI Multimodal Adapter."""

    def test_f31_01_parse_png_data_uri_base64(self):
        part = {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="},
        }
        valid, mime, data = OpenAIMultimodalParser.parse_image_part(part)
        self.assertTrue(valid)
        self.assertEqual(mime, "image/png")
        self.assertTrue(len(data) > 0)

    def test_f31_02_parse_jpeg_data_uri_base64(self):
        part = {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSkZJRg=="},
        }
        valid, mime, data = OpenAIMultimodalParser.parse_image_part(part)
        self.assertTrue(valid)
        self.assertEqual(mime, "image/jpeg")

    def test_f31_03_parse_remote_image_http_url(self):
        part = {
            "type": "image_url",
            "image_url": {"url": "https://example.com/sample_diagram.png"},
        }
        valid, mime, url = OpenAIMultimodalParser.parse_image_part(part)
        self.assertTrue(valid)
        self.assertEqual(mime, "remote_url")
        self.assertEqual(url, "https://example.com/sample_diagram.png")

    def test_f31_04_reject_invalid_image_part_type(self):
        part = {"type": "text", "text": "Not an image"}
        valid, _, _ = OpenAIMultimodalParser.parse_image_part(part)
        self.assertFalse(valid)

    def test_f31_05_reject_malformed_data_uri(self):
        part = {"type": "image_url", "image_url": {"url": "data:corrupt_uri_without_base64"}}
        valid, _, _ = OpenAIMultimodalParser.parse_image_part(part)
        self.assertFalse(valid)


if __name__ == "__main__":
    unittest.main()
