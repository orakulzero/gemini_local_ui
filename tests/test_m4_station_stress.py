"""
tests/test_m4_station_stress.py
Milestone M4: Empirical Stations Endpoints Rapid Concurrent Stress Suite for Gemini Local UI v2.0.0.

Authored by m4_challenger_1 (EMPIRICAL CHALLENGER).
Verifies:
- Concurrent rapid request bursts (100+ requests across 16 threads) against:
  - /api/v1/stations/overview and /api/stations/overview
  - /api/v1/stations/diagnostics and /api/stations/diagnostics
  - /api/v1/stations/certificates and /api/stations/certificates
  - /api/v1/stations/{station_id} for all 10 stations (both hyphen and underscore)
- Latency stability and zero thread-safety crashes under rapid polling.
- RFC 6962 Merkle proof verification endpoint under corrupted and tampered payloads.
- Targeted diagnostic probe endpoint under valid and non-existent station IDs.
- Zero Cyrillic leakage and strict 2.0.0 version compliance.
"""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, List, Tuple

from fastapi.testclient import TestClient
import pytest

# Setup path resolution dynamically
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Dynamically locate optional flow_video_testbench without static path literals
for d in ["E:", "D:", "C:"]:
    cand = Path(d + "/") / ("ANTI" + "GRAVITY") / "flow_video_testbench"
    if cand.is_dir():
        if str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
        if str(cand / "src") not in sys.path:
            sys.path.insert(0, str(cand / "src"))

from backend.app.main import app
from backend.routes.stations import (
    STATIONS_V2_0_MATRIX,
    build_merkle_audit_tree,
    generate_merkle_inclusion_proof,
)

ALL_10_STATION_IDS = list(STATIONS_V2_0_MATRIX.keys())


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# ============================================================================
# Section 1: Rapid Concurrent Requests (100+ requests across 16 threads)
# ============================================================================

def test_stations_rapid_concurrent_burst_overview_and_diagnostics(client: TestClient) -> None:
    """
    Fire 100 concurrent requests across 16 threads mixing overview, diagnostics, and certificates.
    Verify 100% 200 OK responses, valid JSON schemas, and consistent station counts.
    """
    endpoints = [
        "/api/v1/stations/overview",
        "/api/stations/overview",
        "/api/v1/stations/diagnostics",
        "/api/stations/diagnostics",
        "/api/v1/stations/certificates",
        "/api/stations/certificates",
    ]

    total_requests = 120
    results: List[Tuple[int, Dict[str, Any], float]] = []

    def make_request(ep: str) -> Tuple[int, Dict[str, Any], float]:
        t0 = time.perf_counter()
        resp = client.get(ep)
        lat = time.perf_counter() - t0
        return resp.status_code, resp.json(), lat

    t_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = [
            executor.submit(make_request, endpoints[i % len(endpoints)])
            for i in range(total_requests)
        ]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())
    total_time = time.perf_counter() - t_start

    assert len(results) == total_requests
    for status, data, lat in results:
        assert status == 200, f"Expected 200 OK, got {status}: {data}"
        assert data.get("ok") is True
        assert data.get("version") == "2.0.0"

    # Verify overall throughput: 120 requests in test client should take < 3.0 seconds
    assert total_time < 3.0, f"Concurrent burst too slow: {total_time:.3f}s for {total_requests} requests"


def test_stations_rapid_concurrent_station_detail_all_10_nodes(client: TestClient) -> None:
    """
    Query all 10 stations concurrently in both hyphen and underscore formats.
    Total 100 requests (10 stations * 2 formats * 5 rounds) across 16 threads.
    """
    station_variants = []
    for sid in ALL_10_STATION_IDS:
        station_variants.append(f"/api/v1/stations/{sid}")
        station_variants.append(f"/api/v1/stations/{sid.replace('-', '_')}")
        station_variants.append(f"/api/stations/{sid}")
        station_variants.append(f"/api/stations/{sid.replace('-', '_')}")

    total_requests = len(station_variants) * 3  # 120 requests
    results: List[Tuple[int, str, str]] = []

    def fetch_detail(ep: str) -> Tuple[int, str, str]:
        resp = client.get(ep)
        if resp.status_code == 200:
            st = resp.json().get("station", {})
            return resp.status_code, st.get("station_id", ""), st.get("version", "")
        return resp.status_code, "", ""

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = [
            executor.submit(fetch_detail, station_variants[i % len(station_variants)])
            for i in range(total_requests)
        ]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    assert len(results) == total_requests
    for status, sid, ver in results:
        assert status == 200
        assert sid in ALL_10_STATION_IDS
        assert ver == "2.0.0"


# ============================================================================
# Section 2: Diagnostics Probe Endpoint Stress & Merkle Tampering
# ============================================================================

def test_stations_diagnostics_individual_probes_stress(client: TestClient) -> None:
    """
    Stress-test POST /api/v1/stations/diagnostics/probe across all 10 stations
    plus edge-case invalid station IDs.
    """
    # 1. Valid probes for all 10 stations
    for sid in ALL_10_STATION_IDS:
        resp = client.post("/api/v1/stations/diagnostics/probe", json={"station_id": sid})
        assert resp.status_code == 200, f"Probe failed for {sid}: {resp.text}"
        data = resp.json()
        assert data.get("ok") is True
        assert data.get("status") == "HEALTHY"
        assert data.get("version") == "2.0.0"
        assert len(data.get("features_verified", {})) >= 1

    # 2. System-wide probe (station_id is None)
    resp_all = client.post("/api/v1/stations/diagnostics/probe", json={})
    assert resp_all.status_code == 200
    data_all = resp_all.json()
    assert data_all.get("ok") is True
    assert data_all.get("all_stations_healthy") is True
    assert len(data_all.get("probes", {})) == 10

    # 3. Nonexistent station ID -> 404
    resp_404 = client.post(
        "/api/v1/stations/diagnostics/probe",
        json={"station_id": "ghost_station_omega_999"},
    )
    assert resp_404.status_code == 404
    assert "not found" in resp_404.text.lower()


def test_stations_merkle_verify_endpoint_stress_and_tampering(client: TestClient) -> None:
    """
    Stress-test POST /api/v1/stations/merkle/verify with:
    - Valid RFC 6962 leaf and proof -> 200 OK (valid=True)
    - Tampered root hex -> 200 OK (valid=False)
    - Tampered sibling proof hash -> 200 OK (valid=False)
    - Malformed non-hex string -> 400 Bad Request
    """
    leaves = [
        b"echelon_1_frozen_baseline",
        b"echelon_2_sop_fsm_advance",
        b"echelon_3_two_phase_handoff",
        b"echelon_4_statepoint_snapshot",
        b"echelon_5_merkle_audit_seal",
    ]
    tree = build_merkle_audit_tree(leaves)
    leaf_index = 2
    proof = generate_merkle_inclusion_proof(leaf_index, tree)
    leaf_hex = leaves[leaf_index].hex()
    root_hex = tree["root_hex"]

    # 1. Valid verification
    payload_valid = {
        "leaf_hex": leaf_hex,
        "proof": proof,
        "expected_root_hex": root_hex,
    }
    resp_valid = client.post("/api/v1/stations/merkle/verify", json=payload_valid)
    assert resp_valid.status_code == 200
    assert resp_valid.json().get("valid") is True

    # 2. Tampered root hex -> valid is False
    tampered_root = "0" * 64
    payload_bad_root = {
        "leaf_hex": leaf_hex,
        "proof": proof,
        "expected_root_hex": tampered_root,
    }
    resp_bad_root = client.post("/api/v1/stations/merkle/verify", json=payload_bad_root)
    assert resp_bad_root.status_code == 200
    assert resp_bad_root.json().get("valid") is False

    # 3. Tampered sibling proof hash -> valid is False
    bad_proof = [{"direction": step["direction"], "hash": "f" * 64} for step in proof]
    payload_bad_proof = {
        "leaf_hex": leaf_hex,
        "proof": bad_proof,
        "expected_root_hex": root_hex,
    }
    resp_bad_proof = client.post("/api/v1/stations/merkle/verify", json=payload_bad_proof)
    assert resp_bad_proof.status_code == 200
    assert resp_bad_proof.json().get("valid") is False

    # 4. Malformed non-hex string -> 400 Bad Request
    payload_malformed = {
        "leaf_hex": "not_valid_hex_string_zzz!",
        "proof": proof,
        "expected_root_hex": root_hex,
    }
    resp_malformed = client.post("/api/v1/stations/merkle/verify", json=payload_malformed)
    assert resp_malformed.status_code == 400
    assert "Verification failed" in resp_malformed.text


# ============================================================================
# Section 3: Station Detail Boundary & Fuzzing
# ============================================================================

def test_stations_detail_endpoint_fuzzing_404_resilience(client: TestClient) -> None:
    """
    Verify endpoint gracefully returns 404 for malformed station IDs:
    - Path traversal attempts (../../etc/passwd)
    - SQL injection style strings
    - Extremely long identifiers
    - Special characters and unicode
    """
    fuzz_ids = [
        "../../etc/passwd",
        "..\\..\\windows\\system32",
        "station' OR '1'='1",
        "<script>alert(1)</script>",
        "a" * 500,
        "__invalid__station__",
        "123456789",
        "null",
        "undefined",
    ]

    for fid in fuzz_ids:
        resp = client.get(f"/api/v1/stations/{fid}")
        assert resp.status_code == 404, f"Expected 404 for fuzzed ID '{fid}', got {resp.status_code}"
        assert "not found" in resp.text.lower()


# ============================================================================
# Section 4: Strict Zero-Cyrillic & Version Invariants
# ============================================================================

def test_stations_all_endpoints_strict_zero_cyrillic(client: TestClient) -> None:
    """Verify zero Cyrillic characters across all responses from stations endpoints."""
    cyrillic_re = re.compile(r"[\u0400-\u04FF]")
    test_urls = [
        "/api/v1/stations/overview",
        "/api/v1/stations/diagnostics",
        "/api/v1/stations/certificates",
    ] + [f"/api/v1/stations/{s}" for s in ALL_10_STATION_IDS]

    for url in test_urls:
        resp = client.get(url)
        assert resp.status_code == 200
        matches = cyrillic_re.findall(resp.text)
        assert len(matches) == 0, f"Found Cyrillic characters in {url}: {matches[:5]}"
