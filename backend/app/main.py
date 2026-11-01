"""
backend/app/main.py
FastAPI daemon for Gemini Local UI:
- Bound strictly to 127.0.0.1
- Host header verification & CORS restrictions
- Session status and credential management endpoints
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
from typing import Any, AsyncIterator, Dict, Optional
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.app.api.accounts import router as accounts_router
from backend.app.api.chat import router as chat_router
from backend.app.api.claude_plugins import router as claude_plugins_router
from backend.app.api.conversations import router as conversations_router
from backend.app.api.flow import router as flow_router
from backend.app.api.gems import extensions_router, router as gems_router
from backend.app.api.limits import router as limits_router
from backend.app.api.mission_control import router as mission_control_router
from backend.app.api.notebooklm import router as notebooklm_router
from backend.app.api.task_pipeline import router as task_pipeline_router
from backend.routes.stations import legacy_router as stations_legacy_router, router as stations_router
from backend.app.bridge.browser_extractor import BrowserCookieExtractor
from backend.app.bridge.models import DEFAULT_MODELS
from backend.app.bridge.session import SessionHealthReport, SessionHealthStatus, SessionManager
from backend.app.anthropic.routes import router as anthropic_router
from backend.app.mcp.routes import router as mcp_router
from backend.app.mcp.server import MCPServer
from backend.app.openai.routes import router as openai_router
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.search import SearchEngine
from backend.app.storage.vault import CredentialVault

logger = logging.getLogger("gemini_backend")

# Global singletons
vault: CredentialVault = CredentialVault()
session_manager: Optional[SessionManager] = None
db: Database = Database()
repository: ConversationRepository = ConversationRepository(db)
search_engine: SearchEngine = SearchEngine(db)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown lifecycle."""
    global session_manager, db, repository, search_engine, vault
    base_url = os.environ.get("GEMINI_BASE_URL", "https://gemini.google.com")

    if getattr(app.state, "vault", None) is not None:
        vault = app.state.vault

    if getattr(app.state, "session_manager", None) is not None:
        session_manager = app.state.session_manager
    else:
        session_manager = SessionManager(base_url=base_url, vault=vault)

    if getattr(app.state, "db", None) is not None:
        db = app.state.db
    if getattr(app.state, "repository", None) is not None:
        repository = app.state.repository

    # Initialize SQLite database schema
    try:
        await db.init_schema()
    except Exception as e:
        logger.error("Failed initializing database schema: %s", e)

    # Attach shared instances to app.state
    app.state.db = db
    app.state.repository = repository
    app.state.search_engine = search_engine
    app.state.session_manager = session_manager
    app.state.vault = vault
    app.state.mcp_server = MCPServer(session_manager=session_manager)

    # Attempt bootstrapping with existing stored credentials from vault
    try:
        vdata = vault.load_vault_data() if vault else None
        if vdata and vdata.accounts:
            await session_manager.initialize_all()
            logger.info(
                "Session bootstrapped from credential vault with %d account(s) (active: %s)",
                len(session_manager.sessions),
                session_manager.active_account_id,
            )
            if session_manager.get_health().is_authenticated:
                from backend.app.bridge.client import GeminiWebClient
                c = GeminiWebClient(session_context=session_manager.context, base_url=base_url, timeout=15.0)
                try:
                    await c.list_models()
                    remote_list = await c.list_remote_conversations(limit=50)
                    if remote_list:
                        await repository.reconcile_remote_conversations(remote_list)
                except Exception as sync_err:
                    logger.debug("Initial conversation sync skipped: %s", sync_err)
                finally:
                    await c.close()
        else:
            stored_cookies = vault.load_credentials() if vault else None
            if stored_cookies and isinstance(stored_cookies, dict):
                await session_manager.init_session(stored_cookies)
                logger.info("Session bootstrapped from legacy credential vault")
    except Exception as e:
        logger.warning("Could not bootstrap session from vault: %s", e)

    import asyncio as _aio
    from backend.app.bridge.task_pipeline import get_task_pipeline_orchestrator

    async def _task_pipeline_scheduler_loop() -> None:
        while True:
            try:
                await _aio.sleep(30.0)
                orch = get_task_pipeline_orchestrator()
                due = orch.get_due_schedules()
                if due:
                    await orch.trigger_due_schedules()
            except _aio.CancelledError:
                break
            except Exception as loop_err:
                logger.debug("Task pipeline scheduler loop warning: %s", loop_err)

    scheduler_task = _aio.create_task(_task_pipeline_scheduler_loop())

    yield

    scheduler_task.cancel()
    if session_manager:
        await session_manager.close()
    await db.close()


app = FastAPI(
    title="Gemini Local UI API",
    description="Local client and backend bridge daemon for Google Gemini web accounts",
    version="1.3.0",
    lifespan=lifespan,
)

# CORS restriction strictly to local interfaces and browser extensions
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|(chrome|moz)-extension://[a-zA-Z0-9_-]+)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_local_host(request: Request, call_next: Any) -> Response:
    """Security middleware: Enforces local-only Host header validation."""
    raw_host = request.headers.get("host")
    host_header = raw_host.split(":")[0].strip().lower() if raw_host else ""
    allowed_hosts = {"127.0.0.1", "localhost", "testserver"}

    if not host_header or host_header not in allowed_hosts:
        return Response(
            content=b"Invalid Host header: Access restricted to local loopback interface.",
            status_code=status.HTTP_400_BAD_REQUEST,
            media_type="text/plain",
        )

    return await call_next(request)


# Request / Response Schemas
class CredentialsRequest(BaseModel):
    cookies: Optional[str] = None
    auto_import: bool = False


class AdminSessionRequest(BaseModel):
    cookie: Optional[str] = None
    sapisid: Optional[str] = None
    auth_user: Optional[Any] = None
    xsrf_token: Optional[str] = None
    gemini_bl: Optional[str] = None
    account_id: Optional[str] = None


FOREIGN_SERVICE_COOKIES = {"OSID", "__Secure-OSID", "LSID", "__Host-1PLSID", "__Host-3PLSID"}


class HealthResponse(BaseModel):
    status: str


@app.get("/health", response_model=HealthResponse)
@app.get("/healthz", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Basic health check endpoint."""
    return HealthResponse(status="ok")


@app.get("/api/session/status")
async def get_session_status() -> Dict[str, Any]:
    """Returns the current Gemini web session authentication and health diagnostics."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    health = session_manager.get_health()
    vdata = vault.load_vault_data()
    accounts_info = [
        {
            "id": a.id,
            "name": a.name,
            "tier": a.tier,
            "is_active": a.id == session_manager.active_account_id,
            "health_status": (
                session_manager.get_health(a.id).status.value
                if isinstance(session_manager.get_health(a.id).status, SessionHealthStatus)
                else str(session_manager.get_health(a.id).status)
            ),
        }
        for a in vdata.accounts.values()
    ]

    return {
        "authenticated": health.is_authenticated,
        "active_account": session_manager.active_account_id,
        "accounts": accounts_info,
        "health": {
            "status": health.status.value if isinstance(health.status, SessionHealthStatus) else health.status,
            "is_authenticated": health.is_authenticated,
            "has_psid": health.has_psid,
            "has_psidts": health.has_psidts,
            "has_access_token": health.has_access_token,
            "build_label": health.build_label,
            "last_refresh_timestamp": health.last_refresh_timestamp,
            "next_refresh_seconds": health.next_refresh_seconds,
            "account_tier": health.account_tier,
            "error_message": health.error_message,
        },
    }


@app.post("/api/session/credentials")
async def set_credentials(req: CredentialsRequest) -> Dict[str, Any]:
    """
    Ingests session credentials either via auto-extraction from installed browsers
    or from manual paste (raw Cookie header or JSON).
    """
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    cookies_dict: Dict[str, str] = {}
    source = "manual"

    if req.auto_import:
        try:
            cookies_dict, browser = BrowserCookieExtractor.auto_extract()
            source = browser.value
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Auto extraction failed: {e}")
    elif req.cookies:
        cookies_dict = BrowserCookieExtractor.parse_manual(req.cookies)
    else:
        raise HTTPException(status_code=400, detail="Either cookies string or auto_import=True must be provided")

    validation = BrowserCookieExtractor.validate_cookies(cookies_dict)
    if not validation.is_valid:
        raise HTTPException(
            status_code=400,
            detail=validation.error_message or "Invalid cookies: missing __Secure-1PSID",
        )

    # Save to vault
    vault.store_credentials(cookies_dict)

    # Update active session
    target_account_id = session_manager.active_account_id or (vault.get_active_account_id() if vault else "default")
    ctx = await session_manager.init_session(cookies_dict, account_id=target_account_id)
    health = session_manager.get_health()

    if health.is_authenticated:
        from backend.app.bridge.client import GeminiWebClient
        c = GeminiWebClient(
            session_context=session_manager.context,
            base_url=session_manager.base_url,
            timeout=15.0,
        )
        try:
            await c.list_models()
            remote_list = await c.list_remote_conversations(limit=50)
            if remote_list:
                await repository.reconcile_remote_conversations(remote_list)
        except Exception as sync_err:
            logger.debug("Post-auth conversation sync skipped: %s", sync_err)
        finally:
            await c.close()

    return {
        "success": True,
        "source": source,
        "authenticated": health.is_authenticated,
        "health": {
            "status": health.status.value if isinstance(health.status, SessionHealthStatus) else health.status,
            "is_authenticated": health.is_authenticated,
            "has_psid": health.has_psid,
            "has_psidts": health.has_psidts,
            "has_access_token": health.has_access_token,
            "account_tier": session_manager.context.account_tier,
        },
    }


@app.post("/api/session/rotate")
async def rotate_session() -> Dict[str, Any]:
    """Manually triggers a RotateCookies token refresh."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    success = await session_manager.rotate_cookies()
    return {"success": success, "health": session_manager.get_health().status}


@app.delete("/api/session/credentials")
async def clear_credentials() -> Dict[str, Any]:
    """Clears stored credentials and resets active session."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    vault.clear()
    await session_manager.update_cookies({})
    return {"success": True, "message": "Credentials cleared and session reset"}


@app.get("/admin/session")
async def get_admin_session_status() -> Dict[str, Any]:
    """Returns current session status for the Gemini Cookie Sync browser extension."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    health = session_manager.get_health()
    status_val = health.status.value if isinstance(health.status, SessionHealthStatus) else str(health.status)
    return {
        "ok": True,
        "status": "ok" if health.is_authenticated else "unauthenticated",
        "authenticated": health.is_authenticated,
        "account": session_manager.active_account_id,
        "models": ["gemini-4-argon", "gemini-3.8-flash", "gemini-3.5-flash-lite"],
        "health": {
            "status": status_val,
            "is_authenticated": health.is_authenticated,
            "has_psid": health.has_psid,
            "has_psidts": health.has_psidts,
            "has_access_token": health.has_access_token,
            "build_label": health.build_label,
            "last_refresh_timestamp": health.last_refresh_timestamp,
            "next_refresh_seconds": health.next_refresh_seconds,
            "account_tier": health.account_tier,
            "error_message": health.error_message,
        },
    }


@app.post("/admin/session", response_model=None)
async def sync_admin_session(req: AdminSessionRequest) -> Any:
    """Ingests live cookies and WIZ tokens (SNlM0e, cfb2h) pushed by the Gemini Cookie Sync extension."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    if not req.cookie or not req.cookie.strip():
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "status": "error",
                "error": "Cookie string is empty",
                "detail": "Cookie string is empty",
            },
        )

    cookies_dict = BrowserCookieExtractor.parse_manual(req.cookie)

    for foreign_key in FOREIGN_SERVICE_COOKIES:
        cookies_dict.pop(foreign_key, None)

    if req.sapisid and req.sapisid.strip() and "SAPISID" not in cookies_dict:
        cookies_dict["SAPISID"] = req.sapisid.strip()

    validation = BrowserCookieExtractor.validate_cookies(cookies_dict)
    if not validation.is_valid:
        reason = validation.error_message or "missing __Secure-1PSID"
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "status": "error",
                "error": f"Invalid cookies: {reason}",
                "detail": f"Invalid cookies: {reason}",
            },
        )

    target_account_id = req.account_id
    if not target_account_id:
        if session_manager.active_account_id and session_manager.active_account_id != "default":
            target_account_id = session_manager.active_account_id
        elif vault:
            target_account_id = vault.get_active_account_id() or "default"
        else:
            target_account_id = "default"

    vault.store_credentials(cookies_dict)
    await session_manager.init_session(
        cookies_dict,
        account_id=target_account_id,
        xsrf_token=req.xsrf_token,
        gemini_bl=req.gemini_bl,
    )
    session_manager.active_account_id = target_account_id
    health = session_manager.get_health(target_account_id)
    status_val = health.status.value if isinstance(health.status, SessionHealthStatus) else str(health.status)

    return {
        "ok": True,
        "status": "ok",
        "models": ["gemini-4-argon", "gemini-3.8-flash", "gemini-3.5-flash-lite"],
        "skipped": [],
        "account": target_account_id,
        "authenticated": health.is_authenticated,
        "health": {
            "status": status_val,
            "is_authenticated": health.is_authenticated,
            "has_psid": health.has_psid,
            "has_psidts": health.has_psidts,
            "has_access_token": health.has_access_token,
            "build_label": health.build_label,
            "account_tier": session_manager.context.account_tier,
        },
    }


@app.get("/api/models")
async def list_models() -> Dict[str, Any]:
    """Lists available model definitions, combining live otAQ7b account models with Gemini 3.x/4 catalog."""
    from backend.app.bridge.client import GeminiWebClient
    from backend.app.bridge.models import UI_PRIMARY_MODEL_IDS

    seen_ids: set[str] = set()
    models_list = []

    # 1. Primary modern UI models (3.5 Flash-Lite, 3.8 Flash, 3.1 Pro, 4 Argon, Auto Gemini 3)
    for m_id in UI_PRIMARY_MODEL_IDS:
        m = DEFAULT_MODELS.get(m_id)
        if m and m.id not in seen_ids:
            seen_ids.add(m.id)
            models_list.append(
                {
                    "id": m.id,
                    "display_name": m.display_name,
                    "description": m.description,
                    "supports_thinking": m.supports_thinking,
                    "is_default": m.is_default,
                }
            )

    # 2. Dynamically discovered models via otAQ7b if authenticated
    if session_manager and session_manager.get_health().is_authenticated:
        client = GeminiWebClient(
            session_context=session_manager.context,
            base_url=session_manager.base_url,
            timeout=10.0,
        )
        try:
            discovered = await client.list_models()
            for m in discovered:
                if m.id not in seen_ids:
                    seen_ids.add(m.id)
                    models_list.append(
                        {
                            "id": m.id,
                            "display_name": m.display_name,
                            "description": m.description,
                            "supports_thinking": m.supports_thinking,
                            "is_default": False,
                        }
                    )
        except Exception as e:
            logger.debug("Live /api/models discovery skipped: %s", e)
        finally:
            await client.close()

    return {"models": models_list}




@app.get("/api/search")
async def search_messages(
    q: str = "",
    limit: int = 20,
    offset: int = 0,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Instant FTS5 full-text search across messages and thinking traces."""
    results = await search_engine.search(
        query=q,
        limit=limit,
        offset=offset,
        conversation_id=conversation_id,
    )
    return {
        "query": q,
        "count": len(results),
        "results": [r.model_dump() for r in results],
    }


@app.post("/api/sync")
async def sync_remote_alias() -> Dict[str, Any]:
    """Alias for POST /api/conversations/sync."""
    if not session_manager or not session_manager.get_health().is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated with Google Gemini")
    from backend.app.bridge.client import GeminiWebClient
    client = GeminiWebClient(
        session_context=session_manager.context,
        base_url=session_manager.base_url,
        timeout=15.0,
    )
    try:
        remote_list = await client.list_remote_conversations(limit=50)
        result = await repository.reconcile_remote_conversations(remote_list)
        return {"success": True, "synced_count": len(remote_list), **result}
    finally:
        await client.close()


class ImageDownloadRequest(BaseModel):
    url: str
    image_id: Optional[str] = None
    cid: Optional[str] = None
    rid: Optional[str] = None
    rcid: Optional[str] = None
    format: str = "base64"


@app.post("/api/images/download", response_model=None)
async def download_generated_image_endpoint(req: ImageDownloadRequest) -> Any:
    """
    Downloads a Gemini-generated image via `c8o8Fe` (GetFullSizeImage) + `=d-I?alr=yes`
    redirect chain with foreign cookies (OSID, LSID) stripped.
    """
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    import base64
    from fastapi.responses import Response
    from backend.app.bridge.client import GeminiWebClient
    from backend.app.bridge.image_downloader import GeminiImageDownloader

    client = GeminiWebClient(
        session_context=session_manager.context,
        base_url=session_manager.base_url,
        timeout=30.0,
    )
    try:
        downloader = GeminiImageDownloader(
            session_context=session_manager.context,
            client=client,
            timeout=30.0,
        )
        meta = {
            "url": req.url,
            "image_id": req.image_id or "",
            "cid": req.cid or "",
            "rid": req.rid or "",
            "rcid": req.rcid or "",
        }
        img_bytes, mime = await downloader.download_image(meta)
        if not img_bytes:
            raise HTTPException(status_code=502, detail="Failed to download generated image from FIFE")

        if req.format == "raw":
            return Response(content=img_bytes, media_type=mime or "image/png")

        return {
            "ok": True,
            "mime_type": mime or "image/png",
            "size_bytes": len(img_bytes),
            "data_base64": base64.b64encode(img_bytes).decode("ascii"),
        }
    finally:
        await client.close()


@app.get("/files/{name}", response_model=None)
async def serve_local_generated_image(name: str, request: Request) -> Any:
    """
    Serves locally cached Gemini-generated images (`<uuid32>.(png|jpg|webp|gif)`)
    so external OpenAI clients can render Markdown image links without Google cookies.
    """
    from fastapi.responses import Response
    from backend.app.openai.image_store import read_image

    custom_dir = getattr(request.app.state, "image_data_dir", None)
    found = read_image(name, data_dir=custom_dir)
    if not found:
        raise HTTPException(status_code=404, detail="Image not found")
    img_bytes, mime = found
    return Response(
        content=img_bytes,
        media_type=mime,
        headers={"Cache-Control": "public, max-age=86400"},
    )


# Mount API, OpenAI, Anthropic, and MCP routers
app.include_router(conversations_router)
app.include_router(chat_router)
app.include_router(accounts_router)
app.include_router(limits_router)
app.include_router(claude_plugins_router)
app.include_router(gems_router)
app.include_router(extensions_router)
app.include_router(notebooklm_router)
app.include_router(flow_router)
app.include_router(mission_control_router)
app.include_router(task_pipeline_router)
app.include_router(stations_router)
app.include_router(stations_legacy_router)
app.include_router(openai_router)
app.include_router(anthropic_router, prefix="/v1", tags=["anthropic"])
app.include_router(mcp_router, prefix="/mcp", tags=["mcp"])


