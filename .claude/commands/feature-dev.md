---
name: feature-dev
description: Official Anthropic 7-phase guided feature development workflow adapted for Gemini 3 Flash (code-explorer) and Gemini 4 Argon Task Mode (code-architect & code-reviewer).
argument-hint: "[feature description]"
---

# `/feature-dev` — Guided Architectural Feature Development

Implement **$ARGUMENTS** using Anthropic's `feature-dev` multi-agent workflow adapted for our `Flash` + `Argon` hybrid gateway:

## Phase 1: Discovery & Grounding
1. Check existing specifications in NotebookLM via `mcp__gemini-local__notebooklm_query` (`notebook_id="nb_2f3e4d5c-6b7a-8c9d-0e1f-2a3b4c5d6e7f"`).
2. State the problem, scope, and constraints clearly.

## Phase 2: Parallel Codebase Exploration (`Gemini 3 Flash`)
1. Launch 1–2 `code-explorer` subagents (`subagent_type="code-explorer"`) to trace execution paths, map abstraction layers, and return the 5–10 essential files with `file:line` references.
2. Read the essential files identified by `code-explorer`.

## Phase 3: Architecture Design (`Gemini 4 Argon Task Mode`)
1. Launch `code-architect` (`subagent_type="code-architect"`) to design the concrete implementation blueprint, component interfaces, data flow, and phased EARS build sequence (`WHEN ... THE SYSTEM SHALL ...` + `Verify:` command).
2. Save the blueprint to `./blueprints/<feature-slug>-blueprint.md`.

## Phase 4: Implementation
1. Implement the feature following the blueprint and `CLAUDE.md` conventions.
2. Add or update automated tests (`pytest` / `vitest`).

## Phase 5: Quality & Architecture Review (`Gemini 4 Argon`)
1. Launch `code-reviewer` (`subagent_type="code-reviewer"`) to audit the changes and run confidence scoring (`0–100`, reporting issues `>= 80`).
2. Remediate any high-confidence findings and verify tests pass.

## Phase 6: Grounded Documentation Sync
1. Call `mcp__gemini-local__notebooklm_sync_repo` to sync updated docs and blueprints into NotebookLM.
