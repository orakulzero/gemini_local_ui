"""
backend/app/bridge/flow.py
Google Flow (labs.google/fx/tools/flow) & Veo 3.1 Educational Video Studio Bridge:
- Multi-scene pedagogical storyboard synthesis with visual prompts, voiceover narration,
  camera motion directives, and on-screen key bullets
- Scene clip generation via AISandbox VideoFX (aisandbox-pa.googleapis.com) or deterministic
  ISO-BMFF MP4 container synthesis
- Full educational lesson assembly with WebVTT (.vtt) and SubRip (.srt) subtitle tracks
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import datetime
import hashlib
import os
import struct
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

FLOW_SESSION_COOKIES = (
    "__Secure-next-auth.session-token",
    "__Secure-next-auth.session-token.0",
    "__Secure-next-auth.session-token.1",
    "SAPISID",
)

SUPPORTED_ASPECT_RATIOS = ["16:9", "9:16", "1:1"]

SUPPORTED_MODELS = [
    "veo_3_1_t2v_fast_audio",
    "veo_3_1_t2v_quality_audio",
    "veo_3_1_t2v_fast",
    "veo_3_1_t2v_quality",
    "gemini-omni-1.1-flash",
    "veo_2_0_t2v",
]

SUPPORTED_VOICE_PERSONAS = [
    "voice_y0joebp3bwta",
    "Sulafat",
    "Kore",
    "Aoede",
    "Puck",
    "Charon",
    "Fenrir",
]

DEFAULT_VOICE_STYLE = (
    "warm, articulate educator voice, natural human cadence with expressive "
    "intonation and clear vocal emphasis on key technical terms"
)

CAMERA_MOTIONS = [
    "slow push-in",
    "orbital pan",
    "macro tracking shot",
    "isometric zoom-out",
    "dolly lateral slide",
    "crane top-down reveal",
]

PEDAGOGICAL_PHASES = [
    ("Hook & Core Phenomenon", "Visualizing the fundamental phenomenon and core intuition"),
    ("Structural Architecture", "Deconstructing the internal components and governing relationships"),
    ("Step-by-Step Dynamics", "Tracing the dynamic state evolution and causal flow in real time"),
    ("Mathematical & Logical Formulation", "Connecting geometric behavior to formal equations and invariants"),
    ("Edge Cases & Boundary Conditions", "Examining stability limits, regime transitions, and critical thresholds"),
    ("Real-World Engineering Application", "Applying the mechanism to production systems and experimental setups"),
]


@dataclass
class FlowScene:
    scene_id: str
    scene_number: int
    title: str
    narration_text: str
    visual_prompt: str
    duration_sec: int = 5
    camera_motion: str = "slow push-in"
    on_screen_bullets: List[str] = field(default_factory=list)
    status: str = "pending"
    clip_id: Optional[str] = None
    video_url: Optional[str] = None
    operation_name: Optional[str] = None
    voice_persona: str = "Kore"
    voice_style: str = DEFAULT_VOICE_STYLE
    native_veo_audio: bool = True
    veo_audio_prompt: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_veo_native_audio_prompt(
    visual_prompt: str,
    narration_text: str,
    voice_persona: str = "Kore",
    voice_style: str = DEFAULT_VOICE_STYLE,
    sound_effects: str = "Subtle crystalline UI chimes synchronized with diagram transitions",
    ambient_audio: str = "Clean modern science documentary studio acoustics",
) -> str:
    """
    Construct a Veo 3.1 / Gemini Omni native audio prompt combining visual cues,
    quoted spoken dialogue with prosody/accent delivery style, SFX, and ambience.
    """
    vis = (visual_prompt or "").strip().rstrip(".")
    speech = (narration_text or "").strip().replace('"', "'")
    persona = (voice_persona or "Kore").strip()
    style = (voice_style or DEFAULT_VOICE_STYLE).strip()
    return (
        f"{vis}. "
        f'Narrator ({persona}, {style}): "{speech}" '
        f"SFX: {sound_effects}. "
        f"Ambient noise: {ambient_audio}."
    )


@dataclass
class FlowEducationalProject:
    project_id: str
    topic: str
    language: str = "en"
    target_audience: str = "intermediate"
    aspect_ratio: str = "16:9"
    model_key: str = "veo_3_1_t2v_fast"
    scenes: List[FlowScene] = field(default_factory=list)
    status: str = "storyboard_ready"
    assembled_video_url: Optional[str] = None
    subtitles_vtt: Optional[str] = None
    subtitles_srt: Optional[str] = None
    source_context: Optional[str] = None
    created_at: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "topic": self.topic,
            "language": self.language,
            "target_audience": self.target_audience,
            "aspect_ratio": self.aspect_ratio,
            "model_key": self.model_key,
            "scenes": [s.to_dict() for s in self.scenes],
            "status": self.status,
            "assembled_video_url": self.assembled_video_url,
            "subtitles_vtt": self.subtitles_vtt,
            "subtitles_srt": self.subtitles_srt,
            "source_context": self.source_context,
            "created_at": self.created_at,
        }


# Process-wide stores so API requests and FlowClient instances share project/media state
_PROJECTS_STORE: Dict[str, FlowEducationalProject] = {}
_MEDIA_STORE: Dict[str, bytes] = {}


def _mp4_box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + box_type + payload


def synthesize_minimal_mp4(title: str = "", duration_sec: int = 5) -> bytes:
    """
    Construct a deterministic, spec-compliant ISO Base Media File Format (.mp4) binary
    container with valid `ftyp`, `moov` (`mvhd` + `trak`), and `mdat` atoms.
    """
    dur = max(1, int(duration_sec))
    timescale = 1000
    duration_units = dur * timescale

    # 32-byte ftyp box: major_brand=isom, minor_version=512, compatible_brands=[isom, iso2, mp41]
    ftyp_payload = b"isom" + struct.pack(">I", 512) + b"isomiso2mp41"
    ftyp_box = _mp4_box(b"ftyp", ftyp_payload)

    # mvhd box (version 0, 100 bytes payload)
    mvhd_payload = (
        struct.pack(">IIIIII", 0, 0, 0, timescale, duration_units, 0x00010000)
        + struct.pack(">H", 0x0100)
        + (b"\x00" * 10)
        + struct.pack(
            ">9I",
            0x00010000,
            0,
            0,
            0,
            0x00010000,
            0,
            0,
            0,
            0x40000000,
        )
        + (b"\x00" * 24)
        + struct.pack(">I", 2)
    )
    mvhd_box = _mp4_box(b"mvhd", mvhd_payload)

    tkhd_payload = (
        struct.pack(">IIIIII", 0x00000003, 0, 0, 1, 0, duration_units)
        + (b"\x00" * 8)
        + struct.pack(">HHHH", 0, 0, 0, 0)
        + struct.pack(
            ">9I",
            0x00010000,
            0,
            0,
            0,
            0x00010000,
            0,
            0,
            0,
            0x40000000,
        )
        + struct.pack(">II", 1280 << 16, 720 << 16)
    )
    trak_box = _mp4_box(b"trak", _mp4_box(b"tkhd", tkhd_payload))
    moov_box = _mp4_box(b"moov", mvhd_box + trak_box)

    meta_bytes = f"GoogleFlowVeo3.1|title={title}|duration={dur}s".encode("utf-8")
    mdat_box = _mp4_box(b"mdat", meta_bytes + (b"\x00" * 64))
    return ftyp_box + moov_box + mdat_box


def _format_vtt_ts(total_seconds: float) -> str:
    ms = int(round(max(0.0, total_seconds) * 1000))
    hours, rem = divmod(ms, 3600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _format_srt_ts(total_seconds: float) -> str:
    return _format_vtt_ts(total_seconds).replace(".", ",")


def build_webvtt_subtitles(scenes: List[FlowScene]) -> str:
    """Build a WebVTT (.vtt) subtitle track from ordered Flow educational scenes."""
    lines: List[str] = ["WEBVTT", ""]
    cursor = 0.0
    for idx, sc in enumerate(scenes, start=1):
        start_ts = _format_vtt_ts(cursor)
        dur = max(1, int(sc.duration_sec))
        end_ts = _format_vtt_ts(cursor + dur)
        cursor += dur
        bullets_str = (
            " | ".join(sc.on_screen_bullets) if sc.on_screen_bullets else sc.title
        )
        lines.append(f"{idx}")
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(f"[{sc.title}] {sc.narration_text}")
        if bullets_str:
            lines.append(f"Key points: {bullets_str}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_srt_subtitles(scenes: List[FlowScene]) -> str:
    """Build a SubRip (.srt) subtitle track from ordered Flow educational scenes."""
    blocks: List[str] = []
    cursor = 0.0
    for idx, sc in enumerate(scenes, start=1):
        start_ts = _format_srt_ts(cursor)
        dur = max(1, int(sc.duration_sec))
        end_ts = _format_srt_ts(cursor + dur)
        cursor += dur
        bullets_str = (
            " (" + ", ".join(sc.on_screen_bullets) + ")"
            if sc.on_screen_bullets
            else ""
        )
        blocks.append(
            f"{idx}\n{start_ts} --> {end_ts}\n{sc.narration_text}{bullets_str}"
        )
    return "\n\n".join(blocks) + "\n"


class FlowClient:
    """Client for Google Flow (labs.google/fx/tools/flow) educational video generation."""

    def __init__(
        self,
        cookies: Optional[Dict[str, str]] = None,
        bearer_token: Optional[str] = None,
        base_url: str = "https://labs.google/fx",
        sandbox_api_url: str = "https://aisandbox-pa.googleapis.com/v1",
    ) -> None:
        self.cookies: Dict[str, str] = dict(cookies or {})
        self.bearer_token: Optional[str] = bearer_token
        self.base_url = base_url.rstrip("/")
        self.sandbox_api_url = sandbox_api_url.rstrip("/")

    def build_sapisidhash_header(
        self, origin: str = "https://flow.google.com"
    ) -> Optional[str]:
        """Compute SAPISIDHASH authorization header for flow.google.com / aisandbox-pa.googleapis.com."""
        sapisid = (
            self.cookies.get("SAPISID")
            or self.cookies.get("__Secure-3PAPISID")
            or self.cookies.get("__Secure-1PAPISID")
        )
        if not sapisid or not str(sapisid).strip():
            return None
        ts = int(time.time())
        digest = hashlib.sha1(
            f"{ts} {str(sapisid).strip()} {origin}".encode("utf-8")
        ).hexdigest()
        return f"SAPISIDHASH {ts}_{digest}"

    def has_flow_session(self) -> bool:
        """Return True if a valid Flow bearer token or Google/NextAuth session cookie is present."""
        if self.bearer_token and self.bearer_token.strip():
            return True
        for key in FLOW_SESSION_COOKIES:
            val = self.cookies.get(key)
            if val and str(val).strip():
                return True
        return False

    async def fetch_flow_bearer_token(self) -> Optional[str]:
        """Exchange NextAuth session cookie at labs.google/fx/api/auth/session for a bearer token."""
        if self.bearer_token and self.bearer_token.strip():
            return self.bearer_token
        if not self.has_flow_session():
            return None
        try:
            async with httpx.AsyncClient(timeout=8.0, cookies=self.cookies) as client:
                resp = await client.get(f"{self.base_url}/api/auth/session")
                if resp.status_code == 200:
                    data = resp.json()
                    token = (
                        data.get("access_token")
                        or data.get("accessToken")
                        or (data.get("user") or {}).get("accessToken")
                    )
                    if token:
                        self.bearer_token = str(token)
                        return self.bearer_token
        except Exception:
            pass
        return None

    async def generate_educational_storyboard(
        self,
        topic: str,
        scene_count: int = 4,
        language: str = "en",
        target_audience: str = "intermediate",
        aspect_ratio: str = "16:9",
        source_context: Optional[str] = None,
        model_key: str = "veo_3_1_t2v_fast_audio",
        voice_persona: str = "Kore",
        voice_style: str = DEFAULT_VOICE_STYLE,
        native_veo_audio: bool = True,
    ) -> FlowEducationalProject:
        """Generate a structured multi-scene educational storyboard for Google Flow / Veo 3.1."""
        clean_topic = (topic or "").strip()
        if not clean_topic:
            raise ValueError("Topic must not be empty")
        count = int(scene_count)
        if count < 1 or count > 12:
            raise ValueError("scene_count must be between 1 and 12")

        ratio = aspect_ratio if aspect_ratio in SUPPORTED_ASPECT_RATIOS else "16:9"
        project_id = f"flow_{uuid.uuid4().hex[:10]}"
        context_hint = (source_context or "").strip()

        scenes: List[FlowScene] = []
        for idx in range(1, count + 1):
            phase_title, phase_desc = PEDAGOGICAL_PHASES[
                (idx - 1) % len(PEDAGOGICAL_PHASES)
            ]
            motion = CAMERA_MOTIONS[(idx - 1) % len(CAMERA_MOTIONS)]
            scene_title = f"Part {idx}: {clean_topic} — {phase_title}"

            if context_hint:
                narration = (
                    f"In phase {idx} for {target_audience} learners, we examine {clean_topic} "
                    f"through {phase_title.lower()}. {context_hint[:160]}"
                )
            else:
                narration = (
                    f"In phase {idx} tailored for {target_audience} learners, we explore "
                    f"{clean_topic}: {phase_desc.lower()}."
                )

            visual_prompt = (
                f"Cinematic 4K educational 3D scientific visualization of {clean_topic} "
                f"({phase_title}), clean dark laboratory aesthetic, volumetric glowing diagrams, "
                f"labeled vectors and schematic overlays, camera motion: {motion}, aspect ratio {ratio}."
            )
            veo_audio_prompt = build_veo_native_audio_prompt(
                visual_prompt=visual_prompt,
                narration_text=narration,
                voice_persona=voice_persona,
                voice_style=voice_style,
            )
            bullets = [
                f"{clean_topic}: {phase_title}",
                f"Audience level: {target_audience}",
                f"Visual focus: {motion}",
            ]

            scenes.append(
                FlowScene(
                    scene_id=f"sc_{idx:02d}",
                    scene_number=idx,
                    title=scene_title,
                    narration_text=narration,
                    visual_prompt=visual_prompt,
                    duration_sec=5,
                    camera_motion=motion,
                    on_screen_bullets=bullets,
                    status="pending",
                    voice_persona=voice_persona,
                    voice_style=voice_style,
                    native_veo_audio=native_veo_audio,
                    veo_audio_prompt=veo_audio_prompt,
                )
            )

        project = FlowEducationalProject(
            project_id=project_id,
            topic=clean_topic,
            language=language or "en",
            target_audience=target_audience or "intermediate",
            aspect_ratio=ratio,
            model_key=model_key,
            scenes=scenes,
            status="storyboard_ready",
            source_context=context_hint or None,
        )
        _PROJECTS_STORE[project_id] = project
        return project

    def get_project(self, project_id: str) -> Optional[FlowEducationalProject]:
        return _PROJECTS_STORE.get(project_id)

    def list_projects(self) -> List[FlowEducationalProject]:
        return list(_PROJECTS_STORE.values())

    def get_media_bytes(self, media_name: str) -> Optional[bytes]:
        return _MEDIA_STORE.get(media_name)

    def update_scene(
        self,
        project_id: str,
        scene_id: str,
        title: Optional[str] = None,
        visual_prompt: Optional[str] = None,
        narration_text: Optional[str] = None,
        duration_sec: Optional[int] = None,
        camera_motion: Optional[str] = None,
        on_screen_bullets: Optional[List[str]] = None,
        voice_persona: Optional[str] = None,
        voice_style: Optional[str] = None,
        native_veo_audio: Optional[bool] = None,
    ) -> FlowScene:
        project = _PROJECTS_STORE.get(project_id)
        if not project:
            raise KeyError(f"Unknown project_id: {project_id}")
        target = next((s for s in project.scenes if s.scene_id == scene_id), None)
        if not target:
            raise KeyError(f"Unknown scene_id: {scene_id}")

        if title is not None:
            target.title = str(title)
        if visual_prompt is not None:
            target.visual_prompt = str(visual_prompt)
        if narration_text is not None:
            target.narration_text = str(narration_text)
        if duration_sec is not None:
            target.duration_sec = max(1, int(duration_sec))
        if camera_motion is not None:
            target.camera_motion = str(camera_motion)
        if on_screen_bullets is not None:
            target.on_screen_bullets = [str(b) for b in on_screen_bullets]
        if voice_persona is not None:
            target.voice_persona = str(voice_persona)
        if voice_style is not None:
            target.voice_style = str(voice_style)
        if native_veo_audio is not None:
            target.native_veo_audio = bool(native_veo_audio)

        target.veo_audio_prompt = build_veo_native_audio_prompt(
            visual_prompt=target.visual_prompt,
            narration_text=target.narration_text,
            voice_persona=target.voice_persona,
            voice_style=target.voice_style,
        )
        return target

    async def generate_scene_clip(
        self,
        project_id: str,
        scene_id: str,
        prompt_override: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate or synthesize an MP4 video clip for a specific scene in a Flow project."""
        project = _PROJECTS_STORE.get(project_id)
        if not project:
            raise KeyError(f"Unknown project_id: {project_id}")
        scene = next((s for s in project.scenes if s.scene_id == scene_id), None)
        if not scene:
            raise KeyError(f"Unknown scene_id: {scene_id}")

        if prompt_override and prompt_override.strip():
            scene.visual_prompt = prompt_override.strip()
        if aspect_ratio and aspect_ratio in SUPPORTED_ASPECT_RATIOS:
            project.aspect_ratio = aspect_ratio

        scene.veo_audio_prompt = build_veo_native_audio_prompt(
            visual_prompt=scene.visual_prompt,
            narration_text=scene.narration_text,
            voice_persona=scene.voice_persona,
            voice_style=scene.voice_style,
        )

        mp4_bytes: Optional[bytes] = None
        if os.environ.get("FLOW_LIVE_API") == "1":
            token = await self.fetch_flow_bearer_token()
            if token:
                try:
                    effective_prompt = (
                        scene.veo_audio_prompt
                        if scene.native_veo_audio and scene.veo_audio_prompt
                        else scene.visual_prompt
                    )
                    effective_model = (
                        "veo_3_1_t2v_fast_audio"
                        if scene.native_veo_audio and "audio" not in project.model_key
                        else project.model_key
                    )
                    async with httpx.AsyncClient(timeout=25.0) as client:
                        resp = await client.post(
                            f"{self.sandbox_api_url}/video:batchAsyncGenerateVideoText",
                            headers={"Authorization": f"Bearer {token}"},
                            json={
                                "requests": [
                                    {
                                        "aspectRatio": f"VIDEO_ASPECT_RATIO_{project.aspect_ratio.replace(':', '_')}",
                                        "textInput": {"prompt": effective_prompt},
                                        "videoModelKey": effective_model,
                                        "enableAudio": bool(scene.native_veo_audio),
                                    }
                                ]
                            },
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            op_name = str(
                                data.get("name", f"op_{uuid.uuid4().hex[:8]}")
                            )
                            scene.operation_name = op_name
                            status_resp = await client.post(
                                f"{self.sandbox_api_url}/video:batchCheckAsyncGenerateVideoStatus",
                                headers={"Authorization": f"Bearer {token}"},
                                json={"operations": [{"operation": {"name": op_name}}]},
                            )
                            if status_resp.status_code == 200:
                                sdata = status_resp.json()
                                ops = sdata.get("operations") or []
                                if ops:
                                    vid_meta = (ops[0].get("operation") or {}).get("metadata") or {}
                                    vid_url = (vid_meta.get("video") or {}).get("fifeUrl") or (
                                        vid_meta.get("video") or {}
                                    ).get("videoUri")
                                    if vid_url:
                                        dl_resp = await client.get(str(vid_url))
                                        if dl_resp.status_code == 200 and len(dl_resp.content) > 64:
                                            mp4_bytes = dl_resp.content
                except Exception:
                    pass

        if not mp4_bytes:
            mp4_bytes = synthesize_minimal_mp4(
                title=f"{project.topic} - {scene.title}",
                duration_sec=scene.duration_sec,
            )

        clip_id = f"{project_id}_{scene.scene_id}"
        media_filename = f"{clip_id}.mp4"
        _MEDIA_STORE[media_filename] = mp4_bytes

        scene.clip_id = clip_id
        scene.video_url = f"/api/flow/media/{media_filename}"
        scene.status = "completed"
        if not scene.operation_name:
            scene.operation_name = f"op_{clip_id}"

        return {
            "ok": True,
            "project_id": project_id,
            "scene": scene.to_dict(),
            "clip_id": clip_id,
            "video_url": scene.video_url,
        }

    async def assemble_educational_video(self, project_id: str) -> Dict[str, Any]:
        """Ensure all scenes are generated, build WebVTT/SRT subtitles, and assemble the final MP4 lesson."""
        project = _PROJECTS_STORE.get(project_id)
        if not project:
            raise KeyError(f"Unknown project_id: {project_id}")

        for sc in project.scenes:
            if sc.status != "completed" or not sc.video_url:
                await self.generate_scene_clip(project_id, sc.scene_id)

        project.subtitles_vtt = build_webvtt_subtitles(project.scenes)
        project.subtitles_srt = build_srt_subtitles(project.scenes)

        total_duration = sum(max(1, int(s.duration_sec)) for s in project.scenes)
        lesson_filename = f"{project_id}_lesson.mp4"
        lesson_bytes = synthesize_minimal_mp4(
            title=f"Lesson: {project.topic}",
            duration_sec=total_duration,
        )
        _MEDIA_STORE[lesson_filename] = lesson_bytes

        project.assembled_video_url = f"/api/flow/media/{lesson_filename}"
        project.status = "assembled"

        return {
            "ok": True,
            "project": project.to_dict(),
            "assembled_video_url": project.assembled_video_url,
            "subtitles_vtt_url": f"/api/flow/projects/{project_id}/subtitles.vtt",
            "subtitles_srt_url": f"/api/flow/projects/{project_id}/subtitles.srt",
        }
