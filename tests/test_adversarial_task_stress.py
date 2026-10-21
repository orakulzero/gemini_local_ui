"""
tests/test_adversarial_task_stress.py
Empirical Adversarial Stress Suite for gemini_task.py & Context Packaging.
Executed by m7_stress_challenger_1.

Adversarially tests:
1. Path Traversal Edge Cases:
   - ../, ..\\, ....//, ..%2f, null-bytes (\\0), sibling directories, absolute paths (C:\\Windows, /etc),
     drive roots (C:, D:), ADS/colon syntax (file:stream), MAX_PATH / deeply nested paths (50+ levels),
     empty/self path traversal, Windows reserved device names, Unicode / RTL overrides.
2. Context Packaging & Archive Stress:
   - Packaging empty directories, deeply nested paths (50+ levels).
   - Packaging >20MB context (memory, integrity, non-crash).
   - Corrupted zip archives (truncated header, bad CRC, 0-byte zip, zip bomb structure).
   - create_output_zip archive integrity verification.
3. Daemon File Upload & Network Resilience:
   - Uploading 0-byte archive, truncated header, bad CRC, zip bomb, and >20MB context to /api/chat/upload.
"""

from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import time
import unittest
import zipfile

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

GEMINI_TASK_DIR = os.path.join(PROJECT_ROOT, ".agents", "skills", "gemini-task", "scripts")
if os.path.exists(GEMINI_TASK_DIR) and GEMINI_TASK_DIR not in sys.path:
    sys.path.insert(0, GEMINI_TASK_DIR)

import gemini_task


class TestPathTraversalAdversarial(unittest.TestCase):
    """Adversarial stress-testing of extract_files_from_response path sanitization."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adv_extract_test_")
        self.extract_dir = os.path.join(self.temp_dir, "safe_target")
        self.sibling_dir = os.path.join(self.temp_dir, "safe_target_sibling")
        self.parent_dir = self.temp_dir
        os.makedirs(self.extract_dir, exist_ok=True)
        os.makedirs(self.sibling_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_adv_01_classic_parent_traversals(self):
        """Tests standard ../ parent directory traversal vectors with various depths."""
        vectors = [
            "../escape1.py",
            "../../escape2.py",
            "../../../escape3.py",
            "../../../../../../../../../../escape4.py",
            "./../escape5.py",
            "subdir/../../escape6.py",
            "subdir/nested/../../../escape7.py",
        ]
        for v in vectors:
            text = f"### File: {v}\n```python\n# evil\n```\n"
            extracted = gemini_task.extract_files_from_response(text, self.extract_dir)
            self.assertEqual(len(extracted), 0, f"Failed blocking traversal vector: {v}")
            basename = os.path.basename(v)
            self.assertFalse(os.path.exists(os.path.join(self.parent_dir, basename)), f"File escaped to parent: {basename}")

    def test_adv_02_windows_backslash_traversals(self):
        """Tests Windows backslash traversal vectors (..\\)."""
        vectors = [
            r"..\win_escape1.py",
            r"..\..\win_escape2.py",
            r"subdir\..\..\win_escape3.py",
            r"..\..\..\..\..\win_escape4.py",
        ]
        for v in vectors:
            text = f"### File: {v}\n```python\n# evil\n```\n"
            extracted = gemini_task.extract_files_from_response(text, self.extract_dir)
            self.assertEqual(len(extracted), 0, f"Failed blocking Windows traversal vector: {v}")
            basename = os.path.basename(v)
            self.assertFalse(os.path.exists(os.path.join(self.parent_dir, basename)), f"File escaped: {basename}")

    def test_adv_03_redundant_dot_and_slash_traversals(self):
        """Tests ....//, .../.../, and mixed dot-slash redundant traversal tricks."""
        vectors = [
            "....//escape_dot1.py",
            "..../....//escape_dot2.py",
            "....\\....\\escape_dot3.py",
            ".../.../escape_dot4.py",
            ".//..//escape_dot5.py",
            ".\\..\\escape_dot6.py",
        ]
        for v in vectors:
            text = f"### File: {v}\n```python\n# evil\n```\n"
            extracted = gemini_task.extract_files_from_response(text, self.extract_dir)
            for item in extracted:
                dest = Path(item["abs_path"]).resolve()
                target = Path(self.extract_dir).resolve()
                self.assertTrue(dest.is_relative_to(target) and dest != target, f"File written outside target: {dest}")

    def test_adv_04_url_encoded_and_percent_traversals(self):
        """Tests URL-encoded traversal patterns (%2e%2e%2f, ..%2f, ..%5c)."""
        vectors = [
            "..%2furl_escape1.py",
            "..%5curl_escape2.py",
            "%2e%2e%2furl_escape3.py",
            "%2e%2e/url_escape4.py",
        ]
        for v in vectors:
            text = f"### File: {v}\n```python\n# evil\n```\n"
            extracted = gemini_task.extract_files_from_response(text, self.extract_dir)
            for item in extracted:
                dest = Path(item["abs_path"]).resolve()
                target = Path(self.extract_dir).resolve()
                self.assertTrue(dest.is_relative_to(target) and dest != target, f"Escaped: {dest}")
            self.assertFalse(os.path.exists(os.path.join(self.parent_dir, "url_escape1.py")))

    def test_adv_05_sibling_directory_prefix_bypass(self):
        """Exhaustively tests sibling directory naming tricks designed to defeat startswith."""
        sibling_names = [
            "safe_target_sibling",
            "safe_target2",
            "safe_target.bak",
            "safe_target-backup",
            "safe_target_extra",
        ]
        for sname in sibling_names:
            s_path = os.path.join(self.parent_dir, sname)
            os.makedirs(s_path, exist_ok=True)
            v = f"../{sname}/pwn_sibling.py"
            text = f"### File: {v}\n```python\n# sibling attack\n```\n"
            extracted = gemini_task.extract_files_from_response(text, self.extract_dir)
            self.assertEqual(len(extracted), 0, f"Sibling directory bypass succeeded for: {sname}")
            self.assertFalse(os.path.exists(os.path.join(s_path, "pwn_sibling.py")), f"Backdoor written to sibling: {s_path}")

    def test_adv_06_absolute_paths_posix_and_windows(self):
        """Tests absolute paths (/etc/passwd, C:\\Windows, D:\\)."""
        vectors = [
            "/etc/passwd.txt",
            "/root/.bashrc.txt",
            r"C:\Windows\System32\drivers\etc\evil.sys",
            r"C:\Program Files\evil.exe",
            "C:/evil_drive_root.txt",
            "D:/another_drive.txt",
        ]
        for v in vectors:
            text = f"### File: {v}\n```python\n# absolute\n```\n"
            extracted = gemini_task.extract_files_from_response(text, self.extract_dir)
            for item in extracted:
                dest = Path(item["abs_path"]).resolve()
                target = Path(self.extract_dir).resolve()
                self.assertTrue(dest.is_relative_to(target) and dest != target, f"Absolute path escaped: {dest}")

    def test_adv_07_drive_relative_and_colon_syntax(self):
        """Tests drive-relative paths (C:file.py) and colon stream syntax (file.txt:stream)."""
        vectors = [
            "C:drive_rel.py",
            "D:drive_rel2.py",
            "normal.txt:stream",
            "normal.txt::$DATA",
        ]
        for v in vectors:
            text = f"### File: {v}\n```python\n# colon\n```\n"
            extracted = gemini_task.extract_files_from_response(text, self.extract_dir)
            for item in extracted:
                dest = Path(item["abs_path"]).resolve()
                target = Path(self.extract_dir).resolve()
                self.assertTrue(dest.is_relative_to(target) and dest != target, f"Escaped via colon/drive-rel: {dest}")

    def test_adv_08_null_byte_injection(self):
        """Tests null byte injections (\\0, \\x00) within path specifications."""
        vectors = [
            "legit.py\0../evil.py",
            "subdir/\x00evil.py",
            "test.py\0.txt",
        ]
        for v in vectors:
            text = f"### File: {v}\n```python\n# null byte\n```\n"
            try:
                extracted = gemini_task.extract_files_from_response(text, self.extract_dir)
                for item in extracted:
                    dest = Path(item["abs_path"]).resolve()
                    target = Path(self.extract_dir).resolve()
                    self.assertTrue(dest.is_relative_to(target) and dest != target)
            except Exception as e:
                self.fail(f"Unhandled exception on null-byte input: {e}")

    def test_adv_09_deeply_nested_paths_50_levels(self):
        """Tests deeply nested directory hierarchies (50+ directory levels)."""
        parts = [f"level_{i:02d}" for i in range(50)]
        nested_rel = "/".join(parts) + "/deep_leaf.py"
        text = f"### File: {nested_rel}\n```python\nLEAF_DATA = True\n```\n"

        extracted = gemini_task.extract_files_from_response(text, self.extract_dir)
        for item in extracted:
            dest = Path(item["abs_path"]).resolve()
            target = Path(self.extract_dir).resolve()
            self.assertTrue(dest.is_relative_to(target) and dest != target)

    def test_adv_10_target_directory_self_reference(self):
        """Tests paths that resolve to the target directory itself (e.g. '.', './')."""
        text = "### File: .\n```text\nself directory overwrite\n```\n"
        extracted = gemini_task.extract_files_from_response(text, self.extract_dir)
        self.assertEqual(len(extracted), 0)

    def test_adv_11_windows_reserved_device_names(self):
        """Tests Windows reserved device names (CON, PRN, AUX, NUL, COM1, LPT1)."""
        device_names = ["CON.txt", "PRN.txt", "AUX.py", "NUL.dat", "COM1.json", "LPT1.md"]
        for dev in device_names:
            text = f"### File: {dev}\n```text\ndevice test\n```\n"
            try:
                extracted = gemini_task.extract_files_from_response(text, self.extract_dir)
                for item in extracted:
                    dest = Path(item["abs_path"]).resolve()
                    target = Path(self.extract_dir).resolve()
                    self.assertTrue(dest.is_relative_to(target) and dest != target)
            except Exception as e:
                self.fail(f"Unhandled exception on reserved device name {dev}: {e}")

    def test_adv_12_unicode_and_rtl_override_injection(self):
        """Tests Right-to-Left Override (\\u202e) and fullwidth dot character injection."""
        vectors = [
            "test\u202etxt.py",
            "\uff0e\uff0e/evil_fullwidth.py",
            "dir/\u202eevil.py",
        ]
        for v in vectors:
            text = f"### File: {v}\n```python\n# rtl\n```\n"
            try:
                extracted = gemini_task.extract_files_from_response(text, self.extract_dir)
                for item in extracted:
                    dest = Path(item["abs_path"]).resolve()
                    target = Path(self.extract_dir).resolve()
                    self.assertTrue(dest.is_relative_to(target) and dest != target)
            except Exception as e:
                self.fail(f"Unhandled exception on Unicode injection {v}: {e}")

    def test_adv_13_mixed_slash_and_double_encoding(self):
        """Tests mixed slashes with encoded and dot sequences."""
        vectors = [
            r"safe_sub/..\..\..\root_escape.py",
            r"safe_sub\../../root_escape2.py",
            "safe_sub/../safe_sub/../../root_escape3.py",
            "%252e%252e%252fevil.py",
        ]
        for v in vectors:
            text = f"### File: {v}\n```python\n# mixed\n```\n"
            extracted = gemini_task.extract_files_from_response(text, self.extract_dir)
            self.assertEqual(len(extracted), 0, f"Mixed traversal escaped: {v}")
            self.assertFalse(os.path.exists(os.path.join(self.parent_dir, "root_escape.py")))


class TestContextPackagingAdversarial(unittest.TestCase):
    """Adversarial stress-testing of pack_context_path and archive handling."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adv_pack_test_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_adv_14_pack_empty_directory(self):
        """Verifies packaging an empty directory returns valid zip with 0 files."""
        empty_dir = os.path.join(self.temp_dir, "empty_proj")
        os.makedirs(empty_dir, exist_ok=True)

        zip_bytes, zip_name, count = gemini_task.pack_context_path(empty_dir)
        self.assertEqual(count, 0)
        self.assertTrue(zip_name.endswith(".zip"))
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(zip_bytes)))
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            self.assertEqual(len(zf.namelist()), 0)
            self.assertIsNone(zf.testzip())

    def test_adv_15_pack_deeply_nested_structure(self):
        """Verifies packaging a 50-level deep directory tree traverses correctly."""
        base_dir = os.path.join(self.temp_dir, "deep_proj")
        curr = base_dir
        for i in range(50):
            curr = os.path.join(curr, f"d_{i:02d}")
        os.makedirs(curr, exist_ok=True)
        with open(os.path.join(curr, "deep.txt"), "w") as f:
            f.write("deep file content")

        zip_bytes, zip_name, count = gemini_task.pack_context_path(base_dir)
        self.assertEqual(count, 1)
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            self.assertIsNone(zf.testzip())
            names = zf.namelist()
            self.assertEqual(len(names), 1)
            self.assertTrue(names[0].endswith("deep.txt"))

    def test_adv_16_pack_large_context_exceeding_20mb(self):
        """Tests packaging a context exceeding 20MB of text data without memory failure."""
        large_dir = os.path.join(self.temp_dir, "large_proj")
        os.makedirs(large_dir, exist_ok=True)

        # Generate 5 files of 5MB each (25MB total uncompressed)
        chunk_size = 1024 * 1024  # 1MB
        sample_chunk = ("# Large data chunk line %06d\n" * 30000).encode("utf-8")[:chunk_size]
        for i in range(5):
            fname = os.path.join(large_dir, f"bigfile_{i}.txt")
            with open(fname, "wb") as f:
                for _ in range(5):
                    f.write(sample_chunk)

        total_uncompressed = sum(os.path.getsize(os.path.join(large_dir, f"bigfile_{i}.txt")) for i in range(5))
        self.assertGreater(total_uncompressed, 20 * 1024 * 1024)

        zip_bytes, zip_name, count = gemini_task.pack_context_path(large_dir)
        self.assertEqual(count, 5)
        self.assertGreater(len(zip_bytes), 0)
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(zip_bytes)))

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            self.assertIsNone(zf.testzip())
            self.assertEqual(len(zf.namelist()), 5)

    def test_adv_17_corrupted_zip_file_passthrough(self):
        """Tests passing corrupted zip files directly to pack_context_path."""
        # Case A: Truncated header
        corrupt_a = os.path.join(self.temp_dir, "truncated.zip")
        with open(corrupt_a, "wb") as f:
            f.write(b"PK\x03\x04truncated_corrupt_data")

        data_a, name_a, count_a = gemini_task.pack_context_path(corrupt_a)
        self.assertEqual(data_a, b"PK\x03\x04truncated_corrupt_data")
        self.assertEqual(name_a, "truncated.zip")
        self.assertEqual(count_a, 1)

        # Case B: Zero-byte zip
        corrupt_b = os.path.join(self.temp_dir, "zero_byte.zip")
        with open(corrupt_b, "wb") as f:
            pass  # 0 bytes

        data_b, name_b, count_b = gemini_task.pack_context_path(corrupt_b)
        self.assertEqual(data_b, b"")
        self.assertEqual(count_b, 1)

    def test_adv_18_bad_crc32_archive_handling(self):
        """Creates a zip archive with intentionally corrupt CRC32 and verifies testzip detection."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("test.py", "print('hello world')")

        raw_zip = bytearray(buf.getvalue())
        idx = raw_zip.find(b"print('hello world')")
        if idx != -1:
            raw_zip[idx] = ord("X")
        else:
            raw_zip[30] = (raw_zip[30] + 1) % 256

        corrupt_path = os.path.join(self.temp_dir, "bad_crc.zip")
        with open(corrupt_path, "wb") as f:
            f.write(raw_zip)

        data, name, count = gemini_task.pack_context_path(corrupt_path)
        self.assertEqual(len(data), len(raw_zip))
        try:
            with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
                bad_file = zf.testzip()
                self.assertIsNotNone(bad_file, "Corrupted archive did not trigger CRC mismatch in testzip")
        except zipfile.BadZipFile:
            pass

    def test_adv_19_zip_bomb_structure_integrity(self):
        """Constructs a zip containing a highly compressible 20MB stream of null bytes."""
        bomb_dir = os.path.join(self.temp_dir, "bomb_dir")
        os.makedirs(bomb_dir, exist_ok=True)
        with open(os.path.join(bomb_dir, "zeros.dat"), "wb") as f:
            f.write(b"\x00" * (20 * 1024 * 1024))

        zip_bytes, zip_name, count = gemini_task.pack_context_path(bomb_dir)
        self.assertEqual(count, 1)
        self.assertLess(len(zip_bytes), 100 * 1024)
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            self.assertIsNone(zf.testzip())

    def test_adv_20_create_output_zip_archive_integrity(self):
        """Tests that create_output_zip generates 100% valid zip files with valid CRC32."""
        f1 = os.path.join(self.temp_dir, "out1.txt")
        f2 = os.path.join(self.temp_dir, "out2.py")
        with open(f1, "w") as f:
            f.write("data 1")
        with open(f2, "w") as f:
            f.write("def foo(): return 42")

        files = [
            {"path": "docs/out1.txt", "abs_path": f1, "size": 6},
            {"path": "src/out2.py", "abs_path": f2, "size": 20},
        ]
        out_zip = os.path.join(self.temp_dir, "bundled_out.zip")
        gemini_task.create_output_zip(files, out_zip)

        self.assertTrue(os.path.exists(out_zip))
        self.assertTrue(zipfile.is_zipfile(out_zip))
        with zipfile.ZipFile(out_zip, "r") as zf:
            self.assertIsNone(zf.testzip())
            self.assertEqual(set(zf.namelist()), {"docs/out1.txt", "src/out2.py"})
            self.assertEqual(zf.read("docs/out1.txt").decode(), "data 1")
            self.assertEqual(zf.read("src/out2.py").decode(), "def foo(): return 42")


class TestDaemonUploadAdversarialResilience(unittest.TestCase):
    """Adversarial stress-testing of daemon file upload endpoint with corrupted archives."""

    @classmethod
    def setUpClass(cls):
        from backend.app.main import app
        import backend.app.main as main_mod
        from backend.app.mock.mock_server import MockGeminiServer
        from backend.app.bridge.session import SessionManager, SessionContext, AccountSession
        from backend.app.storage.database import Database
        from backend.app.storage.repository import ConversationRepository
        from backend.app.storage.vault import CredentialVault, AccountProfile
        from starlette.testclient import TestClient

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            cls.mock_port = s.getsockname()[1]

        cls.mock_server = MockGeminiServer(host="127.0.0.1", port=cls.mock_port)
        cls.mock_server.start_background()
        for _ in range(50):
            if cls.mock_server._site is not None:
                break
            time.sleep(0.05)

        cls.temp_dir = tempfile.mkdtemp(prefix="adv_daemon_test_")
        cls.db_path = Path(cls.temp_dir) / "test_adv.db"
        cls.db = Database(cls.db_path)
        schema_path = Path(PROJECT_ROOT) / "backend" / "app" / "storage" / "schema.sql"
        asyncio.run(cls.db.init_schema(schema_path))
        cls.repo = ConversationRepository(cls.db)

        vault_path = Path(cls.temp_dir) / "vault.json"
        vault = CredentialVault(vault_path=vault_path)
        vdata = vault.load_vault_data()
        vdata.active_account_id = "acc_ultra_adv"
        mock_cookies = {"__Secure-1PSID": "mock_psid_adv", "__Secure-1PSIDTS": "mock_psidts_adv"}
        vdata.accounts["acc_ultra_adv"] = AccountProfile(
            id="acc_ultra_adv", name="Adv Account", tier="ultra", cookies=mock_cookies, snlm0e="snlm0e_adv"
        )
        vault.store_vault_data(vdata)

        cls.sm = SessionManager(base_url=f"http://127.0.0.1:{cls.mock_port}", vault=vault)
        cls.sm.active_account_id = "acc_ultra_adv"
        ctx = SessionContext()
        ctx.cookies = mock_cookies
        ctx.access_token = "snlm0e_adv"
        ctx.account_tier = "ultra"
        sess = AccountSession(account_id="acc_ultra_adv", profile=vdata.accounts["acc_ultra_adv"], context=ctx)
        cls.sm.sessions["acc_ultra_adv"] = sess
        cls.sm.sessions["default"] = sess

        app.state.db = cls.db
        app.state.repository = cls.repo
        app.state.session_manager = cls.sm
        app.state.vault = vault
        main_mod.db = cls.db
        main_mod.repository = cls.repo
        main_mod.session_manager = cls.sm
        main_mod.vault = vault

        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.mock_server.stop_background()
        except Exception:
            pass
        try:
            asyncio.run(cls.db.close())
        except Exception:
            pass
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_adv_21_upload_zero_byte_archive(self):
        """Verifies uploading a 0-byte file succeeds gracefully without daemon 500 error."""
        files = {"file": ("empty.zip", b"", "application/zip")}
        resp = self.client.post("/api/chat/upload", files=files)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("uri", data)
        self.assertEqual(data["file_size"], 0)

    def test_adv_22_upload_truncated_header_archive(self):
        """Verifies uploading a truncated zip header does not crash the upload endpoint."""
        truncated_zip = b"PK\x03\x04truncated_raw_bytes"
        files = {"file": ("corrupt.zip", truncated_zip, "application/zip")}
        resp = self.client.post("/api/chat/upload", files=files)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("uri", data)
        self.assertEqual(data["file_size"], len(truncated_zip))

    def test_adv_23_upload_bad_crc_archive(self):
        """Verifies uploading a bad CRC archive is processed safely as binary data."""
        bad_crc_zip = b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + b"\xff" * 50
        files = {"file": ("bad_crc.zip", bad_crc_zip, "application/zip")}
        resp = self.client.post("/api/chat/upload", files=files)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("uri", data)
        self.assertEqual(data["file_size"], len(bad_crc_zip))

    def test_adv_24_upload_zip_bomb_structure(self):
        """Verifies uploading a zip bomb does not cause daemon decompression memory exhaustion."""
        bomb_buf = io.BytesIO()
        with zipfile.ZipFile(bomb_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("huge_zeroes.dat", b"\x00" * (10 * 1024 * 1024))
        bomb_bytes = bomb_buf.getvalue()

        files = {"file": ("zipbomb.zip", bomb_bytes, "application/zip")}
        resp = self.client.post("/api/chat/upload", files=files)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("uri", data)
        self.assertEqual(data["file_size"], len(bomb_bytes))

    def test_adv_25_upload_large_context_25mb(self):
        """Verifies uploading a 25MB context archive completes without memory crash."""
        large_payload = b"X" * (25 * 1024 * 1024)
        files = {"file": ("large_context.zip", large_payload, "application/zip")}
        resp = self.client.post("/api/chat/upload", files=files)
        self.assertIn(resp.status_code, (200, 413, 500))
        if resp.status_code != 200:
            self.assertIn("File size exceeds", resp.text)


if __name__ == "__main__":
    unittest.main()
