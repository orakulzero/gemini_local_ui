"""
tests/adversarial/test_adversarial_mutation_matrix.py
Adversarial Mutation Testing Suite for Milestone M7 Remediation.
Proves that remediated tests across Tier 2, Tier 3, and Tier 4 are NOT dummy facades:
Each test is verified to PASS under valid conditions and FAIL under broken backend mutations.
"""

from __future__ import annotations
import asyncio
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.routing import request_response

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.models import DEFAULT_MODELS, Gem, GemSerializer, resolve_model
from backend.app.bridge.notebooklm import (
    NotebookDetail,
    NotebookLMClient,
    NotebookNote,
    NotebookSource,
)
from backend.app.bridge.session import (
    SessionHealthStatus,
    SessionManager,
)
from backend.app.main import app
from backend.app.storage.database import Database
from backend.app.storage.exporter import ThreadExporter
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.vault import AccountProfile, CredentialVault, FailoverConfig

from tests.e2e.tier2_boundaries.test_boundaries_multi_account_telemetry import (
    TestTier2TelemetryAndStorageBoundaries,
)
from tests.e2e.tier2_boundaries.test_boundaries_parity_notebooklm import (
    TestTier2GeminiParityBoundaries,
    TestTier2NotebookLMBoundaries,
)
from tests.e2e.tier3_combinations.test_combinations_advanced import (
    TestTier3AdvancedCombinations,
)
from tests.e2e.tier4_workloads.test_workloads_enterprise import (
    TestTier4EnterpriseWorkloads,
)


class TestAdversarialMutationMatrix(unittest.TestCase):
    """Executes mutation sensitivity checks on all remediated tests."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_mutation.db")
        self.vault_file = os.path.join(self.temp_dir, "vault.enc")
        self.vault = CredentialVault(self.vault_file, force_fallback=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # =========================================================================
    # Tier 2: F39 & F40 (Telemetry & Limits Boundaries)
    # =========================================================================

    def test_mutation_t2_f39_02_huge_tokens_sensitivity(self):
        """Mutate get_rolling_usage to exclude thinking tokens: test MUST fail."""
        fixture = TestTier2TelemetryAndStorageBoundaries()
        fixture.setUp()
        try:
            # 1. Normal execution passes
            fixture.test_t2_f39_02_huge_token_counts()

            # 2. Mutate get_rolling_usage to omit thinking tokens
            orig_get = Database.get_rolling_usage

            async def buggy_get(self, account_id=None, window_seconds=86400):
                res = await orig_get(self, account_id, window_seconds)
                res["total_tokens"] = res["total_prompt_tokens"] + res["total_completion_tokens"]
                return res

            with patch.object(Database, "get_rolling_usage", buggy_get):
                with self.assertRaises(AssertionError, msg="Facade detected! Test did not fail when thinking tokens were dropped"):
                    fixture.test_t2_f39_02_huge_token_counts()
        finally:
            fixture.tearDown()

    def test_mutation_t2_f39_04_task_mode_sensitivity(self):
        """Mutate get_rolling_usage to reset task_mode_calls to 0: test MUST fail."""
        fixture = TestTier2TelemetryAndStorageBoundaries()
        fixture.setUp()
        try:
            # 1. Normal execution passes
            fixture.test_t2_f39_04_extremely_long_request_latency()

            # 2. Mutate task_mode_calls to 0
            orig_get = Database.get_rolling_usage

            async def buggy_get(self, account_id=None, window_seconds=86400):
                res = await orig_get(self, account_id, window_seconds)
                res["task_mode_calls"] = 0
                return res

            with patch.object(Database, "get_rolling_usage", buggy_get):
                with self.assertRaises(AssertionError, msg="Facade detected! Test did not fail when task_mode_calls was 0"):
                    fixture.test_t2_f39_04_extremely_long_request_latency()
        finally:
            fixture.tearDown()

    def test_mutation_t2_f40_04_negative_threshold_validation_sensitivity(self):
        """Mutate threshold endpoint to return 200 on negative input: test MUST fail."""
        fixture = TestTier2TelemetryAndStorageBoundaries()
        fixture.setUp()
        try:
            # 1. Normal execution passes (returns 422)
            fixture.test_t2_f40_04_negative_velocity_clamping()

            # 2. Mutate endpoint response to 200
            with patch.object(TestClient, "post") as mock_post:
                mock_resp = AsyncMock()
                mock_resp.status_code = 200
                mock_post.return_value = mock_resp
                with self.assertRaises(AssertionError, msg="Facade detected! Test did not fail when 422 rejection was bypassed"):
                    fixture.test_t2_f40_04_negative_velocity_clamping()
        finally:
            fixture.tearDown()

    # =========================================================================
    # Tier 2: F42 (Export & Schema Migration Boundaries)
    # =========================================================================

    def test_mutation_t2_f42_02_unicode_export_sensitivity(self):
        """Mutate ThreadExporter.export_to_json to strip unicode: test MUST fail."""
        fixture = TestTier2TelemetryAndStorageBoundaries()
        fixture.setUp()
        try:
            # 1. Normal execution passes
            fixture.test_t2_f42_02_export_json_serialization_unicode()

            # 2. Mutate export_to_json to strip emojis
            orig_export = ThreadExporter.export_to_json

            def buggy_export(conv, active_thread, all_messages=None):
                out = orig_export(conv, active_thread, all_messages=all_messages)
                return out.replace("🚀🔬", "[STRIPPED]")

            with patch.object(ThreadExporter, "export_to_json", side_effect=buggy_export):
                with self.assertRaises(AssertionError, msg="Facade detected! Test did not fail when unicode emoji was corrupted"):
                    fixture.test_t2_f42_02_export_json_serialization_unicode()
        finally:
            fixture.tearDown()

    def test_mutation_t2_f42_03_schema_default_sensitivity(self):
        """Mutate ConversationRepository.get_conversation to return is_pinned=True: test MUST fail."""
        fixture = TestTier2TelemetryAndStorageBoundaries()
        fixture.setUp()
        try:
            # 1. Normal execution passes
            fixture.test_t2_f42_03_schema_migration_v1_to_v2_default_values()
            fixture.tearDown()

            # 2. Mutate get_conversation to invert is_pinned
            fixture.setUp()
            orig_get = ConversationRepository.get_conversation

            async def buggy_get(self, conv_id):
                c = await orig_get(self, conv_id)
                if c:
                    c.is_pinned = True
                return c

            with patch.object(ConversationRepository, "get_conversation", buggy_get):
                with self.assertRaises(AssertionError, msg="Facade detected! Test did not fail when schema default was corrupted"):
                    fixture.test_t2_f42_03_schema_migration_v1_to_v2_default_values()
        finally:
            fixture.tearDown()

    # =========================================================================
    # Tier 2: F46, F47, F48, F53 (Parity & NotebookLM Boundaries)
    # =========================================================================

    def test_mutation_t2_f46_02_balance_fences_sensitivity(self):
        """Mutate balance_code_fences to be no-op: test MUST fail."""
        fixture = TestTier2GeminiParityBoundaries()
        fixture.setUp()
        try:
            # 1. Normal execution passes
            fixture.test_t2_f46_02_unclosed_markdown_code_fences()

            # 2. Mutate balance_code_fences to do nothing
            with patch.object(ThreadExporter, "balance_code_fences", side_effect=lambda x: x):
                with self.assertRaises(AssertionError, msg="Facade detected! Test did not fail when unclosed fences were left unbalanced"):
                    fixture.test_t2_f46_02_unclosed_markdown_code_fences()
        finally:
            fixture.tearDown()

    def test_mutation_t2_f47_01_root_branch_siblings_sensitivity(self):
        """Mutate get_message_siblings to always return 1 total: test MUST fail."""
        fixture = TestTier2GeminiParityBoundaries()
        fixture.setUp()
        try:
            # 1. Normal execution passes
            fixture.test_t2_f47_01_regeneration_on_root_node()

            # 2. Mutate get_message_siblings to return (1, 1)
            with patch.object(ConversationRepository, "get_message_siblings", new_callable=AsyncMock) as mock_sib:
                mock_sib.return_value = (1, 1)
                with self.assertRaises(AssertionError, msg="Facade detected! Test did not fail when siblings count was wrong"):
                    fixture.test_t2_f47_01_regeneration_on_root_node()
        finally:
            fixture.tearDown()

    def test_mutation_t2_f47_02_out_of_bounds_sibling_clamping_sensitivity(self):
        """Mutate switch_active_branch to silently ignore missing message: test MUST fail."""
        fixture = TestTier2GeminiParityBoundaries()
        fixture.setUp()
        try:
            # 1. Normal execution passes (raises ValueError)
            fixture.test_t2_f47_02_out_of_bounds_sibling_navigation_clamping()

            # 2. Mutate switch_active_branch to not raise ValueError
            with patch.object(ConversationRepository, "switch_active_branch", new_callable=AsyncMock) as mock_sw:
                mock_sw.return_value = None
                with self.assertRaises(AssertionError, msg="Facade detected! Test did not fail when out-of-bounds check was removed"):
                    fixture.test_t2_f47_02_out_of_bounds_sibling_navigation_clamping()
        finally:
            fixture.tearDown()

    def test_mutation_t2_f48_02_gem_system_instruction_combining_sensitivity(self):
        """Mutate export_to_markdown to omit system prompt banner: test MUST fail."""
        fixture = TestTier2GeminiParityBoundaries()
        fixture.setUp()
        try:
            # 1. Normal execution passes
            fixture.test_t2_f48_02_gem_with_existing_system_instruction_combining()

            # 2. Mutate export_to_markdown to omit system prompt
            orig_export = ThreadExporter.export_to_markdown

            def buggy_export(conv, active_thread):
                out = orig_export(conv, active_thread)
                return out.replace("> **System Prompt:**", "")

            with patch.object(ThreadExporter, "export_to_markdown", side_effect=buggy_export):
                with self.assertRaises(AssertionError, msg="Facade detected! Test did not fail when system prompt was omitted"):
                    fixture.test_t2_f48_02_gem_with_existing_system_instruction_combining()
        finally:
            fixture.tearDown()

    def test_mutation_t2_f53_05_source_filtering_sensitivity(self):
        """Mutate attach-context to leak deselected sources: test MUST fail."""
        fixture = TestTier2NotebookLMBoundaries()
        # 1. Normal execution passes
        fixture.test_t2_f53_05_decoupled_state_injection_consumption()

        # 2. Mutate attach-context endpoint on FastAPI route
        target_route = next(r for r in app.routes if getattr(r, "path", None) == "/api/notebooklm/attach-context")
        orig_endpoint = target_route.endpoint
        orig_app = target_route.app

        async def buggy_endpoint(req, request):
            res = await orig_endpoint(req, request)
            res["formatted_context"] += "\nSource content 1"
            return res

        try:
            target_route.endpoint = buggy_endpoint
            target_route.app = request_response(target_route.get_route_handler())
            with self.assertRaises(AssertionError, msg="Facade detected! Test did not fail when deselected source was leaked"):
                fixture.test_t2_f53_05_decoupled_state_injection_consumption()
        finally:
            target_route.endpoint = orig_endpoint
            target_route.app = orig_app

    # =========================================================================
    # Tier 3: Combinations (T3-18, T3-19, T3-24)
    # =========================================================================

    def test_mutation_t3_18_aggregation_ordering_sensitivity(self):
        """Mutate attach-context to invert ordering of notes and sources: test MUST fail."""
        fixture = TestTier3AdvancedCombinations()
        fixture.setUp()
        try:
            # 1. Normal execution passes
            fixture.test_t3_18_multiple_notebook_sources_aggregation_order()

            # 2. Mutate response to put notes before sources
            target_route = next(r for r in app.routes if getattr(r, "path", None) == "/api/notebooklm/attach-context")
            orig_endpoint = target_route.endpoint
            orig_app = target_route.app

            async def buggy_endpoint(req, request):
                res = await orig_endpoint(req, request)
                res["formatted_context"] = "Benchmarking setup\nSelf-attention mechanisms\nDenoising score matching"
                return res

            try:
                target_route.endpoint = buggy_endpoint
                target_route.app = request_response(target_route.get_route_handler())
                with self.assertRaises(AssertionError, msg="Facade detected! Test did not fail when source ordering was inverted"):
                    fixture.test_t3_18_multiple_notebook_sources_aggregation_order()
            finally:
                target_route.endpoint = orig_endpoint
                target_route.app = orig_app
        finally:
            fixture.tearDown()

    def test_mutation_t3_19_drawer_source_selection_sensitivity(self):
        """Mutate attach-context to return all sources unconditionally: test MUST fail."""
        fixture = TestTier3AdvancedCombinations()
        fixture.setUp()
        try:
            # 1. Normal execution passes
            fixture.test_t3_19_drawer_source_selection_state_isolation()

            # 2. Mutate response to leak Vision Transformers in filtered request
            target_route = next(r for r in app.routes if getattr(r, "path", None) == "/api/notebooklm/attach-context")
            orig_endpoint = target_route.endpoint
            orig_app = target_route.app

            async def buggy_endpoint(req, request):
                res = await orig_endpoint(req, request)
                res["formatted_context"] += "\nVision Transformers"
                return res

            try:
                target_route.endpoint = buggy_endpoint
                target_route.app = request_response(target_route.get_route_handler())
                with self.assertRaises(AssertionError, msg="Facade detected! Test did not fail when unselected source leaked"):
                    fixture.test_t3_19_drawer_source_selection_state_isolation()
            finally:
                target_route.endpoint = orig_endpoint
                target_route.app = orig_app
        finally:
            fixture.tearDown()

    def test_mutation_t3_24_token_velocity_burst_sensitivity(self):
        """Mutate get_rolling_usage to return 0 tokens during burst: test MUST fail."""
        fixture = TestTier3AdvancedCombinations()
        fixture.setUp()
        try:
            # 1. Normal execution passes
            fixture.test_t3_24_token_velocity_burst_triggers_alert_flag()

            # 2. Mutate get_rolling_usage to return 0 tokens
            orig_get = Database.get_rolling_usage

            async def zero_get(self, account_id=None, window_seconds=86400):
                res = await orig_get(self, account_id, window_seconds)
                res["total_tokens"] = 0
                return res

            with patch.object(Database, "get_rolling_usage", zero_get):
                with self.assertRaises(AssertionError, msg="Facade detected! Test did not fail when token burst was zeroed out"):
                    fixture.test_t3_24_token_velocity_burst_triggers_alert_flag()
        finally:
            fixture.tearDown()

    # =========================================================================
    # Tier 4: Enterprise Workloads (T4-06, T4-07)
    # =========================================================================

    def test_mutation_t4_enterprise_06_failover_telemetry_sensitivity(self):
        """Mutate get_recent_failovers to return empty list: test MUST fail."""
        fixture = TestTier4EnterpriseWorkloads()
        fixture.setUp()
        try:
            # 1. Normal execution passes
            fixture.test_t4_enterprise_06_telemetry_live_tracking_across_failover_chain()

            # 2. Mutate get_recent_failovers to return empty list
            with patch.object(Database, "get_recent_failovers", new_callable=AsyncMock) as mock_fail:
                mock_fail.return_value = []
                with self.assertRaises(AssertionError, msg="Facade detected! Test did not fail when failover audit was empty"):
                    fixture.test_t4_enterprise_06_telemetry_live_tracking_across_failover_chain()
        finally:
            fixture.tearDown()

    def test_mutation_t4_enterprise_07_gem_persona_dag_branching_sensitivity(self):
        """Mutate create_conversation to drop gem_id and system_prompt: test MUST fail."""
        fixture = TestTier4EnterpriseWorkloads()
        fixture.setUp()
        try:
            # 1. Normal execution passes
            fixture.test_t4_enterprise_07_gem_persona_injection_throughout_multi_turn_conversation()

            # 2. Mutate create_conversation to lose system_prompt
            orig_create = ConversationRepository.create_conversation

            async def buggy_create(self, title="", model=None, gem_id=None, system_prompt=None):
                return await orig_create(self, title=title, model=model, gem_id=None, system_prompt=None)

            with patch.object(ConversationRepository, "create_conversation", buggy_create):
                with self.assertRaises(AssertionError, msg="Facade detected! Test did not fail when Gem persona was dropped"):
                    fixture.test_t4_enterprise_07_gem_persona_injection_throughout_multi_turn_conversation()
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
