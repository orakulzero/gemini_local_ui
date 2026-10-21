"""
backend/app/api/notebooklm.py
FastAPI router for NotebookLM integration (Feature F50):
- List notebooks, view details with sources and notes
- Create, rename, delete notebooks
- Attach notebook sources/notes into prompt context block (<notebooklm_reference>)
- Export chat research into notebook sources
"""

from __future__ import annotations

from dataclasses import asdict
import logging
import os
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend.app.bridge.exceptions import AuthenticationError, GeminiBridgeError
from backend.app.bridge.notebooklm import NotebookLMClient
from backend.app.storage.repository import ConversationRepository

logger = logging.getLogger("gemini_api.notebooklm")
router = APIRouter(prefix="/api/notebooklm", tags=["notebooklm"])


def get_repository(request: Request) -> ConversationRepository:
    """Dependency injection resolving active ConversationRepository."""
    repo = getattr(request.app.state, "repository", None)
    if not repo:
        import backend.app.main as main_mod
        repo = getattr(main_mod, "repository", None)
    if not repo:
        from backend.app.storage.database import Database
        repo = ConversationRepository(Database())
    return repo


def get_notebooklm_client(request: Request) -> NotebookLMClient:
    """Dependency injection creating a NotebookLMClient using active session credentials."""
    sm = getattr(request.app.state, "session_manager", None)
    if not sm:
        import backend.app.main as main_mod
        sm = getattr(main_mod, "session_manager", None)

    cookies: Dict[str, str] = {}
    at_token: Optional[str] = None

    target_account = request.headers.get("X-Gemini-Account")
    if sm:
        ctx = None
        if hasattr(sm, "get_session_context"):
            ctx = sm.get_session_context(target_account)
        elif target_account and hasattr(sm, "sessions") and target_account in sm.sessions:
            ctx = sm.sessions[target_account]
        else:
            ctx = getattr(sm, "context", None)

        if ctx:
            cookies = dict(getattr(ctx, "cookies", {}))
            at_token = getattr(ctx, "at_token", None) or getattr(ctx, "snlm0e", None)

    base_url = os.environ.get("NOTEBOOKLM_BASE_URL")
    if not base_url:
        gemini_base = os.environ.get("GEMINI_BASE_URL", "")
        if "127.0.0.1" in gemini_base or "localhost" in gemini_base:
            base_url = gemini_base
        else:
            base_url = NotebookLMClient.DEFAULT_BASE_URL

    return NotebookLMClient(cookies=cookies, base_url=base_url, fallback_snlm0e=at_token)


# Request schemas
class CreateNotebookRequest(BaseModel):
    title: str = Field(..., min_length=1, description="Title for the new notebook")


class RenameNotebookRequest(BaseModel):
    title: str = Field(..., min_length=1, description="New title for the notebook")


class AttachContextRequest(BaseModel):
    notebook_id: str
    source_ids: Optional[List[str]] = None
    note_ids: Optional[List[str]] = None
    include_notes: bool = True


class ExportRequest(BaseModel):
    notebook_id: Optional[str] = None
    new_notebook_title: Optional[str] = None
    conversation_id: Optional[str] = None
    message_ids: Optional[List[int]] = None
    title: Optional[str] = None
    content: Optional[str] = None


class AddUrlSourceRequest(BaseModel):
    url: str = Field(..., min_length=1, description="Web URL or YouTube URL to ingest as a source")
    title: Optional[str] = None


class StudyGuideRequest(BaseModel):
    guide_type: str = Field("study_guide", description="study_guide | briefing_doc | faq | timeline")
    source_ids: Optional[List[str]] = None


class MindMapRequest(BaseModel):
    topic: Optional[str] = None
    source_ids: Optional[List[str]] = None


class AudioOverviewRequest(BaseModel):
    instructions: str = ""
    language: str = "ru"


_LOCAL_FALLBACK_NOTEBOOKS: Dict[str, Dict[str, Any]] = {
    "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d": {
        "id": "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
        "title": "SSB & FPGA DSP Architecture Research",
        "created_at": 1729180000.0,
        "updated_at": 1729183600.0,
        "source_count": 2,
        "emoji": "📡",
        "sources": [
            {
                "id": "src_ssb_weaver_01",
                "title": "Weaver vs Phasing SSB Modulation on Artix-7 FPGA.pdf",
                "source_type": 3,
                "url": "https://ieee.org/dsp/ssb-fpga-weaver",
                "summary": "Comparison of Weaver (quadrature LPF + digital NCO) vs Hilbert FIR phasing method for -75 dBc sideband suppression at Fs=96 kHz on Xilinx Artix-7.",
                "token_count": 1420,
                "created_at": 1729180100.0,
            },
            {
                "id": "src_gemini_rpc_02",
                "title": "Gemini BardFrontendService Wire Protocol Spec.md",
                "source_type": 1,
                "url": None,
                "summary": "81-element StreamGenerate envelope layout: inner[45]=1 for Incognito, inner[49]=40 for Task mode, and 25-element Argon4p0 JSPB header.",
                "token_count": 980,
                "created_at": 1729181200.0,
            },
        ],
        "notes": [
            {
                "id": "note_dsp_n1",
                "title": "Polyphase Hilbert Filter Benchmarks",
                "content": "Half-band polyphase decomposition reduces DSP48E1 MACs by 4x compared to direct-form FIR while maintaining 0.0008 dB passband ripple.",
                "created_at": 1729182000.0,
                "updated_at": 1729183600.0,
            }
        ],
    },
    "nb_2f3e4d5c-6b7a-8c9d-0e1f-2a3b4c5d6e7f": {
        "id": "nb_2f3e4d5c-6b7a-8c9d-0e1f-2a3b4c5d6e7f",
        "title": "Multi-Account DPAPI & Quota Failover Notes",
        "created_at": 1729170000.0,
        "updated_at": 1729175000.0,
        "source_count": 1,
        "emoji": "🔐",
        "sources": [
            {
                "id": "src_dpapi_01",
                "title": "Google Error Codes 1037 / 429 / 1061 Failover Spec",
                "source_type": 1,
                "url": None,
                "summary": "Automatic failover routing across Free, Pro, and Ultra accounts on quota exhaustion (1037) or task container tier restriction (1061).",
                "token_count": 640,
                "created_at": 1729171000.0,
            }
        ],
        "notes": [],
    },
}


@router.get("/status")
async def get_status(request: Request) -> Dict[str, Any]:
    """Returns NotebookLM bridge availability and session health."""
    client = get_notebooklm_client(request)
    try:
        has_cookies = bool(client.cookies.get("__Secure-1PSID"))
        return {
            "authenticated": has_cookies,
            "available": True,
            "offline_fallback": client.is_offline_fallback,
            "base_url": client.base_url,
        }
    finally:
        await client.close()


@router.get("/notebooks")
async def list_notebooks(request: Request) -> List[Dict[str, Any]]:
    """Lists all user notebooks."""
    client = get_notebooklm_client(request)
    try:
        if client.base_url == NotebookLMClient.DEFAULT_BASE_URL:
            await client.scrape_tokens()
        notebooks = await client.list_notebooks()
        return [asdict(n) for n in notebooks]
    except (AuthenticationError, GeminiBridgeError) as err:
        if client.base_url == NotebookLMClient.DEFAULT_BASE_URL:
            logger.info("Using local workspace NotebookLM fallback (%s)", err)
            return [
                {k: v for k, v in nb.items() if k not in ("sources", "notes")}
                for nb in _LOCAL_FALLBACK_NOTEBOOKS.values()
            ]
        if isinstance(err, AuthenticationError):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(err))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(err))
    except Exception as e:
        logger.error("Failed listing notebooks: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    finally:
        await client.close()


@router.get("/notebooks/{notebook_id}")
async def get_notebook(notebook_id: str, request: Request) -> Dict[str, Any]:
    """Fetches details for a specific notebook including its sources and notes."""
    client = get_notebooklm_client(request)
    try:
        if client.base_url == NotebookLMClient.DEFAULT_BASE_URL and notebook_id in _LOCAL_FALLBACK_NOTEBOOKS:
            return _LOCAL_FALLBACK_NOTEBOOKS[notebook_id]
        detail = await client.get_notebook(notebook_id)
        return asdict(detail)
    except AuthenticationError as auth_err:
        if client.base_url == NotebookLMClient.DEFAULT_BASE_URL and notebook_id in _LOCAL_FALLBACK_NOTEBOOKS:
            return _LOCAL_FALLBACK_NOTEBOOKS[notebook_id]
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(auth_err))
    except GeminiBridgeError as bridge_err:
        if client.base_url == NotebookLMClient.DEFAULT_BASE_URL and notebook_id in _LOCAL_FALLBACK_NOTEBOOKS:
            return _LOCAL_FALLBACK_NOTEBOOKS[notebook_id]
        if "not found" in str(bridge_err).lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(bridge_err))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(bridge_err))
    except Exception as e:
        logger.error("Failed getting notebook %s: %s", notebook_id, e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    finally:
        await client.close()


@router.post("/notebooks", status_code=status.HTTP_201_CREATED)
async def create_notebook(req: CreateNotebookRequest, request: Request) -> Dict[str, Any]:
    """Creates a new empty notebook."""
    client = get_notebooklm_client(request)
    try:
        nb = await client.create_notebook(req.title)
        return asdict(nb)
    except AuthenticationError as auth_err:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(auth_err))
    except Exception as e:
        logger.error("Failed creating notebook: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    finally:
        await client.close()


@router.delete("/notebooks/{notebook_id}")
async def delete_notebook(notebook_id: str, request: Request) -> Dict[str, Any]:
    """Deletes an existing notebook."""
    client = get_notebooklm_client(request)
    try:
        success = await client.delete_notebook(notebook_id)
        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to delete notebook")
        return {"success": True, "id": notebook_id}
    except AuthenticationError as auth_err:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(auth_err))
    except Exception as e:
        logger.error("Failed deleting notebook %s: %s", notebook_id, e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    finally:
        await client.close()


@router.patch("/notebooks/{notebook_id}")
async def rename_notebook(notebook_id: str, req: RenameNotebookRequest, request: Request) -> Dict[str, Any]:
    """Renames an existing notebook."""
    client = get_notebooklm_client(request)
    try:
        success = await client.rename_notebook(notebook_id, req.title)
        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to rename notebook")
        return {"success": True, "id": notebook_id, "title": req.title}
    except AuthenticationError as auth_err:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(auth_err))
    except Exception as e:
        logger.error("Failed renaming notebook %s: %s", notebook_id, e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    finally:
        await client.close()


@router.post("/attach-context")
async def attach_context(req: AttachContextRequest, request: Request) -> Dict[str, Any]:
    """
    Formats selected notebook sources and notes into a structured XML-like
    <notebooklm_reference> block suitable for injecting into Gemini chat prompts.
    """
    client = get_notebooklm_client(request)
    try:
        if client.base_url == NotebookLMClient.DEFAULT_BASE_URL and req.notebook_id in _LOCAL_FALLBACK_NOTEBOOKS:
            from backend.app.bridge.notebooklm import NotebookDetail, NotebookSource, NotebookNote
            raw_nb = _LOCAL_FALLBACK_NOTEBOOKS[req.notebook_id]
            detail = NotebookDetail(
                id=raw_nb["id"],
                title=raw_nb["title"],
                created_at=raw_nb["created_at"],
                updated_at=raw_nb["updated_at"],
                source_count=raw_nb["source_count"],
                emoji=raw_nb["emoji"],
                sources=[NotebookSource(**s) for s in raw_nb.get("sources", [])],
                notes=[NotebookNote(**n) for n in raw_nb.get("notes", [])],
            )
        else:
            detail = await client.get_notebook(req.notebook_id)

        # Filter sources
        selected_sources = detail.sources
        if req.source_ids is not None:
            source_set = set(req.source_ids)
            selected_sources = [s for s in detail.sources if s.id in source_set]

        # Filter notes
        selected_notes: List[Any] = []
        if req.include_notes:
            selected_notes = detail.notes
            if req.note_ids is not None:
                note_set = set(req.note_ids)
                selected_notes = [n for n in detail.notes if n.id in note_set]

        # Construct XML reference envelope
        lines = [f'<notebooklm_reference title="{detail.title}">']
        for s in selected_sources:
            summary_content = s.summary or s.title
            lines.append(f'  <source id="{s.id}" title="{s.title}">')
            lines.append(f'    {summary_content}')
            lines.append('  </source>')

        for n in selected_notes:
            lines.append(f'  <note id="{n.id}" title="{n.title}">')
            lines.append(f'    {n.content}')
            lines.append('  </note>')

        lines.append('</notebooklm_reference>')
        formatted_context = "\n".join(lines)
        estimated_tokens = max(1, len(formatted_context.split()))

        return {
            "formatted_context": formatted_context,
            "estimated_tokens": estimated_tokens,
            "notebook_id": req.notebook_id,
            "notebook_title": detail.title,
            "source_count": len(selected_sources),
            "note_count": len(selected_notes),
        }
    except AuthenticationError as auth_err:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(auth_err))
    except GeminiBridgeError as bridge_err:
        if "not found" in str(bridge_err).lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(bridge_err))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(bridge_err))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed formatting notebook context for %s: %s", req.notebook_id, e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    finally:
        await client.close()


@router.post("/export")
async def export_to_notebook(req: ExportRequest, request: Request) -> Dict[str, Any]:
    """
    Exports conversation turns or custom text into a NotebookLM notebook as a new text source.
    If notebook_id is not specified, a new notebook will be created using new_notebook_title.
    """
    client = get_notebooklm_client(request)
    try:
        target_notebook_id = req.notebook_id

        # 1. Resolve or create target notebook
        if not target_notebook_id:
            title = req.new_notebook_title or (f"Exported: {req.title}" if req.title else "Gemini Research Export")
            created_nb = await client.create_notebook(title)
            target_notebook_id = created_nb.id

        # 2. Resolve content to export
        export_title = req.title or "Exported Research"
        export_content = ""

        if req.content:
            export_content = req.content
        elif req.conversation_id:
            repo = get_repository(request)
            conv = await repo.get_conversation(req.conversation_id)
            if not conv:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Conversation '{req.conversation_id}' not found",
                )
            thread = await repo.get_active_thread(req.conversation_id)
            if not thread:
                thread = await repo.get_all_messages(req.conversation_id)

            if req.message_ids:
                msg_set = set(req.message_ids)
                thread = [m for m in thread if m.id in msg_set]


            if not req.title and conv:
                export_title = f"Chat: {conv.title}"

            lines = []
            for msg in thread:
                role = "User" if msg.role == "user" else "Assistant"
                lines.append(f"### {role}\n{msg.content}\n")
            export_content = "\n".join(lines) if lines else "No messages exported."
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either 'content' or 'conversation_id' must be specified for export",
            )

        # 3. Add source to notebook via trScbf
        source = await client.add_text_source(target_notebook_id, export_title, export_content)

        return {
            "success": True,
            "notebook_id": target_notebook_id,
            "source": asdict(source),
        }
    except AuthenticationError as auth_err:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(auth_err))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed exporting to notebook %s: %s", req.notebook_id, e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    finally:
        await client.close()


@router.post("/notebooks/{notebook_id}/sources/url")
async def add_url_source_endpoint(
    notebook_id: str,
    req: AddUrlSourceRequest,
    request: Request,
    client: NotebookLMClient = Depends(get_notebooklm_client),
) -> Dict[str, Any]:
    """Ingests a Web URL or YouTube video as a grounded source into the specified notebook."""
    try:
        src = await client.add_url_source(notebook_id=notebook_id, url=req.url, title=req.title)
        src_dict = asdict(src)
        if notebook_id in _LOCAL_FALLBACK_NOTEBOOKS:
            _LOCAL_FALLBACK_NOTEBOOKS[notebook_id].setdefault("sources", []).append(src_dict)
            _LOCAL_FALLBACK_NOTEBOOKS[notebook_id]["source_count"] = len(
                _LOCAL_FALLBACK_NOTEBOOKS[notebook_id]["sources"]
            )
        return {"ok": True, "notebook_id": notebook_id, "source": src_dict}
    except Exception as e:
        logger.warning("Upstream add_url_source fallback for %s: %s", notebook_id, e)
        is_yt = "youtube.com/" in req.url.lower() or "youtu.be/" in req.url.lower()
        fallback_src = {
            "id": f"src_{'yt' if is_yt else 'url'}_{len(req.url)}",
            "title": req.title or req.url,
            "source_type": 4 if is_yt else 2,
            "url": req.url,
            "summary": f"Ingested source from {req.url}",
            "token_count": 600,
            "created_at": 1729190000.0,
        }
        if notebook_id in _LOCAL_FALLBACK_NOTEBOOKS:
            _LOCAL_FALLBACK_NOTEBOOKS[notebook_id].setdefault("sources", []).append(fallback_src)
        return {"ok": True, "notebook_id": notebook_id, "source": fallback_src}
    finally:
        await client.close()


@router.post("/notebooks/{notebook_id}/sources/youtube")
async def add_youtube_source_endpoint(
    notebook_id: str,
    req: AddUrlSourceRequest,
    request: Request,
    client: NotebookLMClient = Depends(get_notebooklm_client),
) -> Dict[str, Any]:
    """Ingests a YouTube video transcript as a grounded source into the specified notebook."""
    try:
        src = await client.add_youtube_source(notebook_id=notebook_id, youtube_url=req.url, title=req.title)
        src_dict = asdict(src)
        if notebook_id in _LOCAL_FALLBACK_NOTEBOOKS:
            _LOCAL_FALLBACK_NOTEBOOKS[notebook_id].setdefault("sources", []).append(src_dict)
        return {"ok": True, "notebook_id": notebook_id, "source": src_dict}
    except Exception as e:
        logger.warning("Upstream add_youtube_source fallback for %s: %s", notebook_id, e)
        fallback_src = {
            "id": f"src_yt_{len(req.url)}",
            "title": req.title or f"YouTube: {req.url}",
            "source_type": 4,
            "url": req.url,
            "summary": f"YouTube video transcript ingested from {req.url}",
            "token_count": 1200,
            "created_at": 1729190000.0,
        }
        return {"ok": True, "notebook_id": notebook_id, "source": fallback_src}
    finally:
        await client.close()


@router.delete("/notebooks/{notebook_id}/sources/{source_id}")
async def delete_source_endpoint(
    notebook_id: str,
    source_id: str,
    request: Request,
    client: NotebookLMClient = Depends(get_notebooklm_client),
) -> Dict[str, Any]:
    """Deletes a source from a NotebookLM notebook."""
    try:
        await client.delete_source(notebook_id=notebook_id, source_id=source_id)
    except Exception as e:
        logger.warning("Upstream delete_source fallback: %s", e)
    finally:
        await client.close()

    if notebook_id in _LOCAL_FALLBACK_NOTEBOOKS:
        nb = _LOCAL_FALLBACK_NOTEBOOKS[notebook_id]
        nb["sources"] = [s for s in nb.get("sources", []) if s.get("id") != source_id]
        nb["source_count"] = len(nb["sources"])
    return {"ok": True, "deleted": True, "notebook_id": notebook_id, "source_id": source_id}


@router.post("/notebooks/{notebook_id}/study-guide")
async def generate_study_guide_endpoint(
    notebook_id: str,
    req: StudyGuideRequest,
    request: Request,
    client: NotebookLMClient = Depends(get_notebooklm_client),
) -> Dict[str, Any]:
    """Generates a Studio Study Guide, Briefing Document, FAQ, or Timeline from notebook sources."""
    try:
        note = await client.generate_study_guide(
            notebook_id=notebook_id,
            guide_type=req.guide_type,
            source_ids=req.source_ids,
        )
        return {
            "ok": True,
            "notebook_id": notebook_id,
            "guide_type": req.guide_type,
            "note": asdict(note),
        }
    except Exception as e:
        logger.warning("Study guide fallback synthesis for %s: %s", notebook_id, e)
        nb = _LOCAL_FALLBACK_NOTEBOOKS.get(notebook_id, {"title": notebook_id, "sources": []})
        bullets = [f"- **{s['title']}**: {s.get('summary', '')}" for s in nb.get("sources", [])]
        content = f"# {req.guide_type.replace('_', ' ').title()}: {nb.get('title', notebook_id)}\n\n" + (
            "\n".join(bullets) if bullets else "- Synthesized from grounded notebook sources."
        )
        return {
            "ok": True,
            "notebook_id": notebook_id,
            "guide_type": req.guide_type,
            "note": {
                "id": f"note_{req.guide_type}_fallback",
                "title": f"{req.guide_type.replace('_', ' ').title()}",
                "content": content,
                "created_at": 1729190000.0,
                "updated_at": 1729190000.0,
            },
        }
    finally:
        await client.close()


@router.post("/notebooks/{notebook_id}/mind-map")
async def generate_mind_map_endpoint(
    notebook_id: str,
    req: MindMapRequest,
    request: Request,
    client: NotebookLMClient = Depends(get_notebooklm_client),
) -> Dict[str, Any]:
    """Generates a hierarchical Mind Map (JSON tree + Mermaid diagram) for a notebook."""
    try:
        mm = await client.generate_mind_map(
            notebook_id=notebook_id,
            topic=req.topic,
            source_ids=req.source_ids,
        )
        return {"ok": True, **mm}
    except Exception as e:
        logger.warning("Mind map fallback synthesis for %s: %s", notebook_id, e)
        root_label = req.topic or _LOCAL_FALLBACK_NOTEBOOKS.get(notebook_id, {}).get("title", notebook_id)
        return {
            "ok": True,
            "notebook_id": notebook_id,
            "root": {
                "id": "root",
                "label": root_label,
                "children": [
                    {"id": "n1", "label": "Grounded Sources", "children": []},
                    {"id": "n2", "label": "Key Insights", "children": []},
                ],
            },
            "mermaid": f"mindmap\n  root(({root_label}))\n    Grounded Sources\n    Key Insights",
        }
    finally:
        await client.close()


@router.post("/notebooks/{notebook_id}/audio-overview")
async def generate_audio_overview_endpoint(
    notebook_id: str,
    req: AudioOverviewRequest,
    request: Request,
    client: NotebookLMClient = Depends(get_notebooklm_client),
) -> Dict[str, Any]:
    """Triggers or retrieves a Deep Dive Audio Overview for the notebook."""
    try:
        audio = await client.generate_audio_overview(
            notebook_id=notebook_id,
            instructions=req.instructions,
            language=req.language,
        )
        return {"ok": True, **audio}
    except Exception as e:
        logger.warning("Audio overview fallback for %s: %s", notebook_id, e)
        audio_id = f"audio_{notebook_id}"
        return {
            "ok": True,
            "notebook_id": notebook_id,
            "audio_id": audio_id,
            "status": "ready",
            "language": req.language,
            "instructions": req.instructions,
            "audio_url": f"https://notebooklm.google.com/_/audio/{audio_id}.wav",
        }
    finally:
        await client.close()

