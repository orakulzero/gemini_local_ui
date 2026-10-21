"""
backend/app/mock/mock_server.py
Deterministic mock Google Gemini web server for 100% offline verification.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from typing import Any, Dict, List, Optional, Set
from aiohttp import web

from backend.app.mock.fixtures import (
    HTML_APP_PAGE,
    HTML_APP_PAGE_GUEST,
    HTML_NOTEBOOKLM_APP_PAGE,
    PAYLOAD_CNGDBE_GEMS,
    PAYLOAD_CODE_7_PERMISSION_DENIED,
    PAYLOAD_HNVQHB_TURNS,
    PAYLOAD_MAZIQC_CONVERSATIONS,
    PAYLOAD_NOTEBOOKLM_ADD_SOURCE,
    PAYLOAD_NOTEBOOKLM_CREATE_NOTEBOOK,
    PAYLOAD_NOTEBOOKLM_DELETE_NOTEBOOK,
    PAYLOAD_NOTEBOOKLM_GET_NOTEBOOK,
    PAYLOAD_NOTEBOOKLM_LIST_NOTEBOOKS,
    PAYLOAD_NOTEBOOKLM_RENAME_NOTEBOOK,
    PAYLOAD_OTAQ7B_USER_STATUS,
    build_golden_stream_sequence,
    build_stream_error_frame,
    format_batchexecute_envelope,
    format_stream_frame,
)

logger = logging.getLogger("mock_gemini_server")


class MockFaultConfig:
    """Configurable fault injection rules and recorded request log."""

    def __init__(self) -> None:
        self.http_status_override: Dict[str, int] = {}
        self.error_code_override: Dict[str, int] = {}
        self.drop_connection_paths: Set[str] = set()
        self.emit_malformed_json_paths: Set[str] = set()
        self.streaming_delay: float = 0.0
        self.force_expired_session: bool = False
        self.force_quota_exhausted: bool = False
        self.force_model_inconsistent = False
        self.force_notebooklm_offline = False
        self.stream_generate_handler: Optional[Any] = None
        self.recorded_requests: List[Dict[str, Any]] = []

    def reset(self) -> None:
        self.http_status_override.clear()
        self.error_code_override.clear()
        self.drop_connection_paths.clear()
        self.emit_malformed_json_paths.clear()
        self.streaming_delay = 0.0
        self.force_expired_session = False
        self.force_quota_exhausted = False
        self.force_model_inconsistent = False
        self.force_notebooklm_offline = False
        self.stream_generate_handler = None
        self.recorded_requests.clear()



class MockGeminiServer:
    """Deterministic Mock Gemini Web Server runner."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8999) -> None:
        self.host = host
        self.port = port
        self.fault = MockFaultConfig()
        self.app = web.Application()
        self._setup_routes()
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _setup_routes(self) -> None:
        self.app.router.add_get("/", self._handle_notebooklm_root)
        self.app.router.add_get("/notebooklm", self._handle_notebooklm_root)
        self.app.router.add_get("/app", self._handle_app)
        self.app.router.add_post("/RotateCookies", self._handle_rotate_cookies)
        self.app.router.add_post("/_/BardChatUi/data/batchexecute", self._handle_batchexecute)
        self.app.router.add_post("/_/LabsTailwindUi/data/batchexecute", self._handle_notebooklm_batchexecute)
        self.app.router.add_post(
            "/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
            self._handle_stream_generate,
        )
        self.app.router.add_post("/upload", self._handle_upload)


    async def _record_request(self, request: web.Request, body_params: Optional[Dict[str, Any]] = None) -> None:
        rec = {
            "path": request.path,
            "method": request.method,
            "headers": dict(request.headers),
            "params": dict(request.query),
            "body_params": body_params or {},
        }
        self.fault.recorded_requests.append(rec)

    async def _handle_notebooklm_root(self, request: web.Request) -> web.Response:
        await self._record_request(request)
        if self.fault.force_notebooklm_offline:
            return web.Response(status=503, text="Service Unavailable")
        return web.Response(text=HTML_NOTEBOOKLM_APP_PAGE, content_type="text/html", charset="utf-8")

    async def _handle_app(self, request: web.Request) -> web.Response:

        await self._record_request(request)

        cookies_header = request.headers.get("Cookie", "")
        if self.fault.force_expired_session or "__Secure-1PSID" not in cookies_header:
            return web.Response(text=HTML_APP_PAGE_GUEST, content_type="text/html", charset="utf-8")

        return web.Response(text=HTML_APP_PAGE, content_type="text/html", charset="utf-8")

    async def _handle_rotate_cookies(self, request: web.Request) -> web.Response:
        await self._record_request(request)

        cookies_header = request.headers.get("Cookie", "")
        if self.fault.force_expired_session or "__Secure-1PSID" not in cookies_header:
            return web.Response(status=401, text=json.dumps({"error": "Unauthorized"}), content_type="application/json")

        headers = {
            "Set-Cookie": (
                "__Secure-1PSIDTS=rotated_test_psidts_token_999999; "
                "Domain=.google.com; Path=/; Secure; HttpOnly; SameSite=None"
            )
        }
        return web.Response(
            text=json.dumps(["gemini", "active"]),
            headers=headers,
            content_type="application/json",
        )

    async def _handle_batchexecute(self, request: web.Request) -> web.Response:
        post_data = await request.post()
        body_dict = dict(post_data)
        await self._record_request(request, body_dict)

        rpc_ids = request.query.get("rpcids", "")
        if not rpc_ids:
            # Parse from f.req
            f_req = body_dict.get("f.req", "")
            try:
                parsed_outer = json.loads(f_req)
                if parsed_outer and isinstance(parsed_outer, list) and len(parsed_outer[0]) > 0:
                    rpc_ids = parsed_outer[0][0][0]
            except Exception:
                pass

        if self.fault.force_expired_session:
            wire_resp = format_batchexecute_envelope(rpc_ids or "error", PAYLOAD_CODE_7_PERMISSION_DENIED)
            return web.Response(text=wire_resp, content_type="application/json")

        # Route to appropriate payload
        if "otAQ7b" in rpc_ids:
            wire_resp = format_batchexecute_envelope("otAQ7b", PAYLOAD_OTAQ7B_USER_STATUS)
        elif "MaZiqc" in rpc_ids:
            wire_resp = format_batchexecute_envelope("MaZiqc", PAYLOAD_MAZIQC_CONVERSATIONS)
        elif "hNvQHb" in rpc_ids:
            wire_resp = format_batchexecute_envelope("hNvQHb", PAYLOAD_HNVQHB_TURNS)
        elif "CNgdBe" in rpc_ids:
            wire_resp = format_batchexecute_envelope("CNgdBe", PAYLOAD_CNGDBE_GEMS)
        elif "GzXR5e" in rpc_ids:
            wire_resp = format_batchexecute_envelope("GzXR5e", [True])
        else:
            wire_resp = format_batchexecute_envelope(rpc_ids or "unknown", [])

        return web.Response(text=wire_resp, content_type="application/json")

    async def _handle_notebooklm_batchexecute(self, request: web.Request) -> web.Response:
        post_data = await request.post()
        body_dict = dict(post_data)
        await self._record_request(request, body_dict)

        if self.fault.force_notebooklm_offline:
            return web.Response(status=503, text="Service Unavailable")

        rpc_ids = request.query.get("rpcids", "")
        f_req = body_dict.get("f.req", "")
        inner_args: List[Any] = []
        if f_req:
            try:
                parsed_outer = json.loads(f_req)
                if parsed_outer and isinstance(parsed_outer, list) and len(parsed_outer) > 0 and len(parsed_outer[0]) > 0:
                    entry = parsed_outer[0][0]
                    if not rpc_ids:
                        rpc_ids = entry[0]
                    if len(entry) > 1 and entry[1]:
                        inner_args = json.loads(entry[1])
            except Exception:
                pass

        if self.fault.force_expired_session:
            wire_resp = format_batchexecute_envelope(rpc_ids or "error", PAYLOAD_CODE_7_PERMISSION_DENIED)
            return web.Response(text=wire_resp, content_type="application/json")

        if "wXbhsf" in rpc_ids:
            wire_resp = format_batchexecute_envelope("wXbhsf", PAYLOAD_NOTEBOOKLM_LIST_NOTEBOOKS)
        elif "rLM1Ne" in rpc_ids:
            nb_id = str(inner_args[0]) if inner_args and len(inner_args) > 0 else "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d"
            detail_payload = [
                [nb_id, "Quantum Computing & Shor's Algorithm", [1729180000, 0], [1729184500, 0]],
                PAYLOAD_NOTEBOOKLM_GET_NOTEBOOK[1],
                PAYLOAD_NOTEBOOKLM_GET_NOTEBOOK[2],
            ]
            wire_resp = format_batchexecute_envelope("rLM1Ne", detail_payload)
        elif "CCqFvf" in rpc_ids:
            title = str(inner_args[0]) if inner_args and len(inner_args) > 0 else "New Test Notebook"
            created_payload = [f"nb_{abs(hash(title)) % 100000000}", title, [1729185000, 0]]
            wire_resp = format_batchexecute_envelope("CCqFvf", created_payload)
        elif "WWINqb" in rpc_ids:
            wire_resp = format_batchexecute_envelope("WWINqb", PAYLOAD_NOTEBOOKLM_DELETE_NOTEBOOK)
        elif "s0tc2d" in rpc_ids:
            nb_id = str(inner_args[0]) if inner_args and len(inner_args) > 0 else "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d"
            new_title = str(inner_args[1]) if inner_args and len(inner_args) > 1 else "Renamed Notebook Title"
            wire_resp = format_batchexecute_envelope("s0tc2d", [nb_id, new_title])
        elif "trScbf" in rpc_ids:
            src_title = "Exported Research Transcript"
            src_content = ""
            if inner_args and len(inner_args) > 1 and isinstance(inner_args[1], list):
                src_title = str(inner_args[1][0]) if len(inner_args[1]) > 0 else src_title
                src_content = str(inner_args[1][1]) if len(inner_args[1]) > 1 else ""
            src_payload = [
                f"src_{abs(hash(src_title)) % 1000000}",
                src_title,
                1,
                None,
                src_content[:200] if src_content else "Source summary text exported from Gemini chat turn.",
                len(src_content.split()) if src_content else 1500,
                [1729186000, 0],
            ]
            wire_resp = format_batchexecute_envelope("trScbf", src_payload)
        else:
            wire_resp = format_batchexecute_envelope(rpc_ids or "unknown", [])

        return web.Response(text=wire_resp, content_type="application/json")

    async def _handle_stream_generate(self, request: web.Request) -> web.StreamResponse:
        if self.fault.stream_generate_handler is not None:
            res = self.fault.stream_generate_handler(request)
            if asyncio.iscoroutine(res):
                return await res
            return res

        post_data = await request.post()
        body_dict = dict(post_data)
        await self._record_request(request, body_dict)

        # Inspect prompt from f.req
        prompt = ""
        feature_mode_inner = None
        f_req = body_dict.get("f.req", "")
        if f_req:
            try:
                outer = json.loads(f_req)
                if outer and isinstance(outer, list) and len(outer) > 1 and outer[1]:
                    inner = json.loads(outer[1])
                    if inner and isinstance(inner, list) and len(inner) > 0 and isinstance(inner[0], list):
                        prompt = str(inner[0][0])
                    if inner and isinstance(inner, list) and len(inner) > 49:
                        feature_mode_inner = inner[49]
            except Exception:
                pass

        # Check status overrides
        if "StreamGenerate" in self.fault.http_status_override:
            status = self.fault.http_status_override["StreamGenerate"]
            return web.Response(status=status, text=f"HTTP Error {status}")

        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={"Content-Type": "text/plain;charset=UTF-8"},
        )
        await response.prepare(request)

        # Check fault triggers
        cookie_header = request.headers.get("Cookie", "")
        if self.fault.force_quota_exhausted or (
            "[TRIGGER_QUOTA_ERROR]" in prompt and "backup" not in cookie_header
        ):
            err_frame = build_stream_error_frame(1037, "Usage limit exceeded")
            await response.write(err_frame)
            await response.write_eof()
            return response

        if "[TRIGGER_QUOTA_1061]" in prompt and "backup" not in cookie_header:
            err_frame = build_stream_error_frame(1061, "Resource quota exhausted")
            await response.write(err_frame)
            await response.write_eof()
            return response

        if "[TRIGGER_TASK_1061]" in prompt and feature_mode_inner == 40:
            err_frame = build_stream_error_frame(1061, "Resource quota exhausted (task container)")
            await response.write(err_frame)
            await response.write_eof()
            return response

        if "[TRIGGER_TASK_1173]" in prompt and feature_mode_inner == 40:
            err_frame = build_stream_error_frame(1173, "Stream interrupted (task container)")
            await response.write(err_frame)
            await response.write_eof()
            return response

        if self.fault.force_model_inconsistent or "[TRIGGER_MODEL_ERROR]" in prompt:
            err_frame = build_stream_error_frame(1050, "Model inconsistent across turns")
            await response.write(err_frame)
            await response.write_eof()
            return response

        if "[TRIGGER_OVERLOAD]" in prompt:
            err_frame = build_stream_error_frame(1013, "Temporary backend overload")
            await response.write(err_frame)
            await response.write_eof()
            return response

        if "[TRIGGER_PERMISSION_DENIED]" in prompt:
            err_frame = build_stream_error_frame(7, "Permission denied: Session cookies revoked")
            await response.write(err_frame)
            await response.write_eof()
            return response

        if "[TRIGGER_IP_BLOCK]" in prompt:
            err_frame = build_stream_error_frame(1060, "Source IP temporarily blocked")
            await response.write(err_frame)
            await response.write_eof()
            return response

        if "[TRIGGER_CORRUPT_FRAME]" in prompt:
            await response.write(b"\n\n9999\n[CORRUPT_JSON_DATA\n\n")
            await response.write_eof()
            return response

        if "[TRIGGER_EXTRACT_CODE]" in prompt:
            code_text = (
                "Here is the generated file:\n\n"
                "### File: generated/solution.py\n"
                "```python\n"
                "print('generated solution')\n"
                "```\n"
            )
            part_code = [None, ["c1", "r1", "m1"], None, None, [["m1", [code_text], None, None, None, None, None, None, [2]]]]
            await response.write(format_stream_frame(part_code))
            await response.write_eof()
            return response

        # Standard golden streaming frames
        chunks = build_golden_stream_sequence()
        for chunk in chunks:
            await response.write(chunk)
            if self.fault.streaming_delay > 0:
                await asyncio.sleep(self.fault.streaming_delay)

        await response.write_eof()
        return response

    async def _handle_upload(self, request: web.Request) -> web.Response:
        push_id = request.headers.get("Push-ID", "")
        body_params = {"Push-ID": push_id}
        await self._record_request(request, body_params)

        reader = await request.multipart()
        field = await reader.next()
        # Read the file data
        if field:
            await field.read()

        return web.Response(
            text="/contrib_service/ttl_1d/mock_uploaded_resource_hash_897123",
            content_type="text/plain",
            charset="utf-8",
        )

    async def start_async(self) -> None:
        """Async lifecycle start for asyncio test loops."""
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        logger.info("Mock Gemini server running on http://%s:%s", self.host, self.port)

    async def stop_async(self) -> None:
        """Async lifecycle stop releasing socket immediately."""
        if self._runner:
            runner = self._runner
            self._runner = None
            self._site = None
            await runner.cleanup()

    def start_background(self) -> None:
        """Starts server in a dedicated background daemon thread."""
        self._loop = asyncio.new_event_loop()

        def _target() -> None:
            assert self._loop is not None
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self.start_async())
            try:
                self._loop.run_forever()
            finally:
                if self._runner:
                    try:
                        self._loop.run_until_complete(self.stop_async())
                    except Exception:
                        pass
                if not self._loop.is_closed():
                    try:
                        self._loop.close()
                    except Exception:
                        pass

        self._thread = threading.Thread(target=_target, daemon=True)
        self._thread.start()

    def stop_background(self) -> None:
        """Stops background daemon thread and releases port 8999 immediately."""
        if self._loop and self._loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self.stop_async(), self._loop)
                future.result(timeout=5.0)
            except Exception as exc:
                logger.warning("Error awaiting stop_async in background mock loop: %s", exc)
            self._loop.call_soon_threadsafe(self._loop.stop)
        elif self._loop and not self._loop.is_closed():
            try:
                self._loop.run_until_complete(self.stop_async())
            except Exception as exc:
                logger.warning("Error running stop_async on unclosed loop: %s", exc)

        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._loop = None
