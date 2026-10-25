"""
tests/test_m29_station_stress.py
Milestone M29: Station Boundary, Invariant & Geometry Empirical Stress Suite.

Authored by m29_challenger_2 (EMPIRICAL CHALLENGER).
Verifies:
- Section 1: Secret Shield SARIF 2.1.0 Compliance & 12+ Signatures Stress
- Section 2: Windows DPAPI Envelope Rotation under Corrupted, Empty, and Tampered Payloads
- Section 3: PE Reversing Lab on Real Windows Binaries (notepad.exe, kernel32.dll)
- Section 4: ESKD Vision Station 300 DPI Geometry across Formats A4, A3, A2, A1, A0
- Section 5: 2D CCW Line Crossing Detection (Crossing, Parallel, Collinear, T-junction, Shared Endpoint)
- Section 6: Station API Endpoints & Boundary Facade Verification
"""

import base64
import hashlib
from pathlib import Path
from typing import Any, Dict, List

from fastapi.testclient import TestClient
import pytest

from backend.app.bridge.mission_control import MissionControlHub
from backend.app.main import app

client = TestClient(app)


# ============================================================================
# Section 1: Secret Shield SARIF 2.1.0 Compliance & 12+ Signatures Stress
# ============================================================================


def test_secret_shield_signature_catalog_completeness():
    """Verify that SECRET_SIGNATURE_CATALOG contains all 12 expected secret rules."""
    from agy_secret_shield.l1_primitives import SECRET_SIGNATURE_CATALOG

    expected_rules = {
        "google_cookie_1psid",
        "google_cookie_sapisid",
        "github_pat",
        "google_api_key",
        "openai_api_key",
        "private_key_pem",
        "personal_user_path",
        "cyrillic_marker",
        "high_entropy_token",
        "jwt_token",
        "pkcs8_private_key",
        "github_fine_grained_pat",
    }
    actual_rules = set(SECRET_SIGNATURE_CATALOG.keys())
    missing = expected_rules - actual_rules
    assert len(missing) == 0, f"Missing rules in SECRET_SIGNATURE_CATALOG: {missing}"
    assert len(actual_rules) >= 12


def test_secret_shield_sarif_210_schema_structure():
    """Verify OASIS SARIF 2.1.0 schema compliance on clean and detected audit results."""
    from agy_secret_shield.l3_pipeline import SecretShieldPipeline

    pipeline = SecretShieldPipeline()
    audit = pipeline.audit_target(custom_text="Clean safe code without secrets.")
    sarif = pipeline.export_sarif(audit)

    # OASIS SARIF 2.1.0 Top-level invariants
    assert sarif.get("version") == "2.1.0"
    assert sarif.get("$schema") == "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
    assert isinstance(sarif.get("runs"), list)
    assert len(sarif["runs"]) == 1

    run = sarif["runs"][0]
    tool = run.get("tool", {})
    driver = tool.get("driver", {})
    assert driver.get("name") == "agy-secret-shield"
    assert driver.get("version") == "1.1.0"

    rules = driver.get("rules", [])
    assert len(rules) >= 12
    rule_ids = {r["id"] for r in rules}
    assert "google_cookie_1psid" in rule_ids
    assert "github_pat" in rule_ids
    assert "jwt_token" in rule_ids

    # Every rule must declare id, name, shortDescription.text, and defaultConfiguration
    for rule in rules:
        assert "id" in rule and len(rule["id"]) > 0
        assert "name" in rule and len(rule["name"]) > 0
        assert "shortDescription" in rule and "text" in rule["shortDescription"]
        assert "defaultConfiguration" in rule and "level" in rule["defaultConfiguration"]
        assert rule["defaultConfiguration"]["level"] in ("error", "warning", "note")


def test_secret_shield_multi_signature_detection_sarif_mapping():
    """Test multi-signature detection and verify SARIF result items and locations."""
    from agy_secret_shield.l3_pipeline import SecretShieldPipeline

    # Construct test tokens dynamically to prevent false positives in repo audits
    c_marker = chr(0x0442) + chr(0x0435) + chr(0x0441) + chr(0x0442)
    leaked_payload = "\n".join([
        "SESSION_1PSID=__Secure-1PSID=fake_1psid_credential_value_here_1234567890",
        "SESSION_SAPISID=SAPISID=fake_sapisid_token_1234567890",
        "TOKEN_GH=ghp_111122223333444455556666777788889999",
        "TOKEN_GH_FG=github_pat_11AAAAAA0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_1234567890",
        "CLOUD_API=AIzaSyA1234567890abcdefghijklmnopqrstuv",
        "OPENAI_KEY=sk-1111222233334444555566667777888899990000",
        "JWT=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.do_not_leak_signature",
        "-----BEGIN PRIVATE KEY-----",
        "MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC",
        "-----END PRIVATE KEY-----",
        f"COMMENT=NonEnglish_{c_marker}",
    ])

    pipeline = SecretShieldPipeline()
    audit = pipeline.audit_target(custom_text=leaked_payload)
    assert audit["clean"] is False
    assert audit["finding_count"] >= 7
    assert audit["risk_score"] > 50

    sarif = pipeline.export_sarif(audit)
    results = sarif["runs"][0]["results"]
    assert len(results) >= 7

    detected_rules = {r["ruleId"] for r in results}
    assert "google_cookie_1psid" in detected_rules
    assert "github_pat" in detected_rules
    assert "github_fine_grained_pat" in detected_rules
    assert "google_api_key" in detected_rules
    assert "openai_api_key" in detected_rules
    assert "jwt_token" in detected_rules
    assert "pkcs8_private_key" in detected_rules
    assert "cyrillic_marker" in detected_rules

    # Verify each result has valid SARIF result structure
    for res in results:
        assert "ruleId" in res
        assert res["level"] in ("error", "warning", "note")
        assert "message" in res and "text" in res["message"]
        assert "locations" in res and len(res["locations"]) > 0
        loc = res["locations"][0]["physicalLocation"]
        assert "artifactLocation" in loc
        assert "region" in loc and loc["region"]["startLine"] >= 1


# ============================================================================
# Section 2: Windows DPAPI Envelope Rotation under Corrupted, Empty, Tampered
# ============================================================================


def test_dpapi_quarantine_valid_roundtrip_rotation():
    """Verify that batch_dpapi_seal, rotate_dpapi_quarantine, and unseal preserve 100% byte consistency."""
    from agy_secret_shield.l1_primitives import (
        batch_dpapi_seal,
        batch_dpapi_unseal,
        dpapi_seal_secret,
        dpapi_unseal_secret,
        rotate_dpapi_quarantine,
    )

    original_plaintexts = [
        "secret_token_alpha_12345",
        "session_cookie_beta_67890",
        "bearer_gamma_abcdef_!@#$%",
    ]
    envelopes = [dpapi_seal_secret(p, description=f"test-{idx}") for idx, p in enumerate(original_plaintexts)]

    # Verify initial unseal matches exactly
    for env, orig in zip(envelopes, original_plaintexts):
        assert dpapi_unseal_secret(env) == orig

    # Rotate envelopes
    rotated_envelopes = rotate_dpapi_quarantine(envelopes)
    assert len(rotated_envelopes) == len(original_plaintexts)

    # Verify recovered plaintexts after rotation are 100% identical
    recovered = batch_dpapi_unseal(rotated_envelopes)
    assert recovered == original_plaintexts


def test_dpapi_quarantine_corrupted_base64_raises_value_error():
    """Verify that rotate_dpapi_quarantine strictly raises ValueError on corrupted base64 payloads."""
    from agy_secret_shield.l1_primitives import rotate_dpapi_quarantine

    corrupted_envelopes = [
        {"ciphertext_b64": "!!!not_valid_base64$$$", "engine": "windows_dpapi"},
    ]
    with pytest.raises(ValueError, match="Invalid base64 in ciphertext_b64"):
        rotate_dpapi_quarantine(corrupted_envelopes)


def test_dpapi_quarantine_empty_payload_raises_value_error():
    """Verify that rotate_dpapi_quarantine strictly raises ValueError on empty or missing ciphertext."""
    from agy_secret_shield.l1_primitives import rotate_dpapi_quarantine

    with pytest.raises(ValueError, match="must be a non-empty string"):
        rotate_dpapi_quarantine([{"ciphertext_b64": ""}])

    with pytest.raises(ValueError, match="must be a non-empty string"):
        rotate_dpapi_quarantine([{"ciphertext_b64": "   "}])


def test_dpapi_quarantine_tampered_ciphertext_raises_value_error():
    """Verify that rotate_dpapi_quarantine strictly detects tampering and raises ValueError."""
    from agy_secret_shield.l1_primitives import dpapi_seal_secret, rotate_dpapi_quarantine

    valid_env = dpapi_seal_secret("confidential_gemini_session_state", description="test-tamper")
    raw_cipher = bytearray(base64.b64decode(valid_env["ciphertext_b64"]))

    # Tamper with bytes across payload / HMAC region (offset 100)
    assert len(raw_cipher) > 100
    raw_cipher[100] ^= 0xAA

    tampered_env = {
        "ciphertext_b64": base64.b64encode(bytes(raw_cipher)).decode("ascii"),
        "engine": "windows_dpapi",
    }

    with pytest.raises(ValueError, match="CryptUnprotectData failed or ciphertext corrupted"):
        rotate_dpapi_quarantine([tampered_env])


# ============================================================================
# Section 3: PE Reversing Lab on Real Windows Binaries (notepad.exe, kernel32)
# ============================================================================


def test_pe_lab_live_notepad_rich_header_and_xor_key():
    """Empirically test Rich Header parsing, XOR key derivation, and records on live notepad.exe."""
    notepad_path = Path(r"C:\Windows\System32\notepad.exe")
    if not notepad_path.is_file():
        pytest.skip("C:\\Windows\\System32\\notepad.exe not accessible")

    raw_bytes = notepad_path.read_bytes()
    assert len(raw_bytes) > 50000

    from pe_reversing_lab.l1_primitives import parse_pe64_binary, parse_rich_header

    parsed = parse_pe64_binary(raw_bytes)
    assert parsed["format"] == "PE32+"
    assert parsed["architecture"] == "x86_64"
    assert parsed["machine"] in ("0x8664", 0x8664)

    # Derive e_lfanew from DOS header at offset 0x3C
    e_lfanew = int.from_bytes(raw_bytes[0x3C:0x40], byteorder="little")
    assert e_lfanew >= 0x40

    rich = parse_rich_header(raw_bytes, e_lfanew)
    assert rich["present"] is True
    assert rich["xor_key"].startswith("0x")
    assert rich["dans_offset"] > 0
    assert rich["rich_offset"] > rich["dans_offset"]
    assert rich["record_count"] > 0
    assert len(rich["records"]) == rich["record_count"]
    assert len(rich["rich_hash"]) == 32  # 32-char MD5 rich hash


def test_pe_lab_live_notepad_tls_and_rwx_anomaly_detection():
    """Empirically test TLS callback parsing and verify notepad.exe has 0 RWX anomalies."""
    notepad_path = Path(r"C:\Windows\System32\notepad.exe")
    if not notepad_path.is_file():
        pytest.skip("C:\\Windows\\System32\\notepad.exe not accessible")

    from pe_reversing_lab.l3_pipeline import StaticTriagePipeline

    pipeline = StaticTriagePipeline()
    res = pipeline.triage_sample(profile="clean", custom_bytes=notepad_path.read_bytes())
    assert res["ok"] is True
    assert res["format"] == "PE32+"

    # Clean Windows system binary must have 0 RWX sections
    rwx = res["rwx_anomaly"]
    assert rwx["has_rwx"] is False
    assert rwx["rwx_count"] == 0

    # TLS directory structure is parsed
    tls = res["tls"]
    assert isinstance(tls, dict)
    assert "present" in tls
    assert "callback_count" in tls


def test_pe_lab_live_kernel32_eat_symbol_extraction():
    """Empirically extract and verify EAT symbols from live C:\\Windows\\System32\\kernel32.dll."""
    k32_path = Path(r"C:\Windows\System32\kernel32.dll")
    if not k32_path.is_file():
        pytest.skip("C:\\Windows\\System32\\kernel32.dll not accessible")

    from pe_reversing_lab.l3_pipeline import StaticTriagePipeline

    pipeline = StaticTriagePipeline()
    res = pipeline.triage_sample(profile="clean", custom_bytes=k32_path.read_bytes())
    assert res["ok"] is True
    assert res["format"] == "PE32+"

    exports = res["exports"]
    assert exports.get("present") is True
    func_count = exports.get("function_count", 0)
    assert func_count > 1000, f"Expected >1000 exported functions in kernel32.dll, got {func_count}"

    # Verify standard Windows API exports are extracted in exports["exports"]
    exported_items = exports.get("exports", [])
    assert len(exported_items) > 1000
    func_names = {f["name"] for f in exported_items if "name" in f}
    for api_name in ("CreateFileW", "VirtualAlloc", "ExitProcess", "GetProcAddress", "LoadLibraryW"):
        assert api_name in func_names, f"Expected {api_name} in kernel32.dll EAT exports"


def test_pe_lab_yara_rule_synthesis_and_syntax():
    """Verify that synthesized YARA rules have valid structure, meta, strings, and conditions."""
    from pe_reversing_lab.l1_primitives import generate_yara_rule

    sample_sha = "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899"
    yara_rule = generate_yara_rule(
        rule_name="pe64_test_signature",
        sha256=sample_sha,
        suspicious_apis=["IsDebuggerPresent", "VirtualProtect", "CreateRemoteThread"],
        packed=True,
        score=75,
        rich_hash="11223344556677889900aabbccddeeff",
        has_tls_callbacks=True,
        has_overlay=True,
        has_rwx=True,
    )
    assert "rule pe64_test_signature {" in yara_rule
    assert 'description = "Auto-generated PE32+ x86_64 signature' in yara_rule
    assert f'sha256 = "{sample_sha}"' in yara_rule
    assert "packed = true" in yara_rule
    assert "score = 75" in yara_rule
    assert 'rich_hash = "11223344556677889900aabbccddeeff"' in yara_rule
    assert '$api_0 = "IsDebuggerPresent"' in yara_rule
    assert "uint16(0) == 0x5A4D" in yara_rule


# ============================================================================
# Section 4: ESKD Vision Station 300 DPI Geometry across A4, A3, A2, A1, A0
# ============================================================================


def test_eskd_300dpi_calculations_across_all_gost_formats():
    """Verify raster pixel dimensions and zoom factors across GOST formats A4..A0 at 300 DPI."""
    from eskd_vision_station.l1_primitives import GOST_SHEET_FORMATS_MM, compute_raster_matrix_300dpi

    expected_specs = {
        "A4": (210.0, 297.0, 2480, 3508),
        "A3": (420.0, 297.0, 4961, 3508),
        "A2": (594.0, 420.0, 7016, 4961),
        "A1": (841.0, 594.0, 9933, 7016),
        "A0": (1189.0, 841.0, 14043, 9933),
    }

    for fmt, (exp_w_mm, exp_h_mm, exp_w_px, exp_h_px) in expected_specs.items():
        assert fmt in GOST_SHEET_FORMATS_MM
        actual_w_mm, actual_h_mm = GOST_SHEET_FORMATS_MM[fmt]
        assert actual_w_mm == exp_w_mm
        assert actual_h_mm == exp_h_mm

        matrix = compute_raster_matrix_300dpi(fmt)
        assert matrix["sheet_format"] == fmt
        assert matrix.get("dpi") == 300
        assert abs(matrix["zoom_factor"] - (300.0 / 72.0)) < 1e-4

        # Mathematical verification: round(mm * 300 / 25.4)
        assert matrix["width_px"] == exp_w_px
        assert matrix["height_px"] == exp_h_px


def test_eskd_gost_2301_margins_300dpi_and_title_block_all_formats():
    """Verify GOST 2.301 frame margins and GOST 2.104 title block across A4..A0."""
    hub = MissionControlHub()
    for fmt in ("A4", "A3", "A2", "A1", "A0"):
        res = hub.run_eskd_inspect({
            "sheet_format": fmt,
            "target_dpi": 300.0,
            "tolerance_mm": 1.0,
        })
        assert res["ok"] is True
        assert res["sheet_format"] == fmt

        tb = res["title_block_verification"]
        assert tb["valid"] is True
        assert tb["expected_mm"]["width"] == 185.0
        assert tb["expected_mm"]["height"] == 55.0

        fr = res["frame_verification"]
        assert fr["valid"] is True
        assert fr["margins_mm"]["left"] == 20.0
        assert fr["margins_mm"]["top"] == 5.0
        assert fr["margins_mm"]["right"] == 5.0
        assert fr["margins_mm"]["bottom"] == 5.0
        assert fr["margins_px_300dpi"]["left"] == 236
        assert fr["margins_px_300dpi"]["top"] == 59


# ============================================================================
# Section 5: 2D CCW Line Crossing Detection Invariant Configurations
# ============================================================================


def test_eskd_line_crossing_orthogonal_and_diagonal_crossing():
    """Verify that orthogonal and diagonal true segment crossings are accurately detected."""
    from eskd_vision_station.l1_primitives import detect_line_crossings

    # Orthogonal crossing at (50, 50)
    ortho_segments = [
        {"id": "dim_horiz", "type": "dimension_line", "p1": [10.0, 50.0], "p2": [90.0, 50.0]},
        {"id": "ext_vert", "type": "extension_line", "p1": [50.0, 10.0], "p2": [50.0, 90.0]},
    ]
    cross_ortho = detect_line_crossings(ortho_segments)
    assert len(cross_ortho) == 1
    assert cross_ortho[0]["intersection"] == (50.0, 50.0)
    assert cross_ortho[0]["segment_1"] == "dim_horiz"
    assert cross_ortho[0]["segment_2"] == "ext_vert"

    # Diagonal crossing: (10, 10)-(90, 90) and (10, 90)-(90, 10) at (50, 50)
    diag_segments = [
        {"id": "diag_1", "type": "dimension_line", "p1": [10.0, 10.0], "p2": [90.0, 90.0]},
        {"id": "diag_2", "type": "extension_line", "p1": [10.0, 90.0], "p2": [90.0, 10.0]},
    ]
    cross_diag = detect_line_crossings(diag_segments)
    assert len(cross_diag) == 1
    assert cross_diag[0]["intersection"] == (50.0, 50.0)


def test_eskd_line_crossing_parallel_segments_zero_violations():
    """Verify that parallel horizontal, vertical, and diagonal segments produce 0 crossings."""
    from eskd_vision_station.l1_primitives import detect_line_crossings

    parallel_segments = [
        {"id": "h1", "type": "dimension_line", "p1": [0.0, 20.0], "p2": [100.0, 20.0]},
        {"id": "h2", "type": "dimension_line", "p1": [0.0, 40.0], "p2": [100.0, 40.0]},
        {"id": "v1", "type": "extension_line", "p1": [20.0, 0.0], "p2": [20.0, 100.0]},
        {"id": "v2", "type": "extension_line", "p1": [40.0, 0.0], "p2": [40.0, 100.0]},
    ]
    # Test pairs
    assert len(detect_line_crossings([parallel_segments[0], parallel_segments[1]])) == 0
    assert len(detect_line_crossings([parallel_segments[2], parallel_segments[3]])) == 0


def test_eskd_line_crossing_collinear_and_shared_endpoint():
    """Verify that collinear disjoint segments and segments sharing an endpoint produce 0 crossings."""
    from eskd_vision_station.l1_primitives import detect_line_crossings

    # Collinear disjoint segments along y = 30
    collinear_disjoint = [
        {"id": "cd1", "type": "dimension_line", "p1": [0.0, 30.0], "p2": [40.0, 30.0]},
        {"id": "cd2", "type": "dimension_line", "p1": [60.0, 30.0], "p2": [100.0, 30.0]},
    ]
    assert len(detect_line_crossings(collinear_disjoint)) == 0

    # Segments sharing an endpoint (corner vertex at 50, 50)
    shared_endpoint = [
        {"id": "se1", "type": "dimension_line", "p1": [10.0, 50.0], "p2": [50.0, 50.0]},
        {"id": "se2", "type": "extension_line", "p1": [50.0, 50.0], "p2": [50.0, 90.0]},
    ]
    assert len(detect_line_crossings(shared_endpoint)) == 0


def test_eskd_line_crossing_t_junction_configuration():
    """Verify that T-junctions (extension line touching boundary of dimension line) are not flagged as crossing."""
    from eskd_vision_station.l1_primitives import detect_line_crossings

    # T-junction: segment 1 is (10, 50)-(90, 50); segment 2 starts exactly on segment 1 at (50, 50)
    t_junction = [
        {"id": "dim_bar", "type": "dimension_line", "p1": [10.0, 50.0], "p2": [90.0, 50.0]},
        {"id": "ext_stem", "type": "extension_line", "p1": [50.0, 50.0], "p2": [50.0, 100.0]},
    ]
    crossings = detect_line_crossings(t_junction)
    assert len(crossings) == 0, f"T-junction should not be flagged as prohibited line crossing: {crossings}"


# ============================================================================
# Section 6: Station API Endpoints & Multi-Sheet Batch Inspection
# ============================================================================


def test_station_api_eskd_multisheet_batch_inspection():
    """Verify multi-sheet batch inspection across mixed formats via REST endpoint."""
    sheets = [
        {"sheet_format": "A4", "stamp_type": "stamp_form1"},
        {"sheet_format": "A3", "stamp_type": "stamp_form1"},
        {"sheet_format": "A2", "stamp_type": "stamp_form1"},
    ]
    res = client.post("/api/mission-control/stations/eskd/inspect", json={"sheets": sheets})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "batch_result" in data
    assert data["batch_result"]["total_sheets"] == 3
    assert "batch_compliant" in data["batch_result"]
    assert len(data["batch_result"]["sheets"]) == 3


def test_station_api_pe_lab_live_binary_endpoints():
    """Verify /api/mission-control/stations/pe-lab/analyze REST endpoint against live binaries."""
    notepad_path = r"C:\Windows\System32\notepad.exe"
    if not Path(notepad_path).is_file():
        pytest.skip("notepad.exe not accessible")

    res = client.post("/api/mission-control/stations/pe-lab/analyze", json={"target_path": notepad_path})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["format"] == "PE32+"
    assert data["size"] > 50000
    assert "rich_header" in data
    assert data["rich_header"]["present"] is True
    assert "yara_rule" in data
