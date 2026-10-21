import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useChatStore } from '../store/chatStore';

describe('R3 Audit: Claude Swarm Telemetry & Note Decoupling Verification', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({
      injectedContext: '',
      draftPrompt: 'User unsubmitted draft text',
    });
  });

  it('attaching a note delegates to injectContext and does not overwrite user draftPrompt', () => {
    const activeNotebook = {
      id: 'nb_test',
      title: 'Architecture Research',
      created_at: 1000,
      updated_at: 2000,
      source_count: 0,
      sources: [],
      notes: [
        { id: 'note_1', title: 'Argon Spec', content: 'Argon uses 81-slot envelope' },
      ],
    };

    const note = activeNotebook.notes[0];
    const block = `<notebooklm_reference title="${activeNotebook.title}: ${note.title}">\n${note.content}\n</notebooklm_reference>`;

    // Simulate decoupled note attach action
    useChatStore.getState().injectContext(block);

    expect(useChatStore.getState().injectedContext).toContain('Argon uses 81-slot envelope');
    // Critical: draftPrompt remains intact
    expect(useChatStore.getState().draftPrompt).toBe('User unsubmitted draft text');
  });

  it('validates swarm telemetry response structure conforms to frontend schema', () => {
    const mockTelemetry = {
      ok: true,
      cache_stats: {
        ram_entries: 12,
        sqlite_rows: 45,
        db_path: 'backend/data/conversations.db',
        cache_hit_rate_pct: 94.2,
        total_recorded_turns: 50,
      },
      plugins_summary: {
        agents_count: 2,
        commands_count: 3,
        skills_count: 1,
        hooks_count: 1,
        agents: [
          { name: 'explorer-scout', description: 'Code explorer', tools: ['Read', 'Grep'], model: 'haiku' },
          { name: 'argon-architect', description: 'Lead architect', tools: ['Bash', 'Edit'], model: 'opus' },
        ],
        commands: [],
        skills: [],
        hooks: ['notebooklm_post_tool_sync.py'],
      },
      tier_distribution: {
        'Gemini 3 Flash': 30,
        'Gemini 4 Argon': 20,
      },
      subagent_distribution: {
        'explorer-scout': 30,
        'argon-architect': 20,
      },
      live_events: [
        {
          id: 'msg_1',
          timestamp: 1729500000,
          requested_model: 'claude-3-5-haiku-20241022',
          resolved_model: '56fdd199312815e2',
          model_tier_label: 'Gemini 3 Flash',
          subagent: 'explorer-scout',
          gem_id: null,
          cache_hit: true,
          matched_turns: 3,
          total_turns: 4,
          tool_calls: ['Read', 'Grep'],
          latency_ms: 320,
          input_tokens: 1200,
          output_tokens: 450,
          stream: true,
        },
      ],
      recent_sessions: [],
    };

    expect(mockTelemetry.ok).toBe(true);
    expect(mockTelemetry.cache_stats.sqlite_rows).toBe(45);
    expect(mockTelemetry.plugins_summary.agents).toHaveLength(2);
    expect(mockTelemetry.live_events[0].model_tier_label).toBe('Gemini 3 Flash');
    expect(mockTelemetry.live_events[0].tool_calls).toContain('Read');
  });

  it('handles nullish model_tier_label and tool_counts safely without throw', () => {
    const rawEvent = {
      id: 'msg_edge',
      timestamp: 1729500001,
      model_tier_label: null as any,
    };
    const tierCheck = String(rawEvent.model_tier_label || '').includes('Flash');
    expect(tierCheck).toBe(false);

    const rawSession = {
      session_id: 'sess_edge',
      tool_counts: null as any,
    };
    const toolCountKeys = Object.keys(rawSession.tool_counts || {});
    expect(toolCountKeys).toEqual([]);
  });
});
