"""
backend/app/bridge/wreq_transport.py
Wreq TLS/HTTP transport adapter providing Chrome 145 TLS fingerprinting.
"""

from __future__ import annotations

import datetime
import inspect
import json
import logging
import time
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Union
import wreq

logger = logging.getLogger("gemini_bridge.wreq")


class HeadersAdapter:
    def __init__(self, raw_headers: Any) -> None:
        self._raw = raw_headers

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        val = self._raw.get(name)
        return default if val is None else (val if isinstance(val, str) else bytes(val).decode("latin-1"))

    def get_list(self, name: str) -> List[str]:
        if hasattr(self._raw, "get_all"):
            return [it if isinstance(it, str) else bytes(it).decode("utf-8", "replace") for it in self._raw.get_all(name)]
        single = self.get(name)
        return [single] if single is not None else []

    def __getitem__(self, name: str) -> str:
        val = self.get(name)
        if val is None:
            raise KeyError(name)
        return val


class WreqRequestAdapter:
    def __init__(self, method: str, url: str, headers: Optional[Dict[str, str]] = None, extensions: Optional[Dict[str, Any]] = None) -> None:
        self.method, self.url = method, url
        self.headers, self.extensions = headers or {}, extensions or {}


class WreqResponseAdapter:
    def __init__(self, resp: Any, url: str = "", request: Optional[WreqRequestAdapter] = None) -> None:
        self._resp = resp
        self.url = url or str(getattr(resp, "url", "") or "")
        self.status_code: int = resp.status.as_int() if hasattr(resp.status, "as_int") else int(resp.status)
        self.headers = HeadersAdapter(resp.headers)
        self.request = request or WreqRequestAdapter("GET", self.url)
        self._content: Optional[bytes] = None

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308)

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        if self._content is not None:
            yield self._content
            return
        stream_fn = getattr(self._resp, "stream", None)
        if stream_fn is not None:
            stream_obj = stream_fn()
            if hasattr(stream_obj, "__aiter__"):
                async for chunk in stream_obj:
                    yield bytes(chunk)
            elif hasattr(stream_obj, "__iter__"):
                for chunk in stream_obj:
                    yield bytes(chunk)
            return
        if hasattr(self._resp, "aiter_bytes"):
            stream_obj = self._resp.aiter_bytes()
            if inspect.isawaitable(stream_obj):
                stream_obj = await stream_obj
            if hasattr(stream_obj, "__aiter__"):
                async for chunk in stream_obj:
                    yield bytes(chunk)
            elif hasattr(stream_obj, "__iter__"):
                for chunk in stream_obj:
                    yield bytes(chunk)

    async def aread(self) -> bytes:
        if self._content is None:
            chunks = [ch async for ch in self.aiter_bytes()]
            self._content = b"".join(chunks)
            await self.aclose()
        return self._content

    async def read(self) -> bytes:
        return await self.aread()

    @property
    def content(self) -> bytes:
        return self._content or b""

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.text)

    async def aclose(self) -> None:
        close_fn = getattr(self._resp, "close", None)
        if close_fn:
            res = close_fn()
            if inspect.isawaitable(res):
                await res


class WreqStreamContext:
    def __init__(self, client: WreqTransportClient, method: str, url: str, kwargs: Dict[str, Any]) -> None:
        self.client, self.method, self.url, self.kwargs = client, method, url, kwargs
        self.adapter: Optional[WreqResponseAdapter] = None

    async def __aenter__(self) -> WreqResponseAdapter:
        kw = dict(self.kwargs, stream=True)
        self.adapter = await self.client._execute_request(self.method, self.url, **kw)
        return self.adapter

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.adapter:
            await self.adapter.aclose()


class WreqTransportClient:
    def __init__(self, timeout: Union[float, int, datetime.timedelta, Any] = 60.0, on_response: Optional[Callable[[WreqResponseAdapter], Any]] = None) -> None:
        self._timeout_sec: float = self._parse_timeout(timeout)
        self.on_response = on_response
        self._client: Optional[wreq.Client] = None
        self._is_closed = False
        self._last_used_monotonic: float = 0.0
        self._transport: Any = "wreq_transport"

    def _parse_timeout(self, t: Any) -> float:
        if isinstance(t, (int, float)):
            return float(t)
        if isinstance(t, datetime.timedelta):
            return t.total_seconds()
        return float(getattr(t, "read", 60.0) or 60.0) if hasattr(t, "read") else 60.0

    @property
    def timeout(self) -> float:
        return self._timeout_sec

    @timeout.setter
    def timeout(self, value: Any) -> None:
        self._timeout_sec = self._parse_timeout(value)
        self._client = None

    def get_transport_name(self) -> str:
        return "wreq"

    @property
    def is_closed(self) -> bool:
        return self._is_closed

    def _get_raw_client(self) -> wreq.Client:
        now = time.monotonic()
        if self._client is None or self._is_closed or (self._last_used_monotonic > 0 and (now - self._last_used_monotonic > 35.0)):
            self._client = wreq.Client(
                emulation=wreq.Emulation.Chrome145,
                timeout=datetime.timedelta(seconds=self._timeout_sec),
                pool_idle_timeout=datetime.timedelta(seconds=35),
                http2_keep_alive_interval=datetime.timedelta(seconds=20),
                http2_keep_alive_timeout=datetime.timedelta(seconds=10),
                http2_keep_alive_while_idle=True,
                pool_max_idle_per_host=4,
            )
            self._is_closed = False
        self._last_used_monotonic = now
        return self._client

    async def _execute_request(
        self, method: str, url: str, params: Optional[Dict[str, Any]] = None,
        data: Any = None, content: Any = None, headers: Optional[Dict[str, str]] = None,
        timeout: Any = None, extensions: Optional[Dict[str, Any]] = None,
        stream: bool = False, **kwargs: Any,
    ) -> WreqResponseAdapter:
        cli = self._get_raw_client()
        t_sec = self._parse_timeout(timeout) if timeout is not None else self._timeout_sec
        td = datetime.timedelta(seconds=t_sec)
        form_data = data if isinstance(data, dict) else None
        body_data = data if isinstance(data, (str, bytes)) else None
        if body_data is None and content is not None:
            body_data = content if isinstance(content, (str, bytes)) else bytes(content)
        m = method.upper()
        if m == "POST":
            raw = await cli.post(url, query=params, form=form_data, body=body_data, headers=headers, timeout=td)
        elif m == "GET":
            raw = await cli.get(url, query=params, headers=headers, timeout=td)
        else:
            raw = await cli.request(m, url, query=params, form=form_data, body=body_data, headers=headers, timeout=td)
        req_adapter = WreqRequestAdapter(m, str(url), headers=headers, extensions=extensions)
        adapter = WreqResponseAdapter(raw, url=str(url), request=req_adapter)
        if not stream:
            await adapter.aread()
        if self.on_response:
            try:
                res = self.on_response(adapter)
                if inspect.isawaitable(res):
                    await res
            except Exception as e:
                logger.debug("on_response hook error: %s", e)
        return adapter

    def stream(self, method: str, url: str, **kwargs: Any) -> WreqStreamContext:
        return WreqStreamContext(self, method, url, kwargs)

    async def post(self, url: str, **kwargs: Any) -> WreqResponseAdapter:
        return await self._execute_request("POST", url, **kwargs)

    async def get(self, url: str, **kwargs: Any) -> WreqResponseAdapter:
        return await self._execute_request("GET", url, **kwargs)

    async def request(self, method: str, url: str, **kwargs: Any) -> WreqResponseAdapter:
        return await self._execute_request(method, url, **kwargs)

    async def aclose(self) -> None:
        self._is_closed, self._client = True, None

    async def close(self) -> None:
        await self.aclose()
