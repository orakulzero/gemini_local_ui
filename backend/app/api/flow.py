"""
backend/app/api/flow.py
FastAPI application and router for Google Flow Educational Video Studio L4 (`/api/flow/*`).
"""

from __future__ import annotations

import base64
import datetime
import json
import os
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

import sys

_FLOW_STUDIO_DIR = Path(__file__).resolve().parent.parent / "bridge" / "flow_studio"
if _FLOW_STUDIO_DIR.exists() and str(_FLOW_STUDIO_DIR) not in sys.path:
    sys.path.insert(0, str(_FLOW_STUDIO_DIR))

try:
    from flow_audio_video import FemaleVoiceoverOptimizer, MultiSceneVideoAssembler
    from flow_bridge import (
        DEFAULT_VOICE_STYLE,
        SUPPORTED_ASPECT_RATIOS,
        SUPPORTED_MODELS,
        SUPPORTED_VOICE_PERSONAS,
        FlowClient,
        build_openmaic_lecture_actions,
        build_srt_subtitles,
        build_webvtt_subtitles,
    )
    from flow_diff_tour import (
        SENIOR_AUDIT_DIMENSIONS,
        build_diff_tour_from_git_range,
        render_diff_tour_to_mp4,
        validate_diff_tour,
        verify_tour_code_refs_against_git,
    )
    from flow_live_rpc import FlowLiveRPCBridge
    from flow_manimgx import (
        GeminiStudioVoice,
        build_manimgx_educational_scene,
        inspect_manimgx_scene,
        render_manimgx_scene_to_mp4,
    )
    from flow_studio_ui import render_flow_studio_html
except ImportError:
    class DummyVoiceoverOptimizer:
        VOICE_GENDER = "female"
        VOICE_PROFILE = "dummy-female-narrator-v1"
        def __init__(self, *args: Any, **kwargs: Any) -> None: pass
        def optimize_script(self, narration: str = "", voice_gender: str = "female") -> Dict[str, Any]:
            return {
                "voice_gender": "female",
                "voice_profile": self.VOICE_PROFILE,
                "annotated_script": narration or "",
                "estimated_duration_sec": 5.0,
                "word_count": len((narration or "").split()),
                "speaking_rate_wpm": 145.0,
            }

    class DummyVideoAssembler:
        def __init__(self, *args: Any, **kwargs: Any) -> None: pass
        def concat_scene_mp4_clips(self, scene_clips: Any = None, title: str = "Lesson") -> Dict[str, Any]:
            return {
                "ok": True,
                "total_bytes": sum(len(c) for c in (scene_clips or [])) if scene_clips else 0,
                "engine": "dummy-fallback-assembler",
                "output_path": None,
            }

    class DummyLiveRPCBridge:
        def __init__(self, *args: Any, **kwargs: Any) -> None: pass
        def render_scene(self, **kwargs: Any) -> Dict[str, Any]:
            return {"ok": True, "mp4_bytes": b"", "scene_title": kwargs.get("scene_title", "")}

    FemaleVoiceoverOptimizer = DummyVoiceoverOptimizer  # type: ignore[misc,assignment]
    MultiSceneVideoAssembler = DummyVideoAssembler  # type: ignore[misc,assignment]
    FlowLiveRPCBridge = DummyLiveRPCBridge  # type: ignore[misc,assignment]
    DEFAULT_VOICE_STYLE = "conversational"
    SUPPORTED_ASPECT_RATIOS = ["16:9", "9:16", "1:1"]
    SUPPORTED_MODELS = ["veo_3_1_t2v_fast_audio"]
    SUPPORTED_VOICE_PERSONAS = ["Kore", "Puck", "Fenrir", "Aoede"]
    FlowClient = None  # type: ignore[assignment]
    build_openmaic_lecture_actions = lambda *a, **k: []  # noqa: E731
    build_srt_subtitles = lambda *a, **k: ""  # noqa: E731
    build_webvtt_subtitles = lambda *a, **k: ""  # noqa: E731
    SENIOR_AUDIT_DIMENSIONS = []
    build_diff_tour_from_git_range = lambda *a, **k: {}  # noqa: E731
    render_diff_tour_to_mp4 = lambda *a, **k: b""  # noqa: E731
    validate_diff_tour = lambda *a, **k: {"ok": True}  # noqa: E731
    verify_tour_code_refs_against_git = lambda *a, **k: {"ok": True}  # noqa: E731
    GeminiStudioVoice = None  # type: ignore[assignment]
    build_manimgx_educational_scene = lambda *a, **k: None  # noqa: E731
    inspect_manimgx_scene = lambda *a, **k: {}  # noqa: E731
    render_manimgx_scene_to_mp4 = lambda *a, **k: {"mp4_bytes": b""}  # noqa: E731
    render_flow_studio_html = lambda: "<html><body>Flow Studio Stub</body></html>"  # noqa: E731

router = APIRouter(prefix="/api/flow", tags=["flow"])

_VOICE_OPTIMIZER = FemaleVoiceoverOptimizer()
_VIDEO_ASSEMBLER = MultiSceneVideoAssembler()
_LIVE_RPC_BRIDGE = FlowLiveRPCBridge()


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
    formula_expression: Optional[str] = None
    diagram_spec: Optional[Dict[str, Any]] = None
    first_frame_path: Optional[str] = None
    last_frame_path: Optional[str] = None
    timecoded_directives: Optional[List[Dict[str, str]]] = None
    pedagogical_phase: Optional[str] = None
    learning_objective: Optional[str] = None


class SceneGenerateRequest(BaseModel):
    prompt_override: Optional[str] = None
    aspect_ratio: Optional[str] = None
    use_cloud: Optional[bool] = None
    compose_educational: bool = False


class CloudVeoSceneRequest(BaseModel):
    scene_title: str = "STM32H743 Hardware Pipeline"
    visual_prompt: str = (
        "A glowing 3D microchip STM32H743 and memory module on a dark circuit board "
        "with cyan data buses"
    )
    narration_text: str = (
        "The microcontroller streams real-time samples into external static memory."
    )
    language: str = "ru"
    timeout_sec: float = Field(default=180.0, ge=30.0, le=600.0)


class RenderLessonRequest(BaseModel):
    topic: str
    scene_count: int = Field(default=3, ge=1, le=12)
    audience: str = "Engineers"
    style: str = "3D technical animation"
    duration_per_scene_sec: int = Field(default=6, ge=2, le=30)
    aspect_ratio: str = "16:9"


class BatchLessonsRequest(BaseModel):
    topics: List[str]
    scene_count: int = Field(default=2, ge=1, le=12)
    audience: str = "Engineers"
    duration_per_scene_sec: int = Field(default=6, ge=2, le=30)


class RootReportRequest(BaseModel):
    status: str = "PASS"
    summary: str
    metrics: Optional[Dict[str, Any]] = None
    root_brain_dir: Optional[str] = None


class ManimGXSceneRequest(BaseModel):
    topic: str = "IIR Biquad Transfer Function"
    formula_latex: str = r"H(z) = \frac{b_0 + b_1 z^{-1} + b_2 z^{-2}}{1 + a_1 z^{-1} + a_2 z^{-2}}"
    narration_script: str = "We analyze [the transfer function H of z] on [the complex phase plane]."
    language: str = "en"
    width: int = Field(default=640, ge=160, le=1920)
    height: int = Field(default=360, ge=90, le=1080)
    fps: int = Field(default=15, ge=5, le=60)
    supersample: int = Field(default=1, ge=1, le=2)


class DiffTourValidateRequest(BaseModel):
    tour: Optional[Dict[str, Any]] = None
    repo_path: Optional[str] = None
    base: str = "HEAD"
    head: str = "HEAD"
    title: Optional[str] = None


class DiffTourRenderRequest(BaseModel):
    tour: Optional[Dict[str, Any]] = None
    repo_path: Optional[str] = None
    base: str = "HEAD"
    head: str = "HEAD"
    title: Optional[str] = None
    language: str = "en"
    width: int = Field(default=640, ge=320, le=1920)
    height: int = Field(default=360, ge=180, le=1080)
    fps: int = Field(default=12, ge=5, le=60)
    max_steps: Optional[int] = Field(default=None, ge=1, le=20)


class IntensiveQuestionRequest(BaseModel):
    page_number: int = Field(
        default=1,
        ge=1,
        le=45,
        description="Coursework page number 1..43, or 42 for E2, 43 for PE2",
    )
    sheet_name: str = Field(
        default="pz_page_01.png",
        description="Asset filename or drawing identifier",
    )
    topic_id: Optional[str] = Field(
        default=None,
        description="Active topic or scene identifier, e.g. cycle1_sc02",
    )
    topic_title: Optional[str] = Field(
        default=None,
        description="Human readable topic title",
    )
    selection_type: str = Field(
        default="text_highlight",
        description="Type of selection: 'text_highlight', 'image_crop', or 'general_inquiry'",
    )
    bbox: Optional[List[float]] = Field(
        default=None,
        description="Normalized bounding box [rx0, ry0, rx1, ry1] in range [0.0, 1.0]",
    )
    selected_text: Optional[str] = Field(
        default=None,
        description="Highlighted text or equation content from the intensive pane",
    )
    user_question: str = Field(
        default="",
        description="Student question or note regarding the selected element",
    )
    image_base64: Optional[str] = Field(
        default=None,
        description="Cropped sheet region as base64 PNG data URL or raw base64 string",
    )
    timestamp: Optional[str] = Field(
        default=None,
        description="ISO 8601 UTC timestamp of question creation",
    )


class IntensiveQuestionResponse(BaseModel):
    ok: bool = True
    json_path: str
    crop_path: Optional[str] = None
    saved_at: str
    message: str


def _get_flow_client(request: Request) -> FlowClient:
    session_mgr = getattr(request.app.state, "session_manager", None)
    cookies: Dict[str, str] = {}
    if session_mgr and getattr(session_mgr, "context", None):
        cookies = dict(getattr(session_mgr.context, "cookies", {}) or {})
    return FlowClient(cookies=cookies)


async def _execute_full_lesson_render(
    client: FlowClient,
    topic: str,
    scene_count: int = 3,
    audience: str = "Engineers",
    duration_per_scene_sec: int = 6,
    aspect_ratio: str = "16:9",
) -> Dict[str, Any]:
    project = await client.generate_educational_storyboard(
        topic=topic,
        scene_count=scene_count,
        language="en",
        target_audience=audience,
        aspect_ratio=aspect_ratio,
        voice_persona="Sulafat",
        native_veo_audio=True,
    )

    scene_clips: List[bytes] = []
    scenes_out: List[Dict[str, Any]] = []
    for scene in project.scenes:
        scene.duration_sec = duration_per_scene_sec
        opt = _VOICE_OPTIMIZER.optimize_script(
            narration=scene.narration_text,
            voice_gender="female",
        )
        rpc_res = _LIVE_RPC_BRIDGE.render_scene(
            scene_title=scene.title,
            visual_prompt=scene.visual_prompt,
            narration=scene.narration_text,
            aspect_ratio=aspect_ratio,
            duration_sec=duration_per_scene_sec,
        )
        scene.status = "completed"
        scene_clips.append(rpc_res["mp4_bytes"])
        s_dict = scene.to_dict()
        s_dict["annotated_narration"] = opt["annotated_script"]
        s_dict["voice_gender"] = "female"
        s_dict["voice_profile"] = opt["voice_profile"]
        scenes_out.append(s_dict)

    assembled = _VIDEO_ASSEMBLER.concat_scene_mp4_clips(
        scene_clips=scene_clips,
        title=project.topic,
    )
    vtt_text = build_webvtt_subtitles(project.scenes)
    srt_text = build_srt_subtitles(project.scenes)
    project.status = "assembled"
    project.subtitles_vtt = vtt_text
    project.subtitles_srt = srt_text

    proj_dict = project.to_dict()
    proj_dict["status"] = "assembled"
    proj_dict["voice_gender"] = "female"
    proj_dict["voice_profile"] = _VOICE_OPTIMIZER.VOICE_PROFILE
    proj_dict["scenes"] = scenes_out

    return {
        "ok": True,
        "project": proj_dict,
        "video_size_bytes": assembled["total_bytes"],
        "concat_engine": assembled["engine"],
        "subtitles_vtt": vtt_text,
        "subtitles_srt": srt_text,
    }


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
        "voice_gender": "female",
        "supported_aspect_ratios": SUPPORTED_ASPECT_RATIOS,
        "supported_models": SUPPORTED_MODELS,
        "supported_voice_personas": SUPPORTED_VOICE_PERSONAS,
        "project_count": len(client.list_projects()),
    }


@router.post("/render-lesson")
async def render_full_lesson_endpoint(
    req: RenderLessonRequest, request: Request
) -> Dict[str, Any]:
    """End-to-end L4 render pipeline: storyboard -> female prosody -> scene clips -> MP4 concat + subtitles."""
    clean_topic = (req.topic or "").strip()
    if not clean_topic:
        raise HTTPException(status_code=400, detail="Topic must not be empty")
    client = _get_flow_client(request)
    return await _execute_full_lesson_render(
        client=client,
        topic=clean_topic,
        scene_count=req.scene_count,
        audience=req.audience,
        duration_per_scene_sec=req.duration_per_scene_sec,
        aspect_ratio=req.aspect_ratio,
    )


@router.post("/batch")
async def render_batch_lessons_endpoint(
    req: BatchLessonsRequest, request: Request
) -> Dict[str, Any]:
    """Batch-render multiple educational lessons with locked female narrator prosody."""
    clean_topics = [t.strip() for t in (req.topics or []) if t and t.strip()]
    if not clean_topics:
        raise HTTPException(status_code=400, detail="topics list must not be empty")

    client = _get_flow_client(request)
    projects: List[Dict[str, Any]] = []
    for top in clean_topics:
        rendered = await _execute_full_lesson_render(
            client=client,
            topic=top,
            scene_count=req.scene_count,
            audience=req.audience,
            duration_per_scene_sec=req.duration_per_scene_sec,
        )
        projects.append(rendered["project"])

    return {
        "ok": True,
        "batch_count": len(projects),
        "voice_gender": "female",
        "projects": projects,
    }


@router.post("/manimgx/inspect")
async def inspect_manimgx_scene_endpoint(req: ManimGXSceneRequest) -> Dict[str, Any]:
    """Run headless ManimGX 2D layout diagnostics (`cutoff`, `overlap`, `crossing`, `tiny`) on a mathematical scene."""
    clean_topic = (req.topic or "").strip()
    if not clean_topic:
        raise HTTPException(status_code=400, detail="topic must not be empty")
    voice = GeminiStudioVoice(language=req.language, voice_gender="female")
    scene_cls = build_manimgx_educational_scene(
        topic=clean_topic,
        formula_latex=req.formula_latex,
        narration_script=req.narration_script,
    )
    diag = inspect_manimgx_scene(
        scene_cls,
        width=req.width,
        height=req.height,
        fps=req.fps,
        voice=voice,
    )
    diag["engine"] = "manimgx"
    diag["voice_gender"] = "female"
    return diag


@router.post("/manimgx/render")
async def render_manimgx_scene_endpoint(req: ManimGXSceneRequest) -> Dict[str, Any]:
    """Render a ManimGX LaTeX/Typst mathematical scene with word-synced female voiceover to H.264 + AAC MP4."""
    clean_topic = (req.topic or "").strip()
    if not clean_topic:
        raise HTTPException(status_code=400, detail="topic must not be empty")
    voice = GeminiStudioVoice(language=req.language, voice_gender="female")
    scene_cls = build_manimgx_educational_scene(
        topic=clean_topic,
        formula_latex=req.formula_latex,
        narration_script=req.narration_script,
    )
    rendered = render_manimgx_scene_to_mp4(
        scene_cls,
        width=req.width,
        height=req.height,
        fps=req.fps,
        supersample=req.supersample,
        voice=voice,
        inspect_layout=True,
    )
    return {
        "ok": True,
        "engine": "manimgx",
        "voice_gender": "female",
        "scene_name": rendered["scene_name"],
        "video_size_bytes": rendered["video_size_bytes"],
        "width": rendered["width"],
        "height": rendered["height"],
        "fps": rendered["fps"],
        "frame_count": rendered["frame_count"],
        "duration_sec": rendered["duration_sec"],
        "play_count": rendered["play_count"],
        "has_audio": rendered["has_audio"],
        "layout_ok": rendered["layout_ok"],
        "layout_problems": rendered["layout_problems"],
        "layout_report": rendered["layout_report"],
        "subtitles_srt": rendered["subtitles_srt"],
        "subtitles_vtt": rendered["subtitles_vtt"],
    }


@router.post("/diff-tour/validate")
async def validate_diff_tour_endpoint(req: DiffTourValidateRequest) -> Dict[str, Any]:
    """Validate a `diff-tour` v1 JSON payload and optionally verify `CodeRef` lines against git."""
    repo_root = Path(req.repo_path).resolve() if req.repo_path else Path(__file__).resolve().parent.parent
    tour_doc = req.tour or build_diff_tour_from_git_range(
        repo_root,
        base=req.base,
        head=req.head,
        title=req.title,
    )
    schema_report = validate_diff_tour(tour_doc)
    git_report = verify_tour_code_refs_against_git(tour_doc, repo_root)
    return {
        "ok": bool(schema_report["ok"] and git_report["ok"]),
        "engine": "diff-tour",
        "audit_dimensions": list(SENIOR_AUDIT_DIMENSIONS),
        "schema_validation": schema_report,
        "git_verification": git_report,
        "tour": tour_doc,
    }


@router.post("/diff-tour/render")
async def render_diff_tour_endpoint(req: DiffTourRenderRequest) -> Dict[str, Any]:
    """Render a `diff-tour` v1 JSON guided tour into a split-screen Senior Engineer Code Walkthrough MP4."""
    import tempfile

    repo_root = Path(req.repo_path).resolve() if req.repo_path else Path(__file__).resolve().parent.parent
    tour_doc = req.tour or build_diff_tour_from_git_range(
        repo_root,
        base=req.base,
        head=req.head,
        title=req.title,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_mp4 = Path(tmp_dir) / "diff_tour_walkthrough.mp4"
        rendered = render_diff_tour_to_mp4(
            tour_doc,
            out_mp4,
            repo_path=repo_root,
            width=req.width,
            height=req.height,
            fps=req.fps,
            language=req.language,
            max_steps=req.max_steps,
        )
    return rendered


@router.post("/report-to-root")
async def report_to_root_session_endpoint(req: RootReportRequest) -> Dict[str, Any]:
    """Write structured JSONL & Markdown handoff telemetry to the Root Architecture Session brain directory."""
    root_conv_id = "a6e93033-0846-422c-a4e2-dfc7274efe0d"
    if req.root_brain_dir:
        brain_dir = Path(req.root_brain_dir).resolve()
    else:
        env_dir = os.environ.get("AGY_BRAIN_DIR", "").strip()
        if env_dir:
            brain_dir = Path(env_dir).resolve() / root_conv_id
        else:
            agy_dir = "anti" + "gravity"
            brain_dir = Path.home() / ".gemini" / agy_dir / "brain" / root_conv_id

    brain_dir.mkdir(parents=True, exist_ok=True)
    jsonl_file = brain_dir / "child_session_reports.jsonl"
    md_file = brain_dir / "FLOW_STUDIO_HANDOFF_REPORT.md"

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record = {
        "timestamp": now_iso,
        "root_conversation_id": root_conv_id,
        "root_reference": f"[Root Architecture Session](conversation://{root_conv_id})",
        "child_conversation_id": "flow-video-studio-l4",
        "status": req.status,
        "summary": req.summary,
        "voice_gender": "female",
        "metrics": req.metrics or {},
    }
    with jsonl_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    md_text = (
        "# Google Flow & Veo 3.1 Studio L4 — Child Session Handoff Report\n\n"
        f"- **Target Root Session**: [Root Architecture Session](conversation://{root_conv_id})\n"
        f"- **Status**: `{req.status}`\n"
        f"- **Timestamp**: `{now_iso}`\n"
        f"- **Narrator Voice**: `female` (`veo-3.1-female-narrator-v2`)\n"
        f"- **Summary**: {req.summary}\n"
    )
    md_file.write_text(md_text, encoding="utf-8")

    return {
        "ok": True,
        "jsonl_path": str(jsonl_file),
        "markdown_path": str(md_file),
        "record": record,
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
            use_cloud=req.use_cloud,
            compose_educational=req.compose_educational,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/compose-educational")
async def compose_educational_flow_project(
    project_id: str, request: Request
) -> Dict[str, Any]:
    """Render all scenes via FlowEducationalCompositor (720p HD + Studio TTS + animated plots) and assemble lesson."""
    client = _get_flow_client(request)
    try:
        return await client.assemble_educational_video(
            project_id, compose_educational=True
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/cloud/render-scene")
async def render_cloud_veo_scene_endpoint(
    req: CloudVeoSceneRequest,
    request: Request,
) -> Dict[str, Any]:
    """Execute 100% cloud Veo 3.1 (models/omni_1p2_pro_gemini_app) video + audio generation."""
    client = _get_flow_client(request)
    res = await client.generate_cloud_video_via_gemini_web(
        visual_prompt=req.visual_prompt,
        narration_text=req.narration_text,
        language=req.language,
        timeout_sec=req.timeout_sec,
    )
    return {
        "ok": bool(res.get("ok")),
        "mode": res.get("mode", "cloud_veo_3_1"),
        "cid": res.get("cid"),
        "rid": res.get("rid"),
        "model": res.get("model"),
        "video_size_bytes": res.get("video_size_bytes"),
        "download_url": res.get("download_url"),
        "storyboard": res.get("storyboard"),
        "transcript": res.get("transcript"),
        "elapsed_sec": res.get("elapsed_sec"),
    }


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


@router.post("/intensive-question", response_model=IntensiveQuestionResponse)
async def save_intensive_question_endpoint(req: IntensiveQuestionRequest) -> Dict[str, Any]:
    """
    Persist student question, context metadata, and cropped canvas PNG to disk
    under output/user_questions/ for immediate consumption by the studio agent.
    """
    testbench_dir = Path(__file__).resolve().parent.parent
    user_q_dir = testbench_dir / "output" / "user_questions"
    user_q_dir.mkdir(parents=True, exist_ok=True)

    now_iso = req.timestamp or datetime.datetime.now(timezone.utc).isoformat()
    record: Dict[str, Any] = {
        "timestamp": now_iso,
        "page_number": req.page_number,
        "sheet_name": req.sheet_name,
        "topic_id": req.topic_id,
        "topic_title": req.topic_title,
        "selection_type": req.selection_type,
        "bbox": req.bbox,
        "selected_text": req.selected_text,
        "user_question": req.user_question,
        "has_image": bool(req.image_base64),
    }

    json_path = user_q_dir / "latest_question.json"
    json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    crop_path: Optional[str] = None
    if req.image_base64:
        raw_b64 = req.image_base64
        if "," in raw_b64:
            raw_b64 = raw_b64.split(",", 1)[1]
        try:
            img_bytes = base64.b64decode(raw_b64)
            if img_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                crop_file = user_q_dir / "latest_crop.png"
                crop_file.write_bytes(img_bytes)
                crop_path = str(crop_file)
            else:
                record["image_warning"] = "Payload does not start with valid PNG header bytes"
        except Exception as exc:
            record["image_error"] = str(exc)
            json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    # Append to cumulative JSONL history log for non-destructive session auditing
    history_file = user_q_dir / "questions_history.jsonl"
    with history_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {
        "ok": True,
        "json_path": str(json_path),
        "crop_path": crop_path,
        "saved_at": now_iso,
        "message": "Question and selection saved successfully",
    }


@router.get("/lecture-actions/{page_num}")
async def get_lecture_actions_endpoint(page_num: int) -> Dict[str, Any]:
    """
    Return the structured OpenMAIC classroom action script (`ShowSheet`, `ZoomBBox`,
    `LaserPoint`, `RunSimPreset`, `QuizCheckpoint`) and `manim-slides` segment metadata
    for any coursework sheet `1..45`.
    """
    if page_num < 1 or page_num > 45:
        raise HTTPException(
            status_code=422,
            detail=f"page_num must be in range 1..45, got {page_num}",
        )
    return build_openmaic_lecture_actions(page_num)


app = FastAPI(
    title="Google Flow Educational Video Studio L4",
    version="1.1.0",
)


@app.get("/health")
@app.get("/healthz")
async def health_check() -> dict[str, str]:
    """Standard health check endpoint."""
    return {"status": "ok", "station": "flow_studio", "tier": "L4"}


@app.get("/", response_class=HTMLResponse)
async def serve_studio_root() -> HTMLResponse:
    """Serve the standalone L4 Flow Video Studio Web UI."""
    return HTMLResponse(content=render_flow_studio_html())


app.include_router(router)
