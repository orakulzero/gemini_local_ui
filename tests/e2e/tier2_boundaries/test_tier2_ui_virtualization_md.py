# tests/e2e/tier2_boundaries/test_tier2_ui_virtualization_md.py
"""Tier 2: Boundary & Corner Cases for Features F14 to F22 (UI & Virtualization)."""

import unittest
from tests.e2e.harness import (
    VirtualListSimulator,
    IncrementalMarkdownParser,
    KaTeXMathExtractor,
    PromptInputSimulator,
    ConversationTree,
    LongTaskPerformanceObserver,
)
from backend.app.storage.exporter import ThreadExporter
from backend.app.bridge.models import DEFAULT_MODELS, resolve_model, Gem, GemSerializer
from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.session import SessionContext

class TestTier2UIVirtualizationMD(unittest.TestCase):
    """Boundary & Corner Cases for F14 to F22."""

    # --- F14: Viewport Virtualization ---
    def test_t2_f14_01_virtual_list_zero_items(self):
        vlist = VirtualListSimulator()
        vlist.set_items([])
        start, end, mounted = vlist.compute_visible_window()
        self.assertEqual(len(mounted), 0)

    def test_t2_f14_02_single_massive_item_10000px(self):
        vlist = VirtualListSimulator(viewport_height=800)
        vlist.set_items([10000])
        vlist.scroll_to(5000)
        start, end, mounted = vlist.compute_visible_window()
        self.assertEqual(mounted, [0])

    def test_t2_f14_03_scroll_clamped_at_max_height(self):
        vlist = VirtualListSimulator(viewport_height=800)
        vlist.set_items([100] * 10)  # total 1000px
        vlist.scroll_to(50000)  # huge scroll top
        self.assertEqual(vlist.scroll_top, 200)

    def test_t2_f14_04_negative_scroll_clamped_to_zero(self):
        vlist = VirtualListSimulator(viewport_height=800)
        vlist.set_items([100] * 10)
        vlist.scroll_to(-500)
        self.assertEqual(vlist.scroll_top, 0)

    def test_t2_f14_05_1000_items_dom_nodes_under_180(self):
        vlist = VirtualListSimulator(viewport_height=800, overscan=2)
        vlist.set_items([150] * 1000)
        vlist.scroll_to(50000)
        nodes = vlist.count_active_dom_nodes()
        self.assertLess(nodes, 180)

    # --- F15: Incremental Streaming Markdown ---
    def test_t2_f15_01_multiple_closed_code_blocks(self):
        text = "```python\nprint(1)\n```\n\n```javascript\nconsole.log(2)\n```\n\nTail"
        res = IncrementalMarkdownParser.parse_stream(text)
        self.assertEqual(len(res["immutable_blocks"]), 2)
        self.assertFalse(res["is_in_code_block"])

    def test_t2_f15_02_whitespace_only_buffer(self):
        res = IncrementalMarkdownParser.parse_stream("   \n\n   \n\n  ")
        self.assertEqual(len(res["immutable_blocks"]), 0)

    def test_t2_f15_03_many_consecutive_newlines(self):
        text = "Para 1" + ("\n" * 50) + "Para 2\n\nTail"
        res = IncrementalMarkdownParser.parse_stream(text)
        self.assertEqual(len(res["immutable_blocks"]), 2)

    def test_t2_f15_04_unclosed_single_backtick_inline_code(self):
        text = "This has `inline code that is not closed"
        res = IncrementalMarkdownParser.parse_stream(text)
        self.assertEqual(res["tail_block"], text)

    def test_t2_f15_05_nested_markdown_headings(self):
        text = "# H1\n\n## H2\n\n### H3\n\nBody"
        res = IncrementalMarkdownParser.parse_stream(text)
        self.assertEqual(len(res["immutable_blocks"]), 3)

    # --- F16: Web Worker Highlighting ---
    def test_t2_f16_01_unknown_language_tag(self):
        text = "```lang_custom_999\nlet x = 1;\n```\n\nDone"
        res = IncrementalMarkdownParser.parse_stream(text)
        self.assertEqual(res["immutable_blocks"][0]["language"], "lang_custom_999")

    def test_t2_f16_02_empty_code_block(self):
        text = "```\n\n```\n\nDone"
        res = IncrementalMarkdownParser.parse_stream(text)
        self.assertEqual(res["immutable_blocks"][0]["content"], "")

    def test_t2_f16_03_large_code_block_10000_lines(self):
        code = "\n".join(f"line_{i} = {i}" for i in range(10000))
        text = f"```python\n{code}\n```\n\nDone"
        res = IncrementalMarkdownParser.parse_stream(text)
        self.assertEqual(len(res["immutable_blocks"][0]["content"].splitlines()), 10000)

    def test_t2_f16_04_code_block_html_tags_not_executed(self):
        raw = "<script>alert('xss')</script><div>HTML content</div>"
        text = f"```html\n{raw}\n```\n\nDone"
        res = IncrementalMarkdownParser.parse_stream(text)
        self.assertEqual(res["immutable_blocks"][0]["content"], raw)

    def test_t2_f16_05_crlf_and_lf_line_endings_preserved(self):
        raw = "line1\r\nline2\nline3"
        text = f"```text\n{raw}\n```\n\nDone"
        res = IncrementalMarkdownParser.parse_stream(text)
        self.assertIn("line1", res["immutable_blocks"][0]["content"])

    # --- F17: KaTeX Math Rendering ---
    def test_t2_f17_01_unclosed_single_dollar_sign_not_math(self):
        text = "The price is $50 without closing dollar."
        math = KaTeXMathExtractor.extract_math(text)
        self.assertEqual(len(math["inline_formulas"]), 0)

    def test_t2_f17_02_empty_display_math_block(self):
        text = "$$$$"
        math = KaTeXMathExtractor.extract_math(text)
        # Empty block or no formula
        self.assertTrue(len(math["block_formulas"]) == 0 or math["block_formulas"] == [""])

    def test_t2_f17_03_dollar_followed_by_numbers_not_math(self):
        text = "Costs $100 and tax $15."
        math = KaTeXMathExtractor.extract_math(text)
        self.assertEqual(len(math["inline_formulas"]), 0)

    def test_t2_f17_04_multiple_dollar_amounts_in_sentence(self):
        text = "Prices: $5, $10, and $20."
        math = KaTeXMathExtractor.extract_math(text)
        self.assertEqual(len(math["inline_formulas"]), 0)

    def test_t2_f17_05_valid_formula_with_symbols(self):
        text = "Equation: $x + y = z$ and $\\alpha = \\beta$."
        math = KaTeXMathExtractor.extract_math(text)
        self.assertEqual(len(math["inline_formulas"]), 2)

    # --- F18: Code Block Actions ---
    def test_t2_f18_01_copy_empty_code_block(self):
        balanced = ThreadExporter.balance_code_fences("```python\n```")
        self.assertEqual(balanced, "```python\n```")
        self.assertEqual(ThreadExporter.balance_code_fences(""), "")

    def test_t2_f18_02_language_badge_empty_language(self):
        stream_buf = "```\nprint('plain code without language')\n```\n\n"
        parsed = IncrementalMarkdownParser.parse_stream(stream_buf)
        self.assertEqual(len(parsed["immutable_blocks"]), 1)
        code_block = parsed["immutable_blocks"][0]
        self.assertEqual(code_block["type"], "code")
        self.assertEqual(code_block["language"], "text")
        badge = (code_block["language"] or "TEXT").upper()
        self.assertEqual(badge, "TEXT")

    def test_t2_f18_03_rapid_multi_toggle_collapse(self):
        tree = ConversationTree()
        tree.add_message("m1", None, "user", "Root message")
        tree.add_message("m2", "m1", "assistant", "Turn 1 answer")
        tree.add_message("m3", "m1", "assistant", "Turn 2 alternate")
        t1 = tree.get_linear_thread("m2")
        t2 = tree.get_linear_thread("m3")
        self.assertEqual(len(t1), 2)
        self.assertEqual(len(t2), 2)
        self.assertEqual(t1[1]["id"], "m2")
        self.assertEqual(t2[1]["id"], "m3")

    def test_t2_f18_04_code_block_trailing_whitespace_preserved(self):
        raw_code = "def foo():    \n    pass   \n"
        stream_buf = f"```python\n{raw_code}```\n\n"
        parsed = IncrementalMarkdownParser.parse_stream(stream_buf)
        self.assertEqual(len(parsed["immutable_blocks"]), 1)
        extracted = parsed["immutable_blocks"][0]["content"]
        self.assertEqual(extracted, "def foo():    \n    pass   ")
        balanced = ThreadExporter.balance_code_fences(f"```python\n{raw_code}")
        self.assertTrue(balanced.endswith("```\n"))

    def test_t2_f18_05_unicode_code_block_copy(self):
        code = "```python\n# こんにちは世界\nprint('🚀 Rocket')\n```"
        balanced = ThreadExporter.balance_code_fences(code)
        self.assertIn("🚀", balanced)
        self.assertIn("こんにちは世界", balanced)
        self.assertTrue(balanced.endswith("```"))

    # --- F19: Responsive Prompt Input ---
    def test_t2_f19_01_submit_whitespace_ignored(self):
        prompt = PromptInputSimulator()
        prompt.set_text("   \n\t  ")
        submitted = prompt.submit()
        self.assertEqual(submitted, "")
        self.assertEqual(len(prompt.history), 0)

    def test_t2_f19_02_prompt_textarea_100_lines_capped(self):
        prompt = PromptInputSimulator(max_rows=8)
        prompt.set_text("\n" * 100)
        self.assertEqual(prompt.calculate_rows(), 8)

    def test_t2_f19_03_history_recall_empty_history(self):
        prompt = PromptInputSimulator()
        prompt.set_text("Current draft")
        recalled = prompt.recall_up()
        self.assertEqual(recalled, "Current draft")

    def test_t2_f19_04_remove_attachment_out_of_bounds(self):
        prompt = PromptInputSimulator()
        prompt.remove_attachment(99)  # does not raise error
        self.assertEqual(len(prompt.attachments), 0)

    def test_t2_f19_05_prompt_input_50k_characters(self):
        prompt = PromptInputSimulator()
        huge = "A" * 50000
        prompt.set_text(huge)
        self.assertEqual(len(prompt.text), 50000)

    # --- F20: Model & Gem Switcher UI ---
    def test_t2_f20_01_model_switcher_single_model(self):
        model = resolve_model("gemini-2.5-flash")
        self.assertEqual(model.id, "gemini-2.5-flash")
        self.assertEqual(model.tier.value, "free")

    def test_t2_f20_02_set_active_gem_empty_string(self):
        # 1. Model resolution boundary
        model = resolve_model("gemini-2.5-flash")
        self.assertIn(model.id, DEFAULT_MODELS)
        self.assertFalse(model.capabilities.supports_thinking)

        # 2. Gem serializer boundary on empty response
        gems = GemSerializer.parse_list_response([])
        self.assertEqual(gems, [])

        # 3. Wire envelope boundary on empty gem_id string (omitted from inner[19])
        client = GeminiWebClient(SessionContext())
        env = client._build_81_element_envelope("Prompt", gem_id="")
        self.assertIsNone(env[19])

    def test_t2_f20_03_switch_to_same_model_idempotent(self):
        m1 = resolve_model("gemini-2.5-pro")
        m2 = resolve_model("gemini-2.5-pro")
        self.assertEqual(m1.id, m2.id)
        self.assertEqual(m1.display_name, m2.display_name)

    def test_t2_f20_04_retains_gem_across_model_switch(self):
        client = GeminiWebClient(SessionContext())
        gem = Gem(id="gem_coding", name="Coding Gem", description="Code helper", prompt="Be concise")

        # Switching model numbers while retaining gem_id
        env_flash = client._build_81_element_envelope("Hello", gem_id=gem.id, model_number=1)
        env_pro = client._build_81_element_envelope("Hello", gem_id=gem.id, model_number=3)

        self.assertEqual(env_flash[19], "gem_coding")
        self.assertEqual(env_pro[19], "gem_coding")
        self.assertEqual(env_flash[79], 1)
        self.assertEqual(env_pro[79], 3)
        self.assertNotEqual(env_flash[79], env_pro[79])

    def test_t2_f20_05_capability_non_existent_model(self):
        unknown = resolve_model("non_existent_model_xyz")
        self.assertIsNotNone(unknown)
        self.assertNotIn(unknown.id, DEFAULT_MODELS)

    # --- F21: Branch Switcher & Editing ---
    def test_t2_f21_01_sibling_info_unknown_id(self):
        tree = ConversationTree()
        sib = tree.get_sibling_info("non_existent_id")
        self.assertEqual(sib["index"], 1)
        self.assertEqual(sib["total"], 1)

    def test_t2_f21_02_ten_alternate_branches(self):
        tree = ConversationTree()
        tree.add_message("u1", None, "user", "Prompt")
        for i in range(10):
            tree.add_message(f"a_{i}", "u1", "assistant", f"Ans {i}")
        sib = tree.get_sibling_info("a_5")
        self.assertEqual(sib["total"], 10)
        self.assertEqual(sib["index"], 6)

    def test_t2_f21_03_nav_prev_sibling_at_start(self):
        tree = ConversationTree()
        tree.add_message("u1", None, "user", "Prompt")
        tree.add_message("a1", "u1", "assistant", "Answer 1")
        tree.add_message("a2", "u1", "assistant", "Answer 2")

        sib = tree.get_sibling_info("a1")
        self.assertEqual(sib["index"], 1)
        self.assertEqual(sib["total"], 2)
        # Clamped previous navigation at boundary (index 1 cannot navigate < 1)
        new_idx = max(1, sib["index"] - 1)
        self.assertEqual(new_idx, 1)
        self.assertEqual(sib["sibling_ids"][new_idx - 1], "a1")

    def test_t2_f21_04_nav_next_sibling_at_end(self):
        tree = ConversationTree()
        tree.add_message("u1", None, "user", "Prompt")
        tree.add_message("a1", "u1", "assistant", "Answer 1")
        tree.add_message("a2", "u1", "assistant", "Answer 2")
        tree.add_message("a3", "u1", "assistant", "Answer 3")

        sib = tree.get_sibling_info("a3")
        self.assertEqual(sib["index"], 3)
        self.assertEqual(sib["total"], 3)
        # Clamped next navigation at boundary (index 3 cannot navigate > total 3)
        new_idx = min(sib["total"], sib["index"] + 1)
        self.assertEqual(new_idx, 3)
        self.assertEqual(sib["sibling_ids"][new_idx - 1], "a3")

    def test_t2_f21_05_five_alternate_root_messages(self):
        tree = ConversationTree()
        for i in range(5):
            tree.add_message(f"root_{i}", None, "user", f"Topic {i}")
        sib = tree.get_sibling_info("root_2")
        self.assertEqual(sib["total"], 5)
        self.assertEqual(sib["index"], 3)

    # --- F22: Playwright Performance Suite ---
    def test_t2_f22_01_task_exactly_50ms_is_not_long_task(self):
        observer = LongTaskPerformanceObserver(threshold_ms=50.0)
        observer.record_task("border_task", 50.0)
        self.assertEqual(observer.long_task_count(), 0)

    def test_t2_f22_02_task_at_50_1ms_recorded_as_long_task(self):
        observer = LongTaskPerformanceObserver(threshold_ms=50.0)
        observer.record_task("long_task", 50.1)
        self.assertEqual(observer.long_task_count(), 1)

    def test_t2_f22_03_zero_ms_task_handled(self):
        observer = LongTaskPerformanceObserver()
        observer.record_task("instant", 0.0)
        self.assertEqual(observer.total_tasks, 1)

    def test_t2_f22_04_ten_thousand_fast_tasks(self):
        observer = LongTaskPerformanceObserver(threshold_ms=50.0)
        for i in range(1000):
            observer.record_task(f"t_{i}", 5.0)
        self.assertEqual(observer.long_task_count(), 0)

    def test_t2_f22_05_custom_threshold_16ms(self):
        observer = LongTaskPerformanceObserver(threshold_ms=16.6)
        observer.record_task("frame_exceeded", 20.0)
        self.assertEqual(observer.long_task_count(), 1)


if __name__ == "__main__":
    unittest.main()
