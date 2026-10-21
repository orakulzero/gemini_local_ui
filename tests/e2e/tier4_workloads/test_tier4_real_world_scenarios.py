# tests/e2e/tier4_workloads/test_tier4_real_world_scenarios.py
"""Tier 4: End-to-End Real-World Application Workloads (>=17 tests)."""

import unittest
import json
import time
import tempfile
import shutil
from tests.e2e.harness import (
    CookieParser,
    BrowserProfileExtractor,
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
)

class TestTier4RealWorldScenarios(unittest.TestCase):
    """End-to-End Realistic Application Workloads."""

    def setUp(self):
        self.mock = MockGeminiServer()
        self.storage = StorageEngine(":memory:")
        self.fts = FTS5SearchEngine(self.storage.conn)
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        self.storage.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_t4_01_zero_config_initial_setup_and_bootstrap(self):
        """Scenario 1: User launches app for first time; browser profile extraction -> session bootstrap -> first completion."""
        # 1. Profile extraction
        db_path = BrowserProfileExtractor.create_mock_firefox_profile(
            self.temp_dir, {"__Secure-1PSID": "fresh_user_psid", "__Secure-1PSIDTS": "fresh_ts"}
        )
        cookies = BrowserProfileExtractor.extract_from_firefox_db(db_path)
        valid, missing = CookieParser.validate(cookies)
        self.assertTrue(valid)

        # 2. Encrypt into secure vault
        vault_data = DPAPIVault.encrypt_data(json.dumps(cookies).encode("utf-8"))
        self.assertTrue(len(vault_data) > 0)

        # 3. Bootstrap session from /app
        html = self.mock.handle_get_app(cookies)
        wiz = WIZDataExtractor.extract(html)
        self.assertIsNotNone(wiz["access_token"])

        # 4. Discover models
        resp = self.mock.handle_batchexecute("otAQ7b", [])
        decoded = BatchexecuteEncoder.decode_response(resp)
        self.assertEqual(len(decoded), 1)

        # 5. First chat completion
        stream = self.mock.handle_stream_generate("Hello Gemini")
        frames = list(StreamingFrameParser.parse_stream(stream))
        self.assertGreater(len(frames), 0)
        self.assertTrue(frames[-1]["is_done"])

    def test_t4_02_multi_turn_software_engineering_coding_session(self):
        """Scenario 2: Multi-turn software engineering coding session with code blocks, deltas, and persistence."""
        conv_id = self.storage.create_conversation("c_dev_session", "Python Fast API Refactoring")
        accum = StreamingDeltaAccumulator()
        tree = ConversationTree()

        # Turn 1
        prompt_1 = "Write a basic FastAPI hello world application."
        self.storage.add_message("m_u1", conv_id, "user", prompt_1)
        tree.add_message("m_u1", None, "user", prompt_1)

        ans_1 = "Here is the code:\n\n```python\nfrom fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/')\ndef root():\n    return {'msg': 'Hello'}\n```\n\nRun with uvicorn."
        self.storage.add_message("m_a1", conv_id, "assistant", ans_1, parent_id="m_u1")
        tree.add_message("m_a1", "m_u1", "assistant", ans_1)

        # Turn 2
        prompt_2 = "Now add a health check endpoint."
        self.storage.add_message("m_u2", conv_id, "user", prompt_2, parent_id="m_a1")
        tree.add_message("m_u2", "m_a1", "user", prompt_2)

        ans_2 = "Updated with health check:\n\n```python\n@app.get('/health')\ndef health():\n    return {'status': 'ok'}\n```"
        self.storage.add_message("m_a2", conv_id, "assistant", ans_2, parent_id="m_u2")
        tree.add_message("m_a2", "m_u2", "assistant", ans_2)

        # Verify linear thread
        thread = tree.get_linear_thread("m_a2")
        self.assertEqual(len(thread), 4)

        # Verify code blocks extracted cleanly
        res = IncrementalMarkdownParser.parse_stream(ans_2)
        code_blocks = [b for b in res["immutable_blocks"] if b["type"] == "code"]
        self.assertEqual(len(code_blocks), 1)
        self.assertIn("/health", code_blocks[0]["content"])

    def test_t4_03_deep_research_workflow_with_extended_thinking(self):
        """Scenario 3: Complex reasoning prompt with thought streaming, Markdown export with <details>."""
        prompt = "Prove that the square root of 2 is irrational."
        stream = self.mock.handle_stream_generate(prompt, model_id="gemini-2.0-flash-thinking", is_thinking=True)
        frames = list(StreamingFrameParser.parse_stream(stream))

        thoughts = [f["thought"] for f in frames if f.get("thought")]
        self.assertTrue(len(thoughts) > 0)

        final_answer = "Suppose sqrt(2) = a/b in lowest terms... (Proof by contradiction completes)."
        conv_id = self.storage.create_conversation("c_proof", "Square Root Proof", "gemini-2.0-flash-thinking")
        self.storage.add_message("m_u1", conv_id, "user", prompt)
        self.storage.add_message("m_a1", conv_id, "assistant", final_answer, thinking_content=thoughts[0])

        conv = self.storage.get_conversation(conv_id)
        messages = self.storage.get_messages(conv_id)
        md = ThreadExporter.export_markdown(conv, messages)
        self.assertIn("<details>", md)
        self.assertIn("<summary>Thought Process</summary>", md)
        self.assertIn(thoughts[0], md)

    def test_t4_04_multimodal_document_analysis_workflow(self):
        """Scenario 4: User uploads PDF architecture diagram, runs question answering."""
        # 1. Attachment handling
        filename = "cloud_architecture.pdf"
        file_hash = "sha256_mock_hash_777"
        uri = MultimodalUploadHandler.format_upload_response(filename, file_hash)
        att_payload = MultimodalUploadHandler.format_attachment_payload(uri, filename)

        # 2. Stream generation with attachment
        endpoint, body = StreamGenerateEncoder.encode(
            "Explain the VPC layout in this diagram", "tok", file_attachments=att_payload
        )
        self.assertIn("cloud_architecture.pdf", body)

        # 3. Store conversation and attachment
        conv_id = self.storage.create_conversation("c_vpc", "Cloud Architecture QA")
        msg_id = self.storage.add_message("m_u1", conv_id, "user", "Explain the VPC layout")
        self.storage.conn.execute(
            """
            INSERT INTO attachments(id, message_id, filename, mime_type, size_bytes, resource_uri, created_at)
            VALUES ('att_1', 'm_u1', 'cloud_architecture.pdf', 'application/pdf', 1048576, ?, 1700000000)
            """,
            (uri,),
        )
        self.storage.conn.commit()

        row = self.storage.conn.execute("SELECT * FROM attachments WHERE id = 'att_1'").fetchone()
        self.assertEqual(row["filename"], "cloud_architecture.pdf")

    def test_t4_05_custom_gem_domain_expert_workflow(self):
        """Scenario 5: Discover Gems, select Architect Gem, and issue domain queries."""
        # 1. List Gems
        resp = self.mock.handle_batchexecute("CNgdBe", [])
        decoded = BatchexecuteEncoder.decode_response(resp)
        gems = decoded[0]
        arch_gem = gems[0]
        gem_id = arch_gem[0]
        self.assertEqual(gem_id, "gem_arch_01")

        # 2. Dispatch prompt with selected Gem
        _, body = StreamGenerateEncoder.encode("Design microservices", "tok", gem_id=gem_id)
        from urllib.parse import unquote
        self.assertIn("gem_arch_01", unquote(body))

    def test_t4_06_heavy_conversation_branching_and_exploration(self):
        """Scenario 6: 10 messages -> edit message 3 -> create alternate branch -> switch between branches."""
        tree = ConversationTree()
        # Build initial thread of 5 turns (10 messages)
        prev = None
        for i in range(1, 6):
            u_id = f"u{i}"
            a_id = f"a{i}"
            tree.add_message(u_id, prev, "user", f"Question {i}")
            tree.add_message(a_id, u_id, "assistant", f"Answer {i}")
            prev = a_id

        # Verify initial branch
        thread_orig = tree.get_linear_thread("a5")
        self.assertEqual(len(thread_orig), 10)

        # Edit u3 -> create u3_alt
        tree.add_message("u3_alt", "a2", "user", "Edited Question 3")
        tree.add_message("a3_alt", "u3_alt", "assistant", "Alternate Answer 3")
        tree.add_message("u4_alt", "a3_alt", "user", "Question 4 on branch 2")
        tree.add_message("a4_alt", "u4_alt", "assistant", "Answer 4 on branch 2")

        # Verify alternate branch reconstruction
        thread_alt = tree.get_linear_thread("a4_alt")
        self.assertEqual([m["id"] for m in thread_alt], ["u1", "a1", "u2", "a2", "u3_alt", "a3_alt", "u4_alt", "a4_alt"])

        # Check sibling info at fork point
        sib_u3 = tree.get_sibling_info("u3")
        sib_u3_alt = tree.get_sibling_info("u3_alt")
        self.assertEqual(sib_u3["total"], 2)
        self.assertEqual(sib_u3_alt["total"], 2)
        self.assertEqual(sib_u3["index"], 1)
        self.assertEqual(sib_u3_alt["index"], 2)

    def test_t4_07_large_scale_chat_history_sync_and_search(self):
        """Scenario 7: Sync 50 conversations, populate database, execute sub-50ms FTS5 search."""
        sync = RemoteSyncEngine(self.storage)
        remote_data = [{"cid": f"rem_{i}", "title": f"Project Discussion {i}"} for i in range(50)]
        synced = sync.sync_remote_conversations(remote_data)
        self.assertEqual(synced, 50)

        # Populate with messages
        for i in range(50):
            cid = f"local_rem_{i}"
            self.storage.add_message(f"m_{i}_1", cid, "user", f"Searching for Kubernetes container orchestration in cluster {i}")
            self.storage.add_message(f"m_{i}_2", cid, "assistant", f"Kubernetes manages deployment {i} with pods and ingress controllers.")

        # Search benchmark across all messages
        results, latency_ms = self.fts.search("Kubernetes")
        self.assertEqual(len(results), 20)  # limit 20
        self.assertLess(latency_ms, 50.0)

    def test_t4_08_openai_sdk_v2_drop_in_conformance_non_stream(self):
        """Scenario 8: Standard OpenAI Chat Completions payload mapping and non-stream response."""
        req_payload = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are an AI assistant."},
                {"role": "user", "content": "Explain binary search."},
            ],
            "temperature": 0.3,
            "max_tokens": 1024,
        }
        parsed = OpenAIChatAdapter.parse_request(req_payload)
        self.assertEqual(parsed["model"], "gemini-2.5-pro")
        self.assertEqual(parsed["temperature"], 0.3)

        response = OpenAIChatAdapter.format_non_stream_response(
            "Binary search runs in O(log n) time.", parsed["model"], prompt_tokens=20, completion_tokens=30
        )
        self.assertEqual(response["object"], "chat.completion")
        self.assertEqual(response["choices"][0]["finish_reason"], "stop")
        self.assertEqual(response["usage"]["total_tokens"], 50)

    def test_t4_09_openai_sdk_streaming_completions(self):
        """Scenario 9: OpenAI SSE stream chunk generation and termination sequence."""
        req_payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Count to three"}],
            "stream": True,
        }
        parsed = OpenAIChatAdapter.parse_request(req_payload)
        self.assertEqual(parsed["model"], "gemini-2.0-flash")

        stream_id = "chatcmpl-stream-777"
        chunks = [
            OpenAISSEChunkEncoder.encode_role_chunk(stream_id, parsed["model"]),
            OpenAISSEChunkEncoder.encode_content_chunk(stream_id, parsed["model"], "1, "),
            OpenAISSEChunkEncoder.encode_content_chunk(stream_id, parsed["model"], "2, "),
            OpenAISSEChunkEncoder.encode_content_chunk(stream_id, parsed["model"], "3."),
            OpenAISSEChunkEncoder.encode_finish_chunk(stream_id, parsed["model"], "stop"),
            OpenAISSEChunkEncoder.encode_done(),
        ]
        self.assertEqual(len(chunks), 6)
        self.assertEqual(chunks[-1], "data: [DONE]\n\n")

    def test_t4_10_vision_multimodal_analysis_via_openai_api(self):
        """Scenario 10: OpenAI image_url message -> multimodal parsing -> completion."""
        openai_req = {
            "model": "gpt-4o",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What color is this?"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="},
                        },
                    ],
                }
            ],
        }
        part = openai_req["messages"][0]["content"][1]
        valid, mime, data = OpenAIMultimodalParser.parse_image_part(part)
        self.assertTrue(valid)
        self.assertEqual(mime, "image/png")

    def test_t4_11_ide_plugin_cursor_integration_simulation(self):
        """Scenario 11: Fast completions with stop sequences and deterministic low temperature."""
        req = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are a code completion engine. Output code only."},
                {"role": "user", "content": "def calculate_mean(numbers):\n    return"},
            ],
            "temperature": 0.0,
            "max_tokens": 64,
            "stop": ["\ndef ", "\nclass "],
        }
        parsed = OpenAIChatAdapter.parse_request(req)
        self.assertEqual(parsed["temperature"], 0.0)
        self.assertEqual(parsed["stop"], ["\ndef ", "\nclass "])
        resp = OpenAIChatAdapter.format_non_stream_response(" sum(numbers) / len(numbers)", parsed["model"])
        self.assertIn("sum(numbers)", resp["choices"][0]["message"]["content"])

    def test_t4_12_high_stress_lag_free_ui_session(self):
        """Scenario 12: 500 messages, 100 code blocks, virtual list windowing, 0 long tasks."""
        vlist = VirtualListSimulator(viewport_height=800, overscan=2)
        # 500 messages: alternating 100px text and 400px code blocks
        heights = [100 if i % 5 != 0 else 400 for i in range(500)]
        vlist.set_items(heights)

        observer = LongTaskPerformanceObserver(threshold_ms=50.0)

        # Scroll in increments of 5000px
        for pos in range(0, vlist.total_height(), 5000):
            t_start = time.perf_counter()
            vlist.scroll_to(pos)
            active_nodes = vlist.count_active_dom_nodes()
            t_ms = (time.perf_counter() - t_start) * 1000.0
            observer.record_task("scroll_frame", t_ms)
            # Guarantee DOM bounding
            self.assertLess(active_nodes, 180)

        self.assertEqual(observer.long_task_count(), 0)

    def test_t4_13_token_rotation_under_long_session(self):
        """Scenario 13: Daemon runs continuously -> periodic RotateCookies maintains session health."""
        cookies = {"__Secure-1PSID": "long_session_psid", "__Secure-1PSIDTS": "ts_initial"}
        client = RotateCookiesClient(cookies)

        # 3 periodic rotations
        for i in range(3):
            status, headers, body = self.mock.handle_rotate_cookies(client.cookies)
            ok = client.process_response(status, headers, body)
            self.assertTrue(ok)
            self.assertEqual(client.health, "healthy")

        self.assertTrue(client.cookies["__Secure-1PSIDTS"].startswith("mock_rotated_ts_"))

    def test_t4_14_full_thread_export_and_archival(self):
        """Scenario 14: Export branched conversation with code and thoughts to Markdown and JSON."""
        conv_id = self.storage.create_conversation("c_archive", "Database Optimization", "gemini-2.5-pro")
        self.storage.add_message("m1", conv_id, "user", "How to index SQLite?")
        self.storage.add_message(
            "m2",
            conv_id,
            "assistant",
            "Use B-Tree indexes:\n\n```sql\nCREATE INDEX idx_user ON users(email);\n```",
            thinking_content="Consider write amplification vs query performance.",
        )

        conv = self.storage.get_conversation(conv_id)
        messages = self.storage.get_messages(conv_id)

        # Markdown export
        md = ThreadExporter.export_markdown(conv, messages)
        self.assertIn("```sql", md)
        self.assertIn("<summary>Thought Process</summary>", md)

        # JSON export
        raw_json = ThreadExporter.export_json(conv, messages)
        parsed = json.loads(raw_json)
        self.assertEqual(parsed["version"], "1.0")
        self.assertEqual(len(parsed["messages"]), 2)

    def test_t4_15_network_disruption_and_auto_recovery(self):
        """Scenario 15: Simulate transient Google 503 error, backoff, and successful stream resume."""
        client = RotateCookiesClient({"__Secure-1PSID": "valid_psid", "__Secure-1PSIDTS": "valid_ts"})

        # Transient 503
        client.process_response(503, {}, "Service Unavailable")
        self.assertEqual(client.health, "refreshing")

        # Retry succeeds
        status, headers, body = self.mock.handle_rotate_cookies(client.cookies)
        client.process_response(status, headers, body)
        self.assertEqual(client.health, "healthy")

        # Stream completes
        stream = self.mock.handle_stream_generate("Recovered stream")
        frames = list(StreamingFrameParser.parse_stream(stream))
        self.assertTrue(frames[-1]["is_done"])

    def test_t4_16_security_perimeter_defense(self):
        """Scenario 16: Attacker attempts DNS rebinding and cross-site requests against 127.0.0.1 daemon."""
        # 1. DNS Rebinding via Host header
        self.assertFalse(LocalSecurityGuard.verify_host("evil-attacker.com"))
        self.assertFalse(LocalSecurityGuard.verify_host("rebinding.local"))
        self.assertTrue(LocalSecurityGuard.verify_host("127.0.0.1:8000"))

        # 2. CORS Cross-Site Origin
        self.assertFalse(LocalSecurityGuard.verify_origin("http://malicious.org"))
        self.assertTrue(LocalSecurityGuard.verify_origin("http://127.0.0.1:5173"))

        # 3. Anti-CSRF on mutating request
        csrf_secret = "csrf_token_secret_abc123"
        self.assertFalse(LocalSecurityGuard.verify_csrf("POST", "forged_token", csrf_secret))
        self.assertTrue(LocalSecurityGuard.verify_csrf("POST", csrf_secret, csrf_secret))

    def test_t4_17_complete_full_lifecycle_system_verification(self):
        """Scenario 17: Ingest -> Chat -> Branch -> Search -> Export -> OpenAI Proxy -> Vault Cleanup."""
        # 1. Ingest
        cookies = {"__Secure-1PSID": "life_psid", "__Secure-1PSIDTS": "life_ts"}
        vault_data = DPAPIVault.encrypt_data(json.dumps(cookies).encode("utf-8"))

        # 2. Chat
        conv_id = self.storage.create_conversation("c_life", "Lifecycle Chat")
        self.storage.add_message("m_u1", conv_id, "user", "What is cryptography?")
        self.storage.add_message("m_a1", conv_id, "assistant", "Cryptography protects information through mathematical encryption.")

        # 3. Branch
        tree = ConversationTree()
        tree.add_message("m_u1", None, "user", "What is cryptography?")
        tree.add_message("m_a1", "m_u1", "assistant", "Cryptography protects information.")
        tree.add_message("m_a1_alt", "m_u1", "assistant", "Cryptography is the science of secure communication.")
        sib = tree.get_sibling_info("m_a1_alt")
        self.assertEqual(sib["total"], 2)

        # 4. Search
        results, _ = self.fts.search("mathematical encryption")
        self.assertEqual(len(results), 1)

        # 5. Export
        conv = self.storage.get_conversation(conv_id)
        md = ThreadExporter.export_markdown(conv, self.storage.get_messages(conv_id))
        self.assertIn("mathematical encryption", md)

        # 6. OpenAI Proxy
        req = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]}
        parsed = OpenAIChatAdapter.parse_request(req)
        resp = OpenAIChatAdapter.format_non_stream_response("Hello from OpenAI proxy", parsed["model"])
        self.assertEqual(resp["model"], "gemini-2.5-pro")

        # 7. Vault Cleanup
        restored = json.loads(DPAPIVault.decrypt_data(vault_data).decode("utf-8"))
        self.assertEqual(restored["__Secure-1PSID"], "life_psid")

    def test_t4_18_katex_and_code_rendering_heavy_workload(self):
        """Scenario 18: Heavy scientific and mathematical markdown processing without blocking."""
        content = """
# Quantum Field Theory Notes

The Dirac equation:
$$\\left( i \\gamma^\\mu \\partial_\\mu - m \\right) \\psi = 0$$

Code implementation in Python:
```python
import numpy as np

def dirac_gamma_matrices():
    g0 = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, -1, 0], [0, 0, 0, -1]])
    return g0
```

Where $\\gamma^\\mu$ are gamma matrices.
"""
        math = KaTeXMathExtractor.extract_math(content)
        self.assertEqual(len(math["block_formulas"]), 1)
        self.assertEqual(len(math["inline_formulas"]), 1)

        res = IncrementalMarkdownParser.parse_stream(content)
        self.assertFalse(res["is_in_code_block"])
        code_blocks = [b for b in res["immutable_blocks"] if b["type"] == "code"]
        self.assertEqual(len(code_blocks), 1)
        self.assertEqual(code_blocks[0]["language"], "python")


if __name__ == "__main__":
    unittest.main()
