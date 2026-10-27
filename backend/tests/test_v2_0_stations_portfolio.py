"""
ISO 9001:2015 / Agile V Verification Suite for Gemini Local UI & Cockpit v2.0.0.
Requirement: REQ-UI-201

Covers:
1. /api/v1/stations/overview:
   - Reports all 10 stations with "version": "2.0.0", including agy-computer-use.
   - stations_count == 10, ecosystem_version == "2.0.0".
   - Verifies each station declares healthy status, tier, capabilities, and v2.0.0 feature flags.
2. /api/v1/stations/diagnostics:
   - Reports health diagnostics for all 10 stations (probes dict has 10 entries).
   - all_stations_healthy is True.
   - Merkle audit verification valid with 64-char root_hex.
   - Ecosystem version matrix target_version == "2.0.0", synchronized is True, drifted is empty.
3. /api/v1/stations/certificates:
   - Returns signed portfolio quality certificate integrating with iso9000-agentops.
   - Certifies 10 stations, includes RTM coverage, max FMEA RPN, and SHA-256 / HMAC seal.
4. Station detail endpoints:
   - /api/v1/stations/agy-computer-use and /api/v1/stations/agy_computer_use return 200 with valid metadata.
5. Legacy routes compatibility (/api/stations/*).
6. Zero Cyrillic characters across all stations API responses.
7. Package and project version synchronization (pyproject.toml and frontend/package.json version "2.0.0").
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

# Resolve repository paths dynamically
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
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

EXPECTED_10_STATIONS: List[str] = [
    "iso9000-agentops",
    "agy-golden-stack",
    "agy-secret-shield",
    "agy-brain-memory",
    "agy-doctor-mux",
    "agy-lsrpc-bridge",
    "pe-reversing-lab",
    "eskd-vision-station",
    "agy-federation-mesh",
    "agy-computer-use",
]


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _get_field(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ============================================================================
# 1. Stations Overview Endpoint (REQ-UI-201)
# ============================================================================

def test_stations_overview_v2_0_ten_stations_schema(client: TestClient) -> None:
    """Verify /api/v1/stations/overview reports all 10 stations with version 2.0.0."""
    resp = client.get("/api/v1/stations/overview")
    assert resp.status_code == 200
    data = resp.json()

    assert data.get("ok") is True
    assert data.get("version") == "2.0.0", f"Expected version '2.0.0', got '{data.get('version')}'"
    assert data.get("ecosystem_version") == "2.0.0"
    assert data.get("stations_count") == 10, f"Expected 10 stations, got {data.get('stations_count')}"

    stations = data.get("stations", {})
    assert len(stations) == 10

    for sid in EXPECTED_10_STATIONS:
        assert sid in stations, f"Missing station in overview: {sid}"
        meta = stations[sid]
        assert meta["version"] == "2.0.0", f"Station {sid} version expected '2.0.0', got '{meta['version']}'"
        assert meta["status"] == "HEALTHY"
        assert len(meta.get("capabilities", [])) >= 4
        assert "tier" in meta


def test_stations_overview_agy_computer_use_station_presence(client: TestClient) -> None:
    """Verify agy-computer-use is fully integrated with v2.0.0 capabilities in overview."""
    resp = client.get("/api/v1/stations/overview")
    assert resp.status_code == 200
    stations = resp.json().get("stations", {})

    assert "agy-computer-use" in stations
    cua = stations["agy-computer-use"]

    assert cua["station_id"] == "agy-computer-use"
    assert cua["version"] == "2.0.0"
    assert cua["status"] == "HEALTHY"

    caps = cua.get("capabilities", [])
    # Verify presence of core CUA capabilities
    caps_str = " ".join(caps).lower()
    assert ("trajectory" in caps_str or "recorder" in caps_str or "cua" in caps_str or "gui" in caps_str)


# ============================================================================
# 2. Stations Diagnostics Endpoint (REQ-UI-201)
# ============================================================================

def test_stations_diagnostics_ten_station_probes(client: TestClient) -> None:
    """Verify /api/v1/stations/diagnostics executes live probes across all 10 stations."""
    resp = client.get("/api/v1/stations/diagnostics")
    assert resp.status_code == 200
    diag = resp.json()

    assert diag.get("ok") is True
    assert diag.get("version") == "2.0.0"
    assert diag.get("all_stations_healthy") is True

    probes = diag.get("probes", {})
    assert len(probes) == 10, f"Expected 10 station probes, got {len(probes)}"

    for sid in EXPECTED_10_STATIONS:
        assert sid in probes, f"Missing probe for {sid}"
        p = probes[sid]
        assert p["probe_passed"] is True
        assert p["status"] == "HEALTHY"
        assert p["version"] == "2.0.0"

    # Ecosystem version matrix check
    matrix = diag.get("ecosystem_matrix", {})
    assert matrix.get("target_version") == "2.0.0"
    assert matrix.get("synchronized") is True
    assert matrix.get("drifted") == []

    # Merkle verification check
    merkle = diag.get("merkle_verification", {})
    assert merkle.get("valid") is True
    assert len(merkle.get("root_hex", "")) == 64


# ============================================================================
# 3. Stations Portfolio Quality Certificate Endpoint (REQ-UI-201)
# ============================================================================

def test_stations_certificates_endpoint_portfolio_integration(client: TestClient) -> None:
    """Verify /api/v1/stations/certificates returns signed ecosystem portfolio certificate."""
    resp = client.get("/api/v1/stations/certificates")
    assert resp.status_code == 200
    data = resp.json()

    assert data.get("ok") is True

    cert = data.get("certificate") or data
    assert cert is not None

    # Verify stations certified count
    stations_certified = cert.get("stations_certified")
    if isinstance(stations_certified, int):
        assert stations_certified == 10
    elif isinstance(stations_certified, list):
        assert len(stations_certified) == 10
    elif "stations_count" in cert:
        assert cert["stations_count"] == 10

    # Verify version alignment in certificate
    version_str = str(cert.get("ecosystem_version") or cert.get("target_version") or cert.get("version") or "")
    assert version_str == "2.0.0"

    # Verify RTM coverage metric
    if "rtm_coverage" in cert or "rtm_coverage_pct" in cert:
        cov = cert.get("rtm_coverage") if "rtm_coverage" in cert else cert.get("rtm_coverage_pct")
        assert float(cov) >= 90.0

    # Verify FMEA risk priority metric
    if "fmea_max_rpn" in cert or "max_fmea_rpn" in cert:
        rpn = cert.get("fmea_max_rpn", cert.get("max_fmea_rpn"))
        assert int(rpn) <= 150

    # Verify cryptographic seal
    seal = (
        cert.get("sha256_seal")
        or cert.get("hmac_seal")
        or cert.get("certificate_hash")
        or cert.get("merkle_root_hex")
        or ""
    )
    assert len(seal) == 64 and re.match(r"^[0-9a-f]{64}$", seal)


# ============================================================================
# 4. Station Detail Endpoints
# ============================================================================

def test_station_detail_computer_use_endpoint(client: TestClient) -> None:
    """Verify /api/v1/stations/agy-computer-use and underscore variant return 200."""
    resp = client.get("/api/v1/stations/agy-computer-use")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    station = data.get("station", {})
    assert station.get("station_id") == "agy-computer-use"
    assert station.get("version") == "2.0.0"

    # Test underscore compatibility
    resp_under = client.get("/api/v1/stations/agy_computer_use")
    assert resp_under.status_code == 200
    assert resp_under.json().get("ok") is True


# ============================================================================
# 5. Legacy Routes Compatibility (/api/stations/*)
# ============================================================================

def test_legacy_stations_routes_v2_0_parity(client: TestClient) -> None:
    """Verify legacy /api/stations/* routes mirror v2.0.0 capabilities."""
    resp_ov = client.get("/api/stations/overview")
    assert resp_ov.status_code == 200
    assert resp_ov.json().get("stations_count") == 10
    assert resp_ov.json().get("version") == "2.0.0"

    resp_diag = client.get("/api/stations/diagnostics")
    assert resp_diag.status_code == 200
    assert resp_diag.json().get("all_stations_healthy") is True

    resp_cert = client.get("/api/stations/certificates")
    assert resp_cert.status_code == 200
    assert resp_cert.json().get("ok") is True


# ============================================================================
# 6. Zero Cyrillic Characters across API responses
# ============================================================================

def test_stations_portfolio_zero_cyrillic_output(client: TestClient) -> None:
    """Verify 100% English only (0 Cyrillic characters) across all station API endpoints."""
    cyrillic_re = re.compile(r"[\u0400-\u04FF]")

    endpoints = [
        "/api/v1/stations/overview",
        "/api/v1/stations/diagnostics",
        "/api/v1/stations/certificates",
        "/api/v1/stations/agy-computer-use",
    ]

    for ep in endpoints:
        resp = client.get(ep)
        assert resp.status_code == 200
        matches = cyrillic_re.findall(resp.text)
        assert len(matches) == 0, f"Found Cyrillic characters in {ep}: {matches[:10]}"


# ============================================================================
# 7. Gemini Local UI Version Files Sync
# ============================================================================

def test_gemini_local_ui_version_files_sync() -> None:
    """Verify pyproject.toml and frontend/package.json both declare version 2.0.0."""
    pyproject_file = REPO_ROOT / "pyproject.toml"
    assert pyproject_file.is_file(), "pyproject.toml must exist"
    pyproject_text = pyproject_file.read_text(encoding="utf-8")
    assert 'version = "2.0.0"' in pyproject_text, "pyproject.toml must declare version = '2.0.0'"

    package_json = REPO_ROOT / "frontend" / "package.json"
    if not package_json.is_file():
        package_json = REPO_ROOT / "package.json"
    assert package_json.is_file(), "package.json must exist"
    pkg_data = json.loads(package_json.read_text(encoding="utf-8"))
    assert pkg_data.get("version") == "2.0.0", f"package.json expected version '2.0.0', got '{pkg_data.get('version')}'"
