"""
backend/app/api/ecosystem_scout.py
FastAPI router for M41: Autonomous Ecosystem Intelligence, Repo/Skill/Forum Scout & Cross-IDE Skill Transpiler.
Implements REQ-SCOUT-05 (`/api/v1/scout/*`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.app.bridge.ecosystem_scout import get_ecosystem_scout

router = APIRouter(prefix="/api/v1/scout", tags=["ecosystem-scout"])


class ScoutSweepRequest(BaseModel):
    lanes: list[str] = Field(
        default_factory=lambda: [
            "repo_solutions",
            "cross_ide_skills",
            "forum_pain_points",
            "emerging_tech",
        ]
    )
    focus_query: str = ""


class AdaptSkillRequest(BaseModel):
    source_ide: str
    skill_name: str
    raw_content: str
    target_plugin: str = "agy-golden-stack"


@router.post("/sweep")
async def trigger_scout_sweep(req: ScoutSweepRequest, request: Request) -> dict[str, Any]:
    """REQ-SCOUT-01, REQ-SCOUT-05: Executes a multi-lane ecosystem intelligence sweep."""
    scout = get_ecosystem_scout()
    session_mgr = getattr(request.app.state, "session_manager", None)
    client = None
    if session_mgr and session_mgr.get_health().is_authenticated:
        from backend.app.bridge.client import GeminiWebClient

        client = GeminiWebClient(
            session_context=session_mgr.context,
            base_url=session_mgr.base_url,
            timeout=20.0,
        )
    try:
        return await scout.run_intelligence_sweep(
            lanes=req.lanes,
            focus_query=req.focus_query,
            client=client,
        )
    finally:
        if client is not None:
            await client.close()


@router.get("/findings")
async def list_scout_findings(
    lane: str | None = None,
    target_agy_repo: str | None = None,
    min_score: float = 0.0,
) -> dict[str, Any]:
    """REQ-SCOUT-01, REQ-SCOUT-05: Lists and filters discovered ecosystem intelligence findings."""
    scout = get_ecosystem_scout()
    findings = scout.list_findings(
        lane=lane,
        target_agy_repo=target_agy_repo,
        min_score=min_score,
    )
    return {"ok": True, "count": len(findings), "findings": findings}


@router.post("/adapt-skill")
async def adapt_cross_ide_skill(req: AdaptSkillRequest) -> dict[str, Any]:
    """REQ-SCOUT-02, REQ-SCOUT-05: Converts a foreign IDE rule/skill into native Antigravity SKILL.md."""
    scout = get_ecosystem_scout()
    return scout.adapt_skill_to_antigravity(
        source_ide=req.source_ide,
        skill_name=req.skill_name,
        raw_content=req.raw_content,
        target_plugin=req.target_plugin,
    )


@router.post("/promote/{finding_id}")
async def promote_scout_finding(finding_id: str) -> dict[str, Any]:
    """REQ-SCOUT-03, REQ-SCOUT-05: Promotes a finding to the Stage-Only Task Pipeline."""
    scout = get_ecosystem_scout()
    try:
        return scout.promote_finding_to_pipeline(finding_id=finding_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/digest")
async def get_scout_digest() -> dict[str, Any]:
    """REQ-SCOUT-04, REQ-SCOUT-05: Generates a consolidated Markdown intelligence digest."""
    scout = get_ecosystem_scout()
    return scout.generate_digest_markdown()
