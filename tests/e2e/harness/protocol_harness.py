# tests/e2e/harness/protocol_harness.py
"""Protocol doubles, parsers, and mock Google Gemini web engine."""

from __future__ import annotations
import re
import json
import uuid
import difflib
from typing import Any, Dict, List, Optional, Tuple, Iterator
from urllib.parse import parse_qs, quote, unquote

class WIZDataExtractor:
    """Extracts session tokens from window.WIZ_global_data in HTML."""

    @staticmethod
    def extract(html: str) -> Dict[str, Optional[str]]:
        result = {
            "access_token": None,
            "build_label": None,
            "session_id": None,
            "language": "en",
            "push_id": None,
        }
        if not html:
            return result

        patterns = {
            "access_token": r'"SNlM0e":\s*"([^"]+)"',
            "build_label": r'"cfb2h":\s*"([^"]+)"',
            "session_id": r'"FdrFJe":\s*"([^"]+)"',
            "language": r'"TuX5cc":\s*"([^"]+)"',
            "push_id": r'"qKIAYe":\s*"([^"]+)"',
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, html)
            if match:
                result[key] = match.group(1)

        # Fallback check for single-quoted or unescaped JS syntax
        if not result["access_token"]:
            match = re.search(r"'SNlM0e':\s*'([^']+)'", html)
            if match:
                result["access_token"] = match.group(1)

        return result


class CookieParser:
    """Parses and validates Gemini web session cookies."""

    MANDATORY_COOKIES = ["__Secure-1PSID", "__Secure-1PSIDTS"]

    @classmethod
    def parse(cls, raw: str) -> Dict[str, str]:
        cookies: Dict[str, str] = {}
        if not raw or not raw.strip():
            return cookies

        trimmed = raw.strip()

        # Format 1: JSON array from extension export
        if trimmed.startswith("[") and trimmed.endswith("]"):
            try:
                items = json.loads(trimmed)
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and "name" in item and "value" in item:
                            cookies[str(item["name"])] = str(item["value"])
                    return cookies
            except Exception:
                pass

        # Format 2: JSON object
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                items = json.loads(trimmed)
                if isinstance(items, dict):
                    return {str(k): str(v) for k, v in items.items()}
            except Exception:
                pass

        # Format 3: HTTP Cookie header string or multi-line key=value
        delimiters = [";", "\n", "&"]
        # Split on line breaks first if present
        lines = [line.strip() for line in trimmed.splitlines() if line.strip()]
        for line in lines:
            parts = line.split(";")
            for part in parts:
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k:
                        cookies[k] = v

        return cookies

    @classmethod
    def validate(cls, cookies: Dict[str, str]) -> Tuple[bool, List[str]]:
        missing = [c for c in cls.MANDATORY_COOKIES if c not in cookies or not cookies[c].strip()]
        return len(missing) == 0, missing


class RotateCookiesClient:
    """Manages RotateCookies token refresh and health state."""

    def __init__(self, cookies: Dict[str, str]):
        self.cookies = dict(cookies)
        self.health = "healthy" if CookieParser.validate(cookies)[0] else "unauthenticated"
        self.last_refresh: float = 0.0

    def build_request(self) -> Dict[str, Any]:
        return {
            "url": "https://accounts.google.com/RotateCookies",
            "headers": {
                "Origin": "https://gemini.google.com",
                "Referer": "https://gemini.google.com/",
                "Content-Type": "application/json",
                "Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items()),
            },
            "body": json.dumps(["gemini", "session_state"]),
        }

    def process_response(self, status_code: int, headers: Dict[str, str], body: str) -> bool:
        if status_code in (401, 403):
            self.health = "unauthenticated"
            return False

        if status_code == 200:
            set_cookie = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
            match = re.search(r"__Secure-1PSIDTS=([^;]+)", set_cookie)
            if match:
                self.cookies["__Secure-1PSIDTS"] = match.group(1)
                self.health = "healthy"
                return True
            # If server body contains token update
            try:
                data = json.loads(body)
                if isinstance(data, list) and len(data) > 1 and isinstance(data[1], str):
                    self.cookies["__Secure-1PSIDTS"] = data[1]
                    self.health = "healthy"
                    return True
            except Exception:
                pass

        if status_code >= 500:
            self.health = "refreshing"
            return False

        return False


class HeaderSynthesizer:
    """Synthesizes Google web headers including JSPB metadata."""

    @staticmethod
    def synthesize_jspb_header(
        model_id: str = "gemini-2.0-flash",
        model_number: int = 1,
        thinking_flag: int = 1,
        capacity_tail: int = 12,
        session_uuid: Optional[str] = None,
    ) -> str:
        s_uuid = session_uuid or str(uuid.uuid4()).upper()
        # [1,null,null,null,"model",null,null,0,[4,5,6,8],null,null,12,null,null,1,1,"UUID"]
        jspb = [
            1,
            None,
            None,
            None,
            model_id,
            None,
            None,
            0,
            [4, 5, 6, 8],
            None,
            None,
            capacity_tail,
            None,
            None,
            model_number,
            thinking_flag,
            s_uuid,
        ]
        return json.dumps(jspb, separators=(",", ":"))

    @staticmethod
    def get_standard_headers(model_header: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Host": "gemini.google.com",
            "Origin": "https://gemini.google.com",
            "Referer": "https://gemini.google.com/",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "x-same-domain": "1",
            "sec-ch-ua": '"Chromium";v="145", "Not A(Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        }
        if model_header:
            headers["x-goog-ext-525001261-jspb"] = model_header
        return headers


class BatchexecuteEncoder:
    """Serializes and deserializes unary batchexecute RPC payloads."""

    @staticmethod
    def encode_request(
        rpc_id: str,
        payload_data: Any,
        access_token: str,
        session_id: str = "123456789",
        build_label: str = "boq_assistant-bard-web-server_20250101.00_p0",
        req_id: int = 100000,
    ) -> Tuple[str, str, Dict[str, str]]:
        url = (
            f"https://gemini.google.com/_/BardChatUi/data/batchexecute"
            f"?rpcids={rpc_id}&source-path=%2Fapp&bl={build_label}&f.sid={session_id}&hl=en&_reqid={req_id}&rt=c"
        )
        inner_json = json.dumps(payload_data, separators=(",", ":"), ensure_ascii=False)
        f_req = json.dumps([[[rpc_id, inner_json, None, "generic"]]], separators=(",", ":"), ensure_ascii=False)
        body = f"at={quote(access_token)}&f.req={quote(f_req)}"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://gemini.google.com",
            "Referer": "https://gemini.google.com/",
        }
        return url, body, headers

    @staticmethod
    def decode_response(raw_text: str) -> List[Any]:
        # Strip anti-XSSI
        clean = raw_text
        if clean.startswith(")]}'"):
            clean = clean[4:].lstrip()

        results = []

        def find_wrb_envelopes(obj: Any):
            if isinstance(obj, list):
                if len(obj) >= 3 and obj[0] == "wrb.fr":
                    inner_data = obj[2]
                    if isinstance(inner_data, str):
                        try:
                            results.append(json.loads(inner_data))
                        except Exception:
                            results.append(inner_data)
                    else:
                        results.append(inner_data)
                    return
                for item in obj:
                    find_wrb_envelopes(item)

        lines = [l for l in clean.split("\n") if l.strip()]
        for line in lines:
            try:
                parsed = json.loads(line)
                find_wrb_envelopes(parsed)
            except Exception:
                continue
        return results


class StreamGenerateEncoder:
    """Encodes StreamGenerate requests and builds 81-element inner_req_list."""

    @staticmethod
    def encode(
        prompt: str,
        access_token: str,
        cid: Optional[str] = None,
        rid: Optional[str] = None,
        rcid: Optional[str] = None,
        gem_id: Optional[str] = None,
        file_attachments: Optional[List[Any]] = None,
        model_number: int = 1,
        thinking_flag: int = 1,
        req_uuid: Optional[str] = None,
    ) -> Tuple[str, str]:
        # 81-element array
        req_list: List[Any] = [None] * 81

        attachments = file_attachments or []
        req_list[0] = [prompt, 0, None, attachments, None, None, 0]
        req_list[1] = ["en"]
        req_list[2] = [cid or "", rid or "", rcid or "", None, None, None, None]
        req_list[6] = [1]
        req_list[7] = 1  # streaming flag
        req_list[10] = 1
        req_list[11] = 0
        req_list[17] = [[0]]
        req_list[18] = 0
        req_list[19] = gem_id
        req_list[27] = 1
        req_list[30] = [4]
        req_list[41] = [1]
        req_list[45] = 0  # not temporary chat
        req_list[53] = 0
        req_list[59] = req_uuid or str(uuid.uuid4()).upper()
        req_list[61] = []
        req_list[68] = 1
        req_list[79] = model_number
        req_list[80] = thinking_flag

        inner_str = json.dumps(req_list, separators=(",", ":"))
        envelope = [None, inner_str]
        f_req = json.dumps(envelope, separators=(",", ":"))
        body = f"at={quote(access_token)}&f.req={quote(f_req)}"
        return "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate", body


class StreamingFrameParser:
    """Parses Google length-prefixed streaming protocol with UTF-16 code unit counting."""

    @staticmethod
    def utf16_code_units(s: str) -> int:
        """Calculate length in UTF-16 code units (JavaScript string.length equivalent)."""
        return len(s.encode("utf-16-le")) // 2

    @classmethod
    def encode_frame(cls, inner_obj: Any) -> str:
        """Format payload into length-prefixed streaming frame."""
        inner_json = json.dumps(inner_obj, separators=(",", ":"))
        envelope = [["wrb.fr", "assistant.lamda.BardFrontendService/StreamGenerate", inner_json]]
        payload = json.dumps(envelope, separators=(",", ":"))
        length = cls.utf16_code_units(payload)
        return f"\n\n{length}\n{payload}\n\n"

    @classmethod
    def parse_stream(cls, raw_stream: str) -> Iterator[Dict[str, Any]]:
        """Parse raw stream output, stripping anti-XSSI and yielding parsed frames."""
        text = raw_stream
        if text.startswith(")]}'"):
            text = text[4:]

        pos = 0
        while pos < len(text):
            # Find length header: (optional newlines followed by digits and newline)
            match = re.search(r"(?:(?:\r?\n)+|^)(\d+)\r?\n", text[pos:])
            if not match:
                break

            header_start = pos + match.start()
            payload_start = pos + match.end()
            expected_units = int(match.group(1))

            # Locate payload by counting UTF-16 code units
            curr_units = 0
            end_idx = payload_start
            while end_idx < len(text):
                ch = text[end_idx]
                code_point = ord(ch)
                curr_units += 2 if code_point > 0xFFFF else 1
                end_idx += 1
                if curr_units == expected_units:
                    break

            if curr_units != expected_units:
                # Incomplete chunk or boundary mismatch
                break

            payload_str = text[payload_start:end_idx]
            pos = end_idx

            try:
                envelope = json.loads(payload_str)
                if isinstance(envelope, list) and len(envelope) > 0 and len(envelope[0]) >= 3:
                    inner_str = envelope[0][2]
                    part_json = json.loads(inner_str)
                    yield cls._extract_part_details(part_json)
            except Exception:
                continue

    @classmethod
    def _extract_part_details(cls, part_json: List[Any]) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "cid": None,
            "rid": None,
            "rcid": None,
            "text": "",
            "thought": None,
            "is_done": False,
            "error_code": None,
            "images": [],
            "citations": [],
        }

        # Check for errors in envelope
        try:
            if len(part_json) > 5 and isinstance(part_json[5], list):
                err_block = part_json[5]
                if len(err_block) > 2 and isinstance(err_block[2], list) and len(err_block[2]) > 0:
                    code = err_block[2][0][1][0]
                    info["error_code"] = code
        except Exception:
            pass

        # Conversation metadata: part_json[1] = [cid, rid, ...]
        if len(part_json) > 1 and isinstance(part_json[1], list):
            meta = part_json[1]
            if len(meta) > 0:
                info["cid"] = meta[0]
            if len(meta) > 1:
                info["rid"] = meta[1]

        # Candidates list: part_json[4]
        if len(part_json) > 4 and isinstance(part_json[4], list) and len(part_json[4]) > 0:
            candidate = part_json[4][0]
            if isinstance(candidate, list):
                if len(candidate) > 0:
                    info["rcid"] = candidate[0]
                if len(candidate) > 1 and isinstance(candidate[1], list) and len(candidate[1]) > 0:
                    info["text"] = candidate[1][0] or ""
                # Turn status: candidate[8][0] == 2 -> completed
                if len(candidate) > 8 and isinstance(candidate[8], list) and len(candidate[8]) > 0:
                    if candidate[8][0] == 2:
                        info["is_done"] = True
                # Thought process: candidate[37][0][0]
                if (
                    len(candidate) > 37
                    and isinstance(candidate[37], list)
                    and len(candidate[37]) > 0
                    and isinstance(candidate[37][0], list)
                    and len(candidate[37][0]) > 0
                ):
                    info["thought"] = candidate[37][0][0]

        return info


class StreamingDeltaAccumulator:
    """Calculates clean incremental text deltas from cumulative streamed responses."""

    def __init__(self):
        self.last_sent_clean = ""

    def calculate_delta(self, cumulative_text: str) -> str:
        # Strip transient trailing escapes and flickering unclosed backticks
        clean = re.sub(r"\\+[`*_~].*$", "", cumulative_text)
        clean = clean.removesuffix("\n```")

        if clean.startswith(self.last_sent_clean):
            delta = clean[len(self.last_sent_clean):]
            self.last_sent_clean = clean
            return delta

        # If not prefix-aligned, use SequenceMatcher
        matcher = difflib.SequenceMatcher(None, self.last_sent_clean, clean)
        match = matcher.find_longest_match(0, len(self.last_sent_clean), 0, len(clean))
        if match.size >= 4:
            delta = clean[match.b + match.size:]
            self.last_sent_clean = clean
            return delta

        # Fallback: full text replacement
        delta = clean
        self.last_sent_clean = clean
        return delta


class ThoughtParser:
    """Extracts and structures reasoning / thought tokens."""

    @staticmethod
    def extract_thoughts(candidate_data: List[Any]) -> Optional[str]:
        if (
            len(candidate_data) > 37
            and isinstance(candidate_data[37], list)
            and len(candidate_data[37]) > 0
            and isinstance(candidate_data[37][0], list)
            and len(candidate_data[37][0]) > 0
        ):
            val = candidate_data[37][0][0]
            return str(val) if val else None
        return None


class MultimodalUploadHandler:
    """Handles content-push uploads and payload formatting."""

    @staticmethod
    def format_upload_response(filename: str, file_hash: str) -> str:
        return f"/contrib_service/ttl_1d/{file_hash}_{filename}"

    @staticmethod
    def format_attachment_payload(resource_uri: str, filename: str) -> List[Any]:
        return [[[resource_uri], filename]]


class MockGeminiServer:
    """Deterministic offline mock server simulating Google Gemini web RPCs."""

    def __init__(self):
        self.auth_cookies = {"__Secure-1PSID": "mock_psid_valid", "__Secure-1PSIDTS": "mock_psidts_valid"}
        self.conversations = [
            {"cid": "c_mock_001", "title": "First mock chat", "turns": 2},
            {"cid": "c_mock_002", "title": "Coding discussion", "turns": 4},
        ]
        self.gems = [
            {"id": "gem_arch_01", "name": "Architect", "prompt": "You are a software architect."},
            {"id": "gem_py_02", "name": "Pythonista", "prompt": "You are an expert Python engineer."},
        ]

    def handle_get_app(self, cookies: Dict[str, str]) -> str:
        # Return HTML with WIZ_global_data
        return """
<!DOCTYPE html>
<html>
<head>
<script>
window.WIZ_global_data = {
  "SNlM0e": "mock_access_token_xyz:1700000000",
  "cfb2h": "boq_assistant-bard-web-server_20250101.00_p0",
  "FdrFJe": "987654321098765432",
  "TuX5cc": "en",
  "qKIAYe": "feeds/mock_push_feed_999"
};
</script>
</head>
<body>Gemini App</body>
</html>
"""

    def handle_rotate_cookies(self, cookies: Dict[str, str]) -> Tuple[int, Dict[str, str], str]:
        if "__Secure-1PSID" not in cookies or not cookies["__Secure-1PSID"]:
            return 401, {}, json.dumps({"error": "Unauthorized"})
        new_ts = f"mock_rotated_ts_{uuid.uuid4().hex[:12]}"
        headers = {
            "Set-Cookie": f"__Secure-1PSIDTS={new_ts}; Domain=.google.com; Path=/; Secure; HttpOnly",
            "Content-Type": "application/json",
        }
        return 200, headers, json.dumps(["gemini", new_ts])

    def handle_batchexecute(self, rpc_id: str, inner_payload: Any) -> str:
        data: Any = []
        if rpc_id == "otAQ7b":  # GET_USER_STATUS / models
            data = [
                1,  # Tier 1
                [["gemini-2.0-flash", 1, 1], ["gemini-2.5-pro", 2, 1], ["gemini-2.0-flash-thinking", 1, 2]],
                15,  # capacity tail
            ]
        elif rpc_id == "MaZiqc":  # LIST_CONVERSATIONS
            data = [[c["cid"], c["title"], None, None, c["turns"]] for c in self.conversations]
        elif rpc_id == "CNgdBe":  # LIST_GEMS
            data = [[g["id"], [g["name"], ""], [g["prompt"]]] for g in self.gems]
        elif rpc_id == "hNvQHb":  # LIST_CONVERSATION_TURNS
            data = [
                ["t_01", "User prompt 1", "user", 1700000000],
                ["t_02", "Assistant response 1", "assistant", 1700000010],
            ]
        else:
            data = ["OK"]

        inner_json = json.dumps(data, separators=(",", ":"))
        envelope = [["wrb.fr", rpc_id, inner_json]]
        return ")]}'\n" + json.dumps(envelope, separators=(",", ":"))

    def handle_stream_generate(
        self, prompt: str, model_id: str = "gemini-2.0-flash", is_thinking: bool = False
    ) -> str:
        cid = f"c_{uuid.uuid4().hex[:8]}"
        rid = f"r_{uuid.uuid4().hex[:8]}"
        rcid = f"rc_{uuid.uuid4().hex[:8]}"

        frames = [")]}'\n"]

        # Chunk 1: Initial delta
        text_1 = "Hello! "
        thought_1 = "Analyzing request..." if is_thinking else None
        p1 = [None, [cid, rid], None, None, [[rcid, [text_1], None, None, None, None, None, None, [1]]]]
        if is_thinking:
            # candidate[37] thought
            p1[4][0].extend([None] * 28)
            p1[4][0].append([[thought_1]])
        frames.append(StreamingFrameParser.encode_frame(p1))

        # Chunk 2: Further text
        text_2 = "Hello! I am Gemini Local."
        p2 = [None, [cid, rid], None, None, [[rcid, [text_2], None, None, None, None, None, None, [1]]]]
        frames.append(StreamingFrameParser.encode_frame(p2))

        # Chunk 3: Final completion
        text_3 = "Hello! I am Gemini Local. Ready to assist!"
        p3 = [None, [cid, rid], None, None, [[rcid, [text_3], None, None, None, None, None, None, [2]]]]
        frames.append(StreamingFrameParser.encode_frame(p3))

        return "".join(frames)
