"""
backend/app/rag/__init__.py
Local Hybrid RAG package (`sqlite-vec` 512d + SQLite FTS5 + RRF + Reranker).
"""

from backend.app.rag.chunker import (
    ChunkPiece,
    chunk_document,
    collect_brain_digest_documents,
    sanitize_digest_text,
)
from backend.app.rag.embedder import (
    EMBEDDING_DIM,
    QWEN3_QUERY_PREFIX,
    LocalEmbedder,
    RAGEmbedder,
    compute_subword_ngram_embeddings,
    extract_subword_tokens,
)
from backend.app.rag.hybrid_search import (
    HybridRAGService,
    format_rag_context_xml,
    reciprocal_rank_fusion,
)
from backend.app.rag.reranker import CrossOverlapReranker, Reranker
from backend.app.rag.vector_store import (
    VECTOR_DIM,
    SqliteVecStore,
    VectorStoreProtocol,
    load_sqlite_vec_extension,
)

__all__ = [
    "ChunkPiece",
    "CrossOverlapReranker",
    "EMBEDDING_DIM",
    "HybridRAGService",
    "LocalEmbedder",
    "QWEN3_QUERY_PREFIX",
    "RAGEmbedder",
    "Reranker",
    "SqliteVecStore",
    "VECTOR_DIM",
    "VectorStoreProtocol",
    "chunk_document",
    "collect_brain_digest_documents",
    "compute_subword_ngram_embeddings",
    "extract_subword_tokens",
    "format_rag_context_xml",
    "load_sqlite_vec_extension",
    "reciprocal_rank_fusion",
    "sanitize_digest_text",
]
