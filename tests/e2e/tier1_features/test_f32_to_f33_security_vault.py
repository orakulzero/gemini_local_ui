# tests/e2e/tier1_features/test_f32_to_f33_security_vault.py
"""Tier 1: Tests for Features F32 to F33 (DPAPI Vault & Security Guard)."""

import unittest
from tests.e2e.harness import (
    DPAPIVault,
    LocalSecurityGuard,
)

class TestF32WindowsDPAPILocalVault(unittest.TestCase):
    """F32: Windows DPAPI Local Vault."""

    def test_f32_01_encrypt_and_decrypt_roundtrip(self):
        secret = b"__Secure-1PSID=secret_auth_token_xyz"
        encrypted = DPAPIVault.encrypt_data(secret)
        decrypted = DPAPIVault.decrypt_data(encrypted)
        self.assertEqual(decrypted, secret)

    def test_f32_02_encrypted_blob_not_equal_to_plaintext(self):
        secret = b"super_sensitive_api_key"
        encrypted = DPAPIVault.encrypt_data(secret)
        self.assertNotEqual(encrypted, secret)

    def test_f32_03_decrypt_corrupted_ciphertext_raises_error(self):
        corrupted = b"DPAPI:corrupted_raw_bytes_that_fail_decryption"
        with self.assertRaises(ValueError):
            DPAPIVault.decrypt_data(corrupted)

    def test_f32_04_empty_data_encryption_handling(self):
        empty = b""
        encrypted = DPAPIVault.encrypt_data(empty)
        decrypted = DPAPIVault.decrypt_data(encrypted)
        self.assertEqual(decrypted, empty)

    def test_f32_05_vault_envelope_prefix_verification(self):
        secret = b"test_payload"
        encrypted = DPAPIVault.encrypt_data(secret)
        self.assertTrue(encrypted.startswith(b"DPAPI:") or encrypted.startswith(b"MOCK_VAULT:"))


class TestF33SecurityAndLocalhostBinding(unittest.TestCase):
    """F33: Security & 127.0.0.1 Binding."""

    def test_f33_01_allow_host_127_0_0_1(self):
        self.assertTrue(LocalSecurityGuard.verify_host("127.0.0.1"))
        self.assertTrue(LocalSecurityGuard.verify_host("127.0.0.1:8000"))

    def test_f33_02_allow_host_localhost_with_port(self):
        self.assertTrue(LocalSecurityGuard.verify_host("localhost"))
        self.assertTrue(LocalSecurityGuard.verify_host("localhost:5173"))

    def test_f33_03_reject_external_host_evil_com(self):
        self.assertFalse(LocalSecurityGuard.verify_host("evil.com"))
        self.assertFalse(LocalSecurityGuard.verify_host("attacker.com:8000"))
        self.assertFalse(LocalSecurityGuard.verify_host("192.168.1.100:8000"))

    def test_f33_04_reject_unauthorized_cors_origin(self):
        self.assertTrue(LocalSecurityGuard.verify_origin("http://localhost:5173"))
        self.assertTrue(LocalSecurityGuard.verify_origin("http://127.0.0.1:3000"))
        self.assertFalse(LocalSecurityGuard.verify_origin("http://malicious-site.com"))

    def test_f33_05_verify_anti_csrf_token_on_mutating_request(self):
        valid = LocalSecurityGuard.verify_csrf("POST", "valid_csrf_token", "valid_csrf_token")
        self.assertTrue(valid)
        invalid = LocalSecurityGuard.verify_csrf("POST", "wrong_token", "valid_csrf_token")
        self.assertFalse(invalid)
        # GET requests pass through CSRF verification
        get_pass = LocalSecurityGuard.verify_csrf("GET", None, "valid_csrf_token")
        self.assertTrue(get_pass)


if __name__ == "__main__":
    unittest.main()
