"""
backend/app/security/origin_guard.py
Origin and Host validation with dynamic GEMINI_LOCAL_API_TOKEN guard.
"""

from __future__ import annotations

import hmac
import os
import re
from typing import Optional
from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

ALLOWED_HOST_PATTERN = re.compile(
    r"^(localhost|127\.0\.0\.1|testserver|tauri\.localhost)(:\d+)?$",
    re.IGNORECASE,
)

ALLOWED_ORIGIN_PATTERN = re.compile(
    r"^(http://(localhost|127\.0\.0\.1|testserver|tauri\.localhost)(:\d+)?|https://tauri\.localhost(:\d+)?|tauri://localhost|chrome-extension://[a-p0-9]{32}|moz-extension://[a-f0-9-]+)$",
    re.IGNORECASE,
)


def validate_host_and_origin(host: Optional[str], origin: Optional[str]) -> None:
    """Validates Host and Origin headers, raising HTTPException on untrusted domains."""
    if host:
        clean_host = host.split(",")[0].strip()
        if not ALLOWED_HOST_PATTERN.match(clean_host):
            raise HTTPException(status_code=400, detail="Invalid Host header: untrusted domain")

    if origin is not None:
        clean_origin = origin.strip().rstrip("/")
        if clean_origin.lower() == "null" or not ALLOWED_ORIGIN_PATTERN.match(clean_origin):
            raise HTTPException(status_code=403, detail="Forbidden Origin")


def validate_fetch_metadata(request: Request) -> None:
    """Validates Sec-Fetch-Site header when present from modern browsers."""
    sec_site = request.headers.get("sec-fetch-site")
    if sec_site:
        clean_site = sec_site.strip().lower()
        if clean_site not in {"same-origin", "same-site", "none"}:
            raise HTTPException(status_code=403, detail="Forbidden Fetch Site")


def validate_local_api_token(request: Request, token_env_var: str = "GEMINI_LOCAL_API_TOKEN") -> None:
    """Enforces Bearer token authentication on protected endpoints when env var is set."""
    token = os.environ.get(token_env_var, "").strip()
    if not token or request.method.upper() == "OPTIONS":
        return

    path = request.url.path
    if path in ("/health", "/healthz", "/favicon.ico") or path.startswith("/files/"):
        return

    if not (path.startswith("/v1") or path.startswith("/api")):
        return

    auth_header = request.headers.get("authorization", "").strip()
    provided_token = ""
    if auth_header.lower().startswith("bearer "):
        provided_token = auth_header[7:].strip()
    elif "x-gemini-local-token" in request.headers:
        provided_token = request.headers["x-gemini-local-token"].strip()
    elif "x-local-api-token" in request.headers:
        provided_token = request.headers["x-local-api-token"].strip()

    if not provided_token or not hmac.compare_digest(provided_token, token):
        raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing API token")


class OriginAndTokenGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            validate_host_and_origin(
                host=request.headers.get("host"),
                origin=request.headers.get("origin"),
            )
            validate_fetch_metadata(request)
            validate_local_api_token(request)
        except HTTPException as exc:
            return Response(
                content=exc.detail if isinstance(exc.detail, str) else str(exc.detail),
                status_code=exc.status_code,
                media_type="text/plain",
            )
        return await call_next(request)
