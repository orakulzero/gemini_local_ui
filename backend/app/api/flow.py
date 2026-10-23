"""
backend/app/api/flow.py
FastAPI router for Google Flow Educational Video Studio (`/api/flow/*`):
- Session & capability discovery (`/api/flow/status`)
- Pedagogical storyboard creation (`/api/flow/projects/storyboard`)
- Project listing & retrieval (`/api/flow/projects`, `/api/flow/projects/{project_id}`)
- Scene editing & Veo clip generation (`/api/flow/projects/{project_id}/scenes/{scene_id}`)
- Full educational video assembly + WebVTT / SRT subtitle tracks & MP4 media streaming
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.app.bridge.flow import (
    DEFAULT_VOICE_STYLE,
    SUPPORTED_ASPECT_RATIOS,
    SUPPORTED_MODELS,
    SUPPORTED_VOICE_PERSONAS,
    FlowClient,
    build_srt_subtitles,
    build_webvtt_subtitles,
)

router = APIRouter(prefix="/api/flow", tags=["flow"])


class StoryboardCreateRequest(BaseModel):
    topic: str
    scene_count: int = Field(default=4, ge=1, le=12)
    language: str = "en"
    target_audience: str = "intermediate"
    aspect_ratio: str = "16:9"
    source_context: Optional[str] = None
    model_key: str = "veo_3_1_t2v_fast_audio"
    voice_persona: str = "Kore"
    voice_style: str = DEFAULT_VOICE_STYLE
    native_veo_audio: bool = True


class ScenePatchRequest(BaseModel):
    title: Optional[str] = None
    visual_prompt: Optional[str] = None
    narration_text: Optional[str] = None
    duration_sec: Optional[int] = None
    camera_motion: Optional[str] = None
    on_screen_bullets: Optional[List[str]] = None
    voice_persona: Optional[str] = None
    voice_style: Optional[str] = None
    native_veo_audio: Optional[bool] = None


class SceneGenerateRequest(BaseModel):
    prompt_override: Optional[str] = None
    aspect_ratio: Optional[str] = None


def _get_flow_client(request: Request) -> FlowClient:
    session_mgr = getattr(request.app.state, "session_manager", None)
    cookies: Dict[str, str] = {}
    if session_mgr and getattr(session_mgr, "context", None):
        cookies = dict(getattr(session_mgr.context, "cookies", {}) or {})
    return FlowClient(cookies=cookies)


@router.get("/status")
async def get_flow_status(request: Request) -> Dict[str, Any]:
    """Return Google Flow (Veo 3.1) session status and supported studio capabilities."""
    client = _get_flow_client(request)
    return {
        "ok": True,
        "engine": "google-flow-veo",
        "flow_url": "https://labs.google/fx/tools/flow",
        "authenticated": client.has_flow_session(),
        "native_veo_audio_supported": True,
        "supported_aspect_ratios": SUPPORTED_ASPECT_RATIOS,
        "supported_models": SUPPORTED_MODELS,
        "supported_voice_personas": SUPPORTED_VOICE_PERSONAS,
        "project_count": len(client.list_projects()),
    }


@router.post("/projects/storyboard")
async def create_educational_storyboard(
    req: StoryboardCreateRequest, request: Request
) -> Dict[str, Any]:
    """Create a multi-scene educational video storyboard for a topic."""
    client = _get_flow_client(request)
    try:
        project = await client.generate_educational_storyboard(
            topic=req.topic,
            scene_count=req.scene_count,
            language=req.language,
            target_audience=req.target_audience,
            aspect_ratio=req.aspect_ratio,
            source_context=req.source_context,
            model_key=req.model_key,
            voice_persona=req.voice_persona,
            voice_style=req.voice_style,
            native_veo_audio=req.native_veo_audio,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "ok": True,
        "project": project.to_dict(),
    }


@router.get("/projects")
async def list_flow_projects(request: Request) -> Dict[str, Any]:
    """List all Flow educational video projects in the current workspace."""
    client = _get_flow_client(request)
    projects = [p.to_dict() for p in client.list_projects()]
    return {
        "ok": True,
        "count": len(projects),
        "projects": projects,
    }


@router.get("/projects/{project_id}")
async def get_flow_project(project_id: str, request: Request) -> Dict[str, Any]:
    """Retrieve a specific Flow educational video project by ID."""
    client = _get_flow_client(request)
    project = client.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return {
        "ok": True,
        "project": project.to_dict(),
    }


@router.patch("/projects/{project_id}/scenes/{scene_id}")
async def patch_flow_scene(
    project_id: str,
    scene_id: str,
    req: ScenePatchRequest,
    request: Request,
) -> Dict[str, Any]:
    """Update a scene's title, visual prompt, narration script, or duration."""
    client = _get_flow_client(request)
    try:
        scene = client.update_scene(
            project_id=project_id,
            scene_id=scene_id,
            title=req.title,
            visual_prompt=req.visual_prompt,
            narration_text=req.narration_text,
            duration_sec=req.duration_sec,
            camera_motion=req.camera_motion,
            on_screen_bullets=req.on_screen_bullets,
            voice_persona=req.voice_persona,
            voice_style=req.voice_style,
            native_veo_audio=req.native_veo_audio,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    project = client.get_project(project_id)
    return {
        "ok": True,
        "scene": scene.to_dict(),
        "project": project.to_dict() if project else None,
    }


@router.post("/projects/{project_id}/scenes/{scene_id}/generate")
async def generate_flow_scene_clip(
    project_id: str,
    scene_id: str,
    req: SceneGenerateRequest,
    request: Request,
) -> Dict[str, Any]:
    """Generate a video clip for a single scene in a Flow educational project."""
    client = _get_flow_client(request)
    try:
        return await client.generate_scene_clip(
            project_id=project_id,
            scene_id=scene_id,
            prompt_override=req.prompt_override,
            aspect_ratio=req.aspect_ratio,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/assemble")
async def assemble_flow_project(project_id: str, request: Request) -> Dict[str, Any]:
    """Render any pending scenes, synthesize WebVTT/SRT subtitles, and assemble the full MP4 lesson."""
    client = _get_flow_client(request)
    try:
        return await client.assemble_educational_video(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/projects/{project_id}/subtitles.vtt", response_model=None)
async def get_project_subtitles_vtt(project_id: str, request: Request) -> Response:
    """Serve the WebVTT (.vtt) subtitle track for an educational video project."""
    client = _get_flow_client(request)
    project = client.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    vtt_text = project.subtitles_vtt or build_webvtt_subtitles(project.scenes)
    return Response(content=vtt_text, media_type="text/vtt; charset=utf-8")


@router.get("/projects/{project_id}/subtitles.srt", response_model=None)
async def get_project_subtitles_srt(project_id: str, request: Request) -> Response:
    """Serve the SubRip (.srt) subtitle track for an educational video project."""
    client = _get_flow_client(request)
    project = client.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    srt_text = project.subtitles_srt or build_srt_subtitles(project.scenes)
    return Response(content=srt_text, media_type="application/x-subrip; charset=utf-8")


@router.get("/media/{media_name}", response_model=None)
async def serve_flow_media(media_name: str, request: Request) -> Response:
    """Stream generated scene clips or assembled educational lesson MP4 files."""
    client = _get_flow_client(request)
    mp4_bytes = client.get_media_bytes(media_name)
    if not mp4_bytes:
        raise HTTPException(status_code=404, detail=f"Media not found: {media_name}")
    return Response(
        content=mp4_bytes,
        media_type="video/mp4",
        headers={"Cache-Control": "no-cache"},
    )
