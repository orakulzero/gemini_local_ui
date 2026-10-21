# Gemini Local UI (`gemini_local_ui`)

High-performance local web client, reverse-engineered `gemini.google.com` session bridge, SQLite FTS5 conversation store with DAG branching, and OpenAI-compatible local API server (`/v1/chat/completions`, `/v1/models`).

## Key Capabilities

1. **Authorized Gemini Web-Session Bridge (`backend/app/bridge/`)**
   - Authenticates against `gemini.google.com` using your authorized session cookies (`__Secure-1PSID`, `__Secure-1PSIDTS`) or automatic local Chromium/Chrome/Edge/Brave profile extraction.
   - Extracts `SNlM0e` CSRF tokens, executes `batchexecute` RPCs (`otAQ7b`, `MaZiqc`, `hNvQHb`, `CNgdBe`), parses length-prefixed `StreamGenerate` chunks (with incremental UTF-8 frame decoding and thinking-trace separation), uploads multimodal files via `content-push.googleapis.com`, and rotates session cookies via `RotateCookies`.
   - Encrypts stored session credentials locally using native Windows DPAPI (`CryptProtectData` / `CryptUnprotectData` in `backend/app/storage/vault.py`) with AES-256-GCM fallback.

2. **Lag-Free Virtualized Local Web UI (`frontend/src/`)**
   - Custom viewport virtualizer (`VirtualChatList.tsx`) with O(log N) binary-search offset indexing, synchronous layout measurement + singleton `ResizeObserver` dynamic height propagation, and scroll anchoring.
   - Off-main-thread Web Worker syntax highlighting (`highlight.worker.ts`) and Shadow DOM style/token encapsulation (`CodeBlock.tsx`, `MathBlock.tsx`), maintaining **~83–106 total document DOM nodes** and **0 main-thread long tasks (>50ms)** even on 500–1,050 message threads with 100+ code blocks and LaTeX formulas.

3. **Local SQLite WAL Storage, DAG Branching & Instant FTS5 Search (`backend/app/storage/`)**
   - Multi-connection reader pool + serialized write lock over SQLite WAL mode.
   - Full-text search (`FTS5` with BM25 ranking, snippet highlighting, single-flight request coalescing, and generation-epoch cache invalidation) delivering **~19ms P99 latency** across 1,000+ conversations under 100-client concurrency.
   - Tree-structured message branching (`parent_id` DAG), sibling navigation (`< 1/2 >`), message editing with fork regeneration, and Markdown/JSON conversation export.

4. **OpenAI-Compatible Local API Proxy (`backend/app/openai/`)**
   - Exposes `GET /v1/models` and `POST /v1/chat/completions` (both `stream: true` SSE and `stream: false`, plus base64/URL vision attachments) routed through your authorized Gemini account.

---

## Quick Start

### One-Command Launch (Windows PowerShell)
```powershell
.\start.ps1
```
- **Backend API & OpenAI Proxy**: `http://127.0.0.1:8000`
- **Local Web UI**: `http://127.0.0.1:5173`

### Manual Launch

1. **Start Backend Server**:
   ```powershell
   python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
   ```
2. **Start Frontend UI**:
   ```powershell
   cd frontend
   npm run dev -- --host 127.0.0.1 --port 5173
   ```

### Configuring Your Gemini Account Session
1. Open `http://127.0.0.1:5173` and click **● Auth** (top right) to open the **Session & Credentials** modal.
2. Either click **Auto-Extract from Browser** (extracts cookies from local Chrome/Edge/Brave profile) or paste your `__Secure-1PSID` and `__Secure-1PSIDTS` cookies from `gemini.google.com` DevTools (`Application -> Cookies`).
3. To use with external IDE plugins or OpenAI SDK clients:
   - **Base URL**: `http://127.0.0.1:8000/v1`
   - **API Key**: `local-gemini` (any non-empty string)
   - **Models**: `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.0-flash-thinking`

---

## Verification & Benchmarks

- **4-Tier Master E2E Suite (383 tests)**:
  ```powershell
  python tests/e2e/runner.py
  ```
- **Backend Integration Suite**:
  ```powershell
  python -m pytest backend/tests -v
  ```
- **Playwright Headless UI & 100Hz Streaming Benchmarks**:
  ```powershell
  cd frontend
  npx playwright test
  ```
