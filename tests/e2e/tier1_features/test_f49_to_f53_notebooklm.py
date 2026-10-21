"""
tests/e2e/tier1_features/test_f49_to_f53_notebooklm.py
Tier 1 Feature Unit Tests for F49 to F53:
- F49: NotebookLM Scraper & Session Sharing
- F50: NotebookLM Batchexecute RPC Handlers (wXbhsf, rLM1Ne, CCqFvf, WWINqb, s0tc2d, trScbf)
- F51: NotebookLM Mock Server & Degraded Mode Fallback
- F52: NotebookLM REST API Router (/api/notebooklm/*)
- F53: UI Research Drawer & Chat Context Bridge
"""

import asyncio
from dataclasses import asdict
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from tests.e2e.harness import PromptInputSimulator

from backend.app.bridge.exceptions import AuthenticationError, GeminiBridgeError
from backend.app.bridge.notebooklm import (
    NotebookDetail,
    NotebookInfo,
    NotebookLMClient,
    NotebookNote,
    NotebookSource,
    _RE_CFB2H,
    _RE_FDRFJE,
    _RE_SNLM0E,
)
from backend.app.main import app
from backend.app.mock.fixtures import (
    HTML_NOTEBOOKLM_APP_PAGE,
    PAYLOAD_NOTEBOOKLM_ADD_SOURCE,
    PAYLOAD_NOTEBOOKLM_CREATE_NOTEBOOK,
    PAYLOAD_NOTEBOOKLM_DELETE_NOTEBOOK,
    PAYLOAD_NOTEBOOKLM_GET_NOTEBOOK,
    PAYLOAD_NOTEBOOKLM_LIST_NOTEBOOKS,
    PAYLOAD_NOTEBOOKLM_RENAME_NOTEBOOK,
    WIZ_GLOBAL_DATA_NOTEBOOKLM,
)


class TestF49NotebookLMScraperSessionSharing(unittest.TestCase):
    """F49: NotebookLM Scraper & Session Sharing."""

    def test_f49_token_scraping_from_wiz_global_data(self):
        m_sn = _RE_SNLM0E.search(HTML_NOTEBOOKLM_APP_PAGE)
        m_cf = _RE_CFB2H.search(HTML_NOTEBOOKLM_APP_PAGE)
        m_fd = _RE_FDRFJE.search(HTML_NOTEBOOKLM_APP_PAGE)
        self.assertIsNotNone(m_sn)
        self.assertIsNotNone(m_cf)
        self.assertIsNotNone(m_fd)
        self.assertTrue(len(m_sn.group(1)) > 5)
        self.assertTrue(len(m_cf.group(1)) > 5)
        self.assertTrue(len(m_fd.group(1)) > 5)

    def test_f49_token_scraping_missing_wiz_falls_back_to_snlm0e(self):
        client = NotebookLMClient(
            cookies={"__Secure-1PSID": "mock_sid"},
            fallback_snlm0e="fallback_token_12345",
        )
        self.assertEqual(client.access_token, "fallback_token_12345")

    def test_f49_session_cookie_propagation(self):
        cookies = {
            "__Secure-1PSID": "test_psid_val",
            "__Secure-1PSIDTS": "test_psidts_val",
        }
        client = NotebookLMClient(cookies=cookies)
        self.assertEqual(client.cookies["__Secure-1PSID"], "test_psid_val")
        self.assertEqual(client.cookies["__Secure-1PSIDTS"], "test_psidts_val")

    def test_f49_shared_account_credential_reuse(self):
        client = NotebookLMClient(
            cookies={"__Secure-1PSID": "shared_account_sid"},
            fallback_snlm0e="shared_at_token",
        )
        self.assertEqual(client.access_token, "shared_at_token")
        self.assertIn("__Secure-1PSID", client.cookies)

    def test_f49_expired_token_triggers_authentication_error(self):
        client = NotebookLMClient(cookies={"__Secure-1PSID": "expired"})
        async def _run():
            mock_resp = MagicMock()
            mock_resp.status_code = 401
            mock_resp.text = "Unauthorized"
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = mock_resp
                await client.call_batchexecute("wXbhsf", [])

        with self.assertRaises(AuthenticationError):
            asyncio.run(_run())


class TestF50NotebookLMBatchexecuteRPCHandlers(unittest.TestCase):
    """F50: NotebookLM Batchexecute RPC Handlers."""

    def test_f50_rpc_wXbhsf_list_notebooks_payload(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.return_value = PAYLOAD_NOTEBOOKLM_LIST_NOTEBOOKS
                notebooks = await client.list_notebooks()
                self.assertIsInstance(notebooks, list)
                self.assertEqual(len(notebooks), 2)
                self.assertEqual(notebooks[0].id, "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d")
                self.assertEqual(notebooks[0].title, "Quantum Computing & Shor's Algorithm")
                self.assertEqual(notebooks[0].source_count, 2)
                mock_rpc.assert_called_once_with("wXbhsf", [None, 1, None, [0, None, 1]])

        asyncio.run(_run())

    def test_f50_rpc_rLM1Ne_get_notebook_payload(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.return_value = PAYLOAD_NOTEBOOKLM_GET_NOTEBOOK
                detail = await client.get_notebook("nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d")
                self.assertIsInstance(detail, NotebookDetail)
                self.assertEqual(detail.id, "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d")
                self.assertEqual(len(detail.sources), 2)
                self.assertEqual(detail.sources[0].id, "src_qcomp_01")
                self.assertEqual(detail.sources[0].title, "Introduction to Quantum Algorithms.pdf")
                self.assertEqual(len(detail.notes), 1)
                self.assertEqual(detail.notes[0].id, "note_qcomp_n1")

        asyncio.run(_run())

    def test_f50_rpc_CCqFvf_create_notebook(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.return_value = PAYLOAD_NOTEBOOKLM_CREATE_NOTEBOOK
                res = await client.create_notebook("Quantum Computing")
                self.assertIsInstance(res, NotebookInfo)
                self.assertEqual(res.id, "nb_created_mock_uuid_12345")
                mock_rpc.assert_called_once_with("CCqFvf", ["Quantum Computing"])

        asyncio.run(_run())

    def test_f50_rpc_WWINqb_delete_notebook(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.return_value = PAYLOAD_NOTEBOOKLM_DELETE_NOTEBOOK
                ok = await client.delete_notebook("nb_001")
                self.assertTrue(ok)
                mock_rpc.assert_called_once_with("WWINqb", [["nb_001"], [2]])

        asyncio.run(_run())

    def test_f50_rpc_s0tc2d_and_trScbf_source_operations(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.return_value = PAYLOAD_NOTEBOOKLM_RENAME_NOTEBOOK
                ok = await client.rename_notebook("nb_001", "Updated Title")
                self.assertTrue(ok)
                self.assertEqual(mock_rpc.call_args[0][0], "s0tc2d")

                mock_rpc.return_value = PAYLOAD_NOTEBOOKLM_ADD_SOURCE
                src = await client.add_text_source("nb_001", "Exported Research Transcript", "Content here")
                self.assertIsInstance(src, NotebookSource)
                self.assertEqual(src.title, "Exported Research Transcript")
                self.assertEqual(mock_rpc.call_args[0][0], "trScbf")

        asyncio.run(_run())


class TestF51NotebookLMMockServerFallback(unittest.TestCase):
    """F51: NotebookLM Mock Server & Degraded Mode Fallback."""

    def test_f51_mock_server_list_notebooks_fixture(self):
        client = NotebookLMClient(fallback_snlm0e="mock_token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.return_value = PAYLOAD_NOTEBOOKLM_LIST_NOTEBOOKS
                parsed = await client.list_notebooks()
                self.assertEqual(len(parsed), 2)
                self.assertTrue(any("Quantum" in n.title for n in parsed))

        asyncio.run(_run())

    def test_f51_mock_server_create_and_fetch_source(self):
        src = NotebookSource(
            id="src_mock_1",
            title="Mock Research Paper",
            source_type=1,
            summary="Key findings on diffusion models",
            token_count=1200,
        )
        self.assertEqual(src.token_count, 1200)
        self.assertEqual(src.source_type, 1)

    def test_f51_degraded_mode_empty_list_on_unreachable(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.side_effect = GeminiBridgeError("Connection failed")
                with self.assertRaises(GeminiBridgeError):
                    await client.list_notebooks()

        asyncio.run(_run())

    def test_f51_mock_fixtures_serialization_integrity(self):
        m_sn = _RE_SNLM0E.search(HTML_NOTEBOOKLM_APP_PAGE)
        m_cfb = _RE_CFB2H.search(HTML_NOTEBOOKLM_APP_PAGE)
        m_fdr = _RE_FDRFJE.search(HTML_NOTEBOOKLM_APP_PAGE)
        self.assertIsNotNone(m_sn)
        self.assertIsNotNone(m_cfb)
        self.assertIsNotNone(m_fdr)
        self.assertEqual(m_sn.group(1), WIZ_GLOBAL_DATA_NOTEBOOKLM["SNlM0e"])
        self.assertEqual(m_cfb.group(1), WIZ_GLOBAL_DATA_NOTEBOOKLM["cfb2h"])
        self.assertEqual(m_fdr.group(1), WIZ_GLOBAL_DATA_NOTEBOOKLM["FdrFJe"])

    def test_f51_error_envelope_raises_gemini_bridge_error(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.side_effect = GeminiBridgeError("RPC error code 500")
                with self.assertRaises(GeminiBridgeError):
                    await client.get_notebook("nb_invalid")

        asyncio.run(_run())


class TestF52NotebookLMRestApiRouter(unittest.TestCase):
    """F52: NotebookLM REST API Router."""

    def setUp(self):
        self.tc = TestClient(app)

    def test_f52_rest_api_status_endpoint(self):
        resp = self.tc.get("/api/notebooklm/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("available", data)
        self.assertIn("authenticated", data)
        self.assertIn("base_url", data)

    def test_f52_rest_api_list_notebooks(self):
        with patch.object(NotebookLMClient, "list_notebooks", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = [
                NotebookInfo(id="nb_test", title="Test Notebook", source_count=2)
            ]
            resp = self.tc.get("/api/notebooklm/notebooks")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["id"], "nb_test")

    def test_f52_rest_api_create_notebook(self):
        with patch.object(NotebookLMClient, "create_notebook", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = NotebookInfo(id="nb_new", title="New Study", source_count=0)
            resp = self.tc.post("/api/notebooklm/notebooks", json={"title": "New Study"})
            self.assertEqual(resp.status_code, 201)
            data = resp.json()
            self.assertEqual(data["id"], "nb_new")
            self.assertEqual(data["title"], "New Study")

    def test_f52_rest_api_attach_context(self):
        with patch.object(NotebookLMClient, "get_notebook", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = NotebookDetail(
                id="nb_001",
                title="AI Research",
                sources=[
                    NotebookSource(id="src_1", title="Doc 1", summary="Summary 1"),
                ],
                notes=[
                    NotebookNote(id="note_1", title="Note 1", content="Key note content"),
                ],
            )
            resp = self.tc.post(
                "/api/notebooklm/attach-context",
                json={
                    "notebook_id": "nb_001",
                    "source_ids": ["src_1"],
                    "note_ids": ["note_1"],
                    "include_notes": True,
                },
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("formatted_context", data)
            self.assertIn("<notebooklm_reference", data["formatted_context"])
            self.assertIn("AI Research", data["formatted_context"])
            self.assertIn("Doc 1", data["formatted_context"])

    def test_f52_rest_api_export_chat_to_notebook(self):
        with patch.object(NotebookLMClient, "add_text_source", new_callable=AsyncMock) as mock_add:
            mock_add.return_value = NotebookSource(id="src_exp", title="Exported Research")
            resp = self.tc.post(
                "/api/notebooklm/export",
                json={
                    "notebook_id": "nb_001",
                    "title": "Exported Research",
                    "content": "Full synthesis text",
                },
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("source", {}).get("id"), "src_exp")


class TestF53UIResearchDrawerContextBridge(unittest.TestCase):
    """F53: UI Research Drawer & Chat Context Bridge."""

    def test_f53_context_injection_formatting(self):
        detail = NotebookDetail(
            id="nb_chem",
            title="Polymer Synthesis",
            sources=[
                NotebookSource(id="s1", title="Synthesis Protocol", summary="Reagent concentrations and yield."),
            ],
            notes=[
                NotebookNote(id="n1", title="Safety Note", content="Maintain below 50C."),
            ],
        )
        context_parts = [
            f'<notebooklm_reference title="{detail.title}" notebook_id="{detail.id}">',
            "Sources:",
            f"- {detail.sources[0].title}: {detail.sources[0].summary}",
            "Notes:",
            f"- {detail.notes[0].title}: {detail.notes[0].content}",
            "</notebooklm_reference>",
        ]
        formatted = "\n".join(context_parts)
        self.assertTrue(formatted.startswith('<notebooklm_reference title="Polymer Synthesis"'))
        self.assertTrue(formatted.endswith("</notebooklm_reference>"))
        self.assertIn("Reagent concentrations", formatted)
        self.assertIn("Safety Note", formatted)

    def test_f53_context_decoupling_with_draft_prompt(self):
        tc = TestClient(app)
        detail = NotebookDetail(
            id="nb_poly",
            title="Polymer Synthesis",
            sources=[NotebookSource(id="src_p1", title="Paper on Polymers", source_type=3, summary="Polymer data")],
            notes=[],
        )
        with patch.object(NotebookLMClient, "get_notebook", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = detail
            res = tc.post("/api/notebooklm/attach-context", json={"notebook_id": "nb_poly", "source_ids": ["src_p1"]})
            self.assertEqual(res.status_code, 200)
            injected_context = res.json()["formatted_context"]

        sim = PromptInputSimulator()
        sim.set_text("User typed question about polymers")
        combined = f"{injected_context}\n\n{sim.text}"

        self.assertNotEqual(sim.text, injected_context)
        self.assertTrue(combined.startswith('<notebooklm_reference title="Polymer Synthesis">'))
        self.assertTrue(combined.endswith("User typed question about polymers"))

    def test_f53_multi_source_selection_aggregation(self):
        tc = TestClient(app)
        detail = NotebookDetail(
            id="nb_multi",
            title="Materials Research",
            sources=[
                NotebookSource(id="src_1", title="Paper A", source_type=3, summary="Summary of A"),
                NotebookSource(id="src_2", title="Paper B", source_type=3, summary="Summary of B"),
            ],
            notes=[],
        )
        with patch.object(NotebookLMClient, "get_notebook", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = detail
            res = tc.post("/api/notebooklm/attach-context", json={"notebook_id": "nb_multi", "source_ids": ["src_1", "src_2"]})
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["source_count"], 2)
            self.assertIn('title="Paper A"', data["formatted_context"])
            self.assertIn("Summary of A", data["formatted_context"])
            self.assertIn('title="Paper B"', data["formatted_context"])
            self.assertIn("Summary of B", data["formatted_context"])

    def test_f53_empty_selection_handling(self):
        tc = TestClient(app)
        detail = NotebookDetail(
            id="nb_01",
            title="Sample Notebook",
            sources=[NotebookSource(id="src_1", title="Doc 1", source_type=3, summary="Preview")],
            notes=[],
        )
        with patch.object(NotebookLMClient, "get_notebook", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = detail
            res = tc.post("/api/notebooklm/attach-context", json={"notebook_id": "nb_01", "source_ids": [], "note_ids": []})
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn("formatted_context", data)
            self.assertEqual(data["source_count"], 0)

    def test_f53_drawer_toggle_and_source_count_sync(self):
        tc = TestClient(app)
        notebooks = [
            NotebookInfo(id="nb_001", title="Quantum Research", source_count=3),
            NotebookInfo(id="nb_002", title="Compiler Notes", source_count=1),
        ]
        with patch.object(NotebookLMClient, "list_notebooks", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = notebooks
            res = tc.get("/api/notebooklm/notebooks")
            self.assertEqual(res.status_code, 200)
            items = res.json()
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["id"], "nb_001")
            self.assertEqual(items[0]["source_count"], 3)


if __name__ == "__main__":
    unittest.main()
