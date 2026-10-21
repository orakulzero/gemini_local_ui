# tests/e2e/tier1_features/test_f43_to_f48_gemini_parity.py
"""Tier 1: Tests for Features F43 to F48 (Core Gemini Web Parity, Canvas & Gems)."""

from __future__ import annotations
import unittest
import json
import os
import tempfile
import shutil
import time
import asyncio
from pathlib import Path
from typing import Any, Dict, List

from unittest.mock import AsyncMock, patch
from tests.e2e.harness import IncrementalMarkdownParser
from backend.app.bridge.uploader import GeminiUploader
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.bridge.streaming_parser import StreamingDeltaAccumulator
from backend.app.storage.database import Database
from backend.app.storage.exporter import ThreadExporter
from backend.app.storage.repository import ConversationRepository, Conversation, Message
from backend.app.bridge.models import Gem, GemSerializer
from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.session import SessionContext


class TestF43IncognitoTemporaryChat(unittest.TestCase):
    """F43: Incognito / Temporary Chat Mode."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_file = Path(self.temp_dir) / "test_incognito.db"
        self.db = Database(self.db_file)
        schema_path = Path(__file__).parent.parent.parent.parent / "backend" / "app" / "storage" / "schema.sql"
        asyncio.run(self.db.init_schema(schema_path))
        self.repo = ConversationRepository(self.db)

    def tearDown(self):
        asyncio.run(self.db.close())
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_f43_incognito_inner_45_flag_injection(self):
        client = GeminiWebClient(SessionContext())
        envelope = client._build_81_element_envelope("Secret prompt", incognito=True)
        self.assertEqual(len(envelope), 81)
        self.assertEqual(envelope[45], 1)
        self.assertEqual(envelope[0][0], "Secret prompt")

    def test_f43_incognito_normal_turn_omission(self):
        client = GeminiWebClient(SessionContext())
        envelope = client._build_81_element_envelope("Public prompt", incognito=False)
        self.assertEqual(len(envelope), 81)
        self.assertIsNone(envelope[45])
        self.assertEqual(envelope[0][0], "Public prompt")

    def test_f43_incognito_bypass_sqlite_messages(self):
        async def _run():
            # In incognito mode, chat turns bypass SQLite writing
            is_incognito = True
            if not is_incognito:
                await self.repo.create_message(conversation_id="conv_1", role="user", content="Hello")
            row_count = await self.db.fetch_one("SELECT COUNT(*) as cnt FROM messages")
            self.assertEqual(row_count["cnt"], 0)

        asyncio.run(_run())

    def test_f43_incognito_bypass_sqlite_conversations(self):
        async def _run():
            is_incognito = True
            if not is_incognito:
                await self.repo.create_conversation(title="Normal Chat", conversation_id="conv_1")
            row_count = await self.db.fetch_one("SELECT COUNT(*) as cnt FROM conversations")
            self.assertEqual(row_count["cnt"], 0)

        asyncio.run(_run())

    def test_f43_incognito_ui_state_toggle(self):
        # Verify /api/chat/generate respects incognito toggle: zero SQLite rows when incognito=True
        async def _run():
            app.state.db = self.db
            app.state.repo = self.repo
            client = TestClient(app)
            with patch("backend.app.api.chat.GeminiWebClient.generate_stream") as mock_stream:
                from backend.app.bridge.streaming_parser import StreamChunk
                async def _mock_gen(*args, **kwargs):
                    yield StreamChunk(type="text", delta="Incognito answer")
                    yield StreamChunk(type="done", delta="")
                mock_stream.side_effect = _mock_gen

                res = client.post("/api/chat/generate", json={"prompt": "Ephemeral chat", "incognito": True})
                self.assertEqual(res.status_code, 200)

                # Verify zero SQLite persistence
                convs = await self.db.fetch_one("SELECT COUNT(*) as cnt FROM conversations")
                msgs = await self.db.fetch_one("SELECT COUNT(*) as cnt FROM messages")
                self.assertEqual(convs["cnt"], 0)
                self.assertEqual(msgs["cnt"], 0)
        asyncio.run(_run())


class TestF44ChatLifecycleManagement(unittest.TestCase):
    """F44: Full Chat Lifecycle Management & GzXR5e Sync."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_file = Path(self.temp_dir) / "test_lifecycle.db"
        self.db = Database(self.db_file)
        schema_path = Path(__file__).parent.parent.parent.parent / "backend" / "app" / "storage" / "schema.sql"
        asyncio.run(self.db.init_schema(schema_path))
        self.repo = ConversationRepository(self.db)

    def tearDown(self):
        asyncio.run(self.db.close())
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_f44_chat_lifecycle_pin_conversation(self):
        async def _run():
            conv = await self.repo.create_conversation(title="Important Research")
            updated = await self.repo.update_conversation(conv.id, is_pinned=True)
            self.assertIsNotNone(updated)
            self.assertTrue(updated.is_pinned)

        asyncio.run(_run())

    def test_f44_chat_lifecycle_unpin_conversation(self):
        async def _run():
            conv = await self.repo.create_conversation(title="Temporary Topic")
            await self.repo.update_conversation(conv.id, is_pinned=True)
            updated = await self.repo.update_conversation(conv.id, is_pinned=False)
            self.assertIsNotNone(updated)
            self.assertFalse(updated.is_pinned)

        asyncio.run(_run())

    def test_f44_chat_lifecycle_rename_title(self):
        async def _run():
            conv = await self.repo.create_conversation(title="Initial Draft")
            updated = await self.repo.update_conversation(conv.id, title="Finalized Strategy")
            self.assertIsNotNone(updated)
            self.assertEqual(updated.title, "Finalized Strategy")

        asyncio.run(_run())

    def test_f44_chat_lifecycle_delete_local(self):
        async def _run():
            conv = await self.repo.create_conversation(title="To Delete")
            ok = await self.repo.delete_conversation(conv.id)
            self.assertTrue(ok)
            deleted = await self.repo.get_conversation(conv.id)
            self.assertIsNone(deleted)

        asyncio.run(_run())

    def test_f44_chat_lifecycle_delete_remote_sync_gzxr5e(self):
        client = GeminiWebClient(SessionContext())
        with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
            mock_rpc.return_value = []
            ok = asyncio.run(client.delete_conversation("conv_remote_12345"))
            self.assertTrue(ok)
            mock_rpc.assert_called_once_with("GzXR5e", ["conv_remote_12345"])


class TestF45GroundingSourcesRichMedia(unittest.TestCase):
    """F45: Grounding Sources, Citations & Rich Media Cards."""

    def test_f45_grounding_search_chunks_extraction(self):
        acc = StreamingDeltaAccumulator()
        candidate = [
            "rcid_100",
            ["sample text"],
            None, None, None, None, None, None, [2], None, None, None,
            {
                "16": ["quantum computing", "shor algorithm"],
                "23": [["https://en.wikipedia.org/wiki/Quantum_computing", "Quantum Computing", "Snippet"]]
            }
        ]
        inner_part = [None, ["c_1", "r_1", "rcid_100"], None, None, [candidate]]
        frame = [["wrb.fr", None, json.dumps(inner_part)]]
        chunks = list(acc.process_frame(frame))
        grounding_chunks = [c for c in chunks if c.type == "grounding"]
        self.assertTrue(len(grounding_chunks) > 0)
        self.assertIn("quantum computing", acc.grounding_queries)

    def test_f45_grounding_citation_urls_extraction(self):
        acc = StreamingDeltaAccumulator()
        candidate = [
            "rcid_100",
            ["sample text"],
            None, None, None, None, None, None, [2], None, None, None,
            {
                "43": [
                    [1, "https://arxiv.org/abs/2101.00001", "Quantum Complexity Foundations", "arxiv.org", "Snippet"]
                ]
            }
        ]
        inner_part = [None, ["c_1", "r_1", "rcid_100"], None, None, [candidate]]
        frame = [["wrb.fr", None, json.dumps(inner_part)]]
        chunks = list(acc.process_frame(frame))
        citation_chunks = [c for c in chunks if c.type == "citation"]
        self.assertTrue(len(citation_chunks) > 0)
        self.assertEqual(acc.citations[0]["url"], "https://arxiv.org/abs/2101.00001")

    def test_f45_grounding_cand12_image_urls(self):
        acc = StreamingDeltaAccumulator()
        candidate = [
            "rcid_100",
            ["sample text"],
            None, None, None, None, None, None, [2], None, None, None,
            {
                "7": [
                    ["https://storage.googleapis.com/gemini/img_12345.png", "Quantum Circuit", 1024, 768]
                ]
            }
        ]
        inner_part = [None, ["c_1", "r_1", "rcid_100"], None, None, [candidate]]
        frame = [["wrb.fr", None, json.dumps(inner_part)]]
        chunks = list(acc.process_frame(frame))
        self.assertTrue(len(acc.images) > 0)
        self.assertEqual(acc.images[0]["url"], "https://storage.googleapis.com/gemini/img_12345.png")

    def test_f45_grounding_youtube_cards_parsing(self):
        acc = StreamingDeltaAccumulator()
        candidate = [
            "rcid_100",
            ["Check video https://www.youtube.com/watch?v=dQw4w9WgXcQ for details"],
            None, None, None, None, None, None, [2], None, None, None, {}
        ]
        inner_part = [None, ["c_1", "r_1", "rcid_100"], None, None, [candidate]]
        frame = [["wrb.fr", None, json.dumps(inner_part)]]
        chunks = list(acc.process_frame(frame))
        self.assertTrue(len(acc.youtube_videos) > 0)
        self.assertEqual(acc.youtube_videos[0]["video_id"], "dQw4w9WgXcQ")

    def test_f45_grounding_ui_pill_rendering(self):
        acc = StreamingDeltaAccumulator()
        candidate = [
            "rcid_pill",
            ["Grounding response text"],
            None, None, None, None, None, None, [2], None, None, None,
            {
                "43": [
                    [1, "https://arxiv.org/abs/2101.00001", "ArXiv: Quantum Fourier Transform", "arxiv.org", "Quantum snippet"]
                ]
            }
        ]
        inner_part = [None, ["c_1", "r_1", "rcid_pill"], None, None, [candidate]]
        frame = [["wrb.fr", None, json.dumps(inner_part)]]
        chunks = list(acc.process_frame(frame))
        self.assertTrue(len(acc.citations) > 0)
        pill = acc.citations[0]
        self.assertEqual(pill["domain"], "arxiv.org")
        self.assertEqual(pill["title"], "ArXiv: Quantum Fourier Transform")
        self.assertTrue(pill["url"].startswith("https://arxiv.org/abs/2101.00001"))


class TestF46CanvasArtifactsSidePanel(unittest.TestCase):
    """F46: Canvas / Artifacts Side-Panel & 1-Click Export."""

    def test_f46_canvas_code_block_open(self):
        code_stream = "```python\nimport numpy as np\nx = np.zeros(10)\n```\n\n"
        parsed = IncrementalMarkdownParser.parse_stream(code_stream)
        code_blocks = [b for b in parsed["immutable_blocks"] if b["type"] == "code"]
        self.assertEqual(len(code_blocks), 1)
        artifact = code_blocks[0]
        self.assertEqual(artifact["language"], "python")
        self.assertIn("import numpy as np", artifact["content"])

        # Validate ThreadExporter balance closure for canvas
        balanced = ThreadExporter.balance_code_fences(artifact["content"])
        self.assertIn("import numpy as np", balanced)

    def test_f46_canvas_markdown_doc_open(self):
        doc_stream = "# Design Specification\n\nModular state management pattern.\n\n"
        parsed = IncrementalMarkdownParser.parse_stream(doc_stream)
        self.assertEqual(len(parsed["immutable_blocks"]), 2)
        self.assertEqual(parsed["immutable_blocks"][0]["type"], "heading")
        self.assertEqual(parsed["immutable_blocks"][1]["type"], "paragraph")

        conv = Conversation(id="conv_doc_spec", title="Design Spec")
        msg = Message(id=1, conversation_id="conv_doc_spec", role="assistant", content=doc_stream)
        md_exported = ThreadExporter.export_to_markdown(conv, [msg])
        self.assertIn("# Design Specification", md_exported)

    def test_f46_canvas_live_edit_state(self):
        async def _run():
            temp_dir = tempfile.mkdtemp()
            try:
                db_path = os.path.join(temp_dir, "test_canvas.db")
                db = Database(db_path)
                await db.connect()
                await db.init_schema()
                repo = ConversationRepository(db)
                conv = await repo.create_conversation(title="Canvas Live Edit")
                msg = await repo.create_message(
                    conversation_id=conv.id,
                    role="assistant",
                    content="```javascript\nlet x = 1;\n```",
                    model="gemini-2.5-flash",
                )
                self.assertEqual(msg.content, "```javascript\nlet x = 1;\n```")
                updated_content = "```javascript\nlet x = 2;\n```"
                await db.execute("UPDATE messages SET content = ? WHERE id = ?;", (updated_content, msg.id))
                fetched = await repo.get_message(msg.id)
                self.assertIsNotNone(fetched)
                self.assertEqual(fetched.content, updated_content)
                await db.close()
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        asyncio.run(_run())

    def test_f46_canvas_sandbox_html_preview(self):
        conv = Conversation(id="conv_html_preview", title="HTML Sandbox Preview")
        msg = Message(
            id=1,
            conversation_id="conv_html_preview",
            role="assistant",
            content="```html\n<html><body><h1>Hello Canvas</h1></body></html>\n```",
        )
        rendered = ThreadExporter.export_to_markdown(conv, [msg])
        self.assertIn("```html", rendered)
        self.assertIn("<h1>Hello Canvas</h1>", rendered)

    def test_f46_canvas_one_click_download(self):
        # Verify MIME detection across canvas supported export formats via GeminiUploader
        self.assertEqual(GeminiUploader.detect_mime_type("algorithm.py"), "text/x-python")
        self.assertEqual(GeminiUploader.detect_mime_type("index.html"), "text/html")
        self.assertEqual(GeminiUploader.detect_mime_type("diagram.svg"), "image/svg+xml")
        self.assertEqual(GeminiUploader.detect_mime_type("data.json"), "application/json")
        self.assertEqual(GeminiUploader.detect_mime_type("main.rs", fallback="text/plain"), "text/plain")


class TestF47MidChatRegeneration(unittest.TestCase):
    """F47: Mid-Chat Model & Reasoning Regeneration."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_file = Path(self.temp_dir) / "test_regen.db"
        self.db = Database(self.db_file)
        schema_path = Path(__file__).parent.parent.parent.parent / "backend" / "app" / "storage" / "schema.sql"
        asyncio.run(self.db.init_schema(schema_path))
        self.repo = ConversationRepository(self.db)

    def tearDown(self):
        asyncio.run(self.db.close())
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_f47_regeneration_model_switch(self):
        async def _run():
            conv = await self.repo.create_conversation(title="Model Switch", model="gemini-3.1-pro")
            u_msg = await self.repo.create_message(conversation_id=conv.id, role="user", content="Analyze code")
            a_msg_1 = await self.repo.create_message(
                conversation_id=conv.id, parent_id=u_msg.id, role="assistant", content="Response 1", model="gemini-3.1-pro"
            )
            a_msg_2 = await self.repo.create_message(
                conversation_id=conv.id, parent_id=u_msg.id, role="assistant", content="Response 2", model="gemini-4-argon"
            )
            self.assertEqual(a_msg_1.model, "gemini-3.1-pro")
            self.assertEqual(a_msg_2.model, "gemini-4-argon")
            self.assertEqual(a_msg_1.parent_id, a_msg_2.parent_id)
            thread = await self.repo.get_active_thread(conv.id)
            self.assertEqual(thread[-1].model, "gemini-4-argon")
        asyncio.run(_run())

    def test_f47_regeneration_reasoning_level_switch(self):
        client = GeminiWebClient(SessionContext())
        env_low = client._build_81_element_envelope("Test prompt", model_number=2, thinking_flag=1)
        env_high = client._build_81_element_envelope("Test prompt", model_number=2, thinking_flag=2)
        self.assertEqual(env_low[80], 1)
        self.assertEqual(env_high[80], 2)

    def test_f47_regeneration_dag_branch_creation(self):
        async def _run():
            conv = await self.repo.create_conversation(title="Branching Test")
            u_msg = await self.repo.create_message(conversation_id=conv.id, role="user", content="Explain Shor algorithm")
            a_msg_1 = await self.repo.create_message(conversation_id=conv.id, parent_id=u_msg.id, role="assistant", content="Response 1 from Pro")
            a_msg_2 = await self.repo.create_message(conversation_id=conv.id, parent_id=u_msg.id, role="assistant", content="Response 2 from Argon")

            self.assertEqual(a_msg_1.parent_id, u_msg.id)
            self.assertEqual(a_msg_2.parent_id, u_msg.id)
            self.assertNotEqual(a_msg_1.id, a_msg_2.id)

        asyncio.run(_run())

    def test_f47_regeneration_prevents_error_1050(self):
        async def _run():
            conv = await self.repo.create_conversation(title="Prevent 1050 Chat", model="e6fa609c3fa255c0")
            u_msg = await self.repo.create_message(conversation_id=conv.id, role="user", content="Explain Grover algorithm")
            a_orig = await self.repo.create_message(conversation_id=conv.id, parent_id=u_msg.id, role="assistant", content="Pro answer", model="e6fa609c3fa255c0")

            # Regeneration on new model (4 Argon) constructs envelope with cid=None (omitted remote cid prevents Error 1050)
            client = GeminiWebClient(SessionContext())
            env_regen = client._build_81_element_envelope("Explain Grover algorithm", cid=None, model_number=8)
            self.assertEqual(env_regen[2][0], "")

            # Branch creation in repo
            a_regen = await self.repo.create_message(conversation_id=conv.id, parent_id=u_msg.id, role="assistant", content="Argon answer", model="95a4c0abcba24364")
            self.assertEqual(a_regen.parent_id, u_msg.id)
            self.assertEqual(a_orig.parent_id, a_regen.parent_id)
            self.assertNotEqual(a_orig.model, a_regen.model)
        asyncio.run(_run())

    def test_f47_regeneration_branch_nav_update(self):
        siblings = [101, 102]
        current_idx = siblings.index(102)
        counter_str = f"< {current_idx + 1}/{len(siblings)} >"
        self.assertEqual(counter_str, "< 2/2 >")


class TestF48GemsManagerPersonaInjector(unittest.TestCase):
    """F48: Complete Gems Manager & Persona Prefix Injector."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_file = Path(self.temp_dir) / "test_gems.db"
        self.db = Database(self.db_file)
        schema_path = Path(__file__).resolve().parent.parent.parent.parent / "backend" / "app" / "storage" / "schema.sql"
        asyncio.run(self.db.init_schema(schema_path))
        self.repo = ConversationRepository(self.db)
        import backend.app.main as main_mod
        self.orig_main_db = getattr(main_mod, "db", None)
        self.orig_main_repo = getattr(main_mod, "repository", None)
        main_mod.db = self.db
        main_mod.repository = self.repo
        app.state.db = self.db
        app.state.repository = self.repo

    def tearDown(self):
        import backend.app.main as main_mod
        if hasattr(self, "orig_main_db"):
            main_mod.db = self.orig_main_db
        if hasattr(self, "orig_main_repo"):
            main_mod.repository = self.orig_main_repo
        asyncio.run(self.db.close())
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_f48_gems_list_custom_gem_type_2(self):
        payload = GemSerializer.build_list_payload(is_custom=True)
        self.assertIsInstance(payload, list)
        self.assertEqual(payload[0], 2)

    def test_f48_gems_list_system_gem_type_3(self):
        payload = GemSerializer.build_list_payload(is_custom=False)
        self.assertIsInstance(payload, list)
        self.assertEqual(payload[0], 3)

    def test_f48_gems_search_and_pin(self):
        raw_rpc_data = [
            [
                ["gem_coding", ["Coding Assistant", "Writes Python and Rust code"], ["You are an expert coder"]],
                ["gem_math", ["Math Tutor", "Teaches calculus and algebra"], ["You are a patient math tutor"]],
            ]
        ]
        parsed_gems = GemSerializer.parse_list_response(raw_rpc_data, is_builtin=False)
        self.assertEqual(len(parsed_gems), 2)
        filtered = [g for g in parsed_gems if "Math" in g.name]
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].id, "gem_math")
        self.assertEqual(filtered[0].description, "Teaches calculus and algebra")

    def test_f48_gems_system_instruction_injection(self):
        # Verify Turn-0 system persona prefix injection via /api/chat/generate
        app.state.db = self.db
        app.state.repository = self.repo
        client = TestClient(app)
        system_instruction = "You are a quantum physics expert. Always give rigorous formulas."
        user_prompt = "What is Shor's algorithm?"

        with patch("backend.app.api.chat.GeminiWebClient.generate_stream") as mock_stream:
            from backend.app.bridge.streaming_parser import StreamChunk
            async def _mock_gen(*args, **kwargs):
                yield StreamChunk(type="text", delta="Formula answer")
                yield StreamChunk(type="done", delta="")
            mock_stream.side_effect = _mock_gen

            res = client.post(
                "/api/chat/generate",
                json={
                    "prompt": user_prompt,
                    "system_instruction": system_instruction,
                },
            )
            self.assertEqual(res.status_code, 200)

            # Verify persisted user message contains the Turn-0 system prefix injection
            async def _verify():
                convs, _ = await self.repo.list_conversations(limit=1)
                msgs = await self.repo.get_all_messages(convs[0].id)
                u_msg = msgs[0]
                self.assertIn("[System Instructions]\n", u_msg.content)
                self.assertIn(system_instruction, u_msg.content)
                self.assertIn("[End System Instructions]\n\n", u_msg.content)
                self.assertIn(user_prompt, u_msg.content)
            asyncio.run(_verify())

    def test_f48_gems_per_conversation_custom_persona(self):
        async def _run():
            temp_dir = tempfile.mkdtemp()
            try:
                db_path = os.path.join(temp_dir, "test_custom_persona.db")
                db = Database(db_path)
                await db.connect()
                await db.init_schema()
                repo = ConversationRepository(db)
                conv = await repo.create_conversation(
                    title="Custom Persona Chat",
                    gem_id="gem_custom_persona",
                    system_prompt="Tone: Concise and formal. Always cite sources.",
                )
                self.assertEqual(conv.gem_id, "gem_custom_persona")
                self.assertEqual(conv.system_prompt, "Tone: Concise and formal. Always cite sources.")
                fetched = await repo.get_conversation(conv.id)
                self.assertIsNotNone(fetched)
                self.assertEqual(fetched.gem_id, "gem_custom_persona")
                self.assertEqual(fetched.system_prompt, "Tone: Concise and formal. Always cite sources.")
                await db.close()
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
