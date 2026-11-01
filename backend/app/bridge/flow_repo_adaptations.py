"""
src/flow_repo_adaptations.py
Architectural adaptations from open-source educational video lecture repositories:
1. ManimCommunity/manim-voiceover + motion-canvas/motion-canvas:
   Bookmark-Driven Audio-Visual Sync (<bookmark mark="..."/> & Named Time Events)
2. showlab/Paper2Video + DangJin/pdf2video:
   CursorGroundingEngine (animated laser pointer & Cubic-Bezier Auto-Zoom camera)
3. THU-MAIC/OpenMAIC + jeertmans/manim-slides:
   Structured Classroom Action Script (ShowSheet, ZoomBBox, LaserPoint, RunSimPreset, QuizCheckpoint)
   & Step-by-Step Slide State Machine
4. remotion-dev/remotion + sekika/slidemovie:
   SceneRenderCache (content-addressable deterministic SHA-256 MP4 scene cache in .scene_cache/)
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Self

# ============================================================================
# R1. BOOKMARK-DRIVEN AUDIO-VISUAL SYNC (manim-voiceover + motion-canvas)
# ============================================================================

_BOOKMARK_TAG_RE = re.compile(
    r"""<\s*bookmark\b[^>]*?\bmark\s*=\s*["']([^"']+)["'][^>]*?/?>|<\s*/\s*bookmark\s*>""",
    re.IGNORECASE,
)
_ANY_BOOKMARK_RE = re.compile(r"<\s*/?\s*bookmark\b[^>]*>", re.IGNORECASE)


@dataclass
class VoiceoverBookmark:
    """Represents a named inline synchronization bookmark extracted from narration text."""

    mark: str
    word_index: int
    total_words: int
    word_ratio: float
    char_offset: int = 0
    timestamp_sec: float = 0.0
    timestamp_ms: int = 0
    frame_offset: int = 0

    @property
    def id(self) -> str:
        return self.mark

    @property
    def name(self) -> str:
        return self.mark

    @property
    def ratio(self) -> float:
        return self.word_ratio

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = self.mark
        d["ratio"] = self.word_ratio
        return d

    def __getitem__(self, key: str) -> Any:
        if key in ("id", "name", "mark"):
            return self.mark
        if key in ("ratio", "word_ratio"):
            return self.word_ratio
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except AttributeError:
            return default


class BookmarkParseResult(tuple):
    """
    Hybrid return object for parse_voiceover_bookmarks(text):
    - Can be unpacked as a 2-tuple: `clean_text, bookmarks = parse_voiceover_bookmarks(text)`
    - Can be accessed via attributes: `res.clean_text`, `res.bookmarks`
    - Can be accessed via dict keys: `res["clean_text"]`, `res["bookmarks"]`
    - Iterating or indexing by int accesses `(clean_text, bookmarks)`
    """

    clean_text: str
    text: str
    bookmarks: list[VoiceoverBookmark]

    def __new__(cls, clean_text: str, bookmarks: list[VoiceoverBookmark]) -> Self:
        instance = super().__new__(cls, (clean_text, bookmarks))
        instance.clean_text = clean_text
        instance.text = clean_text
        instance.bookmarks = bookmarks
        return instance

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            if key in ("clean_text", "text"):
                return self.clean_text
            if key == "bookmarks":
                return self.bookmarks
            raise KeyError(key)
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key in ("clean_text", "text"):
            return self.clean_text
        if key == "bookmarks":
            return self.bookmarks
        return default

    def to_dict(self) -> dict[str, Any]:
        return {
            "clean_text": self.clean_text,
            "bookmarks": [b.to_dict() for b in self.bookmarks],
        }


def strip_voiceover_bookmarks(text: str) -> str:
    """Remove all inline <bookmark .../> tags from narration or subtitle text."""
    if not text:
        return ""
    cleaned = _ANY_BOOKMARK_RE.sub(" ", str(text))
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([,.;:!?])", r"\1", cleaned)
    return cleaned.strip()


def parse_voiceover_bookmarks(text: str) -> BookmarkParseResult:
    """
    Parse inline `<bookmark mark="id"/>` markers from a narration script.
    Returns `BookmarkParseResult(clean_text, bookmarks)` where:
    - `clean_text` contains zero `<bookmark.../>` tags.
    - `bookmarks` is an ordered list of `VoiceoverBookmark` instances with
      `word_ratio` in `[0.0, 1.0]`.
    """
    if not text:
        return BookmarkParseResult("", [])

    raw = str(text)
    raw_marks: list[tuple[str, int, int]] = []
    clean_parts: list[str] = []
    last_pos = 0
    words_so_far = 0
    chars_so_far = 0

    for match in _BOOKMARK_TAG_RE.finditer(raw):
        segment = raw[last_pos : match.start()]
        if segment:
            clean_parts.append(segment)
            seg_words = [w for w in segment.split() if w.strip()]
            words_so_far += len(seg_words)
            chars_so_far += len(segment)
        mark_id = match.group(1)
        if mark_id:
            raw_marks.append((mark_id.strip(), words_so_far, chars_so_far))
        last_pos = match.end()

    tail = raw[last_pos:]
    if tail:
        clean_parts.append(tail)
        words_so_far += len([w for w in tail.split() if w.strip()])

    clean_joined = _ANY_BOOKMARK_RE.sub(" ", " ".join(clean_parts))
    clean_joined = re.sub(r"[ \t]{2,}", " ", clean_joined)
    clean_joined = re.sub(r" +([,.;:!?])", r"\1", clean_joined).strip()

    all_clean_words = [w for w in clean_joined.split() if w.strip()]
    total_words = max(1, len(all_clean_words))

    bookmarks: list[VoiceoverBookmark] = []
    prev_ratio = 0.0
    for mark_id, w_idx, c_off in raw_marks:
        clamped_idx = max(0, min(total_words, w_idx))
        ratio = float(clamped_idx) / float(total_words)
        ratio = max(prev_ratio, min(1.0, round(ratio, 6)))
        prev_ratio = ratio
        bookmarks.append(
            VoiceoverBookmark(
                mark=mark_id,
                word_index=clamped_idx,
                total_words=total_words,
                word_ratio=ratio,
                char_offset=max(0, c_off),
            )
        )

    return BookmarkParseResult(clean_joined, bookmarks)


class ResolvedBookmarkFloat(float):
    """
    A float timestamp (in seconds) that also carries bookmark metadata attributes
    (`.mark`, `.word_ratio`, `.timestamp_sec`, `.timestamp_ms`, `.frame_offset`)
    and supports dictionary-key access (`val["timestamp_sec"]`, `val["frame_offset"]`).
    """

    mark: str
    word_ratio: float
    timestamp_sec: float
    timestamp_ms: int
    frame_offset: int

    def __new__(
        cls,
        value: float,
        mark: str = "",
        word_ratio: float = 0.0,
        timestamp_ms: int = 0,
        frame_offset: int = 0,
    ) -> Self:
        obj = super().__new__(cls, value)
        obj.mark = mark
        obj.word_ratio = word_ratio
        obj.timestamp_sec = float(value)
        obj.timestamp_ms = int(timestamp_ms)
        obj.frame_offset = int(frame_offset)
        return obj

    def __getitem__(self, key: str) -> Any:
        if key in ("id", "name", "mark"):
            return self.mark
        if key in ("ratio", "word_ratio"):
            return self.word_ratio
        if key in ("time", "timestamp", "timestamp_sec", "sec"):
            return self.timestamp_sec
        if key in ("timestamp_ms", "ms"):
            return self.timestamp_ms
        if key in ("frame", "frame_offset"):
            return self.frame_offset
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def to_dict(self) -> dict[str, Any]:
        return {
            "mark": self.mark,
            "word_ratio": self.word_ratio,
            "timestamp_sec": self.timestamp_sec,
            "timestamp_ms": self.timestamp_ms,
            "frame_offset": self.frame_offset,
        }


class ResolvedBookmarkMap(dict):
    """
    Mapping of `mark_id -> ResolvedBookmarkFloat(timestamp_sec)` that also supports
    integer indexing `resolved[0]` in bookmark order and `.items_list`.
    """

    def __init__(self, ordered_items: list[ResolvedBookmarkFloat]) -> None:
        super().__init__((item.mark, item) for item in ordered_items)
        self.items_list: list[ResolvedBookmarkFloat] = ordered_items

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self.items_list[key]
        return super().__getitem__(key)


def _normalize_acoustic_segments(
    acoustic_segments: Sequence[Any] | None,
    total_duration_sec: float,
) -> list[tuple[float, float]]:
    if not acoustic_segments:
        return []
    spans: list[tuple[float, float]] = []
    for seg in acoustic_segments:
        s_val: float | None = None
        e_val: float | None = None
        if isinstance(seg, (tuple, list)) and len(seg) >= 2:
            s_val, e_val = float(seg[0]), float(seg[1])
        elif isinstance(seg, dict):
            if "start_sec" in seg and "end_sec" in seg:
                s_val, e_val = float(seg["start_sec"]), float(seg["end_sec"])
            elif "start" in seg and "end" in seg:
                s_val, e_val = float(seg["start"]), float(seg["end"])
            elif "start_ms" in seg and "end_ms" in seg:
                s_val, e_val = float(seg["start_ms"]) / 1000.0, float(seg["end_ms"]) / 1000.0
        else:
            if hasattr(seg, "start_sec") and hasattr(seg, "end_sec"):
                s_val, e_val = float(seg.start_sec), float(seg.end_sec)
            elif hasattr(seg, "start_ms") and hasattr(seg, "end_ms"):
                s_val, e_val = float(seg.start_ms) / 1000.0, float(seg.end_ms) / 1000.0
        if s_val is not None and e_val is not None:
            s_clamped = max(0.0, min(total_duration_sec, s_val))
            e_clamped = max(s_clamped, min(total_duration_sec, e_val))
            if e_clamped > s_clamped:
                spans.append((s_clamped, e_clamped))
    spans.sort(key=lambda x: x[0])
    return spans


def resolve_bookmark_timestamps(
    bookmarks: BookmarkParseResult | Sequence[Any],
    total_duration_sec: float,
    acoustic_segments: Sequence[Any] | None = None,
    fps: int = 15,
) -> ResolvedBookmarkMap:
    """
    Resolve parsed bookmarks to exact monotonic timestamps in `[0.0, total_duration_sec]`
    and frame offsets in `[0, total_frames]`, optionally aligned against acoustic VAD segments.
    """
    dur = max(0.0, float(total_duration_sec))
    total_frames = max(1, round(dur * max(1, fps)))

    if isinstance(bookmarks, BookmarkParseResult):
        bm_seq: Sequence[Any] = bookmarks.bookmarks
    elif (
        isinstance(bookmarks, tuple)
        and len(bookmarks) == 2
        and isinstance(bookmarks[0], str)
        and isinstance(bookmarks[1], list)
    ):
        bm_seq = bookmarks[1]
    else:
        bm_seq = bookmarks or []

    spans = _normalize_acoustic_segments(acoustic_segments, dur)
    total_voiced = sum(e - s for s, e in spans)

    ordered: list[ResolvedBookmarkFloat] = []
    prev_t = 0.0

    for idx, bm in enumerate(bm_seq):
        if isinstance(bm, VoiceoverBookmark):
            mark_id = bm.mark
            ratio = float(bm.word_ratio)
        elif isinstance(bm, dict):
            mark_id = str(bm.get("mark") or bm.get("id") or f"mark_{idx}")
            ratio = float(bm.get("word_ratio", bm.get("ratio", 0.0)))
        else:
            mark_id = str(getattr(bm, "mark", f"mark_{idx}"))
            ratio = float(getattr(bm, "word_ratio", getattr(bm, "ratio", 0.0)))

        ratio = max(0.0, min(1.0, ratio))

        if spans and total_voiced > 1e-6:
            target_voiced = ratio * total_voiced
            accum = 0.0
            resolved_t = spans[-1][1]
            for s_t, e_t in spans:
                seg_len = e_t - s_t
                if accum + seg_len >= target_voiced - 1e-9:
                    local_offset = max(0.0, target_voiced - accum)
                    resolved_t = s_t + min(seg_len, local_offset)
                    break
                accum += seg_len
        else:
            resolved_t = ratio * dur

        resolved_t = max(prev_t, min(dur, round(resolved_t, 4)))
        prev_t = resolved_t

        ms_val = round(resolved_t * 1000.0)
        frame_val = max(0, min(total_frames, round(resolved_t * max(1, fps))))

        if isinstance(bm, VoiceoverBookmark):
            bm.timestamp_sec = resolved_t
            bm.timestamp_ms = ms_val
            bm.frame_offset = frame_val

        ordered.append(
            ResolvedBookmarkFloat(
                resolved_t,
                mark=mark_id,
                word_ratio=ratio,
                timestamp_ms=ms_val,
                frame_offset=frame_val,
            )
        )

    return ResolvedBookmarkMap(ordered)


# ============================================================================
# R2. CURSOR GROUNDING & AUTO-ZOOM CAMERA (Paper2Video + pdf2video)
# ============================================================================


def cubic_bezier_ease(
    t: float,
    p1x: float = 0.25,
    p1y: float = 0.10,
    p2x: float = 0.25,
    p2y: float = 1.00,
) -> float:
    """Evaluate a CSS-style Cubic-Bezier easing curve at normalized progress t in [0, 1]."""
    x = max(0.0, min(1.0, float(t)))
    if x <= 0.0 or x >= 1.0:
        return x

    # Solve x(u) = x for parameter u using Newton-Raphson
    u = x
    for _ in range(8):
        omt = 1.0 - u
        bx = 3.0 * omt * omt * u * p1x + 3.0 * omt * u * u * p2x + u * u * u
        dbx = (
            3.0 * omt * omt * p1x
            + 6.0 * omt * u * (p2x - p1x)
            + 3.0 * u * u * (1.0 - p2x)
        )
        if abs(dbx) < 1e-6:
            break
        u = max(0.0, min(1.0, u - (bx - x) / dbx))

    omt = 1.0 - u
    by = 3.0 * omt * omt * u * p1y + 3.0 * omt * u * u * p2y + u * u * u
    return max(0.0, min(1.0, by))


class CursorFrameState(dict):
    """Dictionary of camera & laser-pointer coordinates that also supports attribute access."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class CursorGroundingEngine:
    """
    Paper2Video + pdf2video camera and laser-pointer grounding engine.
    Computes smooth Cubic-Bezier `(x, y, zoom)` camera transforms, clamped source crop boxes,
    animated laser-pointer trajectories, and typing-effect callout strings for any frame `f`.
    """

    def __init__(
        self,
        source_width: int = 2481,
        source_height: int = 3508,
        min_zoom: float = 1.0,
        max_zoom: float = 1.85,
    ) -> None:
        self.source_width = max(16, int(source_width))
        self.source_height = max(16, int(source_height))
        self.min_zoom = max(1.0, float(min_zoom))
        self.max_zoom = max(self.min_zoom, float(max_zoom))

    def compute_frame_state(
        self,
        frame_idx: int,
        total_frames: int,
        cues: Sequence[dict[str, Any]] | None = None,
        pz_bbox: Sequence[float] | None = None,
        callout_label: str = "",
    ) -> CursorFrameState:
        tot = max(1, int(total_frames))
        f = max(0, min(tot, int(frame_idx)))
        progress = float(f) / float(max(1, tot - 1)) if tot > 1 else 0.0

        active_bbox: Sequence[float] = pz_bbox if (pz_bbox and len(pz_bbox) >= 4) else [0.10, 0.14, 0.90, 0.48]
        prev_bbox: Sequence[float] = [0.0, 0.0, 1.0, 1.0]
        cue_t = progress
        active_label = callout_label

        if cues:
            active_idx = len(cues) - 1
            for idx, c in enumerate(cues):
                s_r = float(c.get("start_ratio", 0.0))
                e_r = float(c.get("end_ratio", 1.0))
                if s_r <= progress <= e_r:
                    active_idx = idx
                    break
            cur_cue = cues[active_idx]
            fb = cur_cue.get("focus_bbox") or cur_cue.get("pz_bbox")
            if fb and len(fb) >= 4:
                active_bbox = fb
            if active_idx > 0:
                p_fb = cues[active_idx - 1].get("focus_bbox") or cues[active_idx - 1].get("pz_bbox")
                if p_fb and len(p_fb) >= 4:
                    prev_bbox = p_fb
            s_r = float(cur_cue.get("start_ratio", 0.0))
            e_r = float(cur_cue.get("end_ratio", 1.0))
            cue_t = max(0.0, min(1.0, (progress - s_r) / max(1e-4, e_r - s_r)))
            if not active_label:
                active_label = str(
                    cur_cue.get("formula_or_label")
                    or cur_cue.get("target_title_ru")
                    or cur_cue.get("concept_target")
                    or "Focus Region"
                )

        rx0 = max(0.0, min(0.95, float(active_bbox[0])))
        ry0 = max(0.0, min(0.95, float(active_bbox[1])))
        rx1 = max(rx0 + 0.04, min(1.0, float(active_bbox[2])))
        ry1 = max(ry0 + 0.04, min(1.0, float(active_bbox[3])))

        prx0 = max(0.0, min(0.95, float(prev_bbox[0])))
        pry0 = max(0.0, min(0.95, float(prev_bbox[1])))
        prx1 = max(prx0 + 0.04, min(1.0, float(prev_bbox[2])))
        pry1 = max(pry0 + 0.04, min(1.0, float(prev_bbox[3])))

        target_cx = 0.5 * (rx0 + rx1)
        target_cy = 0.5 * (ry0 + ry1)
        prev_cx = 0.5 * (prx0 + prx1)
        prev_cy = 0.5 * (pry0 + pry1)

        bw = max(0.08, rx1 - rx0)
        bh = max(0.08, ry1 - ry0)
        raw_target_zoom = min(self.max_zoom, max(1.38, 0.78 / max(bw, bh)))

        # Ease from overview/previous cue into the active bbox during first 45% of cue,
        # then perform subtle reading drift so left panel always has continuous motion.
        transition_phase = min(1.0, cue_t / 0.45) if cue_t < 0.45 else 1.0
        ease_val = cubic_bezier_ease(transition_phase, 0.22, 1.0, 0.36, 1.0)

        start_zoom = self.min_zoom if progress < 0.12 else max(1.12, raw_target_zoom * 0.86)
        drift_zoom = 0.04 * math.sin(cue_t * math.pi)
        zoom = max(self.min_zoom, min(self.max_zoom, start_zoom + (raw_target_zoom - start_zoom) * ease_val + drift_zoom))

        reading_drift_x = (rx1 - rx0) * 0.08 * math.sin(cue_t * math.pi * 2.0)
        reading_drift_y = (ry1 - ry0) * 0.06 * math.cos(cue_t * math.pi)
        norm_cx = max(0.0, min(1.0, prev_cx + (target_cx - prev_cx) * ease_val + reading_drift_x))
        norm_cy = max(0.0, min(1.0, prev_cy + (target_cy - prev_cy) * ease_val + reading_drift_y))

        cam_x = max(0.0, min(float(self.source_width), norm_cx * self.source_width))
        cam_y = max(0.0, min(float(self.source_height), norm_cy * self.source_height))

        # Laser pointer scans across the active bounding box (Paper2Video cursor grounding)
        laser_norm_x = rx0 + (rx1 - rx0) * (0.12 + 0.76 * (0.5 + 0.5 * math.sin(cue_t * math.pi * 2.0 - math.pi / 2.0)))
        laser_norm_y = ry0 + (ry1 - ry0) * (0.25 + 0.50 * (0.5 + 0.5 * math.sin(cue_t * math.pi * 4.0)))
        laser_x = max(0.0, min(float(self.source_width), laser_norm_x * self.source_width))
        laser_y = max(0.0, min(float(self.source_height), laser_norm_y * self.source_height))

        trail: list[tuple[float, float]] = []
        for step_back in range(1, 6):
            t_back = max(0.0, cue_t - step_back * 0.035)
            lx_b = rx0 + (rx1 - rx0) * (0.12 + 0.76 * (0.5 + 0.5 * math.sin(t_back * math.pi * 2.0 - math.pi / 2.0)))
            ly_b = ry0 + (ry1 - ry0) * (0.25 + 0.50 * (0.5 + 0.5 * math.sin(t_back * math.pi * 4.0)))
            trail.append(
                (
                    max(0.0, min(float(self.source_width), lx_b * self.source_width)),
                    max(0.0, min(float(self.source_height), ly_b * self.source_height)),
                )
            )

        # Clamped source crop window
        win_w = float(self.source_width) / zoom
        win_h = float(self.source_height) / zoom
        crop_x0 = max(0.0, min(float(self.source_width) - win_w, cam_x - win_w * 0.5))
        crop_y0 = max(0.0, min(float(self.source_height) - win_h, cam_y - win_h * 0.5))
        crop_x1 = min(float(self.source_width), crop_x0 + win_w)
        crop_y1 = min(float(self.source_height), crop_y0 + win_h)

        # Typing-effect callout text (pdf2video style)
        full_callout = active_label or "Coursework Focus Region"
        typed_chars = max(1, math.ceil(len(full_callout) * min(1.0, cue_t * 1.35)))
        callout_typed = full_callout[:typed_chars]

        return CursorFrameState(
            frame_idx=f,
            total_frames=tot,
            progress=progress,
            cue_progress=cue_t,
            x=round(cam_x, 3),
            y=round(cam_y, 3),
            zoom=round(zoom, 4),
            norm_cx=round(norm_cx, 5),
            norm_cy=round(norm_cy, 5),
            laser_x=round(laser_x, 3),
            laser_y=round(laser_y, 3),
            laser_norm_x=round(laser_norm_x, 5),
            laser_norm_y=round(laser_norm_y, 5),
            trail=trail,
            focus_bbox=[rx0, ry0, rx1, ry1],
            crop_box=(
                max(0, round(crop_x0)),
                max(0, round(crop_y0)),
                min(self.source_width, max(1, round(crop_x1))),
                min(self.source_height, max(1, round(crop_y1))),
            ),
            callout_full=full_callout,
            callout_typed=callout_typed,
        )


# ============================================================================
# R3. OPENMAIC CLASSROOM ACTION ENGINE & MANIM-SLIDES STEP CONTROLLER
# ============================================================================


def _default_sim_preset_for_page(page_num: int) -> dict[str, Any]:
    if page_num in (10, 25, 26, 27, 28, 29, 30):
        return {"mode": "filter", "p1": 10.0, "p2": 16, "description": "Cauer N=11 IIR & Q15 Pole-Zero Preset"}
    if page_num in (24, 31, 32, 33, 38, 39, 40, 41):
        return {"mode": "osc", "p1": 2.5, "p2": 14, "description": "DSP56824 SSI_Rx_ISR & DO #6 Pipeline Preset"}
    return {"mode": "hw", "p1": 10.0, "p2": 16, "description": "Sallen-Key 25.03 kHz & 0-WS Timing Preset"}


def build_openmaic_lecture_actions(page_num: int) -> dict[str, Any]:
    """
    Build structured OpenMAIC classroom actions (`ShowSheet`, `ZoomBBox`, `LaserPoint`,
    `RunSimPreset`, `QuizCheckpoint`) and `manim-slides` step segments for any sheet `1..45`.
    """
    if page_num < 1 or page_num > 45:
        raise ValueError(f"page_num must be between 1 and 45, got {page_num}")

    if page_num == 44:
        sheet_file = "drawing_e2.png"
        effective_pz = 42
        sheet_title = "ESKD Functional Schematic E2 (A3)"
    elif page_num == 45:
        sheet_file = "drawing_pe2.png"
        effective_pz = 43
        sheet_title = "ESKD Parts List PE2 (A4)"
    else:
        sheet_file = f"pz_page_{page_num:02d}.png"
        effective_pz = page_num
        sheet_title = f"Coursework Sheet p. {page_num}/43"

    # Gather matching scenes/cues from dsp_defense_cycles if available
    matched_cues: list[dict[str, Any]] = []
    matched_title = sheet_title
    try:
        from src.dsp_defense_cycles import DSP_DEFENSE_CYCLES, DSP_SCENE_CONCEPT_CUES

        g_idx = 0
        for cyc in DSP_DEFENSE_CYCLES:
            for sc in cyc.get("scenes", []):
                g_idx += 1
                if int(sc.get("pz_page", -1)) == effective_pz:
                    matched_title = str(sc.get("title", sheet_title)).replace("+", "")
                    cues = DSP_SCENE_CONCEPT_CUES.get(g_idx, sc.get("concept_cues", []))
                    matched_cues.extend(cues)
    except Exception:
        pass

    if not matched_cues:
        matched_cues = [
            {
                "start_ratio": 0.0,
                "end_ratio": 0.5,
                "focus_bbox": [0.10, 0.12, 0.90, 0.48],
                "concept_type": "spec_overview",
                "concept_target": f"Sheet_{page_num}_Primary",
                "formula_or_label": f"Sheet {page_num} Core Specification Block",
            },
            {
                "start_ratio": 0.5,
                "end_ratio": 1.0,
                "focus_bbox": [0.10, 0.50, 0.90, 0.86],
                "concept_type": "engineering_derivation",
                "concept_target": f"Sheet_{page_num}_Secondary",
                "formula_or_label": f"Sheet {page_num} Verification & Parameters",
            },
        ]

    primary_bbox = matched_cues[0].get("focus_bbox", [0.10, 0.14, 0.90, 0.50])
    secondary_bbox = matched_cues[-1].get("focus_bbox", [0.10, 0.50, 0.90, 0.86])
    sim_preset = _default_sim_preset_for_page(effective_pz)

    actions: list[dict[str, Any]] = [
        {
            "action_id": f"p{page_num:02d}_act01_show",
            "type": "ShowSheet",
            "action_type": "ShowSheet",
            "step_index": 0,
            "title": f"ShowSheet: {sheet_file}",
            "description": f"Initialize full-sheet overview for {matched_title}",
            "bbox": [0.0, 0.0, 1.0, 1.0],
            "zoom": 1.0,
            "sim_preset": sim_preset,
        },
        {
            "action_id": f"p{page_num:02d}_act02_zoom",
            "type": "ZoomBBox",
            "action_type": "ZoomBBox",
            "step_index": 1,
            "title": "ZoomBBox: Primary Calculation / Schematic Node",
            "description": str(matched_cues[0].get("formula_or_label", matched_title)).replace("+", ""),
            "bbox": list(primary_bbox[:4]),
            "zoom": 1.65,
            "sim_preset": sim_preset,
        },
        {
            "action_id": f"p{page_num:02d}_act03_laser",
            "type": "LaserPoint",
            "action_type": "LaserPoint",
            "step_index": 2,
            "title": "LaserPoint: Paper2Video Cursor Grounding Trace",
            "description": str(matched_cues[-1].get("formula_or_label", matched_title)).replace("+", ""),
            "bbox": list(secondary_bbox[:4]),
            "zoom": 1.75,
            "laser_target": [
                round(0.5 * (float(secondary_bbox[0]) + float(secondary_bbox[2])), 4),
                round(0.5 * (float(secondary_bbox[1]) + float(secondary_bbox[3])), 4),
            ],
            "sim_preset": sim_preset,
        },
        {
            "action_id": f"p{page_num:02d}_act04_sim",
            "type": "RunSimPreset",
            "action_type": "RunSimPreset",
            "step_index": 3,
            "title": f"RunSimPreset: {sim_preset['description']}",
            "description": "Synchronize interactive HTML5 simulator parameters with active sheet equations",
            "bbox": list(primary_bbox[:4]),
            "zoom": 1.5,
            "sim_preset": sim_preset,
        },
        {
            "action_id": f"p{page_num:02d}_act05_quiz",
            "type": "QuizCheckpoint",
            "action_type": "QuizCheckpoint",
            "step_index": 4,
            "title": f"QuizCheckpoint: Socratic Self-Check (Sheet {page_num})",
            "description": "Interactive defense verification checkpoint",
            "bbox": list(secondary_bbox[:4]),
            "zoom": 1.4,
            "sim_preset": sim_preset,
            "quiz": {
                "question": (
                    f"Which engineering invariant governs Sheet {page_num} "
                    f"(Motorola DSP56824 @ 40 MHz, Fs = 48 kHz, Cauer N = 11)?"
                ),
                "options": [
                    "Strict real-time margin (3.75 us <= 20.833 us, 18% CPU load) & Q15 /2 scaling with ASL A",
                    "Floating-point IEEE-754 double precision ALU without wait-state constraints",
                    "FIR N=237 direct convolution without anti-aliasing filter",
                ],
                "correct_index": 0,
                "explanation": (
                    "Variant 5 runs on fixed-point 16-bit Motorola DSP56824 (40 MHz, 0 WS with 10 ns IS61LV6416), "
                    "executing 6 DF-II biquads in 150 cycles (3.75 us out of 20.833 us) with /2 Q15 coefficient halving."
                ),
            },
        },
    ]

    slides_segments: list[dict[str, Any]] = []
    for idx, act in enumerate(actions):
        slides_segments.append(
            {
                "step_index": idx,
                "segment_id": act["action_id"],
                "label": act["title"],
                "action_type": act["type"],
                "slide_type": "loop" if act["type"] == "LaserPoint" else "normal",
                "bbox": act["bbox"],
                "zoom": act["zoom"],
                "start_ratio": round(idx / float(len(actions)), 3),
                "end_ratio": round((idx + 1) / float(len(actions)), 3),
            }
        )

    return {
        "ok": True,
        "page_number": page_num,
        "effective_pz_page": effective_pz,
        "sheet_file": sheet_file,
        "sheet_title": matched_title,
        "engine": "OpenMAIC+manim-slides",
        "total_steps": len(actions),
        "actions": actions,
        "slides_segments": slides_segments,
    }


# ============================================================================
# R4. INCREMENTAL CONTENT-ADDRESSABLE SCENE CACHE (remotion + slidemovie)
# ============================================================================


class SceneRenderCache:
    """
    Content-addressable SHA-256 scene render cache (`.scene_cache/`) modeled on
    `remotion` and `sekika/slidemovie`.
    Computes a deterministic SHA-256 digest over:
      (scene_id, narration_text, pz_page, pz_bbox, concept_cues, visual_type, tts_wav_sha256, renderer_version)
    and stores/retrieves rendered MP4 segments without redundant frame synthesis.
    """

    DEFAULT_RENDERER_VERSION = "flow_studio_v4.2_paper2video_remotion"

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        renderer_version: str = DEFAULT_RENDERER_VERSION,
    ) -> None:
        base_root = Path(__file__).resolve().parent.parent
        self.cache_dir = Path(cache_dir) if cache_dir is not None else (base_root / ".scene_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.renderer_version = renderer_version
        self.hits: int = 0
        self.misses: int = 0

    def compute_scene_key(
        self,
        scene_or_id: Any = "",
        narration_text: str = "",
        pz_page: int = 1,
        pz_bbox: Sequence[float] | None = None,
        concept_cues: Sequence[dict[str, Any]] | None = None,
        visual_type: str = "split_screen_live_sync",
        tts_wav_sha256: str = "",
        renderer_version: str | None = None,
    ) -> str:
        """Compute deterministic 64-char SHA-256 hex digest for a scene configuration."""
        if not isinstance(scene_or_id, str) and (
            isinstance(scene_or_id, dict) or hasattr(scene_or_id, "scene_id")
        ):
            sc = scene_or_id
            if isinstance(sc, dict):
                s_id = str(sc.get("scene_id", ""))
                n_txt = str(sc.get("narration_text", narration_text))
                p_page = int(sc.get("pz_page", pz_page) or 1)
                p_bbox = sc.get("pz_bbox") or sc.get("highlight_box") or pz_bbox or []
                c_cues = sc.get("concept_cues") or concept_cues or []
                v_type = str(sc.get("visual_type") or sc.get("visualizer_domain") or visual_type)
            else:
                s_id = str(getattr(sc, "scene_id", ""))
                n_txt = str(getattr(sc, "narration_text", narration_text))
                p_page = int(getattr(sc, "pz_page", pz_page) or 1)
                p_bbox = getattr(sc, "pz_bbox", None) or getattr(sc, "highlight_box", None) or pz_bbox or []
                c_cues = getattr(sc, "concept_cues", None) or concept_cues or []
                v_type = str(
                    getattr(sc, "visual_type", None)
                    or getattr(sc, "visualizer_domain", None)
                    or visual_type
                )
        else:
            s_id = str(scene_or_id)
            n_txt = str(narration_text)
            p_page = int(pz_page or 1)
            p_bbox = list(pz_bbox) if pz_bbox else []
            c_cues = list(concept_cues) if concept_cues else []
            v_type = str(visual_type)

        payload = {
            "scene_id": s_id,
            "narration_text": strip_voiceover_bookmarks(n_txt),
            "pz_page": p_page,
            "pz_bbox": [round(float(x), 5) for x in p_bbox] if isinstance(p_bbox, (list, tuple)) else str(p_bbox),
            "concept_cues": c_cues,
            "visual_type": v_type,
            "tts_wav_sha256": str(tts_wav_sha256),
            "renderer_version": str(renderer_version or self.renderer_version),
        }
        canonical_json = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    # Alias for callers expecting compute_digest / get_digest
    compute_digest = compute_scene_key

    def _entry_mp4_path(self, key: str) -> Path:
        safe_key = re.sub(r"[^a-fA-F0-9]", "", str(key))[:64] or hashlib.sha256(str(key).encode()).hexdigest()
        return self.cache_dir / f"{safe_key}.mp4"

    def _entry_meta_path(self, key: str) -> Path:
        safe_key = re.sub(r"[^a-fA-F0-9]", "", str(key))[:64] or hashlib.sha256(str(key).encode()).hexdigest()
        return self.cache_dir / f"{safe_key}.json"

    def has(self, key: str) -> bool:
        p = self._entry_mp4_path(key)
        return p.is_file() and p.stat().st_size > 64

    def get_cached_path(self, key: str) -> Path | None:
        """Return cached MP4 Path on hit (incrementing `hits`), or None on miss (incrementing `misses`)."""
        p = self._entry_mp4_path(key)
        if p.is_file() and p.stat().st_size > 64:
            self.hits += 1
            return p
        self.misses += 1
        return None

    def get_cached_bytes(self, key: str) -> bytes | None:
        p = self.get_cached_path(key)
        return p.read_bytes() if p is not None else None

    def get(self, key: str, dest_path: str | Path | None = None) -> Path | None:
        """Lookup a scene digest in `.scene_cache/`. Optionally copies cached MP4 to `dest_path`."""
        cached_p = self.get_cached_path(key)
        if cached_p is None:
            return None
        if dest_path is not None:
            dp = Path(dest_path)
            dp.parent.mkdir(parents=True, exist_ok=True)
            if cached_p.resolve() != dp.resolve():
                shutil.copy2(cached_p, dp)
            return dp
        return cached_p

    def put(
        self,
        key: str,
        mp4_source: str | Path | bytes,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Store a rendered MP4 segment (from Path or raw bytes) in `.scene_cache/` under `key`."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target_p = self._entry_mp4_path(key)
        if isinstance(mp4_source, (bytes, bytearray)):
            target_p.write_bytes(bytes(mp4_source))
        else:
            src_p = Path(mp4_source)
            if src_p.resolve() != target_p.resolve():
                shutil.copy2(src_p, target_p)
        meta = dict(metadata or {})
        meta["sha256_key"] = key
        meta["size_bytes"] = target_p.stat().st_size
        meta["renderer_version"] = self.renderer_version
        self._entry_meta_path(key).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return target_p

    # Alias for callers expecting store(...)
    store = put

    def get_telemetry(self) -> dict[str, Any]:
        total = self.hits + self.misses
        return {
            "cache_dir": str(self.cache_dir),
            "renderer_version": self.renderer_version,
            "hits": self.hits,
            "misses": self.misses,
            "total_lookups": total,
            "hit_rate": round(float(self.hits) / float(total), 4) if total > 0 else 0.0,
        }
