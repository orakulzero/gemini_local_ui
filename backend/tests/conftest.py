"""Pytest defaults for backend/tests: keep unit tests fast/deterministic via subword embedder
unless a test explicitly requests ONNX via model_dir or GEMINI_RAG_EMBEDDER_MODE=auto."""
from __future__ import annotations

import os

os.environ.setdefault("GEMINI_RAG_EMBEDDER_MODE", "subword")
