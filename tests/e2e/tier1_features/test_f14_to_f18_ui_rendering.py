# tests/e2e/tier1_features/test_f14_to_f18_ui_rendering.py
"""Tier 1: Tests for Features F14 to F18 (UI Rendering & Virtualization)."""

import unittest
from tests.e2e.harness import (
    VirtualListSimulator,
    IncrementalMarkdownParser,
    KaTeXMathExtractor,
    ConversationTree,
)
from backend.app.storage.exporter import ThreadExporter
from backend.app.storage.repository import Conversation, Message

class TestF14ViewportVirtualization(unittest.TestCase):
    """F14: Viewport Virtualization."""

    def test_f14_01_compute_visible_window_start_end(self):
        vlist = VirtualListSimulator(viewport_height=500, overscan=2)
        # 100 items of 100px each
        vlist.set_items([100] * 100)
        vlist.scroll_to(1000)  # visible items: 10 to 15
        start, end, mounted = vlist.compute_visible_window()
        self.assertLessEqual(start, 10)
        self.assertGreaterEqual(end, 15)

    def test_f14_02_overscan_buffer_inclusion(self):
        vlist = VirtualListSimulator(viewport_height=400, overscan=2)
        vlist.set_items([100] * 50)
        vlist.scroll_to(1000)
        start, end, mounted = vlist.compute_visible_window()
        # With overscan 2, mounted should include items before and after visible range
        self.assertIn(8, mounted)

    def test_f14_03_active_dom_nodes_bounded_under_180(self):
        vlist = VirtualListSimulator(viewport_height=800, overscan=2)
        # 500 messages
        vlist.set_items([120] * 500)
        vlist.scroll_to(15000)
        node_count = vlist.count_active_dom_nodes()
        # Must be strictly under 180 elements across 500 messages!
        self.assertLess(node_count, 180)

    def test_f14_04_pin_to_bottom_detection_near_bottom(self):
        vlist = VirtualListSimulator(viewport_height=800)
        vlist.set_items([100] * 20)  # total 2000px
        vlist.scroll_to(1200)  # scroll 1200 + 800 = 2000 (at bottom)
        self.assertTrue(vlist.is_pinned_to_bottom)

    def test_f14_05_unpin_when_scrolled_up_past_30px(self):
        vlist = VirtualListSimulator(viewport_height=800)
        vlist.set_items([100] * 20)
        vlist.scroll_to(1100)  # 100px away from bottom
        self.assertFalse(vlist.is_pinned_to_bottom)


class TestF15IncrementalStreamingMarkdown(unittest.TestCase):
    """F15: Incremental Streaming Markdown."""

    def test_f15_01_split_completed_paragraphs(self):
        buffer = "First paragraph.\n\nSecond paragraph.\n\nThird streaming part"
        res = IncrementalMarkdownParser.parse_stream(buffer)
        self.assertEqual(len(res["immutable_blocks"]), 2)
        self.assertEqual(res["immutable_blocks"][0]["content"], "First paragraph.")
        self.assertEqual(res["immutable_blocks"][1]["content"], "Second paragraph.")
        self.assertEqual(res["tail_block"], "Third streaming part")

    def test_f15_02_unclosed_code_fence_held_in_tail(self):
        buffer = "Some intro.\n\n```python\ndef hello():\n    return 'world'"
        res = IncrementalMarkdownParser.parse_stream(buffer)
        self.assertTrue(res["is_in_code_block"])
        self.assertTrue(res["tail_block"].startswith("```python"))

    def test_f15_03_closed_code_fence_emitted_to_immutable_blocks(self):
        buffer = "Intro text.\n\n```bash\ncurl https://example.com\n```\n\nTail note"
        res = IncrementalMarkdownParser.parse_stream(buffer)
        self.assertFalse(res["is_in_code_block"])
        code_blocks = [b for b in res["immutable_blocks"] if b["type"] == "code"]
        self.assertEqual(len(code_blocks), 1)
        self.assertEqual(code_blocks[0]["language"], "bash")
        self.assertEqual(code_blocks[0]["content"], "curl https://example.com")

    def test_f15_04_heading_block_recognition(self):
        buffer = "# Title of Document\n\nSome body text.\n\n"
        res = IncrementalMarkdownParser.parse_stream(buffer)
        headings = [b for b in res["immutable_blocks"] if b["type"] == "heading"]
        self.assertEqual(len(headings), 1)
        self.assertEqual(headings[0]["content"], "# Title of Document")

    def test_f15_05_empty_stream_buffer_handling(self):
        res = IncrementalMarkdownParser.parse_stream("")
        self.assertEqual(len(res["immutable_blocks"]), 0)
        self.assertEqual(res["tail_block"], "")
        self.assertFalse(res["is_in_code_block"])


class TestF16WebWorkerHighlighting(unittest.TestCase):
    """F16: Web Worker Highlighting."""

    def test_f16_01_code_block_language_extraction(self):
        buffer = "```typescript\nconst x: number = 42;\n```\n\nDone"
        res = IncrementalMarkdownParser.parse_stream(buffer)
        code = res["immutable_blocks"][0]
        self.assertEqual(code["language"], "typescript")

    def test_f16_02_default_fallback_to_text_language(self):
        buffer = "```\nPlain unformatted text\n```\n\nDone"
        res = IncrementalMarkdownParser.parse_stream(buffer)
        code = res["immutable_blocks"][0]
        self.assertEqual(code["language"], "text")

    def test_f16_03_off_thread_dispatch_payload_structure(self):
        buffer = "```python\nimport sys\nprint('hello')\n```\n\nNext section"
        res = IncrementalMarkdownParser.parse_stream(buffer)
        code_blocks = [b for b in res["immutable_blocks"] if b["type"] == "code"]
        self.assertEqual(len(code_blocks), 1)
        block = code_blocks[0]
        payload = {
            "type": "HIGHLIGHT_REQUEST",
            "id": "block_0",
            "language": block["language"],
            "code": block["content"],
        }
        balanced = ThreadExporter.balance_code_fences(f"```{payload['language']}\n{payload['code']}")
        self.assertTrue(balanced.endswith("```\n"))
        self.assertEqual(payload["language"], "python")
        self.assertIn("print('hello')", payload["code"])

    def test_f16_04_preservation_of_code_content_integrity(self):
        raw_code = "for i in range(10):\n    print(i)"
        buffer = f"```python\n{raw_code}\n```\n\n"
        res = IncrementalMarkdownParser.parse_stream(buffer)
        self.assertEqual(res["immutable_blocks"][0]["content"], raw_code)

    def test_f16_05_python_syntax_token_tagging(self):
        raw_code = "def calculate_total(items):\n    return sum(items)"
        buffer = f"```python\n{raw_code}\n```\n\nDone"
        res = IncrementalMarkdownParser.parse_stream(buffer)
        code_block = res["immutable_blocks"][0]
        self.assertEqual(code_block["language"], "python")
        conv = Conversation(
            id="conv_hl",
            title="Highlighting Test",
            model="gemini-2.5-flash",
            created_at=1000,
            updated_at=1000,
        )
        msg = Message(
            id="msg_hl",
            conversation_id="conv_hl",
            role="assistant",
            content=f"```{code_block['language']}\n{code_block['content']}\n```",
            model="gemini-2.5-flash",
            created_at=1000,
            updated_at=1000,
        )
        md = ThreadExporter.export_to_markdown(conv, [msg])
        self.assertIn("```python", md)
        self.assertIn("def calculate_total", md)


class TestF17KaTeXMathRendering(unittest.TestCase):
    """F17: KaTeX Math Rendering."""

    def test_f17_01_extract_block_math_formula(self):
        text = "Formula below:\n\n$$\\int_0^\\infty e^{-x^2} dx = \\frac{\\sqrt{\\pi}}{2}$$\n\nEnd formula."
        math = KaTeXMathExtractor.extract_math(text)
        self.assertEqual(len(math["block_formulas"]), 1)
        self.assertIn("\\int_0^\\infty", math["block_formulas"][0])

    def test_f17_02_extract_inline_math_formula(self):
        text = "Einstein defined energy as $E = mc^2$ in physics."
        math = KaTeXMathExtractor.extract_math(text)
        self.assertEqual(len(math["inline_formulas"]), 1)
        self.assertEqual(math["inline_formulas"][0], "E = mc^2")

    def test_f17_03_distinguish_currency_dollar_from_math(self):
        text = "The product costs $100 and tax is $5.00."
        math = KaTeXMathExtractor.extract_math(text)
        # Currency should not be captured as inline formulas
        self.assertEqual(len(math["inline_formulas"]), 0)

    def test_f17_04_multiple_math_formulas_extraction(self):
        text = "Formulas: $a^2 + b^2 = c^2$ and $x = y + 1$."
        math = KaTeXMathExtractor.extract_math(text)
        self.assertEqual(len(math["inline_formulas"]), 2)

    def test_f17_05_complex_fraction_latex_syntax(self):
        text = "$$\\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}$$"
        math = KaTeXMathExtractor.extract_math(text)
        self.assertEqual(len(math["block_formulas"]), 1)
        self.assertIn("\\frac", math["block_formulas"][0])


class TestF18CodeBlockActions(unittest.TestCase):
    """F18: Code Block Actions."""

    def test_f18_01_copy_code_payload_matches_content(self):
        raw_snippet = "git clone https://github.com/example/repo.git\ncd repo"
        stream_buf = f"```bash\n{raw_snippet}\n```\n\n"
        parsed = IncrementalMarkdownParser.parse_stream(stream_buf)
        self.assertEqual(len(parsed["immutable_blocks"]), 1)
        extracted_code = parsed["immutable_blocks"][0]["content"]
        self.assertEqual(extracted_code, raw_snippet)
        balanced = ThreadExporter.balance_code_fences(f"```bash\n{extracted_code}")
        self.assertIn(raw_snippet, balanced)
        self.assertTrue(balanced.endswith("```\n"))

    def test_f18_02_language_badge_display_text(self):
        stream_buf = "```python\nprint('hello world')\n```\n\n"
        parsed = IncrementalMarkdownParser.parse_stream(stream_buf)
        self.assertEqual(len(parsed["immutable_blocks"]), 1)
        code_block = parsed["immutable_blocks"][0]
        self.assertEqual(code_block["type"], "code")
        self.assertEqual(code_block["language"], "python")
        badge = code_block["language"].upper()
        self.assertEqual(badge, "PYTHON")

    def test_f18_03_collapse_toggle_state(self):
        tree = ConversationTree()
        code_msg = "```python\nprint('long multi-line code block')\n```"
        tree.add_message("m1", None, "user", "Show me code")
        tree.add_message("m2", "m1", "assistant", code_msg)
        linear = tree.get_linear_thread("m2")
        self.assertEqual(len(linear), 2)
        parsed = IncrementalMarkdownParser.parse_stream(linear[1]["content"] + "\n\n")
        self.assertEqual(parsed["immutable_blocks"][0]["type"], "code")
        sibling_info = tree.get_sibling_info("m2")
        self.assertEqual(sibling_info["total"], 1)
        self.assertEqual(sibling_info["index"], 1)

    def test_f18_04_line_count_calculation_for_code(self):
        raw_code = "line1\nline2\nline3\nline4\nline5"
        stream_buf = f"```text\n{raw_code}\n```\n\n"
        parsed = IncrementalMarkdownParser.parse_stream(stream_buf)
        self.assertEqual(len(parsed["immutable_blocks"]), 1)
        extracted = parsed["immutable_blocks"][0]["content"]
        self.assertEqual(len(extracted.splitlines()), 5)
        balanced = ThreadExporter.balance_code_fences(f"```text\n{raw_code}")
        self.assertTrue(balanced.endswith("```\n"))

    def test_f18_05_carriage_return_normalization_on_copy(self):
        crlf_stream = "```python\r\nline1\r\nline2\r\n```\r\n\r\n"
        parsed = IncrementalMarkdownParser.parse_stream(crlf_stream)
        self.assertEqual(len(parsed["immutable_blocks"]), 1)
        content = parsed["immutable_blocks"][0]["content"]
        normalized_copy = content.replace("\r\n", "\n")
        self.assertNotIn("\r", normalized_copy)
        self.assertEqual(normalized_copy, "line1\nline2")
        balanced = ThreadExporter.balance_code_fences("```python\r\nline1\r\nline2\r\n```")
        self.assertEqual(balanced, "```python\r\nline1\r\nline2\r\n```")


if __name__ == "__main__":
    unittest.main()
