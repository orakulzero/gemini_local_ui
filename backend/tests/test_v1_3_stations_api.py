"""
backend/tests/test_v1_3_stations_api.py
Test suite for AGY Stations v1.3.0 Telemetry and Diagnostics Endpoints:
- /api/v1/stations/overview
- /api/v1/stations/diagnostics
- /api/v1/stations/{station_id}
- /api/v1/stations/merkle/verify
- /api/v1/stations/diagnostics/probe
"""

from __future__ import annotations

import re
from typing import Any, Dict, List
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.routes.stations import (
    STATIONS_V1_3_MATRIX,
    build_merkle_audit_tree,
    compute_rfc6962_leaf_hash,
    compute_rfc6962_node_hash,
    generate_merkle_inclusion_proof,
    verify_merkle_inclusion_proof,
)

EXPECTED_9_STATIONS = [
    "iso9000-agentops",
    "agy-golden-stack",
    "agy-secret-shield",
    "agy-brain-memory",
    "agy-doctor-mux",
    "agy-lsrpc-bridge",
    "pe-reversing-lab",
    "eskd-vision-station",
    "agy-federation-mesh",
]


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_stations_overview_endpoint_schema(client: TestClient) -> None:
    """Verify /api/v1/stations/overview returns valid v1.3.0 metadata for all 9 stations."""
    resp = client.get("/api/v1/stations/overview")
    assert resp.status_code == 200
    data = resp.json()

    assert data.get("ok") is True
    assert data.get("version") in ("1.3.0", "2.0.0")
    assert data.get("ecosystem_version") in ("1.3.0", "2.0.0")
    assert data.get("stations_count") in (9, 10)

    stations = data.get("stations", {})
    assert len(stations) in (9, 10)

    for expected_id in EXPECTED_9_STATIONS:
        assert expected_id in stations, f"Missing station: {expected_id}"
        meta = stations[expected_id]
        assert meta["version"] in ("1.3.0", "2.0.0")
        assert meta["status"] == "HEALTHY"
        assert len(meta["capabilities"]) >= 5
        assert isinstance(meta.get("v1_3_features", meta.get("v2_0_features")), dict)


def test_stations_overview_v1_3_capabilities_matrix(client: TestClient) -> None:
    """Verify each station specifically exposes its mandatory v1.3.0 capability flags."""
    resp = client.get("/api/v1/stations/overview")
    assert resp.status_code == 200
    stations = resp.json().get("stations", {})

    # 1. iso9000-agentops: Merkle audit tree & proof verification
    iso_feats = stations["iso9000-agentops"]["v1_3_features"]
    assert iso_feats.get("merkle_audit_tree") is True
    assert iso_feats.get("inclusion_proof_verification") is True

    # 2. agy-golden-stack: Sliding circuit breaker & Merkle digest
    gs_feats = stations["agy-golden-stack"]["v1_3_features"]
    assert gs_feats.get("sliding_window_circuit_breaker") is True
    assert gs_feats.get("bus_merkle_digest") is True

    # 3. agy-secret-shield: AST semantic secret & taint analysis
    ss_feats = stations["agy-secret-shield"]["v1_3_features"]
    assert ss_feats.get("ast_semantic_taint_analysis") is True
    assert ss_feats.get("false_positive_reduction_80pct") is True

    # 4. agy-brain-memory: Hybrid BM25 + subword TF-IDF retriever with RRF k=60, 64-bit SimHash
    bm_feats = stations["agy-brain-memory"]["v1_3_features"]
    assert bm_feats.get("hybrid_bm25_subword_tfidf") is True
    assert bm_feats.get("rrf_k") == 60
    assert bm_feats.get("simhash_64bit_deduplication") is True

    # 5. agy-doctor-mux: MCP schema compaction, ecosystem version matrix sync
    dm_feats = stations["agy-doctor-mux"]["v1_3_features"]
    assert dm_feats.get("mcp_schema_compaction_35pct") is True
    assert dm_feats.get("target_version") in ("1.3.0", "2.0.0")

    # 6. agy-lsrpc-bridge: Automated MCP config token optimizer
    ls_feats = stations["agy-lsrpc-bridge"]["v1_3_features"]
    assert ls_feats.get("optimize_mcp_config_tokens") is True
    assert ls_feats.get("schema_compaction_pipeline") is True

    # 7. pe-reversing-lab: CFG complexity, Mandiant imphash, Rich header DanS XOR, PEB-walk/trampolines
    pe_feats = stations["pe-reversing-lab"]["v1_3_features"]
    assert pe_feats.get("cfg_complexity") is True
    assert pe_feats.get("mandiant_imphash") is True
    assert pe_feats.get("rich_header_dans_xor") is True

    # 8. eskd-vision-station: Vector PDF line weights (GOST 2.303) & GOST 2.106 BOM extractor
    es_feats = stations["eskd-vision-station"]["v1_3_features"]
    assert es_feats.get("gost_2303_line_weights") is True
    assert es_feats.get("gost_2106_bom_specification") is True

    # 9. agy-federation-mesh: Pareto-front router & speculative hedging engine
    fm_feats = stations["agy-federation-mesh"]["v1_3_features"]
    assert fm_feats.get("pareto_frontier_selection") is True
    assert fm_feats.get("speculative_swarm_hedging") is True


def test_stations_diagnostics_endpoint(client: TestClient) -> None:
    """Verify /api/v1/stations/diagnostics runs live probes and Merkle proof verification."""
    resp = client.get("/api/v1/stations/diagnostics")
    assert resp.status_code == 200
    diag = resp.json()

    assert diag.get("ok") is True
    assert diag.get("version") in ("1.3.0", "2.0.0")
    assert diag.get("all_stations_healthy") is True
    assert isinstance(diag.get("golden_bus_latency_ms"), (int, float))

    merkle = diag.get("merkle_verification", {})
    assert merkle.get("valid") is True
    assert len(merkle.get("root_hex", "")) == 64
    assert merkle.get("leaf_count") == 5

    matrix = diag.get("ecosystem_matrix", {})
    assert matrix.get("target_version") in ("1.3.0", "2.0.0")
    assert matrix.get("synchronized") is True
    assert matrix.get("drifted") == []

    probes = diag.get("probes", {})
    assert len(probes) in (9, 10)
    for sid in EXPECTED_9_STATIONS:
        assert probes[sid]["probe_passed"] is True
        assert probes[sid]["status"] == "HEALTHY"


def test_station_detail_endpoint(client: TestClient) -> None:
    """Verify /api/v1/stations/{station_id} returns single station detail and handles 404."""
    for sid in EXPECTED_9_STATIONS:
        resp = client.get(f"/api/v1/stations/{sid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert data["station"]["station_id"] == sid
        assert data["station"]["version"] in ("1.3.0", "2.0.0")

    # Also check underscore variant
    resp_under = client.get("/api/v1/stations/pe_reversing_lab")
    assert resp_under.status_code == 200

    # Check 404 for invalid station
    resp_404 = client.get("/api/v1/stations/non_existent_station")
    assert resp_404.status_code == 404


def test_merkle_inclusion_proof_verification_endpoint(client: TestClient) -> None:
    """Verify /api/v1/stations/merkle/verify accurately accepts valid proofs and rejects tampered proofs."""
    leaves = [b"alpha", b"beta", b"gamma", b"delta"]
    tree = build_merkle_audit_tree(leaves)
    proof = generate_merkle_inclusion_proof(1, tree)

    req_payload = {
        "leaf_hex": leaves[1].hex(),
        "proof": proof,
        "expected_root_hex": tree["root_hex"],
    }
    resp = client.post("/api/v1/stations/merkle/verify", json=req_payload)
    assert resp.status_code == 200
    res = resp.json()
    assert res.get("ok") is True
    assert res.get("valid") is True

    # Tampered leaf should fail verification
    tampered_payload = {
        "leaf_hex": b"tampered".hex(),
        "proof": proof,
        "expected_root_hex": tree["root_hex"],
    }
    resp_bad = client.post("/api/v1/stations/merkle/verify", json=tampered_payload)
    assert resp_bad.status_code == 200
    assert resp_bad.json().get("valid") is False


def test_legacy_station_routes(client: TestClient) -> None:
    """Verify backward-compatible /api/stations/* endpoints mirror /api/v1/stations/*."""
    resp_ov = client.get("/api/stations/overview")
    assert resp_ov.status_code == 200
    assert resp_ov.json().get("stations_count") in (9, 10)

    resp_diag = client.get("/api/stations/diagnostics")
    assert resp_diag.status_code == 200
    assert resp_diag.json().get("all_stations_healthy") is True


def test_zero_cyrillic_characters_in_api_responses(client: TestClient) -> None:
    """Verify 100% English only (zero Cyrillic characters [\\u0400-\\u04FF]) across API outputs."""
    cyrillic_pattern = re.compile(r"[\u0400-\u04FF]")

    for path in ["/api/v1/stations/overview", "/api/v1/stations/diagnostics"]:
        resp = client.get(path)
        assert resp.status_code == 200
        text = resp.text
        matches = cyrillic_pattern.findall(text)
        assert len(matches) == 0, f"Found Cyrillic characters in {path}: {matches[:10]}"


def test_merkle_tree_math_domain_separation() -> None:
    """Verify mathematical properties of RFC 6962 domain separation (0x00 leaf, 0x01 node)."""
    data = b"sample_telemetry_event"
    leaf_h = compute_rfc6962_leaf_hash(data)
    node_h = compute_rfc6962_node_hash(leaf_h, leaf_h)

    assert len(leaf_h) == 32
    assert len(node_h) == 32
    assert leaf_h != node_h

    # Domain separation prevents second-preimage attack:
    # A leaf hash of (leaf_h + leaf_h) must never equal node_h
    coll_leaf = compute_rfc6962_leaf_hash(leaf_h + leaf_h)
    assert coll_leaf != node_h
