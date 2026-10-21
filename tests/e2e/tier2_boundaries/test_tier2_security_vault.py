# tests/e2e/tier2_boundaries/test_tier2_security_vault.py
"""Tier 2: Boundary & Corner Cases for Features F32 to F33 (DPAPI Vault & Security Guard)."""

import unittest
from tests.e2e.harness import (
    DPAPIVault,
    LocalSecurityGuard,
)

class TestTier2SecurityVault(unittest.TestCase):
    """Boundary & Corner Cases for F32 to F33."""

    # --- F32: Windows DPAPI Local Vault ---
    def test_t2_f32_01_decrypt_empty_ciphertext(self):
        decrypted = DPAPIVault.decrypt_data(b"")
        self.assertEqual(decrypted, b"")

    def test_t2_f32_02_decrypt_truncated_dpapi_envelope(self):
        with self.assertRaises(ValueError):
            DPAPIVault.decrypt_data(b"DPAPI:truncated")

    def test_t2_f32_03_encrypt_10mb_payload(self):
        big_data = b"X" * (10 * 1024 * 1024)
        encrypted = DPAPIVault.encrypt_data(big_data)
        decrypted = DPAPIVault.decrypt_data(encrypted)
        self.assertEqual(len(decrypted), len(big_data))

    def test_t2_f32_04_decrypt_random_garbage_bytes(self):
        with self.assertRaises(ValueError):
            DPAPIVault.decrypt_data(b"\x00\x01\x02\x03\x04\x05\x06\x07")

    def test_t2_f32_05_encrypt_full_byte_range(self):
        all_bytes = bytes(range(256))
        encrypted = DPAPIVault.encrypt_data(all_bytes)
        decrypted = DPAPIVault.decrypt_data(encrypted)
        self.assertEqual(decrypted, all_bytes)

    # --- F33: Security & 127.0.0.1 Binding ---
    def test_t2_f33_01_host_ipv6_loopback(self):
        self.assertTrue(LocalSecurityGuard.verify_host("[::1]"))
        self.assertTrue(LocalSecurityGuard.verify_host("[::1]:8000"))

    def test_t2_f33_02_host_spaces_or_newlines_rejected(self):
        self.assertFalse(LocalSecurityGuard.verify_host("127.0.0.1\r\nevil.com"))
        self.assertFalse(LocalSecurityGuard.verify_host("localhost evil.com"))

    def test_t2_f33_03_origin_null_string_rejected(self):
        self.assertFalse(LocalSecurityGuard.verify_origin("null"))

    def test_t2_f33_04_csrf_missing_token_on_post_rejected(self):
        self.assertFalse(LocalSecurityGuard.verify_csrf("POST", None, "valid_session_token"))
        self.assertFalse(LocalSecurityGuard.verify_csrf("DELETE", "", "valid_session_token"))

    def test_t2_f33_05_host_none_or_empty_rejected(self):
        self.assertFalse(LocalSecurityGuard.verify_host(None))
        self.assertFalse(LocalSecurityGuard.verify_host(""))


if __name__ == "__main__":
    unittest.main()
