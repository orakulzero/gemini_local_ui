from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")


# ============================================================================
# REQ-001: Google Flow Educational Video Bridge (backend.app.bridge.flow)
# ============================================================================


def test_req001_flow_client_session_detection_and_mp4_subtitles_helpers() -> None:
    from backend.app.bridge.flow import (
        FlowClient,
        FlowScene,
        build_srt_subtitles,
        build_webvtt_subtitles,
        synthesize_minimal_mp4,
    )

    empty_client = FlowClient()
    assert empty_client.has_flow_session() is False

    bearer_client = FlowClient(bearer_token="ya29.flow_token")
    assert bearer_client.has_flow_session() is True

    next_auth_client = FlowClient(
        cookies={"__Secure-next-auth.session-token": "flow_session_cookie"}
    )
    assert next_auth_client.has_flow_session() is True

    chunked_client = FlowClient(
        cookies={"__Secure-next-auth.session-token.0": "chunk_0"}
    )
    assert chunked_client.has_flow_session() is True

    sapisid_client = FlowClient(cookies={"SAPISID": "sapisid_val"})
    assert sapisid_client.has_flow_session() is True

    mp4_bytes = synthesize_minimal_mp4(title="Quantum Mechanics", duration_sec=5)
    assert isinstance(mp4_bytes, bytes) and len(mp4_bytes) >= 32
    assert mp4_bytes[4:8] == b"ftyp"

    scenes = [
        FlowScene(
            scene_id="sc_1",
            scene_number=1,
            title="Wave-Particle Duality",
            narration_text="Electrons exhibit both wave and particle properties.",
            visual_prompt="Double-slit experiment interference fringe animation",
            duration_sec=5,
            camera_motion="slow push-in",
            on_screen_bullets=["De Broglie wavelength", "Interference pattern"],
            status="completed",
        ),
        FlowScene(
            scene_id="sc_2",
            scene_number=2,
            title="Superposition Principle",
            narration_text="A quantum system exists in a linear combination of eigenstates.",
            visual_prompt="Bloch sphere state vector rotation",
            duration_sec=6,
            camera_motion="orbital pan",
            on_screen_bullets=["Linear combination", "Bloch sphere"],
            status="completed",
        ),
    ]
    vtt = build_webvtt_subtitles(scenes)
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:05.000" in vtt
    assert "Electrons exhibit both wave and particle properties." in vtt

    srt = build_srt_subtitles(scenes)
    assert "00:00:00,000 --> 00:00:05,000" in srt
    assert "Bloch sphere" in srt or "quantum system" in srt


def test_req001_generate_educational_storyboard_validation_and_structure() -> None:
    from backend.app.bridge.flow import FlowClient, FlowEducationalProject

    client = FlowClient()

    with pytest.raises(ValueError):
        asyncio.run(client.generate_educational_storyboard("   ", scene_count=4))

    with pytest.raises(ValueError):
        asyncio.run(
            client.generate_educational_storyboard("Thermodynamics", scene_count=0)
        )

    with pytest.raises(ValueError):
        asyncio.run(
            client.generate_educational_storyboard("Thermodynamics", scene_count=15)
        )

    project = asyncio.run(
        client.generate_educational_storyboard(
            topic="Transformer Self-Attention Mechanism",
            scene_count=4,
            language="en",
            target_audience="university",
            aspect_ratio="16:9",
            source_context="Scaled dot-product attention computes softmax(QK^T / sqrt(d_k))V.",
        )
    )
    assert isinstance(project, FlowEducationalProject)
    assert project.project_id
    assert project.topic == "Transformer Self-Attention Mechanism"
    assert project.language == "en"
    assert project.target_audience == "university"
    assert project.aspect_ratio == "16:9"
    assert project.status == "storyboard_ready"
    assert len(project.scenes) == 4

    for idx, scene in enumerate(project.scenes, start=1):
        assert scene.scene_id
        assert scene.scene_number == idx
        assert scene.title
        assert scene.narration_text
        assert scene.visual_prompt
        assert scene.duration_sec >= 4
        assert scene.camera_motion
        assert isinstance(scene.on_screen_bullets, list) and len(scene.on_screen_bullets) >= 1
        assert scene.status == "pending"


def test_req001_generate_scene_clip_and_media_bytes() -> None:
    from backend.app.bridge.flow import FlowClient

    client = FlowClient()
    project = asyncio.run(
        client.generate_educational_storyboard(
            topic="Backpropagation and Chain Rule",
            scene_count=3,
        )
    )
    first_scene = project.scenes[0]
    clip_res = asyncio.run(
        client.generate_scene_clip(
            project.project_id,
            first_scene.scene_id,
            prompt_override="3D gradient descent surface with tangent vectors",
        )
    )
    assert clip_res["scene"]["status"] == "completed"
    assert first_scene.status == "completed"
    assert first_scene.clip_id
    assert first_scene.video_url.startswith("/api/flow/media/")
    assert first_scene.video_url.endswith(".mp4")

    raw_mp4 = client.get_media_bytes(f"{first_scene.clip_id}.mp4")
    assert raw_mp4 is not None
    assert raw_mp4[4:8] == b"ftyp"


def test_req001_assemble_educational_video_full_pipeline() -> None:
    from backend.app.bridge.flow import FlowClient

    client = FlowClient()
    project = asyncio.run(
        client.generate_educational_storyboard(
            topic="CRISPR-Cas9 Gene Editing",
            scene_count=3,
        )
    )
    assembled = asyncio.run(client.assemble_educational_video(project.project_id))
    assert assembled["project"]["status"] == "assembled"
    assert project.status == "assembled"
    assert project.assembled_video_url == f"/api/flow/media/{project.project_id}_lesson.mp4"
    assert project.subtitles_vtt.startswith("WEBVTT")
    assert "-->" in project.subtitles_srt

    for sc in project.scenes:
        assert sc.status == "completed"
        assert sc.video_url and sc.video_url.endswith(".mp4")

    lesson_bytes = client.get_media_bytes(f"{project.project_id}_lesson.mp4")
    assert lesson_bytes is not None
    assert lesson_bytes[4:8] == b"ftyp"


# ============================================================================
# REQ-002: FastAPI Flow Educational Video Router (backend.app.api.flow)
# ============================================================================


def test_req002_api_flow_status_endpoint() -> None:
    from backend.app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/flow/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["engine"] == "google-flow-veo"
        assert "16:9" in data["supported_aspect_ratios"]
        assert "9:16" in data["supported_aspect_ratios"]


def test_req002_api_storyboard_creation_and_empty_topic_rejection() -> None:
    from backend.app.main import app

    with TestClient(app) as client:
        bad_resp = client.post(
            "/api/flow/projects/storyboard",
            json={"topic": "   ", "scene_count": 4},
        )
        assert bad_resp.status_code in (400, 422)

        ok_resp = client.post(
            "/api/flow/projects/storyboard",
            json={
                "topic": "Quantum Entanglement and Bell Inequalities",
                "scene_count": 4,
                "language": "en",
                "target_audience": "university",
                "aspect_ratio": "16:9",
                "source_context": "Bell theorem proves local hidden variables cannot reproduce quantum correlations.",
            },
        )
        assert ok_resp.status_code == 200
        body = ok_resp.json()
        assert body["ok"] is True
        proj = body["project"]
        assert proj["topic"] == "Quantum Entanglement and Bell Inequalities"
        assert len(proj["scenes"]) == 4


def test_req002_api_list_and_get_project_with_404_guard() -> None:
    from backend.app.main import app

    with TestClient(app) as client:
        create_resp = client.post(
            "/api/flow/projects/storyboard",
            json={"topic": "Bayesian Inference Fundamentals", "scene_count": 3},
        )
        assert create_resp.status_code == 200
        project_id = create_resp.json()["project"]["project_id"]

        list_resp = client.get("/api/flow/projects")
        assert list_resp.status_code == 200
        projects = list_resp.json()["projects"]
        assert any(p["project_id"] == project_id for p in projects)

        get_resp = client.get(f"/api/flow/projects/{project_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["project"]["project_id"] == project_id

        missing_resp = client.get("/api/flow/projects/nonexistent_project_999999")
        assert missing_resp.status_code == 404


def test_req002_api_patch_scene_and_generate_clip() -> None:
    from backend.app.main import app

    with TestClient(app) as client:
        create_resp = client.post(
            "/api/flow/projects/storyboard",
            json={"topic": "Euler Formula and Complex Plane", "scene_count": 3},
        )
        proj = create_resp.json()["project"]
        project_id = proj["project_id"]
        scene_id = proj["scenes"][0]["scene_id"]

        patch_resp = client.patch(
            f"/api/flow/projects/{project_id}/scenes/{scene_id}",
            json={
                "title": "Updated Scene Title",
                "visual_prompt": "Macro shot of unit circle rotation in complex plane",
                "narration_text": "Updated narration script for Euler identity.",
                "duration_sec": 6,
            },
        )
        assert patch_resp.status_code == 200
        updated_scenes = patch_resp.json()["project"]["scenes"]
        sc0 = [s for s in updated_scenes if s["scene_id"] == scene_id][0]
        assert sc0["title"] == "Updated Scene Title"
        assert sc0["duration_sec"] == 6

        gen_resp = client.post(
            f"/api/flow/projects/{project_id}/scenes/{scene_id}/generate",
            json={},
        )
        assert gen_resp.status_code == 200
        gen_data = gen_resp.json()
        assert gen_data["ok"] is True
        assert gen_data["video_url"].startswith("/api/flow/media/")
        assert gen_data["video_url"].endswith(".mp4")


def test_req002_api_assemble_subtitles_and_mp4_media_streaming() -> None:
    from backend.app.main import app

    with TestClient(app) as client:
        create_resp = client.post(
            "/api/flow/projects/storyboard",
            json={"topic": "Navier-Stokes Fluid Dynamics", "scene_count": 3},
        )
        project_id = create_resp.json()["project"]["project_id"]

        asm_resp = client.post(f"/api/flow/projects/{project_id}/assemble")
        assert asm_resp.status_code == 200
        asm_data = asm_resp.json()
        assert asm_data["ok"] is True
        assert asm_data["assembled_video_url"] == f"/api/flow/media/{project_id}_lesson.mp4"
        assert asm_data["subtitles_vtt_url"] == f"/api/flow/projects/{project_id}/subtitles.vtt"
        assert asm_data["subtitles_srt_url"] == f"/api/flow/projects/{project_id}/subtitles.srt"

        vtt_resp = client.get(asm_data["subtitles_vtt_url"])
        assert vtt_resp.status_code == 200
        assert "WEBVTT" in vtt_resp.text

        srt_resp = client.get(asm_data["subtitles_srt_url"])
        assert srt_resp.status_code == 200
        assert "00:00:00,000 -->" in srt_resp.text

        media_resp = client.get(asm_data["assembled_video_url"])
        assert media_resp.status_code == 200
        assert media_resp.headers["content-type"].startswith("video/mp4")
        assert media_resp.content[4:8] == b"ftyp"


# ============================================================================
# REQ-003: Chrome Cookie Sync Extension (labs.google/fx/tools/flow)
# ============================================================================


def test_req003_cookie_sync_extension_supports_labs_google_flow_session() -> None:
    common_js = (
        REPO_ROOT
        / "extensions"
        / "gemini-cookie-sync-extension"
        / "common.js"
    )
    assert common_js.is_file(), "common.js must exist"
    text = common_js.read_text(encoding="utf-8")
    assert "https://labs.google/fx/tools/flow" in text
    assert "__Secure-next-auth.session-token" in text
    assert "labs.google" in text


# ============================================================================
# REQ-004: Frontend Flow Studio UI & L1 Skill Bundle
# ============================================================================


def test_req004_frontend_flow_studio_drawer_and_header_testids() -> None:
    drawer_file = REPO_ROOT / "frontend" / "src" / "components" / "FlowStudioDrawer.tsx"
    header_file = REPO_ROOT / "frontend" / "src" / "components" / "Header.tsx"

    assert drawer_file.is_file(), "FlowStudioDrawer.tsx must exist"
    drawer_text = drawer_file.read_text(encoding="utf-8")
    for testid in (
        'data-testid="flow-studio-drawer"',
        'data-testid="flow-topic-input"',
        'data-testid="flow-generate-storyboard-btn"',
        'data-testid="flow-assemble-btn"',
    ):
        assert testid in drawer_text, f"Missing {testid} in FlowStudioDrawer.tsx"

    assert header_file.is_file(), "Header.tsx must exist"
    header_text = header_file.read_text(encoding="utf-8")
    assert 'data-testid="open-flow-studio-btn"' in header_text


def test_req004_frontend_app_mounts_flow_studio_drawer() -> None:
    app_file = REPO_ROOT / "frontend" / "src" / "App.tsx"
    assert app_file.is_file(), "App.tsx must exist"
    app_text = app_file.read_text(encoding="utf-8")
    assert "FlowStudioDrawer" in app_text


def test_req004_google_flow_video_skill_and_cli_script_bundled() -> None:
    skill_md = REPO_ROOT / ".agents" / "skills" / "google-flow-video" / "SKILL.md"
    cli_script = (
        REPO_ROOT
        / ".agents"
        / "skills"
        / "google-flow-video"
        / "scripts"
        / "flow_lesson_cli.py"
    )
    assert skill_md.is_file(), f"Missing {skill_md}"
    assert cli_script.is_file(), f"Missing {cli_script}"
    skill_text = skill_md.read_text(encoding="utf-8")
    assert "Flow" in skill_text and "educational" in skill_text.lower()


def test_req004_flow_lesson_cli_script_executes_dry_run() -> None:
    cli_script = (
        REPO_ROOT
        / ".agents"
        / "skills"
        / "google-flow-video"
        / "scripts"
        / "flow_lesson_cli.py"
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(cli_script),
            "--topic",
            "Fourier Transform",
            "--scenes",
            "3",
            "--dry-run",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["topic"] == "Fourier Transform"
    assert len(payload["scenes"]) == 3


def test_req004_zero_cyrillic_in_all_new_m27_flow_files() -> None:
    targets = [
        REPO_ROOT / "backend" / "app" / "bridge" / "flow.py",
        REPO_ROOT / "backend" / "app" / "api" / "flow.py",
        REPO_ROOT / "frontend" / "src" / "components" / "FlowStudioDrawer.tsx",
        REPO_ROOT / ".agents" / "skills" / "google-flow-video" / "SKILL.md",
        REPO_ROOT
        / ".agents"
        / "skills"
        / "google-flow-video"
        / "scripts"
        / "flow_lesson_cli.py",
        Path(__file__).resolve(),
    ]
    offenders: list[str] = []
    for path in targets:
        assert path.is_file(), f"Missing M27 target file: {path}"
        text = path.read_text(encoding="utf-8")
        if CYRILLIC_RE.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"Cyrillic characters found in M27 files: {offenders}"
