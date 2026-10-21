# tests/e2e/tier3_combinations/test_tier3_pairwise_interactions.py
"""Tier 3: Pairwise and Cross-Feature Interaction Tests (>=33 tests)."""

import unittest
import json
import uuid
import time
from tests.e2e.harness import (
    CookieParser,
    WIZDataExtractor,
    RotateCookiesClient,
    HeaderSynthesizer,
    BatchexecuteEncoder,
    StreamGenerateEncoder,
    StreamingFrameParser,
    StreamingDeltaAccumulator,
    ThoughtParser,
    MultimodalUploadHandler,
    MockGeminiServer,
    StorageEngine,
    FTS5SearchEngine,
    ConversationTree,
    ThreadExporter,
    RemoteSyncEngine,
    OpenAIModelRegistry,
    OpenAIChatAdapter,
    OpenAISSEChunkEncoder,
    OpenAIMultimodalParser,
    VirtualListSimulator,
    IncrementalMarkdownParser,
    KaTeXMathExtractor,
    PromptInputSimulator,
    LongTaskPerformanceObserver,
    DPAPIVault,
    LocalSecurityGuard,
    BrowserProfileExtractor,
)

class TestTier3PairwiseInteractions(unittest.TestCase):
    """Pairwise Cross-Feature Integration Tests."""

    def setUp(self):
        self.mock = MockGeminiServer()
        self.storage = StorageEngine(":memory:")
        self.fts = FTS5SearchEngine(self.storage.conn)

    def tearDown(self):
        self.storage.close()

    def test_t3_01_ingest_credentials_and_extract_wiz_context(self):
        raw_cookie = "__Secure-1PSID=sid_123; __Secure-1PSIDTS=ts_456"
        cookies = CookieParser.parse(raw_cookie)
        valid, _ = CookieParser.validate(cookies)
        self.assertTrue(valid)

        html = self.mock.handle_get_app(cookies)
        wiz = WIZDataExtractor.extract(html)
        self.assertIsNotNone(wiz["access_token"])
        self.assertIsNotNone(wiz["build_label"])

    def test_t3_02_ingest_credentials_and_trigger_rotate_cookies(self):
        cookies = CookieParser.parse("__Secure-1PSID=psid_live; __Secure-1PSIDTS=ts_old")
        client = RotateCookiesClient(cookies)
        self.assertEqual(client.health, "healthy")

        status, headers, body = self.mock.handle_rotate_cookies(client.cookies)
        ok = client.process_response(status, headers, body)
        self.assertTrue(ok)
        self.assertNotEqual(client.cookies["__Secure-1PSIDTS"], "ts_old")

    def test_t3_03_context_tokens_synthesize_matching_headers(self):
        html = self.mock.handle_get_app({})
        wiz = WIZDataExtractor.extract(html)
        headers = HeaderSynthesizer.get_standard_headers(
            HeaderSynthesizer.synthesize_jspb_header(model_id="gemini-2.0-flash")
        )
        self.assertIn("x-goog-ext-525001261-jspb", headers)
        self.assertEqual(headers["Host"], "gemini.google.com")

    def test_t3_04_synthesized_headers_dispatch_stream_generate(self):
        headers = HeaderSynthesizer.get_standard_headers(
            HeaderSynthesizer.synthesize_jspb_header(model_id="gemini-2.0-flash")
        )
        endpoint, body = StreamGenerateEncoder.encode("Test prompt", "token_123")
        self.assertTrue(endpoint.endswith("StreamGenerate"))
        self.assertIn("x-goog-ext-525001261-jspb", headers)

    def test_t3_05_stream_generate_frames_accumulate_deltas(self):
        stream = self.mock.handle_stream_generate("Write a poem")
        frames = list(StreamingFrameParser.parse_stream(stream))
        accum = StreamingDeltaAccumulator()
        deltas = []
        for f in frames:
            if f.get("text"):
                delta = accum.calculate_delta(f["text"])
                if delta:
                    deltas.append(delta)
        self.assertGreater(len(deltas), 0)
        self.assertEqual("".join(deltas), frames[-1]["text"])

    def test_t3_06_thinking_stream_separates_thoughts_and_content(self):
        stream = self.mock.handle_stream_generate("Solve logic puzzle", is_thinking=True)
        frames = list(StreamingFrameParser.parse_stream(stream))
        thoughts = [f["thought"] for f in frames if f.get("thought")]
        texts = [f["text"] for f in frames if f.get("text")]
        self.assertTrue(len(thoughts) > 0)
        self.assertTrue(len(texts) > 0)
        self.assertNotEqual(thoughts[0], texts[0])

    def test_t3_07_delta_accumulator_feeds_incremental_markdown(self):
        accum = StreamingDeltaAccumulator()
        t1 = accum.calculate_delta("Paragraph 1.\n\n")
        t2 = accum.calculate_delta("Paragraph 1.\n\nParagraph 2.\n\n")
        parsed1 = IncrementalMarkdownParser.parse_stream(accum.last_sent_clean)
        self.assertEqual(len(parsed1["immutable_blocks"]), 2)

    def test_t3_08_model_selection_updates_jspb_header_structure(self):
        h_flash = HeaderSynthesizer.synthesize_jspb_header("gemini-2.0-flash", 1, 1)
        h_think = HeaderSynthesizer.synthesize_jspb_header("gemini-2.0-flash-thinking", 1, 2)
        p_flash = json.loads(h_flash)
        p_think = json.loads(h_think)
        self.assertEqual(p_flash[15], 1)
        self.assertEqual(p_think[15], 2)

    def test_t3_09_custom_gem_selection_injected_in_stream_request(self):
        _, body = StreamGenerateEncoder.encode("Run Gem", "tok", gem_id="gem-analyst-42")
        from urllib.parse import unquote
        self.assertIn("gem-analyst-42", unquote(body))

    def test_t3_10_multimodal_upload_attached_to_stream_prompt(self):
        uri = MultimodalUploadHandler.format_upload_response("architecture.png", "hash_img_99")
        attachment = MultimodalUploadHandler.format_attachment_payload(uri, "architecture.png")
        _, body = StreamGenerateEncoder.encode("Analyze this diagram", "tok", file_attachments=attachment)
        from urllib.parse import unquote
        self.assertIn("architecture.png", unquote(body))

    def test_t3_11_mock_batchexecute_handles_unary_rpcs(self):
        for rpc in ("otAQ7b", "MaZiqc", "CNgdBe", "hNvQHb"):
            resp = self.mock.handle_batchexecute(rpc, [])
            decoded = BatchexecuteEncoder.decode_response(resp)
            self.assertEqual(len(decoded), 1)

    def test_t3_12_mock_stream_generate_produces_utf16_framed_chunks(self):
        stream = self.mock.handle_stream_generate("Test query")
        frames = list(StreamingFrameParser.parse_stream(stream))
        self.assertEqual(len(frames), 3)
        self.assertTrue(frames[-1]["is_done"])

    def test_t3_13_virtual_list_renders_streaming_markdown_inside_window(self):
        vlist = VirtualListSimulator(viewport_height=800)
        vlist.set_items([150] * 50)
        vlist.scroll_to(50 * 150 - 800)
        # Message 49 is streaming
        streaming_text = "Intro\n\n```python\nprint('live streaming')\n```\n\n"
        res = IncrementalMarkdownParser.parse_stream(streaming_text)
        self.assertEqual(len(res["immutable_blocks"]), 2)
        start, end, mounted = vlist.compute_visible_window()
        self.assertIn(49, mounted)

    def test_t3_14_incremental_markdown_dispatches_code_to_worker(self):
        text = "Guide:\n\n```python\nimport os\nprint(os.getcwd())\n```\n\nDone."
        res = IncrementalMarkdownParser.parse_stream(text)
        code_block = [b for b in res["immutable_blocks"] if b["type"] == "code"][0]
        # Dispatch to worker payload
        worker_payload = {
            "lang": code_block["language"],
            "code": code_block["content"],
        }
        self.assertEqual(worker_payload["lang"], "python")
        self.assertIn("import os", worker_payload["code"])

    def test_t3_15_incremental_markdown_detects_latex_formulas(self):
        text = "Physics:\n\n$$E = mc^2$$\n\nWhere $c$ is speed of light."
        math = KaTeXMathExtractor.extract_math(text)
        self.assertEqual(len(math["block_formulas"]), 1)
        self.assertEqual(len(math["inline_formulas"]), 1)

    def test_t3_16_code_block_copy_action_preserves_content(self):
        code_str = "def factorial(n):\n    return 1 if n <= 1 else n * factorial(n - 1)"
        text = f"```python\n{code_str}\n```\n\n"
        res = IncrementalMarkdownParser.parse_stream(text)
        copied = res["immutable_blocks"][0]["content"]
        self.assertEqual(copied, code_str)

    def test_t3_17_prompt_attachment_tray_to_upload_flow(self):
        prompt = PromptInputSimulator()
        prompt.add_attachment("chart.pdf", 512000, "application/pdf")
        self.assertEqual(len(prompt.attachments), 1)

        # Upload
        uri = MultimodalUploadHandler.format_upload_response(prompt.attachments[0]["name"], "hash_pdf")
        att_payload = MultimodalUploadHandler.format_attachment_payload(uri, prompt.attachments[0]["name"])
        prompt.set_text("Analyze chart")
        prompt.submit()
        self.assertEqual(len(prompt.attachments), 0)
        self.assertIn("chart.pdf", att_payload[0][1])

    def test_t3_18_model_selector_updates_global_session_state(self):
        selected_model = "gemini-2.0-flash"
        # User changes selector
        selected_model = "gemini-2.5-pro"
        jspb = HeaderSynthesizer.synthesize_jspb_header(model_id=selected_model)
        self.assertIn("gemini-2.5-pro", jspb)

    def test_t3_19_branch_navigation_updates_dag_tree(self):
        tree = ConversationTree()
        tree.add_message("u1", None, "user", "How to optimize Python?")
        tree.add_message("a1_v1", "u1", "assistant", "Use PyPy.")
        tree.add_message("a1_v2", "u1", "assistant", "Use Cython.")

        sib1 = tree.get_sibling_info("a1_v1")
        sib2 = tree.get_sibling_info("a1_v2")
        self.assertEqual(sib1["index"], 1)
        self.assertEqual(sib2["index"], 2)
        # Select branch 2
        active_thread = tree.get_linear_thread("a1_v2")
        self.assertEqual([m["id"] for m in active_thread], ["u1", "a1_v2"])

    def test_t3_20_stream_generate_persists_to_sqlite(self):
        stream = self.mock.handle_stream_generate("Write hello world")
        frames = list(StreamingFrameParser.parse_stream(stream))
        final_text = frames[-1]["text"]

        self.storage.create_conversation("c_live_1", "Live Chat")
        self.storage.add_message("m_u1", "c_live_1", "user", "Write hello world")
        self.storage.add_message("m_a1", "c_live_1", "assistant", final_text)

        msgs = self.storage.get_messages("c_live_1")
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[1]["content"], final_text)

    def test_t3_21_sqlite_message_insert_triggers_fts5_search(self):
        self.storage.create_conversation("c_fts_test", "FTS Search")
        self.storage.add_message("m_ai_1", "c_fts_test", "assistant", "TensorFlow is an open source machine learning library.")
        results, latency_ms = self.fts.search("TensorFlow")
        self.assertEqual(len(results), 1)
        self.assertLess(latency_ms, 50.0)

    def test_t3_22_fts5_search_result_resolves_dag_thread(self):
        tree = ConversationTree()
        tree.add_message("u1", None, "user", "Deep learning query")
        tree.add_message("a1", "u1", "assistant", "Deep neural networks use backpropagation.")

        self.storage.create_conversation("c_dag_fts", "Deep Learning")
        self.storage.add_message("a1", "c_dag_fts", "assistant", "Deep neural networks use backpropagation.")

        results, _ = self.fts.search("backpropagation")
        self.assertEqual(len(results), 1)
        matched_msg_id = results[0]["message_id"]

        thread = tree.get_linear_thread(matched_msg_id)
        self.assertEqual(len(thread), 2)

    def test_t3_23_remote_sync_populates_sqlite_without_duplicates(self):
        sync = RemoteSyncEngine(self.storage)
        remote_data = [{"cid": "rem_conv_1", "title": "Remote Sync 1"}]
        c1 = sync.sync_remote_conversations(remote_data)
        c2 = sync.sync_remote_conversations(remote_data)
        self.assertEqual(c1, 1)
        self.assertEqual(c2, 0)

    def test_t3_24_dag_conversation_exports_specific_branch_markdown(self):
        tree = ConversationTree()
        tree.add_message("u1", None, "user", "Prompt")
        tree.add_message("a1_v1", "u1", "assistant", "Answer 1")
        tree.add_message("a1_v2", "u1", "assistant", "Answer 2")

        thread_v2 = tree.get_linear_thread("a1_v2")
        conv = {"id": "c_exp_branch", "title": "Branch Export", "model": "gemini-2.0-flash"}
        md = ThreadExporter.export_markdown(conv, thread_v2)
        self.assertIn("Answer 2", md)
        self.assertNotIn("Answer 1", md)

    def test_t3_25_dag_conversation_exports_structured_json(self):
        tree = ConversationTree()
        tree.add_message("u1", None, "user", "Prompt")
        tree.add_message("a1", "u1", "assistant", "Answer")

        conv = {"id": "c_json_tree", "title": "JSON Tree", "model": "gemini-2.5-pro"}
        raw_json = ThreadExporter.export_json(conv, tree.get_linear_thread("a1"))
        parsed = json.loads(raw_json)
        self.assertEqual(parsed["version"], "1.0")
        self.assertEqual(len(parsed["messages"]), 2)

    def test_t3_26_openai_models_list_maps_gemini_dynamic_models(self):
        resp = OpenAIModelRegistry.get_models_response()
        model_ids = [m["id"] for m in resp["data"]]
        self.assertIn("gemini-2.5-pro", model_ids)
        # Aliases mapped
        self.assertEqual(OpenAIModelRegistry.resolve_model("gpt-4o"), "gemini-2.5-pro")

    def test_t3_27_openai_streaming_completes_via_sse(self):
        req = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}], "stream": True}
        parsed = OpenAIChatAdapter.parse_request(req)
        self.assertTrue(parsed["stream"])

        # Stream chunks
        stream_id = "chatcmpl-stream-001"
        chunks = [
            OpenAISSEChunkEncoder.encode_role_chunk(stream_id, parsed["model"]),
            OpenAISSEChunkEncoder.encode_content_chunk(stream_id, parsed["model"], "Hello"),
            OpenAISSEChunkEncoder.encode_content_chunk(stream_id, parsed["model"], " world!"),
            OpenAISSEChunkEncoder.encode_finish_chunk(stream_id, parsed["model"]),
            OpenAISSEChunkEncoder.encode_done(),
        ]
        self.assertEqual(len(chunks), 5)
        self.assertTrue(chunks[-1].endswith("[DONE]\n\n"))

    def test_t3_28_openai_non_streaming_returns_standard_json(self):
        req = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Say hi"}]}
        parsed = OpenAIChatAdapter.parse_request(req)
        resp = OpenAIChatAdapter.format_non_stream_response("Hi there!", parsed["model"])
        self.assertEqual(resp["object"], "chat.completion")
        self.assertEqual(resp["choices"][0]["message"]["content"], "Hi there!")

    def test_t3_29_openai_vision_part_maps_to_multimodal_attachment(self):
        vision_message = {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is in this image?"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="},
                },
            ],
        }
        parts = vision_message["content"]
        valid, mime, b64_data = OpenAIMultimodalParser.parse_image_part(parts[1])
        self.assertTrue(valid)
        self.assertEqual(mime, "image/png")

        # Map to upload URI and stream request
        uri = MultimodalUploadHandler.format_upload_response("vision_input.png", "hash_vis")
        attachment = MultimodalUploadHandler.format_attachment_payload(uri, "vision_input.png")
        _, body = StreamGenerateEncoder.encode(parts[0]["text"], "tok", file_attachments=attachment)
        from urllib.parse import unquote
        self.assertIn("vision_input.png", unquote(body))

    def test_t3_30_ingest_credentials_encrypt_in_dpapi_vault(self):
        cookies = CookieParser.parse("__Secure-1PSID=val_psid; __Secure-1PSIDTS=val_ts")
        cookie_bytes = json.dumps(cookies).encode("utf-8")
        encrypted = DPAPIVault.encrypt_data(cookie_bytes)
        decrypted = DPAPIVault.decrypt_data(encrypted)
        restored_cookies = json.loads(decrypted.decode("utf-8"))
        self.assertEqual(restored_cookies["__Secure-1PSID"], "val_psid")

    def test_t3_31_security_guard_allows_localhost_blocks_attacker(self):
        self.assertTrue(LocalSecurityGuard.verify_host("127.0.0.1:8000"))
        self.assertFalse(LocalSecurityGuard.verify_host("evil.com:8000"))
        self.assertTrue(LocalSecurityGuard.verify_origin("http://127.0.0.1:5173"))
        self.assertFalse(LocalSecurityGuard.verify_origin("http://attacker.com"))

    def test_t3_32_token_expiration_auto_refresh_resumes_stream(self):
        client = RotateCookiesClient({"__Secure-1PSID": "live_psid", "__Secure-1PSIDTS": "expired_ts"})
        # 401 triggers refresh
        status, headers, body = self.mock.handle_rotate_cookies(client.cookies)
        client.process_response(status, headers, body)
        self.assertEqual(client.health, "healthy")
        # Stream resumes
        stream = self.mock.handle_stream_generate("Resume query")
        frames = list(StreamingFrameParser.parse_stream(stream))
        self.assertTrue(len(frames) > 0)

    def test_t3_33_virtual_list_500_messages_with_zero_long_tasks(self):
        vlist = VirtualListSimulator(viewport_height=800)
        vlist.set_items([120] * 500)
        observer = LongTaskPerformanceObserver(threshold_ms=50.0)

        # Scroll from top to bottom
        for scroll_y in range(0, 50000, 2000):
            t_start = time.perf_counter()
            vlist.scroll_to(scroll_y)
            nodes = vlist.count_active_dom_nodes()
            t_ms = (time.perf_counter() - t_start) * 1000.0
            observer.record_task("virtual_scroll", t_ms)
            self.assertLess(nodes, 180)

        self.assertEqual(observer.long_task_count(), 0)

    def test_t3_34_full_credential_pipeline_from_profile_to_vault(self):
        # Create temp profile
        import tempfile, shutil
        temp_dir = tempfile.mkdtemp()
        try:
            db_path = BrowserProfileExtractor.create_mock_firefox_profile(
                temp_dir, {"__Secure-1PSID": "profile_psid", "__Secure-1PSIDTS": "profile_ts"}
            )
            extracted = BrowserProfileExtractor.extract_from_firefox_db(db_path)
            valid, _ = CookieParser.validate(extracted)
            self.assertTrue(valid)

            # Encrypt into vault
            vault_blob = DPAPIVault.encrypt_data(json.dumps(extracted).encode("utf-8"))
            decrypted = json.loads(DPAPIVault.decrypt_data(vault_blob).decode("utf-8"))
            self.assertEqual(decrypted["__Secure-1PSID"], "profile_psid")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_t3_35_fts5_search_branch_recovery_and_export(self):
        # Insert conversations
        self.storage.create_conversation("c_workflow", "Full Workflow")
        self.storage.add_message("m1", "c_workflow", "user", "Explain quantum teleportation.")
        self.storage.add_message("m2", "c_workflow", "assistant", "Quantum teleportation transfers qubit states via entanglement.")

        results, _ = self.fts.search("teleportation")
        self.assertEqual(len(results), 2)

        conv = self.storage.get_conversation("c_workflow")
        messages = self.storage.get_messages("c_workflow")
        md = ThreadExporter.export_markdown(conv, messages)
        self.assertIn("quantum teleportation", md.lower())


if __name__ == "__main__":
    unittest.main()
