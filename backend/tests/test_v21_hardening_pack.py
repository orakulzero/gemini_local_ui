"""backend/tests/test_v21_hardening_pack.py - v2.1 Hardening Pack: Vector-Space Versioning, Vault Generation, Wreq Reset & DPAPI Discovery."""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
import time
from typing import Any

import pytest

from backend.app.bridge.wreq_transport import WreqTransportClient
from backend.app.rag.embedder import LocalEmbedder, verify_onnx_weights_sha256
from backend.app.rag.hybrid_search import HybridRAGService
from backend.app.security.runtime_discovery import (
    clear_runtime_discovery,
    read_runtime_discovery,
    write_runtime_discovery,
)
from backend.app.storage.database import Database
from backend.app.storage.vault import CredentialVault

SCHEMA_PATH = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"


async def _maybe_await(val: Any) -> Any:
    return await val if inspect.isawaitable(val) else val


@pytest.mark.asyncio
async def test_vector_space_versioning_anti_poisoning_and_onnx_sha256(tmp_path: Path):
    db = Database(tmp_path / "v21_rag.db")
    await db.init_schema(SCHEMA_PATH)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "retrieval.md").write_text(
        "Hybrid retrieval fuses dense cosine vectors with sparse BM25 lexical rankings.",
        encoding="utf-8",
    )
    (docs_dir / "supervisor.md").write_text(
        "Win32 Job Objects reap child sidecars deterministically upon supervisor termination.",
        encoding="utf-8",
    )

    emb_v1 = LocalEmbedder(mode="subword")
    assert emb_v1.backend_name == "subword_ngram_512"
    svc_v1 = HybridRAGService(db, embedder=emb_v1)

    emb_v2 = LocalEmbedder(mode="subword")
    emb_v2.backend_name = "onnx_qwen3_512"
    svc_v2 = HybridRAGService(db, embedder=emb_v2)

    try:
        idx1 = await _maybe_await(svc_v1.index_sources(source_types=["workspace_file"], workspace_dir=docs_dir))
        assert idx1["indexed_chunks"] >= 2
        meta1 = await _maybe_await(svc_v1.vector_store.get_vector_space_meta())
        assert meta1["embedder_id"] == "subword_ngram_512"
        assert meta1["dim"] == 512

        # 1. Before re-indexing under onnx_qwen3_512 -> must fail closed out of vector KNN to fts5_degraded
        pre_res = await _maybe_await(svc_v2.search("dense cosine vectors lexical", top_k=5, mode="hybrid"))
        assert pre_res["degraded"] is True
        assert pre_res["effective_mode"] == "fts5_degraded"
        assert pre_res.get("vector_space_mismatch") is True
        assert len(pre_res["results"]) >= 1

        # 2. Re-indexing wipes incompatible vectors & re-embeds all chunks cleanly under onnx_qwen3_512
        idx2 = await _maybe_await(svc_v2.index_sources(source_types=["workspace_file"], workspace_dir=docs_dir))
        assert idx2["indexed_chunks"] >= 2
        meta2 = await _maybe_await(svc_v2.vector_store.get_vector_space_meta())
        assert meta2["embedder_id"] == "onnx_qwen3_512"

        post_res = await _maybe_await(svc_v2.search("dense cosine vectors lexical", top_k=5, mode="hybrid"))
        assert post_res["effective_mode"] == "hybrid"
        assert post_res["degraded"] is False
        assert bool(post_res.get("vector_space_mismatch", False)) is False
        assert len(post_res["results"]) >= 1
    finally:
        await _maybe_await(emb_v1.shutdown())
        await _maybe_await(emb_v2.shutdown())
        await db.close()

    # 3. ONNX weights SHA-256 verification (matches -> True, tampered/wrong -> False)
    onnx_dir = tmp_path / "onnx_model"
    onnx_dir.mkdir()
    onnx_file = onnx_dir / "model.onnx"
    raw_weights = b"deterministic_onnx_graph_bytes_v21"
    onnx_file.write_bytes(raw_weights)
    expected_sha = hashlib.sha256(raw_weights).hexdigest()

    assert verify_onnx_weights_sha256(onnx_dir, expected_sha256=expected_sha) is True
    assert verify_onnx_weights_sha256(onnx_dir, expected_sha256="f" * 64) is False
    onnx_file.write_bytes(b"tampered_onnx_graph_bytes")
    assert verify_onnx_weights_sha256(onnx_dir, expected_sha256=expected_sha) is False


def test_vault_generation_immediate_wreq_pool_reset_and_runtime_discovery(tmp_path: Path):
    vault_file = tmp_path / "v.enc"
    vault = CredentialVault(vault_file)

    vault.save_credentials({"__Secure-1PSID": "cookie_rot_1"})
    gen1 = vault.get_vault_generation()
    assert gen1 >= 1

    vault.save_credentials({"__Secure-1PSID": "cookie_rot_2"})
    gen2 = vault.get_vault_generation()
    assert gen2 == gen1 + 1 and gen2 >= 2

    reloaded = CredentialVault(vault_file)
    assert reloaded.get_vault_generation() == gen2
    assert reloaded.load_vault_data().generation == gen2

    # Immediate (<1s) WreqTransportClient H2 pool reset on generation bump
    gen = [1]
    cli = WreqTransportClient(timeout=15.0, generation_provider=lambda: gen[0])
    t0 = time.monotonic()
    raw1 = cli._get_raw_client()
    assert raw1 is not None

    gen[0] += 1
    raw2 = cli._get_raw_client()
    assert (time.monotonic() - t0) < 1.0
    assert raw2 is not raw1
    assert cli._pool_reset_count >= 1

    # DPAPI-protected runtime_session.json never stores plaintext api_token on disk
    disc_path = tmp_path / "runtime_session.json"
    secret_tok = "tok_super_secret_v21"
    write_runtime_discovery(
        disc_path,
        base_url="http://127.0.0.1:8123",
        acp_ws_url="ws://127.0.0.1:8765",
        api_token=secret_tok,
        pid=4242,
    )
    raw_disk = disc_path.read_bytes()
    assert secret_tok.encode("utf-8") not in raw_disk

    recovered = read_runtime_discovery(disc_path)
    assert recovered is not None
    assert recovered["base_url"] == "http://127.0.0.1:8123"
    assert recovered["acp_ws_url"] == "ws://127.0.0.1:8765"
    assert recovered["api_token"] == secret_tok
    assert recovered["pid"] == 4242

    clear_runtime_discovery(disc_path)
    assert not disc_path.exists()
