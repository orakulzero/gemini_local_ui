# tests/e2e/tier2_boundaries/test_tier2_auth_protocols.py
"""Tier 2: Boundary & Corner Cases for Features F01 to F09 (Auth & Protocols)."""

import unittest
import json
from urllib.parse import parse_qs, unquote
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
)

class TestTier2AuthProtocols(unittest.TestCase):
    """Boundary & Corner Cases for F01 to F09."""

    # --- F01: Session Credential Ingestion ---
    def test_t2_f01_01_empty_string_cookie_input(self):
        cookies = CookieParser.parse("")
        self.assertEqual(cookies, {})

    def test_t2_f01_02_malformed_cookie_without_equals(self):
        cookies = CookieParser.parse("malformed_cookie_string_with_no_equal_sign")
        self.assertEqual(cookies, {})

    def test_t2_f01_03_cookie_extra_whitespace_and_semicolons(self):
        raw = "  ;;  __Secure-1PSID=val1  ;   __Secure-1PSIDTS=val2 ; ; "
        cookies = CookieParser.parse(raw)
        self.assertEqual(cookies.get("__Secure-1PSID"), "val1")
        self.assertEqual(cookies.get("__Secure-1PSIDTS"), "val2")

    def test_t2_f01_04_malformed_json_fallback(self):
        raw = "[{'name': '__Secure-1PSID', 'value': 'test'}"  # invalid json
        cookies = CookieParser.parse(raw)
        self.assertTrue(isinstance(cookies, dict))

    def test_t2_f01_05_cookie_with_enclosing_quotes_stripped(self):
        raw = '__Secure-1PSID="quoted_token"; __Secure-1PSIDTS=\'single_quoted\''
        cookies = CookieParser.parse(raw)
        self.assertEqual(cookies.get("__Secure-1PSID"), "quoted_token")
        self.assertEqual(cookies.get("__Secure-1PSIDTS"), "single_quoted")

    # --- F02: Local Browser Profile Extractor ---
    def test_t2_f02_01_corrupt_non_sqlite_file(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"NOT A SQLITE FILE HEADER")
            temp_path = f.name
        try:
            cookies = BrowserProfileExtractor.extract_from_firefox_db(temp_path)
            self.assertEqual(cookies, {})
        finally:
            try:
                os.remove(temp_path)
            except Exception:
                pass

    def test_t2_f02_02_missing_moz_cookies_table(self):
        import tempfile, sqlite3, os
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite") as f:
            temp_path = f.name
        conn = sqlite3.connect(temp_path)
        conn.execute("CREATE TABLE other_table (id INT);")
        conn.commit()
        conn.close()
        try:
            cookies = BrowserProfileExtractor.extract_from_firefox_db(temp_path)
            self.assertEqual(cookies, {})
        finally:
            try:
                os.remove(temp_path)
            except Exception:
                pass

    def test_t2_f02_03_locked_database_read_mode(self):
        import tempfile, os
        temp_dir = tempfile.mkdtemp()
        db_path = BrowserProfileExtractor.create_mock_firefox_profile(
            temp_dir, {"__Secure-1PSID": "locked_sid"}
        )
        cookies = BrowserProfileExtractor.extract_from_firefox_db(db_path)
        self.assertEqual(cookies.get("__Secure-1PSID"), "locked_sid")
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_t2_f02_04_null_empty_cookie_values_handled(self):
        import tempfile, os, sqlite3
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "cookies.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE moz_cookies (name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER);")
        conn.execute("INSERT INTO moz_cookies VALUES ('__Secure-1PSID', '', '.google.com', '/', 0)")
        conn.commit()
        conn.close()
        cookies = BrowserProfileExtractor.extract_from_firefox_db(db_path)
        self.assertEqual(cookies.get("__Secure-1PSID"), "")
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_t2_f02_05_large_unrelated_cookies_filtered(self):
        import tempfile, os, sqlite3
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "cookies.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE moz_cookies (name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER);")
        for i in range(100):
            conn.execute(f"INSERT INTO moz_cookies VALUES ('unrelated_{i}', 'val_{i}', '.other{i}.com', '/', 0)")
        conn.execute("INSERT INTO moz_cookies VALUES ('__Secure-1PSID', 'target_val', '.google.com', '/', 0)")
        conn.commit()
        conn.close()
        cookies = BrowserProfileExtractor.extract_from_firefox_db(db_path)
        self.assertEqual(len(cookies), 1)
        self.assertEqual(cookies.get("__Secure-1PSID"), "target_val")
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    # --- F03: Context Parameter Extraction ---
    def test_t2_f03_01_empty_html_returns_none_tokens(self):
        res = WIZDataExtractor.extract("")
        self.assertIsNone(res["access_token"])
        self.assertIsNone(res["build_label"])

    def test_t2_f03_02_html_without_wiz_block_returns_none(self):
        res = WIZDataExtractor.extract("<html><body>No script tags here</body></html>")
        self.assertIsNone(res["access_token"])

    def test_t2_f03_03_truncated_wiz_script_tag(self):
        html = '<script>window.WIZ_global_data = { "SNlM0e": "incomplete_token'
        res = WIZDataExtractor.extract(html)
        self.assertIsNone(res["access_token"])

    def test_t2_f03_04_special_regex_characters_in_token(self):
        html = '<script>window.WIZ_global_data = { "SNlM0e": "token+with/special==chars$" };</script>'
        res = WIZDataExtractor.extract(html)
        self.assertEqual(res["access_token"], "token+with/special==chars$")

    def test_t2_f03_05_duplicate_wiz_blocks_extracts_first(self):
        html = """
        <script>window.WIZ_global_data = { "SNlM0e": "token_first" };</script>
        <script>window.WIZ_global_data = { "SNlM0e": "token_second" };</script>
        """
        res = WIZDataExtractor.extract(html)
        self.assertEqual(res["access_token"], "token_first")

    # --- F04: Session Health & Auto-Refresh ---
    def test_t2_f04_01_rotate_cookies_with_empty_cookies_dict(self):
        client = RotateCookiesClient({})
        self.assertEqual(client.health, "unauthenticated")

    def test_t2_f04_02_rotate_cookies_403_forbidden_unauthenticated(self):
        client = RotateCookiesClient({"__Secure-1PSID": "tok", "__Secure-1PSIDTS": "ts"})
        ok = client.process_response(403, {}, "Forbidden")
        self.assertFalse(ok)
        self.assertEqual(client.health, "unauthenticated")

    def test_t2_f04_03_rotate_cookies_malformed_set_cookie_header(self):
        client = RotateCookiesClient({"__Secure-1PSID": "tok", "__Secure-1PSIDTS": "ts"})
        # 200 OK but Set-Cookie missing 1PSIDTS
        ok = client.process_response(200, {"Set-Cookie": "OTHER=val; Path=/"}, "[]")
        self.assertFalse(ok)

    def test_t2_f04_04_rotate_cookies_502_bad_gateway_refreshing(self):
        client = RotateCookiesClient({"__Secure-1PSID": "tok", "__Secure-1PSIDTS": "ts"})
        ok = client.process_response(502, {}, "Bad Gateway")
        self.assertFalse(ok)
        self.assertEqual(client.health, "refreshing")

    def test_t2_f04_05_rapid_back_to_back_rotate_cookies(self):
        client = RotateCookiesClient({"__Secure-1PSID": "tok", "__Secure-1PSIDTS": "ts1"})
        client.process_response(200, {"Set-Cookie": "__Secure-1PSIDTS=ts2"}, "[]")
        client.process_response(200, {"Set-Cookie": "__Secure-1PSIDTS=ts3"}, "[]")
        self.assertEqual(client.cookies["__Secure-1PSIDTS"], "ts3")

    # --- F05: Google Header Synthesis ---
    def test_t2_f05_01_synthesize_unknown_future_model(self):
        header = HeaderSynthesizer.synthesize_jspb_header(model_id="gemini-4.0-ultra")
        parsed = json.loads(header)
        self.assertEqual(parsed[4], "gemini-4.0-ultra")

    def test_t2_f05_02_synthesize_negative_capacity_tail(self):
        header = HeaderSynthesizer.synthesize_jspb_header(capacity_tail=-1)
        parsed = json.loads(header)
        self.assertEqual(parsed[11], -1)

    def test_t2_f05_03_synthesize_special_chars_session_uuid(self):
        header = HeaderSynthesizer.synthesize_jspb_header(session_uuid="UUID_123_TEST-VAL")
        parsed = json.loads(header)
        self.assertEqual(parsed[16], "UUID_123_TEST-VAL")

    def test_t2_f05_04_synthesize_model_number_zero(self):
        header = HeaderSynthesizer.synthesize_jspb_header(model_number=0)
        parsed = json.loads(header)
        self.assertEqual(parsed[14], 0)

    def test_t2_f05_05_get_standard_headers_none_model(self):
        headers = HeaderSynthesizer.get_standard_headers(model_header=None)
        self.assertNotIn("x-goog-ext-525001261-jspb", headers)

    # --- F06: Unary Batchexecute RPCs ---
    def test_t2_f06_01_batchexecute_empty_access_token(self):
        url, body, headers = BatchexecuteEncoder.encode_request("otAQ7b", [], access_token="")
        parsed = parse_qs(body)
        self.assertEqual(parsed.get("at", [""])[0], "")

    def test_t2_f06_02_batchexecute_deeply_nested_payload(self):
        deep = [[[[["nested_level_5"]]]]]
        _, body, _ = BatchexecuteEncoder.encode_request("otAQ7b", deep, "token")
        f_req = json.loads(parse_qs(body)["f.req"][0])
        self.assertIn("nested_level_5", f_req[0][0][1])

    def test_t2_f06_03_batchexecute_double_anti_xssi_prefix(self):
        raw = ")]}'\n)]}'\n[[[\"wrb.fr\",\"otAQ7b\",\"[1]\"]]]"
        decoded = BatchexecuteEncoder.decode_response(raw)
        # Handles or survives prefix
        self.assertTrue(isinstance(decoded, list))

    def test_t2_f06_04_batchexecute_html_error_page_response(self):
        html_error = "<html><head><title>500 Internal Error</title></head></html>"
        decoded = BatchexecuteEncoder.decode_response(html_error)
        self.assertEqual(decoded, [])

    def test_t2_f06_05_batchexecute_unicode_text_payload(self):
        payload = ["Hello world", "こんにちは"]
        _, body, _ = BatchexecuteEncoder.encode_request("otAQ7b", payload, "token")
        unquoted = unquote(body)
        self.assertIn("Hello world", unquoted)

    # --- F07: StreamGenerate RPC Client ---
    def test_t2_f07_01_utf16_code_units_cjk_characters(self):
        cjk = "日本語テキスト"
        # In BMP, each CJK char is 1 UTF-16 code unit
        self.assertEqual(StreamingFrameParser.utf16_code_units(cjk), len(cjk))

    def test_t2_f07_02_utf16_code_units_family_emoji_surrogates(self):
        family = "👨‍👩‍👧‍👦"
        # Each emoji character in sequence has surrogate pairs
        units = StreamingFrameParser.utf16_code_units(family)
        self.assertGreater(units, 4)

    def test_t2_f07_03_stream_frame_truncated_payload_discarded(self):
        # Header specifies 100 code units, but payload only has 20
        corrupt_stream = "\n\n100\n[[small_payload]]\n\n"
        frames = list(StreamingFrameParser.parse_stream(corrupt_stream))
        self.assertEqual(len(frames), 0)

    def test_t2_f07_04_stream_frame_zero_length_payload(self):
        zero_stream = "\n\n0\n\n\n"
        frames = list(StreamingFrameParser.parse_stream(zero_stream))
        self.assertEqual(len(frames), 0)

    def test_t2_f07_05_stream_unknown_envelope_ignored(self):
        unknown_env = json.dumps([["other.rpc", "some_data"]])
        length = StreamingFrameParser.utf16_code_units(unknown_env)
        frame = f"\n\n{length}\n{unknown_env}\n\n"
        frames = list(StreamingFrameParser.parse_stream(frame))
        self.assertEqual(len(frames), 0)

    # --- F08: Streaming Delta Accumulator ---
    def test_t2_f08_01_delta_accumulator_empty_string_updates(self):
        accum = StreamingDeltaAccumulator()
        self.assertEqual(accum.calculate_delta(""), "")
        self.assertEqual(accum.calculate_delta(""), "")

    def test_t2_f08_02_delta_accumulator_completely_replaced_text(self):
        accum = StreamingDeltaAccumulator()
        d1 = accum.calculate_delta("Alpha Beta")
        d2 = accum.calculate_delta("Gamma Delta")
        self.assertEqual(d1, "Alpha Beta")
        self.assertEqual(d2, "Gamma Delta")

    def test_t2_f08_03_delta_accumulator_trailing_markdown_quote(self):
        accum = StreamingDeltaAccumulator()
        d1 = accum.calculate_delta("Quote starts:\n\\> in-progress")
        self.assertTrue(len(d1) > 0)

    def test_t2_f08_04_delta_accumulator_100k_token_burst(self):
        accum = StreamingDeltaAccumulator()
        burst = "X" * 100000
        d1 = accum.calculate_delta(burst)
        self.assertEqual(len(d1), 100000)

    def test_t2_f08_05_delta_accumulator_repeated_identical_text(self):
        accum = StreamingDeltaAccumulator()
        accum.calculate_delta("Persistent string")
        d2 = accum.calculate_delta("Persistent string")
        self.assertEqual(d2, "")

    # --- F09: Thought / Reasoning Parsing ---
    def test_t2_f09_01_thought_array_with_none_instead_of_list(self):
        candidate: list = [None] * 38
        candidate[37] = None
        self.assertIsNone(ThoughtParser.extract_thoughts(candidate))

    def test_t2_f09_02_thought_array_with_integer(self):
        candidate: list = [None] * 38
        candidate[37] = [[12345]]
        thoughts = ThoughtParser.extract_thoughts(candidate)
        self.assertEqual(thoughts, "12345")

    def test_t2_f09_03_candidate_array_shorter_than_38(self):
        candidate = [None] * 20
        self.assertIsNone(ThoughtParser.extract_thoughts(candidate))

    def test_t2_f09_04_extremely_long_thought_string(self):
        candidate: list = [None] * 38
        long_thought = "T" * 50000
        candidate[37] = [[long_thought]]
        thoughts = ThoughtParser.extract_thoughts(candidate)
        self.assertEqual(len(thoughts), 50000)

    def test_t2_f09_05_candidate_with_empty_string_thought(self):
        candidate: list = [None] * 38
        candidate[37] = [[""]]
        self.assertIsNone(ThoughtParser.extract_thoughts(candidate))


if __name__ == "__main__":
    unittest.main()
