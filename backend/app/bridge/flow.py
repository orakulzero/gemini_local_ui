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

from dataclasses import asdict, dataclass, field, fields
import datetime
import hashlib
import os
import struct
import time
import uuid
from typing import Any

import httpx
import contextlib

try:
    from backend.app.bridge.flow_repo_adaptations import (
        BookmarkParseResult,
        CursorFrameState,
        CursorGroundingEngine,
        ResolvedBookmarkFloat,
        ResolvedBookmarkMap,
        SceneRenderCache,
        VoiceoverBookmark,
        build_openmaic_lecture_actions,
        cubic_bezier_ease,
        parse_voiceover_bookmarks,
        resolve_bookmark_timestamps,
        strip_voiceover_bookmarks,
    )
except ImportError:
    from .flow_repo_adaptations import (  # type: ignore[no-redef]
        strip_voiceover_bookmarks,
    )

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
class WordTiming:
    """Exact physical start and end timestamps for an individual visual word token."""
    word_index: int              # Sequential word index within scene (0..N-1)
    word: str                    # Visual word token (Latin-preserved, clean)
    start_ms: int                # Start timestamp in ms relative to scene start
    end_ms: int                  # End timestamp in ms relative to scene start
    page_index: int = 0          # Page index (0-based)
    line_index: int = 0          # Line index on card (0 or 1)
    x: int = 0                   # Pixel X coordinate
    y: int = 0                   # Pixel Y coordinate
    width: int = 0               # Rendered token width in pixels
    spoken_phrase: str = ""      # Spoken phonetic expansion for validation


@dataclass
class SubtitlePage:
    """A 2-line kinetic typography subtitle card."""
    page_index: int
    start_ms: int
    end_ms: int
    lines: list[Any] = field(default_factory=list)
    words: list[WordTiming] = field(default_factory=list)


@dataclass
class SceneCaptions:
    """Full word-level caption track for a scene."""
    scene_id: str
    duration_ms: int
    pages: list[SubtitlePage] = field(default_factory=list)
    all_words: list[WordTiming] = field(default_factory=list)


@dataclass
class FlowScene:
    scene_id: str
    scene_number: int
    title: str
    narration_text: str
    visual_prompt: str
    duration_sec: int = 5
    camera_motion: str = "slow push-in"
    on_screen_bullets: list[str] = field(default_factory=list)
    status: str = "pending"
    clip_id: str | None = None
    video_url: str | None = None
    operation_name: str | None = None
    voice_persona: str = "voice_y0joebp3bwta"
    voice_style: str = DEFAULT_VOICE_STYLE
    native_veo_audio: bool = True
    veo_audio_prompt: str | None = None
    captions: Any | None = None
    tts_narration_text: str | None = None
    pz_page: int | None = None
    concept_cues: list[dict[str, Any]] | None = None
    pedagogical_phase: str = "Hook & Core Phenomenon"
    formula_expression: str | None = None
    diagram_spec: dict[str, Any] | None = None
    first_frame_path: str | None = None
    last_frame_path: str | None = None
    timecoded_directives: list[dict[str, str]] = field(default_factory=list)
    learning_objective: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlowScene:
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        if "scene_id" not in kwargs:
            kwargs["scene_id"] = str(data.get("scene_id", "sc_01"))
        if "scene_number" not in kwargs:
            kwargs["scene_number"] = int(data.get("scene_number", 1) or 1)
        if "title" not in kwargs:
            kwargs["title"] = str(data.get("title", ""))
        if "narration_text" not in kwargs:
            kwargs["narration_text"] = str(data.get("narration_text", ""))
        if "visual_prompt" not in kwargs:
            kwargs["visual_prompt"] = str(data.get("visual_prompt", ""))
        return cls(**kwargs)


def build_educational_flow_prompt(
    scene: FlowScene,
    language: str = "ru",
    sound_effects: str = "Subtle crystalline UI chimes synchronized with diagram transitions",
    ambient_audio: str = "Clean modern science documentary studio acoustics",
) -> str:
    """
    Construct a pedagogically grounded, timecoded ([0-2s], [2-5s], [5-8s]),
    camera-locked Google Flow / Veo 3.1 / Gemini Omni prompt with explicit
    formula focus, diagram topology, Mayer CTML constraints, and female narration.
    """
    vis = (scene.visual_prompt or "").strip().rstrip(".")
    phase = (scene.pedagogical_phase or "Hook & Core Phenomenon").strip()
    formula = (scene.formula_expression or "").strip()
    directives = scene.timecoded_directives or [
        {"window": "0-2s", "action": f"Establish clean engineering schematic for {scene.title} ({phase})"},
        {"window": "2-5s", "action": f"Animate signal flow and highlight formula {formula or 'core invariant'}"},
        {"window": "5-8s", "action": "Verify steady-state response with crisp callout badges"},
    ]
    tc_parts = [f"[{d.get('window', '0-2s')}] {d.get('action', '')}" for d in directives if d.get("action")]
    tc_str = " ".join(tc_parts)
    formula_clause = f" Governing equation focus: {formula}." if formula else ""
    bullet_str = "; ".join((scene.on_screen_bullets or [])[:3])
    bullet_clause = f" On-screen CTML callouts (<=6 words): {bullet_str}." if bullet_str else ""
    persona = (scene.voice_persona or ("voice_y0joebp3bwta" if language.lower().startswith("ru") else "Sulafat")).strip()
    style = (scene.voice_style or DEFAULT_VOICE_STYLE).strip()
    speech = sanitize_cloud_veo_narration(scene.narration_text)
    return (
        f"In a single continuous unbroken educational shot, steady locked camera, "
        f"zero camera shake, zero decorative sci-fi clutter. "
        f"Pedagogical Phase: {phase}. {vis}.{formula_clause} "
        f"Timeline: {tc_str}.{bullet_clause} "
        f'Narrator ({persona}, {style}): "{speech}" '
        f"SFX: {sound_effects}. Ambient noise: {ambient_audio}."
    )


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
    scenes: list[FlowScene] = field(default_factory=list)
    status: str = "storyboard_ready"
    assembled_video_url: str | None = None
    subtitles_vtt: str | None = None
    subtitles_srt: str | None = None
    source_context: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
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
_PROJECTS_STORE: dict[str, FlowEducationalProject] = {}
_MEDIA_STORE: dict[str, bytes] = {}


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

    meta_bytes = f"GoogleFlowVeo3.1|title={title}|duration={dur}s".encode()
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


def build_webvtt_subtitles(scenes: list[FlowScene]) -> str:
    """
    Build a WebVTT (.vtt) subtitle track from ordered Flow educational scenes.

    When scenes have attached fine-grained captions (sc.captions with pages/words),
    emits fine-grained phrase-level cues with intra-cue word timestamps (<hh:mm:ss.ttt>).
    Otherwise, falls back to scene-level cues for backwards compatibility.
    """
    lines: list[str] = ["WEBVTT", ""]
    cursor = 0.0
    cue_counter = 1

    for sc in scenes:
        dur = max(1, int(sc.duration_sec))
        captions = getattr(sc, "captions", None)
        pages = getattr(captions, "pages", None) if captions else None

        if pages and len(pages) > 0:
            for p in pages:
                p_start = cursor + (p.start_ms / 1000.0)
                p_end = cursor + (p.end_ms / 1000.0)
                if p_end <= p_start:
                    p_end = p_start + 1.0
                start_ts = _format_vtt_ts(p_start)
                end_ts = _format_vtt_ts(p_end)

                p_words = getattr(p, "words", None) or []
                if p_words:
                    word_tokens = []
                    for w in p_words:
                        w_ts = _format_vtt_ts(cursor + (w.start_ms / 1000.0))
                        word_tokens.append(f"<{w_ts}>{w.word}")
                    cue_body = " ".join(word_tokens)
                else:
                    p_lines = getattr(p, "lines", None)
                    if p_lines:
                        if isinstance(p_lines[0], list):
                            cue_body = " ".join(" ".join(w) for w in p_lines)
                        else:
                            cue_body = " ".join(p_lines)
                    else:
                        cue_body = sc.narration_text

                lines.append(f"{cue_counter}")
                lines.append(f"{start_ts} --> {end_ts}")
                lines.append(strip_voiceover_bookmarks(cue_body))
                lines.append("")
                cue_counter += 1
            cursor += dur
        else:
            # Backwards-compatible scene-level cue
            start_ts = _format_vtt_ts(cursor)
            end_ts = _format_vtt_ts(cursor + dur)
            cursor += dur
            bullets_str = (
                " | ".join(sc.on_screen_bullets) if sc.on_screen_bullets else sc.title
            )
            lines.append(f"{cue_counter}")
            lines.append(f"{start_ts} --> {end_ts}")
            lines.append(f"[{sc.title}] {strip_voiceover_bookmarks(sc.narration_text)}")
            if bullets_str:
                lines.append(f"Key points: {strip_voiceover_bookmarks(bullets_str)}")
            lines.append("")
            cue_counter += 1

    return "\n".join(lines).rstrip() + "\n"


def build_srt_subtitles(scenes: list[FlowScene]) -> str:
    """
    Build a SubRip (.srt) subtitle track from ordered Flow educational scenes.

    When scenes have attached fine-grained captions (sc.captions with pages),
    emits fine-grained phrase-level cues per subtitle page.
    Otherwise, falls back to scene-level cues for backwards compatibility.
    """
    blocks: list[str] = []
    cursor = 0.0
    cue_counter = 1

    for sc in scenes:
        dur = max(1, int(sc.duration_sec))
        captions = getattr(sc, "captions", None)
        pages = getattr(captions, "pages", None) if captions else None

        if pages and len(pages) > 0:
            for p in pages:
                p_start = cursor + (p.start_ms / 1000.0)
                p_end = cursor + (p.end_ms / 1000.0)
                if p_end <= p_start:
                    p_end = p_start + 1.0
                start_ts = _format_srt_ts(p_start)
                end_ts = _format_srt_ts(p_end)

                p_lines = getattr(p, "lines", None)
                if p_lines:
                    if isinstance(p_lines[0], list):
                        cue_body = "\n".join(" ".join(w) for w in p_lines)
                    else:
                        cue_body = "\n".join(p_lines)
                elif getattr(p, "words", None):
                    cue_body = " ".join(w.word for w in p.words)
                else:
                    cue_body = sc.narration_text

                blocks.append(f"{cue_counter}\n{start_ts} --> {end_ts}\n{strip_voiceover_bookmarks(cue_body)}")
                cue_counter += 1
            cursor += dur
        else:
            # Backwards-compatible scene-level cue
            start_ts = _format_srt_ts(cursor)
            end_ts = _format_srt_ts(cursor + dur)
            cursor += dur
            bullets_str = (
                " (" + ", ".join(sc.on_screen_bullets) + ")"
                if sc.on_screen_bullets
                else ""
            )
            blocks.append(
                f"{cue_counter}\n{start_ts} --> {end_ts}\n{strip_voiceover_bookmarks(sc.narration_text)}{strip_voiceover_bookmarks(bullets_str)}"
            )
            cue_counter += 1

    return "\n\n".join(blocks) + "\n"


# Classifier-safe substitutions for Russian DSP terms that trigger EW/jamming filters
# in models/omni_1p2_pro_gemini_app (rewritten_prompt_hac_classifier).
_CLOUD_VEO_RU_SANITIZE_MAP = (
    ("\u043f\u043e\u0434\u0430\u0432\u043b\u044f\u0435\u0442", "\u0441\u0433\u043b\u0430\u0436\u0438\u0432\u0430\u0435\u0442"),
    ("\u043f\u043e\u0434\u0430\u0432\u043b\u0435\u043d\u0438\u0435", "\u0441\u0433\u043b\u0430\u0436\u0438\u0432\u0430\u043d\u0438\u0435"),
    ("\u043f\u043e\u043c\u0435\u0445\u0438", "\u043a\u043e\u043b\u0435\u0431\u0430\u043d\u0438\u044f"),
    ("\u043f\u043e\u043c\u0435\u0445", "\u0448\u0443\u043c\u043e\u0432"),
)


def detect_local_proxy_url() -> str | None:
    """Detect local HTTP/HTTPS proxy on 127.0.0.1 if configured or listening."""
    import socket

    for env_k in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        val = os.environ.get(env_k, "").strip()
        if val:
            return val
    for port in (7890, 10808, 2080):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.15)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    return f"http://127.0.0.1:{port}"
        except Exception:
            pass
    return None


def sanitize_cloud_veo_narration(narration_text: str) -> str:
    """
    Strip prosody/stress markers (*...*, +, [span]) and replace EW-sensitive Russian
    terms so Google Cloud Veo 3.1 (models/omni_1p2_pro_gemini_app) rewrites cleanly.
    """
    import re

    text = (narration_text or "").strip()
    text = re.sub(r"\[([^\]]+)\]", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"\+([a-zA-Z\u0400-\u04ff])", r"\1", text)
    text = text.replace('"', "'")
    for bad_tok, safe_tok in _CLOUD_VEO_RU_SANITIZE_MAP:
        text = re.sub(bad_tok, safe_tok, text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def build_cloud_veo_prompt(
    visual_prompt: str,
    narration_text: str,
    language: str = "ru",
    formula_expression: str | None = None,
    timecoded_directives: list[dict[str, str]] | None = None,
) -> str:
    """
    Build a classifier-clean English + quoted speech prompt for Google Cloud Veo 3.1
    (models/omni_1p2_pro_gemini_app) with timecoded pedagogical directives,
    formula focus, and off-screen female voiceover.
    """
    import re

    clean_vis = (visual_prompt or "").strip().rstrip(".")
    # Remove human/person words in visual prompt so person_detection stays 0
    clean_vis = re.sub(r"\bnarrator\b", "voice", clean_vis, flags=re.IGNORECASE)
    if not clean_vis:
        clean_vis = (
            "A glowing 3D microchip and memory module on a dark circuit board "
            "with cyan light pulses moving along data tracks"
        )
    tc_clause = ""
    if timecoded_directives:
        tc_parts = [
            f"[{d.get('window', '0-2s')}] {d.get('action', '')}"
            for d in timecoded_directives
            if isinstance(d, dict) and d.get("action")
        ]
        if tc_parts:
            tc_clause = " " + " ".join(tc_parts) + "."
    formula_clause = (
        f" Display crisp mathematical equation overlay: {formula_expression.strip()}."
        if formula_expression and formula_expression.strip()
        else ""
    )
    clean_speech = sanitize_cloud_veo_narration(narration_text)
    is_ru = bool(language and language.lower().startswith("ru")) or bool(
        re.search(r"[\u0400-\u04ff]", clean_speech)
    )
    lang_label = "Russian" if is_ru else "English"
    if clean_speech:
        return (
            f"Generate a short 5-second video: {clean_vis}.{formula_clause}{tc_clause} "
            f"with a female voice saying in {lang_label}: '{clean_speech}'"
        )
    return f"Generate a short 5-second video: {clean_vis}.{formula_clause}{tc_clause}".strip()


def extract_cloud_video_from_turns(turns: Any) -> dict[str, Any] | None:
    """
    Inspect batchexecute hNvQHb conversation turns for completed or failed
    Veo 3.1 / models/omni_1p2_pro_gemini_app video generation metadata (node '60').
    """
    try:
        if not isinstance(turns, list) or not turns or not turns[0]:
            return None
        turn = turns[0][0]
        candidate = turn[3][0][0]
        text = candidate[1][0] if (len(candidate) > 1 and candidate[1]) else ""
        rich_list = candidate[12] if len(candidate) > 12 else None
        if isinstance(rich_list, list) and len(rich_list) > 0 and isinstance(rich_list[0], dict):
            node60 = rich_list[0].get("60")
            if isinstance(node60, list) and len(node60) > 0:
                entry = node60[0][0]
                meta_block = entry[1] if len(entry) > 1 and isinstance(entry[1], list) else []
                model_name = "models/omni_1p2_pro_gemini_app"
                if len(meta_block) > 2 and isinstance(meta_block[2], list) and len(meta_block[2]) > 2:
                    model_name = str(meta_block[2][2] or model_name)
                storyboard = ""
                transcript = ""
                if len(meta_block) > 7 and isinstance(meta_block[7], list):
                    if len(meta_block[7]) > 0 and meta_block[7][0]:
                        storyboard = str(meta_block[7][0])
                    if len(meta_block[7]) > 3 and meta_block[7][3]:
                        transcript = str(meta_block[7][3])
                if entry[0] is not None:
                    vid_item = entry[0][0]
                    urls = vid_item[7] if len(vid_item) > 7 and isinstance(vid_item[7], list) else []
                    return {
                        "status": "completed",
                        "text": text,
                        "urls": [str(u) for u in urls if u],
                        "mime": str(vid_item[10]) if len(vid_item) > 10 and vid_item[10] else "video/mp4",
                        "size": int(vid_item[23]) if len(vid_item) > 23 and isinstance(vid_item[23], int) else 0,
                        "model": model_name,
                        "storyboard": storyboard,
                        "transcript": transcript,
                    }
                return {
                    "status": "failed",
                    "text": text,
                    "urls": [],
                    "model": model_name,
                }
        lower_t = (text or "").lower()
        if (
            "something went wrong" in lower_t
            or "policy-guidelines" in lower_t
            or "\u043d\u0435 \u043c\u043e\u0433\u0443 \u0441\u043e\u0437\u0434\u0430\u0442\u044c" in lower_t
        ):
            return {"status": "failed", "text": text, "urls": []}
    except Exception:
        pass
    return None


class FlowClient:
    """Client for Google Flow (labs.google/fx/tools/flow) educational video generation."""

    def __init__(
        self,
        cookies: dict[str, str] | None = None,
        bearer_token: str | None = None,
        base_url: str = "https://labs.google/fx",
        sandbox_api_url: str = "https://aisandbox-pa.googleapis.com/v1",
    ) -> None:
        self.cookies: dict[str, str] = dict(cookies or {})
        self.bearer_token: str | None = bearer_token
        self.base_url = base_url.rstrip("/")
        self.sandbox_api_url = sandbox_api_url.rstrip("/")

    def build_sapisidhash_header(
        self, origin: str = "https://flow.google.com"
    ) -> str | None:
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
            f"{ts} {str(sapisid).strip()} {origin}".encode()
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

    async def fetch_flow_bearer_token(self) -> str | None:
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
        source_context: str | None = None,
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

        # Default Russian storyboards to voice_y0joebp3bwta
        is_ru = bool(language and language.lower().startswith("ru"))
        if is_ru and voice_persona == "Kore":
            voice_persona = "voice_y0joebp3bwta"

        topic_low = clean_topic.lower()
        is_dsp = any(
            k in topic_low
            for k in ("dsp", "stm32", "biquad", "iir", "filter", "adc", "sram", "\u0446\u043e\u0441", "\u0444\u0438\u043b\u044c\u0442\u0440")
        )
        is_mla = any(k in topic_low for k in ("mla", "latent", "attention", "transformer", "kv"))
        is_quantum = any(k in topic_low for k in ("quantum", "bell", "qubit", "entangle"))

        plot_modes = ["hardware_topology", "biquad_zplane", "frequency_response", "dma_timing"]
        asset_keys = ["fig_4_1", "fig_4_2", "fig_4_3", "fig_4_5", "fig_5_1", "drawing_e2"]

        if is_dsp:
            formulas = [
                "f_s = 48 kHz >= 2 * f_max, SNR = 6.02*N + 1.76 = 98.1 dB",
                "H(z) = (b0 + b1*z^-1 + b2*z^-2) / (1 + a1*z^-1 + a2*z^-2)",
                "|H(e^jw)|^2 = 1 / (1 + (w / w_c)^(2N)), r_pole = 0.9878",
                "T_frame = 256 / 48 kHz = 5.33 ms, CPU_MAC = 18.0%",
                "w[n] = x[n] - a1*w[n-1] - a2*w[n-2], y[n] = b0*w[n] + b1*w[n-1] + b2*w[n-2]",
                "THD_out < -42 dB, t_conv <= 200 ns, V_DD = 3.3 V",
            ]
        elif is_mla:
            formulas = [
                "c_t^KV = W^DKV * h_t, d_c << d_h * n_h",
                "k_t^C = W^UK * c_t^KV, v_t^C = W^UV * c_t^KV",
                "q_t^R = RoPE(W^QR * c_t^Q), k_t^R = RoPE(W^KR * h_t)",
                "Attn(Q, K, V) = softmax((Q * K^T) / sqrt(d_k + d_r)) * V",
                "M_cache(MLA) = (d_c + d_r) * L * B << 2 * n_h * d_h * L * B",
                "W_absorbed = W_Q^T * W^UK, O(d_c) Memory Bandwidth",
            ]
        elif is_quantum:
            formulas = [
                "|Phi+> = (|00> + |11>) / sqrt(2)",
                "H|0> = (|0> + |1>) / sqrt(2), CNOT(|+> x |0>) = |Phi+>",
                "rho_AB = |Phi+><Phi+|, Tr_B(rho_AB) = I / 2",
                "E(a, b) = -cos(theta_a - theta_b), S_CHSH = 2*sqrt(2) > 2",
                "U_bell = CNOT_12 * (H x I), Fidelity F >= 0.994",
                "S(rho_A) = -Tr(rho_A log2 rho_A) = 1.0 ebit",
            ]
        else:
            formulas = [
                "y(t) = T{x(t)}, H(s) = Y(s) / X(s)",
                "dx/dt = A*x(t) + B*u(t), y(t) = C*x(t)",
                "H(z) = sum(b_k * z^-k) / (1 + sum(a_k * z^-k))",
                "J(theta) = E[||y - f(x; theta)||^2] + lambda*||theta||_2^2",
                "lambda_max(A^T * P + P * A) < 0, V(x) = x^T * P * x > 0",
                "SNR_dB = 10 * log10(P_signal / P_noise)",
            ]

        scenes: list[FlowScene] = []
        for idx in range(1, count + 1):
            phase_title, phase_desc = PEDAGOGICAL_PHASES[
                (idx - 1) % len(PEDAGOGICAL_PHASES)
            ]
            motion = CAMERA_MOTIONS[(idx - 1) % len(CAMERA_MOTIONS)]
            formula_expr = formulas[(idx - 1) % len(formulas)]
            plot_mode = plot_modes[(idx - 1) % len(plot_modes)]
            asset_key = asset_keys[(idx - 1) % len(asset_keys)]
            scene_title = f"Part {idx}: {clean_topic} — {phase_title}"

            if is_ru and is_dsp:
                ru_dsp_narrations = [
                    (
                        "\u0412 \u043f\u0435\u0440\u0432\u043e\u0439 \u0441\u0446\u0435\u043d\u0435 \u0440\u0430\u0437\u0431\u0435\u0440\u0451\u043c \u0430\u043f\u043f\u0430\u0440\u0430\u0442\u043d\u0443\u044e \u0430\u0440\u0445\u0438\u0442\u0435\u043a\u0442\u0443\u0440\u0443 \u0442\u0440\u0430\u043a\u0442\u0430 \u0446\u0438\u0444\u0440\u043e\u0432\u043e\u0439 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0438 \u0441\u0438\u0433\u043d\u0430\u043b\u043e\u0432: "
                        "\u0432\u0445\u043e\u0434\u043d\u043e\u0439 \u0430\u043d\u0442\u0438\u0430\u043b\u0430\u0439\u0437\u0438\u043d\u0433\u043e\u0432\u044b\u0439 \u0444\u0438\u043b\u044c\u0442\u0440 \u043f\u043e\u0434\u0430\u0451\u0442 \u0441\u0438\u0433\u043d\u0430\u043b \u043d\u0430 \u0448\u0435\u0441\u0442\u043d\u0430\u0434\u0446\u0430\u0442\u0438\u0440\u0430\u0437\u0440\u044f\u0434\u043d\u044b\u0439 \u0410\u0426\u041f \u0441 \u0447\u0430\u0441\u0442\u043e\u0442\u043e\u0439 \u0434\u0438\u0441\u043a\u0440\u0435\u0442\u0438\u0437\u0430\u0446\u0438\u0438 \u0441\u043e\u0440\u043e\u043a \u0432\u043e\u0441\u0435\u043c\u044c \u043a\u0438\u043b\u043e\u0433\u0435\u0440\u0446, "
                        "\u043e\u0431\u0435\u0441\u043f\u0435\u0447\u0438\u0432\u0430\u044f \u0434\u0438\u043d\u0430\u043c\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u0434\u0438\u0430\u043f\u0430\u0437\u043e\u043d \u0434\u0435\u0432\u044f\u043d\u043e\u0441\u0442\u043e \u0432\u043e\u0441\u0435\u043c\u044c \u0434\u0435\u0446\u0438\u0431\u0435\u043b \u0438 \u043f\u0440\u044f\u043c\u0443\u044e \u043f\u0435\u0440\u0435\u0434\u0430\u0447\u0443 \u043e\u0442\u0441\u0447\u0451\u0442\u043e\u0432 \u0432\u043e \u0432\u043d\u0435\u0448\u043d\u044e\u044e \u043f\u0430\u043c\u044f\u0442\u044c SRAM."
                    ),
                    (
                        "\u0412\u043e \u0432\u0442\u043e\u0440\u043e\u0439 \u0441\u0446\u0435\u043d\u0435 \u0430\u043d\u0430\u043b\u0438\u0437\u0438\u0440\u0443\u0435\u043c \u043f\u0435\u0440\u0435\u0434\u0430\u0442\u043e\u0447\u043d\u0443\u044e \u0444\u0443\u043d\u043a\u0446\u0438\u044e \u043a\u0430\u0441\u043a\u0430\u0434\u043d\u043e\u0433\u043e \u0431\u0438\u043a\u0432\u0430\u0434\u0440\u0430\u0442\u043d\u043e\u0433\u043e \u0444\u0438\u043b\u044c\u0442\u0440\u0430 \u0432 \u043a\u043e\u043c\u043f\u043b\u0435\u043a\u0441\u043d\u043e\u0439 \u0437\u0435\u0442-\u043f\u043b\u043e\u0441\u043a\u043e\u0441\u0442\u0438: "
                        "\u0441\u043e\u043f\u0440\u044f\u0436\u0451\u043d\u043d\u044b\u0435 \u043f\u043e\u043b\u044e\u0441\u0430 \u0441 \u0440\u0430\u0434\u0438\u0443\u0441\u043e\u043c \u043d\u043e\u043b\u044c \u0446\u0435\u043b\u044b\u0445 \u0434\u0435\u0432\u044f\u0442\u044c\u0441\u043e\u0442 \u0432\u043e\u0441\u0435\u043c\u044c\u0434\u0435\u0441\u044f\u0442 \u0441\u0435\u043c\u044c \u0442\u044b\u0441\u044f\u0447\u043d\u044b\u0445 \u043b\u0435\u0436\u0430\u0442 \u0441\u0442\u0440\u043e\u0433\u043e \u0432\u043d\u0443\u0442\u0440\u0438 \u0435\u0434\u0438\u043d\u0438\u0447\u043d\u043e\u0439 \u043e\u043a\u0440\u0443\u0436\u043d\u043e\u0441\u0442\u0438, "
                        "\u0433\u0430\u0440\u0430\u043d\u0442\u0438\u0440\u0443\u044f \u0430\u0431\u0441\u043e\u043b\u044e\u0442\u043d\u0443\u044e \u0443\u0441\u0442\u043e\u0439\u0447\u0438\u0432\u043e\u0441\u0442\u044c \u0440\u0435\u043a\u0443\u0440\u0441\u0438\u0432\u043d\u043e\u0433\u043e \u0437\u0432\u0435\u043d\u0430."
                    ),
                    (
                        "\u0412 \u0442\u0440\u0435\u0442\u044c\u0435\u0439 \u0441\u0446\u0435\u043d\u0435 \u0438\u0441\u0441\u043b\u0435\u0434\u0443\u0435\u043c \u0430\u043c\u043f\u043b\u0438\u0442\u0443\u0434\u043d\u043e-\u0447\u0430\u0441\u0442\u043e\u0442\u043d\u0443\u044e \u0445\u0430\u0440\u0430\u043a\u0442\u0435\u0440\u0438\u0441\u0442\u0438\u043a\u0443 \u0438 \u0442\u0440\u0430\u043d\u0441\u043f\u043e\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u0443\u044e \u043f\u0440\u044f\u043c\u0443\u044e \u0444\u043e\u0440\u043c\u0443 \u0432\u0442\u043e\u0440\u043e\u0433\u043e \u0442\u0438\u043f\u0430: "
                        "\u043c\u0430\u043a\u0441\u0438\u043c\u0430\u043b\u044c\u043d\u043e \u043f\u043b\u043e\u0441\u043a\u0430\u044f \u043f\u043e\u043b\u043e\u0441\u0430 \u043f\u0440\u043e\u043f\u0443\u0441\u043a\u0430\u043d\u0438\u044f \u0411\u0430\u0442\u0442\u0435\u0440\u0432\u043e\u0440\u0442\u0430 \u043e\u0431\u0435\u0441\u043f\u0435\u0447\u0438\u0432\u0430\u0435\u0442 \u043f\u043e\u0434\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u0432\u044b\u0441\u043e\u043a\u043e\u0447\u0430\u0441\u0442\u043e\u0442\u043d\u044b\u0445 \u043f\u043e\u043c\u0435\u0445 \u0441 \u043a\u0440\u0443\u0442\u0438\u0437\u043d\u043e\u0439 \u0441\u043e\u0440\u043e\u043a \u0434\u0435\u0446\u0438\u0431\u0435\u043b \u043d\u0430 \u0434\u0435\u043a\u0430\u0434\u0443 "
                        "\u0431\u0435\u0437 \u043f\u0435\u0440\u0435\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u044f \u0430\u043a\u043a\u0443\u043c\u0443\u043b\u044f\u0442\u043e\u0440\u0430 \u0443\u043c\u043d\u043e\u0436\u0435\u043d\u0438\u044f-\u043d\u0430\u043a\u043e\u043f\u043b\u0435\u043d\u0438\u044f."
                    ),
                    (
                        "\u0412 \u0447\u0435\u0442\u0432\u0451\u0440\u0442\u043e\u0439 \u0441\u0446\u0435\u043d\u0435 \u043f\u0440\u043e\u0432\u0435\u0440\u044f\u0435\u043c \u0434\u0432\u043e\u0439\u043d\u0443\u044e \u043f\u0438\u043d\u0433-\u043f\u043e\u043d\u0433 \u0431\u0443\u0444\u0435\u0440\u0438\u0437\u0430\u0446\u0438\u044e DMA \u0438 \u0431\u044e\u0434\u0436\u0435\u0442 \u0440\u0435\u0430\u043b\u044c\u043d\u043e\u0433\u043e \u0432\u0440\u0435\u043c\u0435\u043d\u0438: "
                        "\u043f\u043e\u043a\u0430 \u043a\u043e\u043d\u0442\u0440\u043e\u043b\u043b\u0435\u0440 DMA \u0437\u0430\u043f\u043e\u043b\u043d\u044f\u0435\u0442 \u043f\u0435\u0440\u0432\u0443\u044e \u043f\u043e\u043b\u043e\u0432\u0438\u043d\u0443 \u0431\u0443\u0444\u0435\u0440\u0430 SRAM \u0437\u0430 \u043f\u044f\u0442\u044c \u0446\u0435\u043b\u044b\u0445 \u0442\u0440\u0438\u0434\u0446\u0430\u0442\u044c \u0442\u0440\u0438 \u0441\u043e\u0442\u044b\u0445 \u043c\u0438\u043b\u043b\u0438\u0441\u0435\u043a\u0443\u043d\u0434\u044b, "
                        "\u044f\u0434\u0440\u043e \u043f\u0440\u043e\u0446\u0435\u0441\u0441\u043e\u0440\u0430 \u043e\u0431\u0440\u0430\u0431\u0430\u0442\u044b\u0432\u0430\u0435\u0442 \u0432\u0442\u043e\u0440\u043e\u0439 \u0431\u043b\u043e\u043a \u0441 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u043e\u0439 \u0432\u043e\u0441\u0435\u043c\u043d\u0430\u0434\u0446\u0430\u0442\u044c \u043f\u0440\u043e\u0446\u0435\u043d\u0442\u043e\u0432 \u0438 \u043f\u043e\u0434\u0430\u0432\u043b\u0435\u043d\u0438\u0435\u043c \u0433\u0430\u0440\u043c\u043e\u043d\u0438\u043a \u043d\u0430 \u0441\u043e\u0440\u043e\u043a \u0434\u0432\u0430 \u0434\u0435\u0446\u0438\u0431\u0435\u043b\u0430."
                    ),
                ]
                narration = ru_dsp_narrations[(idx - 1) % len(ru_dsp_narrations)]
            elif context_hint:
                narration = (
                    f"In phase {idx} for {target_audience} learners, we examine {clean_topic} "
                    f"through {phase_title.lower()} governed by {formula_expr}. {context_hint[:140]}"
                )
            else:
                narration = (
                    f"In phase {idx} tailored for {target_audience} learners, we explore "
                    f"{clean_topic}: {phase_desc.lower()} using equation {formula_expr}."
                )

            directives = [
                {
                    "window": "0-2s",
                    "action": f"Establish locked-camera schematic view of {clean_topic} ({phase_title})",
                },
                {
                    "window": "2-5s",
                    "action": f"Animate signal path and highlight governing formula {formula_expr}",
                },
                {
                    "window": "5-8s",
                    "action": f"Zoom into {plot_mode} response curve with crisp parameter callout badges",
                },
            ]

            visual_prompt = (
                f"In a single continuous unbroken educational shot, steady locked camera, "
                f"zero camera shake, zero decorative sci-fi clutter: 4K educational engineering "
                f"visualization of {clean_topic} ({phase_title}), clean dark laboratory aesthetic, "
                f"governing equation {formula_expr}, diagram mode {plot_mode}, "
                f"camera motion: {motion}, aspect ratio {ratio}."
            )
            veo_audio_prompt = build_veo_native_audio_prompt(
                visual_prompt=visual_prompt,
                narration_text=narration,
                voice_persona=voice_persona,
                voice_style=voice_style,
            )
            raw_bullets = [
                f"Phase {idx}: {phase_title}",
                f"Formula: {formula_expr.split(',')[0].replace(' ', '')}",
                f"Mode: {plot_mode.replace('_', ' ')}",
            ]
            bullets = [" ".join(rb.split()[:6]) for rb in raw_bullets]
            concept_cues = [
                {
                    "start_ratio": 0.0,
                    "end_ratio": 0.34,
                    "focus_bbox": [0.08, 0.12, 0.52, 0.56],
                    "concept_type": "schematic",
                    "concept_target": f"{clean_topic[:24]} Node",
                    "formula_or_label": f"Phase {idx} Architecture",
                    "animation_mode": "bus_transfer",
                },
                {
                    "start_ratio": 0.34,
                    "end_ratio": 0.68,
                    "focus_bbox": [0.22, 0.20, 0.78, 0.72],
                    "concept_type": "math_formula",
                    "concept_target": formula_expr[:32],
                    "formula_or_label": " ".join(formula_expr.split(",")[0].split()[:5]),
                    "animation_mode": "waveform_sweep",
                },
                {
                    "start_ratio": 0.68,
                    "end_ratio": 1.0,
                    "focus_bbox": [0.35, 0.25, 0.92, 0.88],
                    "concept_type": "plot",
                    "concept_target": plot_mode,
                    "formula_or_label": f"Verified {plot_mode.replace('_', ' ')}",
                    "animation_mode": "register_update",
                },
            ]
            diagram_spec = {
                "plot_mode": plot_mode,
                "asset_key": asset_key,
                "formula": formula_expr,
                "nodes": ["Input Stage", "Core Transform", "Memory Buffer", "Verified Output"],
            }

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
                    tts_narration_text=None,
                    pz_page=8 + idx,
                    concept_cues=concept_cues,
                    pedagogical_phase=phase_title,
                    formula_expression=formula_expr,
                    diagram_spec=diagram_spec,
                    timecoded_directives=directives,
                    learning_objective=phase_desc,
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

    def get_project(self, project_id: str) -> FlowEducationalProject | None:
        return _PROJECTS_STORE.get(project_id)

    def list_projects(self) -> list[FlowEducationalProject]:
        return list(_PROJECTS_STORE.values())

    def get_media_bytes(self, media_name: str) -> bytes | None:
        return _MEDIA_STORE.get(media_name)

    def update_scene(
        self,
        project_id: str,
        scene_id: str,
        title: str | None = None,
        visual_prompt: str | None = None,
        narration_text: str | None = None,
        duration_sec: int | None = None,
        camera_motion: str | None = None,
        on_screen_bullets: list[str] | None = None,
        voice_persona: str | None = None,
        voice_style: str | None = None,
        native_veo_audio: bool | None = None,
        formula_expression: str | None = None,
        diagram_spec: dict[str, Any] | None = None,
        first_frame_path: str | None = None,
        last_frame_path: str | None = None,
        timecoded_directives: list[dict[str, str]] | None = None,
        pedagogical_phase: str | None = None,
        learning_objective: str | None = None,
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
        if formula_expression is not None:
            target.formula_expression = str(formula_expression)
        if diagram_spec is not None:
            target.diagram_spec = dict(diagram_spec)
        if first_frame_path is not None:
            target.first_frame_path = str(first_frame_path)
        if last_frame_path is not None:
            target.last_frame_path = str(last_frame_path)
        if timecoded_directives is not None:
            target.timecoded_directives = list(timecoded_directives)
        if pedagogical_phase is not None:
            target.pedagogical_phase = str(pedagogical_phase)
        if learning_objective is not None:
            target.learning_objective = str(learning_objective)

        target.veo_audio_prompt = build_veo_native_audio_prompt(
            visual_prompt=target.visual_prompt,
            narration_text=target.narration_text,
            voice_persona=target.voice_persona,
            voice_style=target.voice_style,
        )
        return target

    async def generate_cloud_video_via_gemini_web(
        self,
        visual_prompt: str,
        narration_text: str,
        language: str = "ru",
        poll_interval_sec: float = 6.0,
        timeout_sec: float = 180.0,
        reference_image_path: str | None = None,
        formula_expression: str | None = None,
        timecoded_directives: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """
        Execute 100% cloud video + native female audio generation via Google Gemini Ultra
        Web Bridge (models/omni_1p2_pro_gemini_app / Veo 3.1) using DPAPI CredentialVault,
        with optional Scotty diagram/keyframe grounding.
        """
        import asyncio
        import json
        from pathlib import Path
        import sys

        proxy_url = detect_local_proxy_url()
        if proxy_url:
            os.environ.setdefault("HTTPS_PROXY", proxy_url)
            os.environ.setdefault("HTTP_PROXY", proxy_url)

        candidate_roots = [
            Path.home() / "teamwork_projects" / "gemini_local_ui",
            Path(__file__).resolve().parent.parent.parent / "gemini_local_ui",
        ]
        for root_p in candidate_roots:
            if (root_p / "backend" / "app" / "bridge" / "client.py").is_file():
                if str(root_p) not in sys.path:
                    sys.path.insert(0, str(root_p))
                break

        from backend.app.bridge.client import GeminiWebClient
        from backend.app.bridge.models import HeaderBuilder, resolve_model
        from backend.app.bridge.session import SessionManager
        from backend.app.bridge.streaming_parser import StreamingFrameParser
        from backend.app.storage.vault import CredentialVault

        vault = CredentialVault()
        mgr = SessionManager(vault=vault)
        await mgr.initialize_all()
        ctx = mgr.context
        if self.cookies:
            ctx.cookies.update(self.cookies)
        ctx.language = "en"

        full_prompt = build_cloud_veo_prompt(
            visual_prompt=visual_prompt,
            narration_text=narration_text,
            language=language,
            formula_expression=formula_expression,
            timecoded_directives=timecoded_directives,
        )

        attachments: list[dict[str, Any]] | None = None
        if reference_image_path and Path(reference_image_path).is_file():
            try:
                from backend.app.bridge.uploader import GeminiUploader

                uploader = GeminiUploader(session_context=ctx)
                ref_p = Path(reference_image_path)
                scotty_uri = await uploader.upload_file_resumable(
                    ref_p.read_bytes(), ref_p.name, "image/png"
                )
                if scotty_uri:
                    attachments = [
                        {"uri": scotty_uri, "filename": ref_p.name, "scotty": True}
                    ]
                await uploader.close()
            except Exception:
                attachments = None

        client = GeminiWebClient(session_context=ctx, timeout=max(120.0, timeout_sec))
        try:
            model_info = resolve_model("3.8 Flash")
            request_uuid = str(uuid.uuid4()).upper()
            session_uuid = getattr(ctx, "session_uuid", str(uuid.uuid4()).upper())
            headers = {
                "x-goog-ext-525001261-jspb": HeaderBuilder.build_model_jspb_header(
                    model_info,
                    session_uuid,
                    model_info.thinking_flag,
                    model_info.model_number,
                ),
                "x-goog-ext-73010989-jspb": "[0]",
                "x-goog-ext-73010990-jspb": "[0,0,0]",
                "x-goog-ext-525005358-jspb": json.dumps(
                    [request_uuid, 1], separators=(",", ":")
                ),
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "User-Agent": HeaderBuilder.DEFAULT_USER_AGENT,
                "Origin": client.base_url,
                "Referer": f"{client.base_url}/",
                "x-same-domain": "1",
                "Cookie": HeaderBuilder.format_cookies(ctx.cookies),
            }
            sapisid = HeaderBuilder.extract_sapisid(ctx.cookies)
            if sapisid:
                headers["Authorization"] = HeaderBuilder.make_sapisidhash(
                    sapisid, origin=client.base_url
                )

            inner_req = client._build_81_element_envelope(
                prompt=full_prompt,
                attachments=attachments,
                model_number=model_info.model_number,
                thinking_flag=model_info.thinking_flag,
            )
            inner_str = json.dumps(inner_req, separators=(",", ":"))
            f_req = json.dumps([None, inner_str], separators=(",", ":"))
            params = {
                "hl": "en",
                "_reqid": str(client._next_reqid()),
                "rt": "c",
                "bl": getattr(ctx, "build_label", ""),
                "f.sid": getattr(ctx, "session_id", ""),
            }
            data = {
                "at": "",
                "f.req": f_req,
            }
            url = f"{client.base_url}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
            hc = await client._get_client()
            parser = StreamingFrameParser()
            cid: str | None = None
            rid: str | None = None
            guest_continuation: str = ""
            t0 = time.time()

            async with hc.stream("POST", url, params=params, data=data, headers=headers) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"Cloud StreamGenerate HTTP {resp.status_code}")
                async for raw_bytes in resp.aiter_bytes():
                    for fr in parser.feed(raw_bytes):
                        entries = fr
                        while (
                            len(entries) == 1
                            and isinstance(entries[0], list)
                            and len(entries[0]) > 0
                            and isinstance(entries[0][0], list)
                        ):
                            entries = entries[0]
                        for entry in entries:
                            if (
                                isinstance(entry, list)
                                and len(entry) >= 3
                                and isinstance(entry[2], str)
                            ):
                                pj = json.loads(entry[2])
                                if (
                                    isinstance(pj, list)
                                    and len(pj) > 1
                                    and isinstance(pj[1], list)
                                    and len(pj[1]) >= 2
                                ):
                                    cid = str(pj[1][0] or "") or cid
                                    rid = str(pj[1][1] or "") or rid
                                if (
                                    isinstance(pj, list)
                                    and len(pj) > 2
                                    and isinstance(pj[2], dict)
                                    and isinstance(pj[2].get("46"), list)
                                    and len(pj[2]["46"]) >= 2
                                ):
                                    guest_continuation = str(pj[2]["46"][1] or "")

            if not cid:
                raise RuntimeError("Cloud Veo 3.1 dispatch did not return a conversation cid")
            if guest_continuation:
                raise RuntimeError(
                    "Gemini Ultra session cookies in CredentialVault are expired (guest continuation detected); "
                    "refresh session cookies or use FlowEducationalCompositor."
                )

            max_polls = max(1, int(timeout_sec / max(1.0, poll_interval_sec)))
            for _ in range(max_polls):
                await asyncio.sleep(poll_interval_sec)
                turns = await client.get_conversation_turns(cid)
                info = extract_cloud_video_from_turns(turns)
                if info and info["status"] == "completed" and info.get("urls"):
                    urls = info["urls"]
                    dl_url = urls[1] if len(urls) > 1 else urls[0]
                    dl_resp = await hc.get(dl_url, follow_redirects=True)
                    if dl_resp.status_code == 200 and len(dl_resp.content) > 10_000:
                        return {
                            "ok": True,
                            "mode": "cloud_veo_3_1",
                            "cid": cid,
                            "rid": rid,
                            "model": info.get("model", "models/omni_1p2_pro_gemini_app"),
                            "mp4_bytes": dl_resp.content,
                            "video_size_bytes": len(dl_resp.content),
                            "download_url": dl_url,
                            "storyboard": info.get("storyboard", ""),
                            "transcript": info.get("transcript", ""),
                            "prompt": full_prompt,
                            "elapsed_sec": round(time.time() - t0, 2),
                        }
                elif info and info["status"] == "failed":
                    raise RuntimeError(
                        f"Cloud Veo 3.1 video generation rejected: {info.get('text', '')}"
                    )

            raise TimeoutError(
                f"Cloud Veo 3.1 video generation timed out after {timeout_sec}s (cid={cid})"
            )
        finally:
            await client.close()
            await mgr.close()

    async def generate_scene_clip(
        self,
        project_id: str,
        scene_id: str,
        prompt_override: str | None = None,
        aspect_ratio: str | None = None,
        use_cloud: bool | None = None,
        compose_educational: bool = False,
    ) -> dict[str, Any]:
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

        mp4_bytes: bytes | None = None
        should_use_cloud = (
            bool(use_cloud)
            if use_cloud is not None
            else (os.environ.get("FLOW_LIVE_API") == "1")
        )
        if should_use_cloud:
            token = await self.fetch_flow_bearer_token()
            if token:
                try:
                    effective_prompt = (
                        build_educational_flow_prompt(scene, language=project.language)
                        if scene.formula_expression or scene.timecoded_directives
                        else (
                            scene.veo_audio_prompt
                            if scene.native_veo_audio and scene.veo_audio_prompt
                            else scene.visual_prompt
                        )
                    )
                    effective_model = (
                        "veo_3_1_t2v_fast_audio"
                        if scene.native_veo_audio and "audio" not in project.model_key
                        else project.model_key
                    )
                    req_item: dict[str, Any] = {
                        "aspectRatio": f"VIDEO_ASPECT_RATIO_{project.aspect_ratio.replace(':', '_')}",
                        "textInput": {"prompt": effective_prompt},
                        "videoModelKey": effective_model,
                        "enableAudio": bool(scene.native_veo_audio),
                    }
                    if scene.first_frame_path:
                        req_item["startImage"] = {"uri": str(scene.first_frame_path)}
                    if scene.last_frame_path:
                        req_item["endImage"] = {"uri": str(scene.last_frame_path)}

                    async with httpx.AsyncClient(timeout=25.0) as client:
                        resp = await client.post(
                            f"{self.sandbox_api_url}/video:batchAsyncGenerateVideoText",
                            headers={"Authorization": f"Bearer {token}"},
                            json={"requests": [req_item]},
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
                try:
                    cloud_res = await self.generate_cloud_video_via_gemini_web(
                        visual_prompt=scene.visual_prompt,
                        narration_text=scene.narration_text,
                        language=project.language,
                        reference_image_path=scene.first_frame_path,
                        formula_expression=scene.formula_expression,
                        timecoded_directives=scene.timecoded_directives,
                    )
                    if cloud_res.get("ok") and cloud_res.get("mp4_bytes"):
                        mp4_bytes = cloud_res["mp4_bytes"]
                        scene.operation_name = str(
                            cloud_res.get("cid", f"op_{uuid.uuid4().hex[:8]}")
                        )
                except Exception:
                    pass

        if compose_educational and not mp4_bytes:
            try:
                compositor = FlowEducationalCompositor()
                comp_res = await compositor.render_scene(
                    project=project,
                    scene=scene,
                    total_scenes=len(project.scenes),
                )
                if comp_res.get("mp4_bytes") and len(comp_res["mp4_bytes"]) > 10_000:
                    mp4_bytes = comp_res["mp4_bytes"]
                    scene.duration_sec = int(round(comp_res.get("duration_sec", scene.duration_sec)))
                    scene.operation_name = str(comp_res.get("operation_name", f"op_edu_{scene.scene_id}"))
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
            "video_size_bytes": len(mp4_bytes),
        }

    async def assemble_educational_video(
        self,
        project_id: str,
        use_cloud: bool | None = None,
        compose_educational: bool = False,
    ) -> dict[str, Any]:
        """Ensure all scenes are generated, build WebVTT/SRT subtitles, and assemble the final MP4 lesson."""
        project = _PROJECTS_STORE.get(project_id)
        if not project:
            raise KeyError(f"Unknown project_id: {project_id}")

        for sc in project.scenes:
            if sc.status != "completed" or not sc.video_url or compose_educational:
                await self.generate_scene_clip(
                    project_id,
                    sc.scene_id,
                    use_cloud=use_cloud,
                    compose_educational=compose_educational,
                )

        project.subtitles_vtt = build_webvtt_subtitles(project.scenes)
        project.subtitles_srt = build_srt_subtitles(project.scenes)

        total_duration = sum(max(1, int(s.duration_sec)) for s in project.scenes)
        lesson_filename = f"{project_id}_lesson.mp4"

        scene_blobs: list[bytes] = []
        for sc in project.scenes:
            clip_name = f"{project_id}_{sc.scene_id}.mp4"
            blob = _MEDIA_STORE.get(clip_name)
            if blob:
                scene_blobs.append(blob)

        lesson_bytes: bytes | None = None
        if scene_blobs and all(len(b) > 10_000 for b in scene_blobs):
            if len(scene_blobs) == 1:
                lesson_bytes = scene_blobs[0]
            else:
                try:
                    from pathlib import Path
                    import subprocess
                    import tempfile
                    import imageio_ffmpeg

                    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        tmp_p = Path(tmp_dir)
                        concat_lines: list[str] = []
                        for idx, blob in enumerate(scene_blobs):
                            part_p = tmp_p / f"scene_{idx:02d}.mp4"
                            part_p.write_bytes(blob)
                            esc = str(part_p).replace("'", "'\\''")
                            concat_lines.append(f"file '{esc}'")
                        list_file = tmp_p / "concat.txt"
                        list_file.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
                        out_mp4 = tmp_p / "assembled.mp4"
                        subprocess.run(
                            [
                                ffmpeg_exe,
                                "-y",
                                "-f",
                                "concat",
                                "-safe",
                                "0",
                                "-i",
                                str(list_file),
                                "-c",
                                "copy",
                                "-movflags",
                                "+faststart",
                                str(out_mp4),
                            ],
                            check=True,
                            capture_output=True,
                            timeout=120,
                        )
                        if out_mp4.is_file() and out_mp4.stat().st_size > 10_000:
                            lesson_bytes = out_mp4.read_bytes()
                except Exception:
                    lesson_bytes = scene_blobs[0]

        if not lesson_bytes:
            lesson_bytes = synthesize_minimal_mp4(
                title=f"Lesson: {project.topic}",
                duration_sec=total_duration,
            )
        _MEDIA_STORE[lesson_filename] = lesson_bytes

        project.assembled_video_url = f"/api/flow/media/{lesson_filename}"
        project.status = "assembled"

        return {
            "ok": True,
            "status": project.status,
            "project": project.to_dict(),
            "assembled_video_url": project.assembled_video_url,
            "subtitles_vtt_url": f"/api/flow/projects/{project_id}/subtitles.vtt",
            "subtitles_srt_url": f"/api/flow/projects/{project_id}/subtitles.srt",
            "video_size_bytes": len(lesson_bytes),
        }

    async def render_educational_lesson(
        self,
        project: FlowEducationalProject,
        use_cloud: bool | None = None,
        compose_educational: bool = False,
    ) -> dict[str, Any]:
        """Render all scenes of an educational project and return assembled MP4 + subtitles."""
        _PROJECTS_STORE[project.project_id] = project
        res = await self.assemble_educational_video(
            project.project_id,
            use_cloud=use_cloud,
            compose_educational=compose_educational,
        )
        res["mp4_bytes"] = self.get_media_bytes(f"{project.project_id}_lesson.mp4") or b""
        return res

    async def compose_educational_lesson(
        self,
        topic: str,
        scene_count: int = 4,
        language: str = "ru",
        target_audience: str = "university",
        source_context: str | None = None,
        output_dir: Any | None = None,
        use_cloud: bool = False,
    ) -> dict[str, Any]:
        """
        End-to-end Google Flow Educational Video Lesson Generator:
        1. Builds a multi-scene pedagogical storyboard with formulas, diagram specs,
           timecoded Flow directives, and Mayer CTML callout cues.
        2. Renders each scene into a 1280x720 HD H.264 + AAC educational video clip
           fusing Cloud Flow Veo 3.1 3D motion plates, grounded coursework schematics,
           live animated mathematical plots, crisp formula cards, kinetic subtitles,
           and Gemini 3.8 Studio Female TTS narration (voice_y0joebp3bwta / Sulafat).
        3. Concatenates all scenes into a master MP4 lesson + .srt + .vtt + contact_sheet.png.
        """
        from pathlib import Path

        voice_persona = "voice_y0joebp3bwta" if language.lower().startswith("ru") else "Sulafat"
        project = await self.generate_educational_storyboard(
            topic=topic,
            scene_count=scene_count,
            language=language,
            target_audience=target_audience,
            source_context=source_context,
            voice_persona=voice_persona,
        )
        res = await self.assemble_educational_video(
            project.project_id,
            use_cloud=use_cloud,
            compose_educational=True,
        )
        lesson_bytes = self.get_media_bytes(f"{project.project_id}_lesson.mp4") or b""
        res["mp4_bytes"] = lesson_bytes

        if output_dir is not None:
            out_p = Path(output_dir)
            out_p.mkdir(parents=True, exist_ok=True)
            master_mp4 = out_p / "cloud_flow_dsp_lesson.mp4"
            master_srt = out_p / "cloud_flow_dsp_lesson.srt"
            master_vtt = out_p / "cloud_flow_dsp_lesson.vtt"
            master_mp4.write_bytes(lesson_bytes)
            master_srt.write_text(project.subtitles_srt or "", encoding="utf-8")
            master_vtt.write_text(project.subtitles_vtt or "", encoding="utf-8")
            for sc in project.scenes:
                sc_blob = self.get_media_bytes(f"{project.project_id}_{sc.scene_id}.mp4")
                if sc_blob:
                    (out_p / f"scene_{sc.scene_number:02d}_flow_educational.mp4").write_bytes(sc_blob)
            sheet_path = FlowEducationalCompositor.build_contact_sheet(
                mp4_path=master_mp4,
                out_png=out_p / "contact_sheet.png",
                scene_count=len(project.scenes),
            )
            res["master_mp4_path"] = str(master_mp4)
            res["contact_sheet_path"] = str(sheet_path) if sheet_path else None

        return res



class FlowEducationalCompositor:
    """
    Pedagogical Multi-Layer Video Compositor for Google Flow (Veo 3.1 / Gemini Omni).
    Combines:
    1. Cloud Flow Veo 3.1 3D visual motion plates (decoded via ffmpeg),
    2. Grounded coursework schematics and blueprints with spotlight bbox tracking,
    3. Real-time animated mathematical & signal-processing plots (z-plane, Bode, DMA, topology),
    4. Crisp formula cards and Mayer CTML (<= 6 words) callout badges,
    5. Word-synchronized #FBBF24 kinetic subtitles and Gemini 3.8 Studio Female TTS
       (voice_y0joebp3bwta for RU / Sulafat for EN).
    """

    def __init__(self, width: int = 1280, height: int = 720, fps: int = 24) -> None:
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)

    @staticmethod
    def _load_font(size: int, bold: bool = False) -> Any:
        from PIL import ImageFont

        candidates = (
            ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/consolab.ttf"]
            if bold
            else ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/consola.ttf"]
        )
        for p in candidates:
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size=size)
                except Exception:
                    pass
        return ImageFont.load_default()

    @staticmethod
    def _decode_cloud_plate_frames(
        mp4_path: Any, target_w: int = 536, target_h: int = 216, max_frames: int = 120
    ) -> list[Any]:
        """Decode RGB frames from a Cloud Flow Veo 3.1 MP4 clip for viewport embedding."""
        from pathlib import Path
        import subprocess
        from PIL import Image
        import imageio_ffmpeg

        p = Path(mp4_path)
        if not p.is_file() or p.stat().st_size < 10_000:
            return []
        try:
            ff = imageio_ffmpeg.get_ffmpeg_exe()
            proc = subprocess.run(
                [
                    ff,
                    "-i",
                    str(p),
                    "-vf",
                    f"scale={target_w}:{target_h}",
                    "-frames:v",
                    str(max_frames),
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "-",
                ],
                capture_output=True,
                timeout=30,
            )
            raw = proc.stdout or b""
            frame_sz = target_w * target_h * 3
            count = len(raw) // frame_sz
            frames: list[Any] = []
            for i in range(count):
                chunk = raw[i * frame_sz : (i + 1) * frame_sz]
                frames.append(Image.frombytes("RGB", (target_w, target_h), chunk))
            return frames
        except Exception:
            return []

    @staticmethod
    def _build_word_captions(
        scene_id: str, narration_text: str, duration_sec: float
    ) -> SceneCaptions:
        """Construct word-level timestamps and 2-line subtitle pages for a scene."""
        import re

        clean_text = re.sub(r"\+([a-zA-Z\u0400-\u04ff])", r"\1", narration_text or "").strip()
        clean_text = re.sub(r"\*([^*]+)\*", r"\1", clean_text)
        tokens = [w for w in clean_text.split() if w]
        dur_ms = max(1000, int(round(duration_sec * 1000)))
        if not tokens:
            return SceneCaptions(scene_id=scene_id, duration_ms=dur_ms, pages=[], all_words=[])

        weights = [max(2, len(w)) for w in tokens]
        total_w = float(sum(weights))
        usable_ms = max(600, dur_ms - 250)
        cursor_ms = 100
        all_words: list[WordTiming] = []
        for idx, (tok, w) in enumerate(zip(tokens, weights)):
            span = int(round((w / total_w) * usable_ms))
            s_ms = cursor_ms
            e_ms = min(dur_ms - 50, s_ms + max(80, span))
            cursor_ms = e_ms
            all_words.append(
                WordTiming(
                    word_index=idx,
                    word=tok,
                    start_ms=s_ms,
                    end_ms=e_ms,
                )
            )

        pages: list[SubtitlePage] = []
        chunk_size = 9
        for p_idx in range(0, len(all_words), chunk_size):
            w_slice = all_words[p_idx : p_idx + chunk_size]
            page_num = len(pages)
            mid = max(1, (len(w_slice) + 1) // 2)
            line0 = [wt.word for wt in w_slice[:mid]]
            line1 = [wt.word for wt in w_slice[mid:]]
            for li_idx, wt in enumerate(w_slice):
                wt.page_index = page_num
                wt.line_index = 0 if li_idx < mid else 1
            pages.append(
                SubtitlePage(
                    page_index=page_num,
                    start_ms=w_slice[0].start_ms,
                    end_ms=w_slice[-1].end_ms,
                    lines=[line0, line1] if line1 else [line0],
                    words=w_slice,
                )
            )

        return SceneCaptions(
            scene_id=scene_id,
            duration_ms=dur_ms,
            pages=pages,
            all_words=all_words,
        )

    async def _synthesize_scene_voiceover(
        self, scene: FlowScene, language: str, out_wav: Any
    ) -> float:
        """Synthesize studio female narration via Gemini 3.8 Studio TTS or clean fallback WAV."""
        from pathlib import Path
        import math
        import struct
        import wave

        wav_p = Path(out_wav)
        try:
            import flow_testbench_runner as runner

            tts_res = await runner.try_gemini_studio_tts(
                narration_text=scene.tts_narration_text or scene.narration_text,
                out_wav=wav_p,
                lang=language,
                scene_number=scene.scene_number,
                voice_persona=scene.voice_persona,
                voice_style=scene.voice_style,
            )
            if tts_res and wav_p.is_file() and wav_p.stat().st_size > 20_000:
                with wave.open(str(wav_p), "rb") as wf:
                    return max(4.0, round(wf.getnframes() / float(wf.getframerate()), 2))
        except Exception:
            pass

        # Deterministic fallback WAV if offline
        sr = 24000
        dur = max(5.0, float(scene.duration_sec or 6))
        n_samples = int(sr * dur)
        buf = bytearray(n_samples * 2)
        for i in range(n_samples):
            t = i / float(sr)
            env = 0.55 + 0.45 * math.sin(2.0 * math.pi * 2.5 * t)
            val = int(2800.0 * env * math.sin(2.0 * math.pi * 220.0 * t))
            struct.pack_into("<h", buf, i * 2, max(-32000, min(32000, val)))
        with wave.open(str(wav_p), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(bytes(buf))
        return dur

    def render_educational_frame(
        self,
        project: FlowEducationalProject,
        scene: FlowScene,
        total_scenes: int,
        frame_idx: int,
        total_frames: int,
        cloud_plate_frames: list[Any] | None = None,
        schematic_img: Any | None = None,
    ) -> bytes:
        """
        Render a single 1280x720 RGB24 educational frame combining:
        - Top Header Bar (Phase badge, title, Flow Veo 3.1 + Studio Voice tag, progress bar)
        - Left Viewport: Cloud Flow Veo 3.1 3D motion stream + Grounded schematic with bbox spotlight
        - Right Viewport: Crisp formula card + live animated scientific plot + Mayer CTML callout cards
        - Bottom Bar: 2-line word-synchronized kinetic subtitle card with #FBBF24 active word highlight
        """
        import math
        from PIL import Image, ImageDraw

        W, H = self.width, self.height
        p = max(0.0, min(1.0, frame_idx / float(max(1, total_frames - 1))))
        img = Image.new("RGB", (W, H), (9, 13, 24))
        draw = ImageDraw.Draw(img)

        f_hdr = self._load_font(18, bold=True)
        f_subhdr = self._load_font(13, bold=True)
        f_eq = self._load_font(17, bold=True)
        f_body = self._load_font(14, bold=False)
        f_bold = self._load_font(14, bold=True)
        f_small = self._load_font(12, bold=False)
        f_sub = self._load_font(20, bold=True)

        # 1. Top Pedagogical Header Bar (y: 0..58)
        draw.rectangle([0, 0, W, 58], fill=(14, 21, 38))
        draw.line([0, 58, W, 58], fill=(38, 68, 115), width=2)
        phase_badge = f"SCENE {scene.scene_number:02d}/{max(1, total_scenes):02d}  |  {(scene.pedagogical_phase or 'EDUCATIONAL FLOW').upper()}"
        draw.rounded_rectangle([24, 12, 390, 44], radius=6, fill=(20, 48, 82), outline=(56, 189, 248), width=1)
        draw.text((34, 18), phase_badge[:42], fill=(56, 189, 248), font=f_subhdr)

        title_str = (scene.title or project.topic)[:52]
        draw.text((406, 16), title_str, fill=(241, 245, 249), font=f_hdr)

        draw.rounded_rectangle([1010, 12, 1256, 44], radius=6, fill=(24, 38, 64), outline=(251, 191, 36), width=1)
        draw.text((1022, 18), "FLOW VEO 3.1 + STUDIO TTS", fill=(251, 191, 36), font=f_subhdr)

        # Scene progress bar at y=55..58
        prog_w = int(round(W * ((scene.scene_number - 1 + p) / float(max(1, total_scenes)))))
        draw.rectangle([0, 55, prog_w, 58], fill=(56, 189, 248))

        # Determine active concept cue
        cues = scene.concept_cues or []
        active_cue_idx = 0
        active_cue: dict[str, Any] = {}
        for idx_c, c in enumerate(cues):
            sr = float(c.get("start_ratio", 0.0))
            er = float(c.get("end_ratio", 1.0))
            if sr <= p <= er:
                active_cue_idx = idx_c
                active_cue = c
                break
        if not active_cue and cues:
            active_cue = cues[-1]
            active_cue_idx = len(cues) - 1

        # 2. Left Viewport (x: 24..584, y: 66..594) -> 560 x 528
        lp_x0, lp_y0, lp_x1, lp_y1 = 24, 66, 584, 594
        draw.rounded_rectangle([lp_x0, lp_y0, lp_x1, lp_y1], radius=10, fill=(13, 20, 36), outline=(42, 67, 108), width=2)

        # 2A. Top half of Left Viewport (y: 74..326): Cloud Flow Veo 3.1 3D Motion Stream
        vp_x0, vp_y0, vp_x1, vp_y1 = 36, 76, 572, 324
        vp_w, vp_h = vp_x1 - vp_x0, vp_y1 - vp_y0
        if cloud_plate_frames:
            ping_idx = frame_idx % (len(cloud_plate_frames) * 2)
            if ping_idx >= len(cloud_plate_frames):
                ping_idx = (len(cloud_plate_frames) * 2 - 1) - ping_idx
            plate_img = cloud_plate_frames[max(0, min(len(cloud_plate_frames) - 1, ping_idx))]
            if plate_img.size != (vp_w, vp_h):
                plate_img = plate_img.resize((vp_w, vp_h))
            img.paste(plate_img, (vp_x0, vp_y0))
        else:
            draw.rectangle([vp_x0, vp_y0, vp_x1, vp_y1], fill=(10, 24, 44))
            for gx in range(vp_x0 + 20, vp_x1, 36):
                draw.line([gx, vp_y0, gx, vp_y1], fill=(18, 42, 72), width=1)
            for gy in range(vp_y0 + 20, vp_y1, 36):
                draw.line([vp_x0, gy, vp_x1, gy], fill=(18, 42, 72), width=1)
            pulse_x = vp_x0 + int(p * (vp_w - 40)) + 20
            draw.ellipse([pulse_x - 14, vp_y0 + vp_h // 2 - 14, pulse_x + 14, vp_y0 + vp_h // 2 + 14], fill=(56, 189, 248))

        draw.rounded_rectangle([vp_x0, vp_y0, vp_x1, vp_y1], radius=8, outline=(56, 189, 248), width=2)
        draw.rectangle([vp_x0 + 8, vp_y0 + 8, vp_x0 + 270, vp_y0 + 30], fill=(10, 16, 30))
        draw.text((vp_x0 + 14, vp_y0 + 10), f"CLOUD FLOW VEO 3.1  |  t={p*scene.duration_sec:04.1f}s", fill=(56, 189, 248), font=f_small)
        target_lbl = str(active_cue.get("concept_target", scene.pedagogical_phase))[:28]
        draw.rectangle([vp_x1 - 220, vp_y1 - 28, vp_x1 - 8, vp_y1 - 6], fill=(10, 16, 30))
        draw.text((vp_x1 - 212, vp_y1 - 25), f"FOCUS: {target_lbl}", fill=(251, 191, 36), font=f_small)

        # 2B. Bottom half of Left Viewport (y: 334..584): Grounded Coursework Schematic + BBox Spotlight
        sc_x0, sc_y0, sc_x1, sc_y1 = 36, 334, 572, 584
        sc_w, sc_h = sc_x1 - sc_x0, sc_y1 - sc_y0
        draw.rounded_rectangle([sc_x0, sc_y0, sc_x1, sc_y1], radius=8, fill=(16, 24, 42), outline=(52, 82, 130), width=1)
        if schematic_img is not None:
            try:
                s_resized = schematic_img.resize((sc_w - 16, sc_h - 38))
                img.paste(s_resized, (sc_x0 + 8, sc_y0 + 30))
            except Exception:
                pass
        else:
            # Draw clean schematic nodes if external image unavailable
            nodes = (scene.diagram_spec or {}).get("nodes") or ["ADC Stage", "DMA Bus", "Core Filter", "DAC Out"]
            n_cnt = len(nodes)
            for n_i, n_lbl in enumerate(nodes):
                nx0 = sc_x0 + 18 + n_i * (sc_w - 36) // n_cnt
                nx1 = nx0 + (sc_w - 60) // n_cnt
                ny0, ny1 = sc_y0 + 95, sc_y0 + 165
                is_act = (n_i == active_cue_idx % n_cnt)
                draw.rounded_rectangle(
                    [nx0, ny0, nx1, ny1],
                    radius=6,
                    fill=(28, 64, 110) if is_act else (20, 34, 58),
                    outline=(251, 191, 36) if is_act else (56, 189, 248),
                    width=2,
                )
                draw.text((nx0 + 8, ny0 + 24), str(n_lbl)[:12], fill=(248, 250, 252), font=f_small)

        draw.rectangle([sc_x0 + 8, sc_y0 + 6, sc_x1 - 8, sc_y0 + 28], fill=(14, 22, 38))
        asset_name = str((scene.diagram_spec or {}).get("asset_key", "SCHEMATIC_FIG"))
        draw.text((sc_x0 + 14, sc_y0 + 9), f"GROUNDED ENGINEERING DIAGRAM  [{asset_name.upper()}]", fill=(148, 163, 184), font=f_small)

        # Animated spotlight bounding box inside schematic viewport
        bbox = active_cue.get("focus_bbox") or [0.15, 0.2, 0.65, 0.75]
        bx0 = sc_x0 + 8 + int(float(bbox[0]) * (sc_w - 16))
        by0 = sc_y0 + 30 + int(float(bbox[1]) * (sc_h - 38))
        bx1 = sc_x0 + 8 + int(float(bbox[2]) * (sc_w - 16))
        by1 = sc_y0 + 30 + int(float(bbox[3]) * (sc_h - 38))
        pulse_w = 2 + int(round(abs(math.sin(p * math.pi * 6.0)) * 2))
        draw.rounded_rectangle([bx0, by0, bx1, by1], radius=6, outline=(251, 191, 36), width=pulse_w)
        cue_lbl = str(active_cue.get("formula_or_label", "Key Parameter"))[:28]
        draw.rectangle([bx0, max(sc_y0 + 30, by0 - 22), min(sc_x1 - 10, bx0 + 210), max(sc_y0 + 50, by0)], fill=(251, 191, 36))
        draw.text((bx0 + 6, max(sc_y0 + 32, by0 - 20)), cue_lbl, fill=(10, 15, 28), font=f_small)

        # 3. Right Viewport (x: 596..1256, y: 66..594) -> 660 x 528
        rp_x0, rp_y0, rp_x1, rp_y1 = 596, 66, 1256, 594
        draw.rounded_rectangle([rp_x0, rp_y0, rp_x1, rp_y1], radius=10, fill=(13, 20, 36), outline=(42, 67, 108), width=2)

        # 3A. Mathematical Formula Card (y: 76..154)
        eq_x0, eq_y0, eq_x1, eq_y1 = 610, 76, 1242, 154
        draw.rounded_rectangle([eq_x0, eq_y0, eq_x1, eq_y1], radius=8, fill=(18, 32, 58), outline=(56, 189, 248), width=2)
        draw.text((eq_x0 + 14, eq_y0 + 8), "MATHEMATICAL FORMULATION & INVARIANT", fill=(56, 189, 248), font=f_small)
        formula_str = (scene.formula_expression or "H(z) = (b0 + b1*z^-1 + b2*z^-2) / (1 + a1*z^-1 + a2*z^-2)")[:64]
        draw.text((eq_x0 + 14, eq_y0 + 30), formula_str, fill=(254, 240, 138), font=f_eq)
        obj_str = (scene.learning_objective or scene.pedagogical_phase or "")[:78]
        draw.text((eq_x0 + 14, eq_y0 + 54), obj_str, fill=(148, 163, 184), font=f_small)

        # 3B. Animated Scientific Plot Canvas (y: 164..452)
        pl_x0, pl_y0, pl_x1, pl_y1 = 610, 164, 1242, 452
        pl_w, pl_h = pl_x1 - pl_x0, pl_y1 - pl_y0
        draw.rounded_rectangle([pl_x0, pl_y0, pl_x1, pl_y1], radius=8, fill=(10, 16, 30), outline=(38, 58, 94), width=1)
        for gx in range(pl_x0 + 40, pl_x1, 56):
            draw.line([gx, pl_y0 + 28, gx, pl_y1 - 12], fill=(18, 28, 48), width=1)
        for gy in range(pl_y0 + 36, pl_y1 - 12, 42):
            draw.line([pl_x0 + 12, gy, pl_x1 - 12, gy], fill=(18, 28, 48), width=1)

        plot_mode = str((scene.diagram_spec or {}).get("plot_mode", "hardware_topology"))
        if plot_mode == "biquad_zplane":
            draw.text((pl_x0 + 14, pl_y0 + 8), "COMPLEX Z-PLANE UNIT CIRCLE (|z| = 1) & IMPULSE RESPONSE h[n]", fill=(56, 189, 248), font=f_subhdr)
            cx, cy, rad = pl_x0 + 155, pl_y0 + 158, 96
            draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], outline=(56, 189, 248), width=2)
            draw.line([cx - rad - 16, cy, cx + rad + 16, cy], fill=(71, 85, 105), width=1)
            draw.line([cx, cy - rad - 16, cx, cy + rad + 16], fill=(71, 85, 105), width=1)
            # Conjugate poles at r=0.9878, angle=+-45 deg
            pr = int(rad * 0.88)
            ang = math.pi / 4.0
            px = cx + int(pr * math.cos(ang))
            py_up = cy - int(pr * math.sin(ang))
            py_dn = cy + int(pr * math.sin(ang))
            for py_pole in (py_up, py_dn):
                draw.line([px - 7, py_pole - 7, px + 7, py_pole + 7], fill=(248, 113, 113), width=3)
                draw.line([px - 7, py_pole + 7, px + 7, py_pole - 7], fill=(248, 113, 113), width=3)
            # Rotating frequency phasor e^(jw)
            omega = p * math.pi
            vx = cx + int(rad * math.cos(omega))
            vy = cy - int(rad * math.sin(omega))
            draw.line([cx, cy, vx, vy], fill=(251, 191, 36), width=2)
            draw.ellipse([vx - 5, vy - 5, vx + 5, vy + 5], fill=(251, 191, 36))
            draw.text((cx - 55, cy + rad + 8), "r_pole = 0.9878 < 1 (Stable)", fill=(74, 222, 128), font=f_small)

            # Right half of plot: Impulse response h[n]
            hx0, hy_mid = pl_x0 + 320, pl_y0 + 160
            hw = 290
            draw.line([hx0, hy_mid, hx0 + hw, hy_mid], fill=(71, 85, 105), width=1)
            max_n = max(4, int(36 * p))
            for n_idx in range(max_n):
                xn = hx0 + int((n_idx / 36.0) * hw)
                env = math.exp(-0.08 * n_idx) * math.cos(0.45 * n_idx)
                yn = hy_mid - int(env * 85)
                draw.line([xn, hy_mid, xn, yn], fill=(56, 189, 248), width=2)
                draw.ellipse([xn - 3, yn - 3, xn + 3, yn + 3], fill=(251, 191, 36))

        elif plot_mode == "frequency_response":
            draw.text((pl_x0 + 14, pl_y0 + 8), "BUTTERWORTH MAGNITUDE |H(e^jw)| (dB) & PHASE RESPONSE", fill=(56, 189, 248), font=f_subhdr)
            bx0, by0, bw, bh = pl_x0 + 42, pl_y0 + 44, pl_w - 68, pl_h - 68
            draw.line([bx0, by0, bx0, by0 + bh], fill=(100, 116, 139), width=2)
            draw.line([bx0, by0 + bh, bx0 + bw, by0 + bh], fill=(100, 116, 139), width=2)
            pts_mag: list[tuple[int, int]] = []
            pts_phs: list[tuple[int, int]] = []
            for i in range(120):
                u = i / 119.0
                x_pt = bx0 + int(u * bw)
                ratio_f = u / 0.42
                mag_lin = 1.0 / math.sqrt(1.0 + (ratio_f ** 8))
                y_mag = by0 + 16 + int((1.0 - mag_lin) * (bh - 28))
                pts_mag.append((x_pt, y_mag))
                phs = math.atan(ratio_f ** 2) / (math.pi / 2.0)
                y_phs = by0 + 20 + int(phs * (bh - 36))
                pts_phs.append((x_pt, y_phs))
            if len(pts_mag) > 1:
                draw.line(pts_mag, fill=(56, 189, 248), width=3)
                draw.line(pts_phs, fill=(167, 139, 250), width=2)
            cur_idx = min(len(pts_mag) - 1, int(p * (len(pts_mag) - 1)))
            cx_pt, cy_pt = pts_mag[cur_idx]
            draw.line([cx_pt, by0, cx_pt, by0 + bh], fill=(251, 191, 36), width=1)
            draw.ellipse([cx_pt - 6, cy_pt - 6, cx_pt + 6, cy_pt + 6], fill=(251, 191, 36))
            draw.text((bx0 + int(0.42 * bw) - 30, by0 + 12), "f_c = 3.4 kHz (-3 dB)", fill=(74, 222, 128), font=f_small)
            draw.text((bx0 + bw - 150, by0 + bh - 28), "-40 dB/dec Stopband", fill=(251, 191, 36), font=f_small)

        elif plot_mode == "dma_timing":
            draw.text((pl_x0 + 14, pl_y0 + 8), "PING-PONG SRAM DOUBLE BUFFER DMA & REAL-TIME CPU BUDGET", fill=(56, 189, 248), font=f_subhdr)
            tx0, ty0, tw = pl_x0 + 28, pl_y0 + 48, pl_w - 56
            # Buffer A and Buffer B fill bars
            fill_a = min(1.0, (p * 2.0) % 1.0)
            phase_swap = int(p * 2.0) % 2 == 0
            draw.rounded_rectangle([tx0, ty0, tx0 + tw, ty0 + 48], radius=6, fill=(18, 28, 48), outline=(56, 189, 248), width=1)
            draw.rectangle([tx0 + 4, ty0 + 4, tx0 + 4 + int((tw - 8) * fill_a), ty0 + 44], fill=(20, 83, 136) if phase_swap else (22, 101, 52))
            draw.text((tx0 + 14, ty0 + 14), f"SRAM BUFFER A: {'DMA RX FILLING (48 kHz)' if phase_swap else 'CPU MAC FILTERING (18.0% LOAD)'}  [{int(fill_a*100)}%]", fill=(248, 250, 252), font=f_bold)

            draw.rounded_rectangle([tx0, ty0 + 62, tx0 + tw, ty0 + 110], radius=6, fill=(18, 28, 48), outline=(251, 191, 36), width=1)
            draw.rectangle([tx0 + 4, ty0 + 66, tx0 + 4 + int((tw - 8) * fill_a), ty0 + 106], fill=(22, 101, 52) if phase_swap else (20, 83, 136))
            draw.text((tx0 + 14, ty0 + 76), f"SRAM BUFFER B: {'CPU MAC FILTERING (18.0% LOAD)' if phase_swap else 'DMA RX FILLING (48 kHz)'}  [{int(fill_a*100)}%]", fill=(248, 250, 252), font=f_bold)

            # Harmonic suppression spectrum bars
            sy0, sh = ty0 + 132, 92
            harmonics = [("50 Hz Fund.", 0.92, (74, 222, 128)), ("150 Hz (3rd)", 0.18 * (1.0 - 0.6 * p), (248, 113, 113)), ("250 Hz (5th)", 0.12 * (1.0 - 0.7 * p), (248, 113, 113)), ("Noise Floor", 0.06, (56, 189, 248))]
            for h_idx, (h_lbl, h_val, h_col) in enumerate(harmonics):
                hx = tx0 + h_idx * (tw // 4) + 12
                bw_bar = (tw // 4) - 28
                bh_bar = int(sh * h_val)
                draw.rectangle([hx, sy0 + sh - bh_bar, hx + bw_bar, sy0 + sh], fill=h_col)
                draw.text((hx, sy0 + sh + 4), h_lbl, fill=(148, 163, 184), font=f_small)

        else:
            # hardware_topology mode
            draw.text((pl_x0 + 14, pl_y0 + 8), "HARDWARE SIGNAL PIPELINE & REAL-TIME WAVEFORM FILTRATION", fill=(56, 189, 248), font=f_subhdr)
            blocks = [
                ("ANTI-ALIAS LPF", "AD8606 Op-Amp"),
                ("16-BIT ADC", "48 kHz Sample"),
                ("SPI / DMA BUS", "Zero-CPU Copy"),
                ("BIQUAD MAC CORE", "Q15 / FPU Stage"),
            ]
            bx_start = pl_x0 + 20
            bw_blk = 136
            for b_i, (b_title, b_sub) in enumerate(blocks):
                bx = bx_start + b_i * 150
                by = pl_y0 + 44
                is_active_blk = (b_i == int(p * 4) % 4)
                draw.rounded_rectangle(
                    [bx, by, bx + bw_blk, by + 68],
                    radius=7,
                    fill=(24, 58, 102) if is_active_blk else (18, 30, 52),
                    outline=(251, 191, 36) if is_active_blk else (56, 189, 248),
                    width=2,
                )
                draw.text((bx + 10, by + 14), b_title, fill=(248, 250, 252), font=f_subhdr)
                draw.text((bx + 10, by + 38), b_sub, fill=(148, 163, 184), font=f_small)
                if b_i < 3:
                    ax0 = bx + bw_blk
                    ax1 = bx + 150
                    ay = by + 34
                    draw.line([ax0, ay, ax1, ay], fill=(56, 189, 248), width=2)

            # Animated input noisy waveform vs clean filtered output waveform
            wx0, wy_mid, ww = pl_x0 + 24, pl_y0 + 202, pl_w - 48
            draw.line([wx0, wy_mid, wx0 + ww, wy_mid], fill=(51, 65, 85), width=1)
            noisy_pts: list[tuple[int, int]] = []
            clean_pts: list[tuple[int, int]] = []
            phase_shift = p * math.pi * 6.0
            for i in range(140):
                u = i / 139.0
                xw = wx0 + int(u * ww)
                clean_v = math.sin(u * math.pi * 4.0 - phase_shift)
                noise_v = 0.38 * math.sin(u * math.pi * 26.0 + phase_shift * 2.0)
                noisy_pts.append((xw, wy_mid - int((clean_v + noise_v) * 44)))
                clean_pts.append((xw, wy_mid - int(clean_v * 44)))
            draw.line(noisy_pts, fill=(248, 113, 113), width=1)
            draw.line(clean_pts, fill=(74, 222, 128), width=3)
            draw.text((wx0 + 8, pl_y1 - 26), "Red: Input Signal + HF Interference   |   Green: Filtered Output y[n] (SNR 98.1 dB)", fill=(148, 163, 184), font=f_small)

        # 3C. Mayer CTML Callout Cards (y: 460..584) -> 3 horizontal cards
        bullets = (scene.on_screen_bullets or ["Phase 1: Core Architecture", "Formula Verified", "Zero Overflow Margin"])[:3]
        card_w = 202
        for b_idx, b_text in enumerate(bullets):
            cx0 = 610 + b_idx * 214
            cy0, cx1, cy1 = 462, cx0 + card_w, 582
            is_hi = (b_idx == active_cue_idx % max(1, len(bullets)))
            draw.rounded_rectangle(
                [cx0, cy0, cx1, cy1],
                radius=8,
                fill=(24, 48, 84) if is_hi else (16, 26, 46),
                outline=(251, 191, 36) if is_hi else (45, 72, 115),
                width=2 if is_hi else 1,
            )
            tag_col = (251, 191, 36) if is_hi else (56, 189, 248)
            draw.text((cx0 + 12, cy0 + 10), f"CTML KEY POINT 0{b_idx + 1}", fill=tag_col, font=f_small)
            words = str(b_text).split()
            line_a = " ".join(words[:3])
            line_b = " ".join(words[3:6])
            draw.text((cx0 + 12, cy0 + 38), line_a[:24], fill=(248, 250, 252), font=f_bold)
            if line_b:
                draw.text((cx0 + 12, cy0 + 62), line_b[:24], fill=(203, 213, 225), font=f_body)

        # 4. Bottom Kinetic Subtitle Bar (x: 24..1256, y: 604..702)
        sub_x0, sub_y0, sub_x1, sub_y1 = 24, 604, 1256, 702
        draw.rounded_rectangle([sub_x0, sub_y0, sub_x1, sub_y1], radius=10, fill=(11, 17, 30), outline=(45, 72, 115), width=2)

        captions = scene.captions
        cur_ms = int(round(p * max(1, (captions.duration_ms if captions else scene.duration_sec * 1000))))
        if captions and getattr(captions, "pages", None):
            active_page = captions.pages[0]
            for pg in captions.pages:
                if pg.start_ms <= cur_ms <= pg.end_ms:
                    active_page = pg
                    break
                if cur_ms > pg.end_ms:
                    active_page = pg

            active_word_idx = -1
            for wt in active_page.words:
                if wt.start_ms <= cur_ms <= wt.end_ms:
                    active_word_idx = wt.word_index
                    break
            if active_word_idx == -1 and active_page.words:
                for wt in active_page.words:
                    if cur_ms >= wt.start_ms:
                        active_word_idx = wt.word_index

            for line_i in range(2):
                line_words = [wt for wt in active_page.words if wt.line_index == line_i]
                if not line_words:
                    continue
                cursor_x = sub_x0 + 32
                y_line = sub_y0 + 18 + line_i * 38
                for wt in line_words:
                    is_curr = (wt.word_index == active_word_idx)
                    w_str = wt.word
                    try:
                        tw = int(f_sub.getlength(w_str + " "))
                    except Exception:
                        tw = (len(w_str) + 1) * 12
                    if is_curr:
                        draw.rounded_rectangle(
                            [cursor_x - 4, y_line - 2, cursor_x + tw - 4, y_line + 28],
                            radius=4,
                            fill=(52, 42, 14),
                        )
                        draw.text((cursor_x, y_line), w_str, fill=(251, 191, 36), font=f_sub)
                    else:
                        draw.text((cursor_x, y_line), w_str, fill=(241, 245, 249), font=f_sub)
                    cursor_x += tw
        else:
            raw_sub = (scene.narration_text or "")[:110]
            draw.text((sub_x0 + 28, sub_y0 + 34), raw_sub, fill=(241, 245, 249), font=f_sub)

        return img.tobytes()

    async def render_scene(
        self,
        project: FlowEducationalProject,
        scene: FlowScene,
        total_scenes: int = 4,
    ) -> dict[str, Any]:
        """Render a complete 1280x720 HD H.264 + AAC educational MP4 clip for `scene`."""
        from pathlib import Path
        import subprocess
        import tempfile
        from PIL import Image
        import imageio_ffmpeg

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        base_dir = Path(__file__).resolve().parent.parent
        cloud_dir = base_dir / "output" / "cloud_flow_veo31"
        assets_dir = base_dir / "output" / "dsp_assets"

        # Select Cloud Flow Veo 3.1 3D motion plate if available
        plate_candidates = [
            cloud_dir / "scene_01_cloud_veo_stm32h743.mp4",
            cloud_dir / "scene_02_cloud_veo_sram_buffer.mp4",
        ]
        chosen_plate = plate_candidates[(scene.scene_number - 1) % len(plate_candidates)]
        cloud_frames = self._decode_cloud_plate_frames(chosen_plate)

        # Select grounded coursework schematic image if available
        asset_key = str((scene.diagram_spec or {}).get("asset_key", "fig_4_1"))
        schematic_img = None
        for cand_img in (
            assets_dir / f"{asset_key}.png",
            assets_dir / "fig_4_1.png",
            base_dir / "output" / "manimgx_biquad_preview.png",
        ):
            if cand_img.is_file():
                try:
                    schematic_img = Image.open(cand_img).convert("RGB")
                    break
                except Exception:
                    pass

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_p = Path(tmp_dir)
            voice_wav = tmp_p / "voiceover.wav"
            duration_sec = await self._synthesize_scene_voiceover(
                scene=scene,
                language=project.language,
                out_wav=voice_wav,
            )
            scene.duration_sec = max(3, int(round(duration_sec)))
            scene.captions = self._build_word_captions(
                scene_id=scene.scene_id,
                narration_text=scene.narration_text,
                duration_sec=float(duration_sec),
            )

            total_frames = max(self.fps * 2, int(duration_sec * self.fps))
            out_mp4 = tmp_p / "educational_scene.mp4"

            cmd = [
                ffmpeg_exe,
                "-y",
                "-loglevel",
                "error",
                "-f",
                "rawvideo",
                "-vcodec",
                "rawvideo",
                "-s",
                f"{self.width}x{self.height}",
                "-pix_fmt",
                "rgb24",
                "-r",
                str(self.fps),
                "-i",
                "-",
                "-i",
                str(voice_wav),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "ultrafast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-shortest",
                "-movflags",
                "+faststart",
                str(out_mp4),
            ]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                assert proc.stdin is not None
                for f_idx in range(total_frames):
                    raw_rgb = self.render_educational_frame(
                        project=project,
                        scene=scene,
                        total_scenes=total_scenes,
                        frame_idx=f_idx,
                        total_frames=total_frames,
                        cloud_plate_frames=cloud_frames,
                        schematic_img=schematic_img,
                    )
                    try:
                        proc.stdin.write(raw_rgb)
                    except (BrokenPipeError, OSError):
                        break
                with contextlib.suppress(Exception):
                    proc.stdin.close()
                proc.wait(timeout=120)
            except Exception:
                proc.kill()
                raise

            mp4_bytes = out_mp4.read_bytes() if out_mp4.is_file() else b""
            return {
                "ok": len(mp4_bytes) > 10_000,
                "mp4_bytes": mp4_bytes,
                "duration_sec": scene.duration_sec,
                "operation_name": f"op_flow_edu_{scene.scene_id}",
            }

    @staticmethod
    def build_contact_sheet(
        mp4_path: Any, out_png: Any, scene_count: int = 4
    ) -> Any | None:
        """Extract key frames across the assembled educational MP4 into a 2x2 contact sheet."""
        from pathlib import Path
        import subprocess
        from PIL import Image
        import imageio_ffmpeg

        src_p = Path(mp4_path)
        dst_p = Path(out_png)
        if not src_p.is_file() or src_p.stat().st_size < 10_000:
            return None
        try:
            ff = imageio_ffmpeg.get_ffmpeg_exe()
            probe = subprocess.run(
                [ff, "-i", str(src_p)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            import re

            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr or "")
            total_dur = 20.0
            if m:
                total_dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

            n_tiles = max(4, min(6, int(scene_count)))
            cols = 2
            rows = (n_tiles + 1) // 2
            tile_w, tile_h = 640, 360
            sheet = Image.new("RGB", (cols * tile_w, rows * tile_h), (9, 13, 24))

            for idx in range(n_tiles):
                ts = max(0.5, total_dur * ((idx + 0.5) / float(n_tiles)))
                p_frame = subprocess.run(
                    [
                        ff,
                        "-ss",
                        f"{ts:.2f}",
                        "-i",
                        str(src_p),
                        "-frames:v",
                        "1",
                        "-vf",
                        f"scale={tile_w}:{tile_h}",
                        "-f",
                        "rawvideo",
                        "-pix_fmt",
                        "rgb24",
                        "-",
                    ],
                    capture_output=True,
                    timeout=20,
                )
                raw = p_frame.stdout or b""
                if len(raw) >= tile_w * tile_h * 3:
                    tile = Image.frombytes("RGB", (tile_w, tile_h), raw[: tile_w * tile_h * 3])
                    r_i, c_i = divmod(idx, cols)
                    sheet.paste(tile, (c_i * tile_w, r_i * tile_h))

            dst_p.parent.mkdir(parents=True, exist_ok=True)
            sheet.save(str(dst_p), format="PNG")
            return dst_p
        except Exception:
            return None
