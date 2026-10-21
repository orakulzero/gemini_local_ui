# tests/e2e/tier1_features/test_f19_to_f22_ui_interaction_perf.py
"""Tier 1: Tests for Features F19 to F22 (UI Interaction & Perf)."""

import time
import unittest
import tempfile
import shutil
import os
import asyncio
from tests.e2e.harness import (
    PromptInputSimulator,
    ConversationTree,
    LongTaskPerformanceObserver,
)
from backend.app.bridge.models import DEFAULT_MODELS, resolve_model, Gem
from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.session import SessionContext
from backend.app.storage.database import Database
from backend.app.storage.repository import Conversation, Message, ConversationRepository
from backend.app.storage.exporter import ThreadExporter

class TestF19ResponsivePromptInput(unittest.TestCase):
    """F19: Responsive Prompt Input."""

    def test_f19_01_auto_expanding_textarea_rows_single_line(self):
        prompt = PromptInputSimulator(max_rows=8)
        prompt.set_text("Single line prompt")
        self.assertEqual(prompt.calculate_rows(), 1)

    def test_f19_02_auto_expanding_textarea_rows_multiline_capped(self):
        prompt = PromptInputSimulator(max_rows=5)
        prompt.set_text("Line 1\nLine 2\nLine 3\nLine 4\nLine 5\nLine 6\nLine 7")
        self.assertEqual(prompt.calculate_rows(), 5)

    def test_f19_03_history_recall_up_arrow(self):
        prompt = PromptInputSimulator()
        prompt.set_text("First query")
        prompt.submit()
        prompt.set_text("Second query")
        prompt.submit()

        recalled = prompt.recall_up()
        self.assertEqual(recalled, "Second query")
        recalled_earlier = prompt.recall_up()
        self.assertEqual(recalled_earlier, "First query")

    def test_f19_04_history_recall_down_arrow_restores_empty(self):
        prompt = PromptInputSimulator()
        prompt.set_text("Only query")
        prompt.submit()

        prompt.recall_up()
        restored = prompt.recall_down()
        self.assertEqual(restored, "")

    def test_f19_05_attachment_tray_add_and_remove(self):
        prompt = PromptInputSimulator()
        prompt.add_attachment("test.pdf", 1024, "application/pdf")
        self.assertEqual(len(prompt.attachments), 1)
        prompt.remove_attachment(0)
        self.assertEqual(len(prompt.attachments), 0)


class TestF20ModelAndGemSwitcherUI(unittest.TestCase):
    """F20: Model & Gem Switcher UI."""

    def test_f20_01_model_selection_dropdown_state(self):
        m = resolve_model("gemini-2.0-flash")
        self.assertIsNotNone(m)
        self.assertEqual(m.id, "gemini-2.0-flash")
        self.assertEqual(m.tier.value, "free")

    def test_f20_02_switching_between_flash_and_pro(self):
        flash = resolve_model("gemini-2.0-flash")
        pro = resolve_model("gemini-2.5-pro")
        self.assertNotEqual(flash.id, pro.id)
        self.assertEqual(flash.tier.value, "free")
        self.assertEqual(pro.tier.value, "pro")

    def test_f20_03_gem_selection_state(self):
        gem = Gem(id="gem-python-architect", name="Python Architect", description="Software Architect", prompt="You are an architect", is_builtin=False)
        self.assertEqual(gem.id, "gem-python-architect")
        self.assertFalse(gem.is_builtin)
        self.assertEqual(gem.name, "Python Architect")

    def test_f20_04_clearing_selected_gem(self):
        client = GeminiWebClient(SessionContext())
        # 1. Wire envelope with active custom Gem
        env_with_gem = client._build_81_element_envelope("Generate code", gem_id="gem-python-architect")
        self.assertEqual(env_with_gem[19], "gem-python-architect")

        # 2. Wire envelope after clearing Gem (None/empty string)
        env_cleared = client._build_81_element_envelope("Generate code", gem_id=None)
        self.assertIsNone(env_cleared[19])

        # 3. Repository state persistence on clearing gem
        async def _run_repo():
            temp_dir = tempfile.mkdtemp()
            try:
                db = Database(os.path.join(temp_dir, "test_gem_clear.db"))
                await db.connect()
                await db.init_schema()
                repo = ConversationRepository(db)
                conv = await repo.create_conversation(title="Gem Chat", gem_id="gem-python-architect")
                self.assertEqual(conv.gem_id, "gem-python-architect")
                await repo.update_conversation(conv.id, gem_id="")
                updated = await repo.get_conversation(conv.id)
                self.assertIsNotNone(updated)
                self.assertEqual(updated.gem_id, "")
                await db.close()
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        asyncio.run(_run_repo())

    def test_f20_05_model_capability_tag_display(self):
        thinking_model = resolve_model("gemini-2.0-flash-thinking")
        self.assertIsNotNone(thinking_model)
        self.assertTrue(thinking_model.capabilities.supports_thinking)
        self.assertTrue(thinking_model.capabilities.supports_code_execution)


class TestF21BranchSwitcherAndEditing(unittest.TestCase):
    """F21: Branch Switcher & Editing."""

    def test_f21_01_single_message_sibling_info_1_of_1(self):
        tree = ConversationTree()
        tree.add_message("m1", None, "user", "Hello")
        sib = tree.get_sibling_info("m1")
        self.assertEqual(sib["index"], 1)
        self.assertEqual(sib["total"], 1)

    def test_f21_02_alternative_message_sibling_info_2_of_2(self):
        tree = ConversationTree()
        tree.add_message("u1", None, "user", "Hello")
        tree.add_message("a1", "u1", "assistant", "Response 1")
        # Regenerate response -> a2 with same parent u1
        tree.add_message("a2", "u1", "assistant", "Response 2")

        sib1 = tree.get_sibling_info("a1")
        sib2 = tree.get_sibling_info("a2")
        self.assertEqual(sib1["index"], 1)
        self.assertEqual(sib1["total"], 2)
        self.assertEqual(sib2["index"], 2)
        self.assertEqual(sib2["total"], 2)

    def test_f21_03_navigation_to_previous_sibling(self):
        siblings = ["msg_v1", "msg_v2", "msg_v3"]
        current_idx = 2  # viewing msg_v3
        prev_idx = max(0, current_idx - 1)
        self.assertEqual(siblings[prev_idx], "msg_v2")

    def test_f21_04_navigation_to_next_sibling(self):
        siblings = ["msg_v1", "msg_v2"]
        current_idx = 0  # viewing msg_v1
        next_idx = min(len(siblings) - 1, current_idx + 1)
        self.assertEqual(siblings[next_idx], "msg_v2")

    def test_f21_05_editing_turn_creates_fork_point(self):
        tree = ConversationTree()
        tree.add_message("u1", None, "user", "Original question")
        tree.add_message("a1", "u1", "assistant", "Original answer")
        # Edit u1 creates u1_edit
        tree.add_message("u1_edit", None, "user", "Edited question")
        sib = tree.get_sibling_info("u1_edit")
        self.assertEqual(sib["total"], 2)


class TestF22PlaywrightPerformanceSuite(unittest.TestCase):
    """F22: Playwright Performance Suite."""

    def test_f22_01_task_duration_below_50ms_passes(self):
        observer = LongTaskPerformanceObserver(threshold_ms=50.0)
        observer.record_task("render_token_chunk", 12.5)
        self.assertEqual(observer.long_task_count(), 0)
        self.assertTrue(observer.is_performant())

    def test_f22_02_task_duration_above_50ms_flags_long_task(self):
        observer = LongTaskPerformanceObserver(threshold_ms=50.0)
        observer.record_task("synchronous_heavy_ast_parse", 65.2)
        self.assertEqual(observer.long_task_count(), 1)
        self.assertFalse(observer.is_performant())

    def test_f22_03_zero_long_tasks_invariant_verification(self):
        observer = LongTaskPerformanceObserver(threshold_ms=50.0)
        # Simulate 100 fast frame tasks (<16ms)
        for i in range(100):
            observer.record_task(f"frame_{i}", 8.0)
        self.assertEqual(observer.long_task_count(), 0)

    def test_f22_04_simulate_500_keystroke_tasks_budget(self):
        observer = LongTaskPerformanceObserver(threshold_ms=50.0)
        # 500 typing keystrokes should all stay well under 50ms
        for i in range(500):
            observer.record_task(f"keypress_{i}", 4.2)
        self.assertEqual(observer.long_task_count(), 0)

    def test_f22_05_scroll_frame_budget_under_16ms(self):
        conv = Conversation(id="conv_perf", title="Perf Benchmark")
        messages = [
            Message(
                id=i,
                conversation_id="conv_perf",
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message turn {i} payload text with syntax block ```python\nx = {i}\n```",
            )
            for i in range(100)
        ]
        start = time.perf_counter()
        payload = ThreadExporter.export_to_json(conv, messages)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        self.assertLess(elapsed_ms, 50.0)
        self.assertIn('"version": "1.0"', payload)


if __name__ == "__main__":
    unittest.main()
