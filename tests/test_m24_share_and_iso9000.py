import hashlib
import json
from typing import Any, Dict, List
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.api.claude_plugins import (
    parse_shared_conversation_payload,
    resolve_share_url_ids,
    router,
)

client = TestClient(app)


def _build_realistic_ujx1bf_inner_list(
    prompt: str = "Design an ISO 9001:2015 compliant AgentOps quality pipeline.",
    model_id: str = "95a4c0abcba24364",
    direct_text: str = "Direct summary response text.",
    title_56: str | None = "ISO 9001:2015 AgentOps Architecture",
    title_57: str | None = "ISO 9001:2015 AgentOps Deep Research",
    title_30: str | None = "ISO 9001:2015 Normative Baseline Report",
    report_markdown: str | None = "# ISO 9001:2015 AgentOps Pipeline\n\n## Echelon 1: Normative Planning\nFull markdown report body.",
    thinking_steps: List[tuple[str, str]] | None = None,
    html_widgets: List[str] | None = None,
) -> list:
    """Constructs a realistic nested Google ujx1Bf inner list payload matching REQ-001."""
    if thinking_steps is None:
        thinking_steps = [
            ("Analyzing Normative Standards", "Reviewing ISO 9001:2015 and Agile V self-grading bias controls."),
            ("Synthesizing Echelon Guards", "Mapping W3C PROV-DM provenance graph and SHA-256 statepoints."),
        ]

    cand: List[Any] = [None] * 35
    cand[1] = [direct_text]

    cand12: List[Any] = [None] * 60
    if title_56 is not None:
        cand12[56] = [[None, None, None, title_56]]

    raw_steps = []
    for step_title, step_body in thinking_steps:
        step_node: List[Any] = [None] * 6
        step_node[5] = [step_title, step_body]
        raw_steps.append(step_node)

    cand12[57] = [
        None,
        [
            None,
            None,
            None,
            None,
            [title_57, None, raw_steps],
        ],
    ]
    cand[12] = cand12

    if title_30 is not None or report_markdown is not None:
        cand[30] = [[None, None, title_30, None, report_markdown]]

    if html_widgets:
        cand[18] = html_widgets

    turn: List[Any] = [None] * 4
    turn[2] = [[prompt], None, None, None, model_id]
    turn[3] = [[cand]]

    root = [[None, [turn]]]
    return root


# ============================================================================
# REQ-001: parse_shared_conversation_payload
# ============================================================================


def test_req001_parse_ujx1bf_inner_list_full_deep_research():
    """REQ-001: Parses a complete ujx1Bf inner list with Deep Research report, thinking steps, and deduplicated HTML widgets."""
    widget_a = "<!DOCTYPE html><html><head><title>Widget A</title></head><body><div>Interactive Chart</div></body></html>"
    widget_b = "<!DOCTYPE html><html><head><title>Widget B</title></head><body><div>Matrix Sim</div></body></html>"
    inner_list = _build_realistic_ujx1bf_inner_list(
        html_widgets=[widget_a, widget_b, widget_a],  # duplicate widget_a to verify deduplication
    )

    result = parse_shared_conversation_payload(inner_list)

    assert isinstance(result, dict)
    assert result["title"] in (
        "ISO 9001:2015 AgentOps Architecture",
        "ISO 9001:2015 AgentOps Deep Research",
        "ISO 9001:2015 Normative Baseline Report",
    )
    assert result["prompts"] == ["Design an ISO 9001:2015 compliant AgentOps quality pipeline."]
    assert result["model_id"] == "95a4c0abcba24364"
    assert result["main_report_markdown"].startswith("# ISO 9001:2015 AgentOps Pipeline")
    assert result["char_count"] == len(result["main_report_markdown"])
    assert len(result["thinking_steps"]) == 2
    assert result["thinking_steps"][0] == {
        "title": "Analyzing Normative Standards",
        "content": "Reviewing ISO 9001:2015 and Agile V self-grading bias controls.",
    }
    assert result["thinking_steps"][1] == {
        "title": "Synthesizing Echelon Guards",
        "content": "Mapping W3C PROV-DM provenance graph and SHA-256 statepoints.",
    }
    assert result["html_widgets"] == [widget_a, widget_b]


def test_req001_parse_raw_batchexecute_envelope_string():
    """REQ-001: Parses a raw batchexecute string starting with )]}' and wrapping ujx1Bf inner JSON string."""
    inner_list = _build_realistic_ujx1bf_inner_list(
        prompt="Explain quantum error correction.",
        model_id="cd443e5432524c58",
        report_markdown="# Quantum Error Correction\n\nSurface codes and stabilizer formalism.",
    )
    inner_json_str = json.dumps(inner_list)
    batchexecute_envelope = ")]}'\n\n" + json.dumps(
        [["wrb.fr", "ujx1Bf", inner_json_str, None, None, None, "generic"]]
    )

    result = parse_shared_conversation_payload(batchexecute_envelope)

    assert result["prompts"] == ["Explain quantum error correction."]
    assert result["model_id"] == "cd443e5432524c58"
    assert result["main_report_markdown"] == "# Quantum Error Correction\n\nSurface codes and stabilizer formalism."
    assert result["char_count"] == len(result["main_report_markdown"])


def test_req001_parse_fallback_to_direct_candidate_text_and_alt_titles():
    """REQ-001: Falls back to cand[1][0] when cand[30][0][4] is absent and resolves alternative title locations."""
    inner_list = _build_realistic_ujx1bf_inner_list(
        direct_text="Fallback direct response markdown when no deep research report is present.",
        title_56=None,
        title_57=None,
        title_30="Canvas Title From Index 30",
        report_markdown=None,
        thinking_steps=[],
        html_widgets=[],
    )

    result = parse_shared_conversation_payload(inner_list)

    assert result["title"] == "Canvas Title From Index 30"
    assert result["main_report_markdown"] == "Fallback direct response markdown when no deep research report is present."
    assert result["char_count"] == len("Fallback direct response markdown when no deep research report is present.")
    assert result["thinking_steps"] == []
    assert result["html_widgets"] == []


def test_req001_parse_prebuilt_dict_input():
    """REQ-001: Accepts an already-normalized dict payload and ensures all required output keys including char_count exist."""
    raw_dict = {
        "title": "Pre-parsed Share Document",
        "main_report_markdown": "## Summary\nPre-parsed markdown content.",
        "prompts": ["Initial user prompt"],
        "model_id": "95a4c0abcba24364",
        "thinking_steps": [{"title": "Step 1", "content": "Thought process"}],
        "html_widgets": ["<!DOCTYPE html><html><body>Widget</body></html>"],
    }

    result = parse_shared_conversation_payload(raw_dict)

    assert result["title"] == "Pre-parsed Share Document"
    assert result["main_report_markdown"] == "## Summary\nPre-parsed markdown content."
    assert result["prompts"] == ["Initial user prompt"]
    assert result["model_id"] == "95a4c0abcba24364"
    assert result["thinking_steps"] == [{"title": "Step 1", "content": "Thought process"}]
    assert result["html_widgets"] == ["<!DOCTYPE html><html><body>Widget</body></html>"]
    assert result["char_count"] == len("## Summary\nPre-parsed markdown content.")


# ============================================================================
# REQ-002: resolve_share_url_ids
# ============================================================================


def test_req002_resolve_full_gemini_share_url_with_skid():
    """REQ-002: Extracts share_id, skid, and is_short_link=False from full gemini.google.com/share URL."""
    url = "https://gemini.google.com/share/e0229f89d62d?social_preview=false&skid=742a6612-8e8f-45dc-8bd9-c24d8a46c89e"
    resolved = resolve_share_url_ids(url)

    assert resolved == {
        "share_id": "e0229f89d62d",
        "skid": "742a6612-8e8f-45dc-8bd9-c24d8a46c89e",
        "is_short_link": False,
    }


def test_req002_resolve_short_share_link():
    """REQ-002: Extracts share_id, skid=None, and is_short_link=True from share.gemini.google short URL."""
    url = "https://share.gemini.google/qocuwt7LtGLO"
    resolved = resolve_share_url_ids(url)

    assert resolved == {
        "share_id": "qocuwt7LtGLO",
        "skid": None,
        "is_short_link": True,
    }


def test_req002_resolve_bare_hex_id():
    """REQ-002: Extracts bare hex share_id with skid=None and is_short_link=False."""
    resolved = resolve_share_url_ids("e0229f89d62d")

    assert resolved == {
        "share_id": "e0229f89d62d",
        "skid": None,
        "is_short_link": False,
    }


@pytest.mark.parametrize(
    "invalid_input",
    [
        "",
        "   ",
        "https://example.com/share/e0229f89d62d",
        "https://malicious.domain.org/qocuwt7LtGLO",
    ],
)
def test_req002_resolve_invalid_or_unsupported_raises_value_error(invalid_input: str):
    """REQ-002: Raises ValueError on empty string or unsupported URL domain."""
    with pytest.raises(ValueError):
        resolve_share_url_ids(invalid_input)


# ============================================================================
# REQ-003: POST /api/claude-plugins/share-ingest
# ============================================================================


def test_req003_share_ingest_with_rpc_payload():
    """REQ-003: Ingests a share URL with direct rpc_payload and returns parsed document."""
    inner_list = _build_realistic_ujx1bf_inner_list(
        prompt="Ingest test prompt",
        report_markdown="# Ingested Report\n\nVerified via FastAPI TestClient.",
    )
    response = client.post(
        "/api/claude-plugins/share-ingest",
        json={
            "url": "https://gemini.google.com/share/e0229f89d62d?social_preview=false&skid=742a6612-8e8f-45dc-8bd9-c24d8a46c89e",
            "account_id": "primary",
            "rpc_payload": inner_list,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["share_id"] == "e0229f89d62d"
    doc = body["document"]
    assert doc["prompts"] == ["Ingest test prompt"]
    assert doc["main_report_markdown"] == "# Ingested Report\n\nVerified via FastAPI TestClient."
    assert doc["char_count"] == len("# Ingested Report\n\nVerified via FastAPI TestClient.")


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "   ",
        "https://unsupported-domain.example.com/share/123456",
    ],
)
def test_req003_share_ingest_invalid_url_returns_400(bad_url: str):
    """REQ-003: Returns HTTP 400 when url is empty or from an unsupported domain."""
    response = client.post(
        "/api/claude-plugins/share-ingest",
        json={"url": bad_url},
    )
    assert response.status_code == 400


# ============================================================================
# REQ-004: POST /api/claude-plugins/iso9000-validate
# ============================================================================


def test_req004_iso9000_validate_valid_baseline_yaml():
    """REQ-004: Validates a compliant Frozen Baseline YAML and returns SHA-256, req_count, risk_level, and valid=True."""
    baseline_yaml = """baseline_id: "BL-20261022-114352"
risk_level: "L2"
aop_validation:
  solvability: true
  completeness: true
  non_redundancy: true
artifact_scope:
  allowed_paths:
    - "backend/app/api/claude_plugins.py"
    - "tests/test_m24_share_and_iso9000.py"
  forbidden_paths:
    - "backend/app/core/database.py"
requirements:
  - id: "REQ-001"
    description: "Parse ujx1Bf shared conversation payload"
  - id: "REQ-002"
    description: "Resolve share URL IDs"
  - id: "REQ-003"
    description: "Share ingest API endpoint"
  - id: "REQ-004"
    description: "ISO 9000 baseline validator endpoint"
"""
    expected_sha256 = hashlib.sha256(baseline_yaml.encode("utf-8")).hexdigest()

    response = client.post(
        "/api/claude-plugins/iso9000-validate",
        json={"baseline_yaml": baseline_yaml},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True
    assert data["req_count"] == 4
    assert data["risk_level"] == "L2"
    assert data["baseline_sha256"] == expected_sha256
    assert len(data["baseline_sha256"]) == 64
    assert data["errors"] == []


def test_req004_iso9000_validate_detects_aop_scope_and_requirement_violations():
    """REQ-004: Returns valid=False and populated errors list when AOP flags are false, allowed_paths is empty, or requirement IDs are malformed."""
    invalid_baseline_yaml = json.dumps(
        {
            "baseline_id": "BL-INVALID-01",
            "risk_level": "L3",
            "aop_validation": {
                "solvability": True,
                "completeness": False,
                "non_redundancy": True,
            },
            "artifact_scope": {
                "allowed_paths": [],
            },
            "requirements": [
                {"id": "BAD-ID-1", "description": "Does not match REQ-\\d+"},
            ],
        }
    )
    expected_sha256 = hashlib.sha256(invalid_baseline_yaml.encode("utf-8")).hexdigest()

    response = client.post(
        "/api/claude-plugins/iso9000-validate",
        json={"baseline_yaml": invalid_baseline_yaml},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False
    assert data["baseline_sha256"] == expected_sha256
    assert len(data["errors"]) >= 3
