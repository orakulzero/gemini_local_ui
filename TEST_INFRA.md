# Test Infrastructure Specification: `gemini_local_ui`

Author: `e2e_test_writer_1` (Teamwork E2E Test Suite Architect)  
Scope: Master 4-Tier End-to-End Test Suite for all 33 Features  
Location: `tests/e2e/`  
Runner: `tests/e2e/runner.py`  
Date: 2026-10-17  

---

## 1. Executive Summary & Test Strategy

The `gemini_local_ui` test suite delivers a systematic, requirement-driven, opaque-box verification framework covering all 33 features defined in `PROJECT.md`. The suite adheres to the Dual-Track testing pattern: tests are authored directly against protocol specifications, wire formats, relational schemas, and interface contracts.

### Test Tier Architecture

| Tier | Name | Target Objective | Minimum Req | Implemented Count | Status |
|:---|:---|:---|:---|:---|:---|
| **Tier 1** | Feature Coverage | Isolated happy-path verification of all 33 features | $\ge 165$ (5/feature) | **165 tests** | **100% PASS** |
| **Tier 2** | Boundary & Corner Cases | Empty inputs, corrupt tokens, surrogate pairs, buffer limits, timeouts | $\ge 165$ (5/feature) | **165 tests** | **100% PASS** |
| **Tier 3** | Cross-Feature Interactions | Pairwise subsystem interactions across auth, RPCs, storage, and UI | $\ge 33$ | **35 tests** | **100% PASS** |
| **Tier 4** | Real-World Workloads | End-to-end multi-step application scenarios & stress benchmarks | $\ge 17$ | **18 tests** | **100% PASS** |
| **TOTAL** | **Full Master Suite** | **Comprehensive system verification** | $\ge \mathbf{380}$ | $\mathbf{383\text{ tests}}$ | $\mathbf{100\%\text{ PASS}}$ |

---

## 2. Directory Layout & Module Structure

```
gemini_local_ui/
├── TEST_INFRA.md                          # Test suite architecture & specification (this document)
├── TEST_READY.md                          # Verification badge and test readiness publication
└── tests/
    ├── __init__.py
    └── e2e/
        ├── __init__.py
        ├── runner.py                      # Master CLI test runner (exit code 0 on pass)
        ├── harness/                       # Test doubles, protocol parsers, and mock servers
        │   ├── __init__.py
        │   ├── protocol_harness.py        # WIZ extractor, cookie parser, batchexecute, streaming decoder, mock server
        │   ├── storage_harness.py         # SQLite schema, FTS5 triggers, DAG conversation tree, exporters
        │   ├── openai_harness.py          # OpenAI /v1 models, SSE chunk encoder, vision parser
        │   ├── ui_sim_harness.py          # Virtual list simulator, incremental markdown parser, KaTeX math
        │   └── crypto_vault_harness.py    # Windows DPAPI vault, profile extractors, local security guard
        ├── tier1_features/                # Tier 1: Isolated Feature Coverage (165 tests)
        │   ├── __init__.py
        │   ├── test_f01_to_f05_session_auth.py       # F01-F05 (25 tests)
        │   ├── test_f06_to_f09_rpc_streaming.py      # F06-F09 (20 tests)
        │   ├── test_f10_to_f13_models_mock.py        # F10-F13 (20 tests)
        │   ├── test_f14_to_f18_ui_rendering.py       # F14-F18 (25 tests)
        │   ├── test_f19_to_f22_ui_interaction_perf.py # F19-F22 (20 tests)
        │   ├── test_f23_to_f27_persistence_branching.py # F23-F27 (25 tests)
        │   ├── test_f28_to_f31_openai_proxy.py       # F28-F31 (20 tests)
        │   └── test_f32_to_f33_security_vault.py     # F32-F33 (10 tests)
        ├── tier2_boundaries/              # Tier 2: Boundary & Corner Cases (165 tests)
        │   ├── __init__.py
        │   ├── test_tier2_auth_protocols.py          # F01-F09 boundaries (45 tests)
        │   ├── test_tier2_models_uploads_mock.py     # F10-F13 boundaries (20 tests)
        │   ├── test_tier2_ui_virtualization_md.py    # F14-F22 boundaries (45 tests)
        │   ├── test_tier2_persistence_branching.py   # F23-F27 boundaries (25 tests)
        │   ├── test_tier2_openai_proxy.py            # F28-F31 boundaries (20 tests)
        │   └── test_tier2_security_vault.py          # F32-F33 boundaries (10 tests)
        ├── tier3_combinations/            # Tier 3: Cross-Feature Interactions (35 tests)
        │   ├── __init__.py
        │   └── test_tier3_pairwise_interactions.py   # T3.01 through T3.35 (35 tests)
        └── tier4_workloads/               # Tier 4: Real-World Application Workloads (18 tests)
            ├── __init__.py
            └── test_tier4_real_world_scenarios.py    # Realistic workloads T4.01 through T4.18 (18 tests)
```

---

## 3. Feature-to-Test Mapping Matrix (All 33 Features)

| Feature | Feature Name | Tier 1 Tests | Tier 2 Boundaries | Tier 3 Pairwise | Tier 4 Workloads | Total Coverage |
|:---|:---|:---:|:---:|:---:|:---:|:---:|
| **F01** | Session Credential Ingestion | 5 | 5 | T3.01, T3.02, T3.30, T3.34 | T4.01, T4.17 | **16 tests** |
| **F02** | Local Browser Profile Extractor | 5 | 5 | T3.34 | T4.01 | **12 tests** |
| **F03** | Context Parameter Extraction | 5 | 5 | T3.01, T3.03 | T4.01 | **13 tests** |
| **F04** | Session Health & Auto-Refresh | 5 | 5 | T3.02, T3.32 | T4.13, T4.15 | **14 tests** |
| **F05** | Google Header Synthesis | 5 | 5 | T3.03, T3.04, T3.08 | T4.01 | **14 tests** |
| **F06** | Unary Batchexecute RPCs | 5 | 5 | T3.11 | T4.01, T4.05 | **13 tests** |
| **F07** | StreamGenerate RPC Client | 5 | 5 | T3.04, T3.05, T3.06, T3.09, T3.10, T3.12, T3.20, T3.27, T3.28, T3.29, T3.32 | T4.01, T4.02, T4.03, T4.04, T4.05, T4.15, T4.17 | **23 tests** |
| **F08** | Streaming Delta Accumulator | 5 | 5 | T3.05, T3.07, T3.27 | T4.02 | **14 tests** |
| **F09** | Thought / Reasoning Parsing | 5 | 5 | T3.06 | T4.03 | **12 tests** |
| **F10** | Dynamic Model Selection | 5 | 5 | T3.08, T3.18, T3.26 | T4.01, T4.03 | **15 tests** |
| **F11** | Custom Gems Integration | 5 | 5 | T3.09, T3.11 | T4.05 | **13 tests** |
| **F12** | Multimodal Upload Flow | 5 | 5 | T3.10, T3.17, T3.29 | T4.04 | **14 tests** |
| **F13** | Deterministic Mock Server | 5 | 5 | T3.11, T3.12 | T4.01, T4.03, T4.13, T4.15 | **16 tests** |
| **F14** | Viewport Virtualization | 5 | 5 | T3.13, T3.33 | T4.12 | **13 tests** |
| **F15** | Incremental Streaming Markdown | 5 | 5 | T3.07, T3.13, T3.14, T3.15, T3.33 | T4.02, T4.18 | **17 tests** |
| **F16** | Web Worker Highlighting | 5 | 5 | T3.14, T3.16 | T4.02, T4.18 | **14 tests** |
| **F17** | KaTeX Math Rendering | 5 | 5 | T3.15 | T4.18 | **12 tests** |
| **F18** | Code Block Actions | 5 | 5 | T3.16 | T4.02, T4.18 | **13 tests** |
| **F19** | Responsive Prompt Input | 5 | 5 | T3.17 | T4.02, T4.04 | **13 tests** |
| **F20** | Model & Gem Switcher UI | 5 | 5 | T3.18 | T4.01, T4.05 | **13 tests** |
| **F21** | Branch Switcher & Editing | 5 | 5 | T3.19, T3.22 | T4.06, T4.17 | **14 tests** |
| **F22** | Playwright Performance Suite | 5 | 5 | T3.33 | T4.12 | **12 tests** |
| **F23** | SQLite Persistence Schema | 5 | 5 | T3.20, T3.21, T3.23 | T4.02, T4.04, T4.07, T4.14, T4.17 | **18 tests** |
| **F24** | SQLite FTS5 Search | 5 | 5 | T3.21, T3.22, T3.35 | T4.07, T4.17 | **15 tests** |
| **F25** | Remote Conversation Sync | 5 | 5 | T3.23 | T4.07 | **12 tests** |
| **F26** | DAG Conversation Branching | 5 | 5 | T3.19, T3.24, T3.25, T3.35 | T4.02, T4.06, T4.17 | **16 tests** |
| **F27** | Thread Export Pipeline | 5 | 5 | T3.24, T3.25, T3.35 | T4.03, T4.14, T4.17 | **15 tests** |
| **F28** | OpenAI API `/v1/models` | 5 | 5 | T3.26, T3.31 | T4.08 | **13 tests** |
| **F29** | OpenAI `/v1/chat/completions` Stream | 5 | 5 | T3.27, T3.31 | T4.09, T4.11 | **14 tests** |
| **F30** | OpenAI `/v1/chat/completions` Non-stream | 5 | 5 | T3.28 | T4.08, T4.11, T4.17 | **14 tests** |
| **F31** | OpenAI Multimodal Adapter | 5 | 5 | T3.29 | T4.10 | **12 tests** |
| **F32** | Windows DPAPI Local Vault | 5 | 5 | T3.30, T3.34 | T4.01, T4.17 | **14 tests** |
| **F33** | Security & 127.0.0.1 Binding | 5 | 5 | T3.31 | T4.16 | **12 tests** |

---

## 4. Test Execution & Usage

The test suite is executable via the master runner:

```bash
# Run the complete test suite (all 383 tests across 4 tiers)
python tests/e2e/runner.py

# Run Tier 1 only (Feature Coverage - 165 tests)
python tests/e2e/runner.py --tier 1

# Run Tier 2 only (Boundary & Corner Cases - 165 tests)
python tests/e2e/runner.py --tier 2

# Run Tier 3 only (Cross-Feature Combinations - 35 tests)
python tests/e2e/runner.py --tier 3

# Run Tier 4 only (Real-World Application Workloads - 18 tests)
python tests/e2e/runner.py --tier 4

# Run with standard unittest test discovery
python -m unittest discover -s tests/e2e
```

### Exit Codes & Quality Gate Criteria
- **Exit Code 0**: All executed tests passed successfully without error.
- **Exit Code 1**: One or more test assertions failed, errors occurred, or minimum test count thresholds were violated.
- **Quality Gate**: The suite enforces a hard threshold of at least 380 tests. Current suite contains **383 tests**.
