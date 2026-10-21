# tests/e2e/tier1_features/test_f01_to_f05_session_auth.py
"""Tier 1: Tests for Features F01 to F05 (Session & Auth)."""

import unittest
import json
import os
import tempfile
import shutil
from tests.e2e.harness import (
    CookieParser,
    BrowserProfileExtractor,
    WIZDataExtractor,
    RotateCookiesClient,
    HeaderSynthesizer,
)

class TestF01SessionCredentialIngestion(unittest.TestCase):
    """F01: Session Credential Ingestion."""

    def test_f01_01_parse_cookie_header_string(self):
        raw = "__Secure-1PSID=sid_val_12345; __Secure-1PSIDTS=ts_val_67890; NID=999"
        parsed = CookieParser.parse(raw)
        self.assertEqual(parsed.get("__Secure-1PSID"), "sid_val_12345")
        self.assertEqual(parsed.get("__Secure-1PSIDTS"), "ts_val_67890")
        self.assertEqual(parsed.get("NID"), "999")

    def test_f01_02_parse_json_cookie_list(self):
        cookie_list = [
            {"name": "__Secure-1PSID", "value": "json_psid_val"},
            {"name": "__Secure-1PSIDTS", "value": "json_ts_val"},
            {"name": "SAPISID", "value": "json_sapisid_val"},
        ]
        parsed = CookieParser.parse(json.dumps(cookie_list))
        self.assertEqual(parsed.get("__Secure-1PSID"), "json_psid_val")
        self.assertEqual(parsed.get("__Secure-1PSIDTS"), "json_ts_val")
        self.assertEqual(parsed.get("SAPISID"), "json_sapisid_val")

    def test_f01_03_parse_multiline_cookie_format(self):
        raw = "__Secure-1PSID=multi_line_psid\n__Secure-1PSIDTS=multi_line_ts\nOTHER=test"
        parsed = CookieParser.parse(raw)
        self.assertEqual(parsed.get("__Secure-1PSID"), "multi_line_psid")
        self.assertEqual(parsed.get("__Secure-1PSIDTS"), "multi_line_ts")
        self.assertEqual(parsed.get("OTHER"), "test")

    def test_f01_04_validate_mandatory_cookies_present(self):
        cookies = {"__Secure-1PSID": "token_a", "__Secure-1PSIDTS": "token_b"}
        valid, missing = CookieParser.validate(cookies)
        self.assertTrue(valid)
        self.assertEqual(len(missing), 0)

    def test_f01_05_validate_missing_1psidts_fails(self):
        cookies = {"__Secure-1PSID": "token_a"}
        valid, missing = CookieParser.validate(cookies)
        self.assertFalse(valid)
        self.assertIn("__Secure-1PSIDTS", missing)


class TestF02LocalBrowserProfileExtractor(unittest.TestCase):
    """F02: Local Browser Profile Extractor."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_f02_01_firefox_profile_cookie_extraction(self):
        cookies = {"__Secure-1PSID": "ff_psid_val", "__Secure-1PSIDTS": "ff_ts_val"}
        db_path = BrowserProfileExtractor.create_mock_firefox_profile(self.temp_dir, cookies)
        extracted = BrowserProfileExtractor.extract_from_firefox_db(db_path)
        self.assertEqual(extracted.get("__Secure-1PSID"), "ff_psid_val")
        self.assertEqual(extracted.get("__Secure-1PSIDTS"), "ff_ts_val")

    def test_f02_02_missing_profile_path_returns_empty(self):
        non_existent = os.path.join(self.temp_dir, "does_not_exist.sqlite")
        extracted = BrowserProfileExtractor.extract_from_firefox_db(non_existent)
        self.assertEqual(extracted, {})

    def test_f02_03_filter_only_google_domain_cookies(self):
        db_path = os.path.join(self.temp_dir, "cookies.sqlite")
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE moz_cookies (name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER);")
        conn.execute("INSERT INTO moz_cookies VALUES ('__Secure-1PSID', 'google_val', '.google.com', '/', 1800000000)")
        conn.execute("INSERT INTO moz_cookies VALUES ('__Secure-1PSID', 'evil_val', '.evil.com', '/', 1800000000)")
        conn.commit()
        conn.close()

        extracted = BrowserProfileExtractor.extract_from_firefox_db(db_path)
        self.assertEqual(extracted.get("__Secure-1PSID"), "google_val")

    def test_f02_04_ignore_unrelated_session_cookies(self):
        cookies = {"unrelated_cookie": "some_value"}
        db_path = BrowserProfileExtractor.create_mock_firefox_profile(self.temp_dir, cookies)
        extracted = BrowserProfileExtractor.extract_from_firefox_db(db_path)
        self.assertNotIn("unrelated_cookie", extracted)

    def test_f02_05_read_only_access_mode_for_sqlite(self):
        cookies = {"__Secure-1PSID": "test_sid", "__Secure-1PSIDTS": "test_ts"}
        db_path = BrowserProfileExtractor.create_mock_firefox_profile(self.temp_dir, cookies)
        extracted = BrowserProfileExtractor.extract_from_firefox_db(db_path)
        self.assertTrue(len(extracted) > 0)


class TestF03ContextParameterExtraction(unittest.TestCase):
    """F03: Context Parameter Extraction."""

    MOCK_HTML = """
    <html>
      <head>
        <script>
          window.WIZ_global_data = {
            "SNlM0e": "AIzaSy_mock_access_token_abc:1700000000",
            "cfb2h": "boq_assistant-bard-web-server_20260401.00_p0",
            "FdrFJe": "123456789098765432",
            "TuX5cc": "en-US",
            "qKIAYe": "feeds/mock_push_token_xyz"
          };
        </script>
      </head>
      <body>App Content</body>
    </html>
    """

    def test_f03_01_extract_snlm0e_access_token(self):
        res = WIZDataExtractor.extract(self.MOCK_HTML)
        self.assertEqual(res["access_token"], "AIzaSy_mock_access_token_abc:1700000000")

    def test_f03_02_extract_cfb2h_build_label(self):
        res = WIZDataExtractor.extract(self.MOCK_HTML)
        self.assertEqual(res["build_label"], "boq_assistant-bard-web-server_20260401.00_p0")

    def test_f03_03_extract_fdrfje_session_id(self):
        res = WIZDataExtractor.extract(self.MOCK_HTML)
        self.assertEqual(res["session_id"], "123456789098765432")

    def test_f03_04_extract_qkiaye_push_id(self):
        res = WIZDataExtractor.extract(self.MOCK_HTML)
        self.assertEqual(res["push_id"], "feeds/mock_push_token_xyz")

    def test_f03_05_single_quote_js_syntax_fallback(self):
        html_single = "<script>window.WIZ_global_data = { 'SNlM0e': 'single_token_123' };</script>"
        res = WIZDataExtractor.extract(html_single)
        self.assertEqual(res["access_token"], "single_token_123")


class TestF04SessionHealthAndAutoRefresh(unittest.TestCase):
    """F04: Session Health & Auto-Refresh."""

    def test_f04_01_initial_healthy_state_with_valid_cookies(self):
        cookies = {"__Secure-1PSID": "psid_val", "__Secure-1PSIDTS": "ts_val"}
        client = RotateCookiesClient(cookies)
        self.assertEqual(client.health, "healthy")

    def test_f04_02_rotate_cookies_request_structure(self):
        cookies = {"__Secure-1PSID": "psid_val", "__Secure-1PSIDTS": "ts_val"}
        client = RotateCookiesClient(cookies)
        req = client.build_request()
        self.assertEqual(req["url"], "https://accounts.google.com/RotateCookies")
        self.assertIn("__Secure-1PSID=psid_val", req["headers"]["Cookie"])
        self.assertEqual(json.loads(req["body"]), ["gemini", "session_state"])

    def test_f04_03_rotate_cookies_success_updates_1psidts(self):
        cookies = {"__Secure-1PSID": "psid_val", "__Secure-1PSIDTS": "ts_val"}
        client = RotateCookiesClient(cookies)
        headers = {"Set-Cookie": "__Secure-1PSIDTS=rotated_ts_new_value; Path=/; Secure"}
        ok = client.process_response(200, headers, "[]")
        self.assertTrue(ok)
        self.assertEqual(client.cookies["__Secure-1PSIDTS"], "rotated_ts_new_value")
        self.assertEqual(client.health, "healthy")

    def test_f04_04_rotate_cookies_401_transitions_to_unauthenticated(self):
        cookies = {"__Secure-1PSID": "psid_val", "__Secure-1PSIDTS": "ts_val"}
        client = RotateCookiesClient(cookies)
        ok = client.process_response(401, {}, "{}")
        self.assertFalse(ok)
        self.assertEqual(client.health, "unauthenticated")

    def test_f04_05_rotate_cookies_500_transitions_to_refreshing(self):
        cookies = {"__Secure-1PSID": "psid_val", "__Secure-1PSIDTS": "ts_val"}
        client = RotateCookiesClient(cookies)
        ok = client.process_response(500, {}, "Server error")
        self.assertFalse(ok)
        self.assertEqual(client.health, "refreshing")


class TestF05GoogleHeaderSynthesis(unittest.TestCase):
    """F05: Google Header Synthesis."""

    def test_f05_01_synthesize_jspb_model_header_array(self):
        header_val = HeaderSynthesizer.synthesize_jspb_header(
            model_id="gemini-2.5-pro",
            model_number=2,
            thinking_flag=1,
            capacity_tail=15,
            session_uuid="SESSION-UUID-1234",
        )
        parsed = json.loads(header_val)
        self.assertEqual(parsed[0], 1)
        self.assertEqual(parsed[4], "gemini-2.5-pro")
        self.assertEqual(parsed[8], [4, 5, 6, 8])
        self.assertEqual(parsed[11], 15)
        self.assertEqual(parsed[14], 2)
        self.assertEqual(parsed[15], 1)
        self.assertEqual(parsed[16], "SESSION-UUID-1234")

    def test_f05_02_jspb_header_session_uuid_injection(self):
        header_val = HeaderSynthesizer.synthesize_jspb_header(session_uuid="CUSTOM-UUID-XYZ")
        parsed = json.loads(header_val)
        self.assertEqual(parsed[16], "CUSTOM-UUID-XYZ")

    def test_f05_03_jspb_thinking_model_flag(self):
        header_val = HeaderSynthesizer.synthesize_jspb_header(
            model_id="gemini-2.0-flash-thinking", thinking_flag=2
        )
        parsed = json.loads(header_val)
        self.assertEqual(parsed[15], 2)

    def test_f05_04_standard_browser_headers_presence(self):
        headers = HeaderSynthesizer.get_standard_headers()
        self.assertEqual(headers["Host"], "gemini.google.com")
        self.assertEqual(headers["Origin"], "https://gemini.google.com")
        self.assertEqual(headers["Referer"], "https://gemini.google.com/")
        self.assertIn("Chromium", headers["sec-ch-ua"])

    def test_f05_05_x_same_domain_header_inclusion(self):
        headers = HeaderSynthesizer.get_standard_headers()
        self.assertEqual(headers.get("x-same-domain"), "1")


if __name__ == "__main__":
    unittest.main()
