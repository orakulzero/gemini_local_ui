import React, { useState, useEffect, useCallback } from 'react';

export interface ClaudeAgent {
  id?: string;
  name: string;
  description: string;
  prompt?: string;
  system_prompt?: string;
  tools: string[] | string;
  model: string;
  feature_mode?: number;
}

function normalizeTools(tools: string[] | string | undefined | null): string[] {
  if (!tools) return [];
  if (Array.isArray(tools)) return tools.map((t) => String(t).trim()).filter(Boolean);
  return String(tools)
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean);
}

export interface ClaudeCommand {
  name: string;
  description: string;
  prompt: string;
}

export interface ClaudeSkill {
  name: string;
  description: string;
  prompt: string;
}

export interface SwarmLiveEvent {
  id: string;
  timestamp: number;
  requested_model: string;
  resolved_model: string;
  model_tier_label: string;
  subagent: string;
  gem_id?: string | null;
  cache_hit: boolean;
  matched_turns: number;
  total_turns: number;
  tool_calls: string[];
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  stream: boolean;
}

export interface ClaudeSessionSummary {
  session_id: string;
  project: string;
  updated_at: number;
  size_bytes: number;
  turn_count: number;
  subagents_count: number;
  tool_counts: Record<string, number>;
  last_prompt_preview: string;
}

export interface SwarmTelemetryResponse {
  ok: boolean;
  cache_stats: {
    ram_entries: number;
    sqlite_rows: number;
    db_path: string | null;
    cache_hit_rate_pct: number;
    total_recorded_turns: number;
  };
  plugins_summary: {
    agents_count: number;
    commands_count: number;
    skills_count: number;
    hooks_count: number;
    agents: ClaudeAgent[];
    commands: ClaudeCommand[];
    skills: ClaudeSkill[];
    hooks: string[];
  };
  tier_distribution: Record<string, number>;
  subagent_distribution: Record<string, number>;
  live_events: SwarmLiveEvent[];
  recent_sessions: ClaudeSessionSummary[];
}

export interface ClaudePluginsPanelProps {
  isOpen?: boolean;
  onClose?: () => void;
}

export const ClaudePluginsPanel: React.FC<ClaudePluginsPanelProps> = ({
  isOpen = true,
  onClose,
}) => {
  const [activeTab, setActiveTab] = useState<'swarm' | 'plugins' | 'installer'>('swarm');
  const [telemetry, setTelemetry] = useState<SwarmTelemetryResponse | null>(null);
  const [agents, setAgents] = useState<ClaudeAgent[]>([]);
  const [commands, setCommands] = useState<ClaudeCommand[]>([]);
  const [skills, setSkills] = useState<ClaudeSkill[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

  // New Agent Form State
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [newPrompt, setNewPrompt] = useState('');
  const [newTools, setNewTools] = useState('Read, Grep, Glob, Bash');
  const [newModel, setNewModel] = useState('haiku');

  // Workspace Installer State
  const [targetDir, setTargetDir] = useState('');
  const [includeMcpConfig, setIncludeMcpConfig] = useState(true);
  const [overwriteFiles, setOverwriteFiles] = useState(true);
  const [gatewayUrl, setGatewayUrl] = useState('http://127.0.0.1:8000');
  const [installResult, setInstallResult] = useState<{
    target_dir: string;
    counts: Record<string, number>;
    installed_files: string[];
  } | null>(null);

  const fetchAllData = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const telemetryRes = await fetch('/api/claude-plugins/swarm-telemetry');
      if (telemetryRes.ok) {
        const tData: SwarmTelemetryResponse = await telemetryRes.json();
        setTelemetry(tData);
        if (tData.plugins_summary) {
          setAgents(tData.plugins_summary.agents || []);
          setCommands(tData.plugins_summary.commands || []);
          setSkills(tData.plugins_summary.skills || []);
        }
      }
    } catch (err) {
      console.error('Failed to fetch Claude Swarm telemetry:', err);
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    fetchAllData();
    const timer = setInterval(() => {
      fetchAllData(true);
    }, 4000);
    return () => clearInterval(timer);
  }, [isOpen, fetchAllData]);

  if (!isOpen) return null;

  const handleSyncGems = async () => {
    setSyncMessage('Syncing .claude/agents with Custom Gems...');
    try {
      const res = await fetch('/api/claude-plugins/sync-gems', { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        setSyncMessage(`Synced ${data.synced_count} subagents to Gemini Custom Gems`);
      } else {
        setSyncMessage('Failed to sync Custom Gems.');
      }
    } catch {
      setSyncMessage('Error syncing Custom Gems.');
    }
  };

  const handleSyncRepoNotebookLM = async () => {
    setSyncMessage('Syncing repository docs with NotebookLM (SHA-256 deduplicated)...');
    try {
      const res = await fetch('/api/notebooklm/sync-repo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          notebook_id: 'nb_2f3e4d5c-6b7a-8c9d-0e1f-2a3b4c5d6e7f',
          generate_briefing: true,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        setSyncMessage(
          `NotebookLM Repo Sync complete: ${data.synced_count} updated, ${data.skipped_count} unchanged`
        );
      } else {
        setSyncMessage('NotebookLM Repo Sync failed.');
      }
    } catch {
      setSyncMessage('Error calling /api/notebooklm/sync-repo.');
    }
  };

  const handleCreateAgent = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim()) return;
    const toolsList = newTools
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean);
    try {
      const res = await fetch('/api/claude-plugins/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newName.trim(),
          description: newDesc.trim(),
          prompt: newPrompt.trim(),
          tools: toolsList,
          model: newModel,
        }),
      });
      if (res.ok) {
        setNewName('');
        setNewDesc('');
        setNewPrompt('');
        fetchAllData();
      }
    } catch (err) {
      console.error('Error creating agent:', err);
    }
  };

  const handleDeleteAgent = async (name: string) => {
    try {
      const res = await fetch(`/api/claude-plugins/agents/${encodeURIComponent(name)}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        fetchAllData();
      }
    } catch (err) {
      console.error('Error deleting agent:', err);
    }
  };

  const handleInstallWorkspace = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!targetDir.trim()) return;
    setSyncMessage(`Installing .claude workspace pack into ${targetDir.trim()}...`);
    try {
      const res = await fetch('/api/claude-plugins/init-workspace', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_dir: targetDir.trim(),
          include_mcp_config: includeMcpConfig,
          overwrite: overwriteFiles,
          gateway_url: gatewayUrl.trim() || 'http://127.0.0.1:8000',
        }),
      });
      const data = await res.json();
      if (res.ok && data.ok) {
        setInstallResult(data);
        setSyncMessage(`Workspace pack installed into ${data.target_dir}`);
      } else {
        setSyncMessage(`Error: ${data.detail || 'Failed installing workspace pack'}`);
      }
    } catch (err) {
      setSyncMessage(`Error installing workspace pack: ${err}`);
    }
  };

  const cacheStats = telemetry?.cache_stats;
  const liveEvents = telemetry?.live_events || [];
  const recentSessions = telemetry?.recent_sessions || [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4"
      data-testid="claude-swarm-modal"
    >
      <div className="bg-zinc-950 border border-zinc-800 rounded-xl shadow-2xl w-full max-w-5xl max-h-[90vh] flex flex-col overflow-hidden text-zinc-100">
        {/* Header */}
        <div className="px-5 py-3.5 border-b border-zinc-800 flex items-center justify-between bg-zinc-900/70">
          <div className="flex items-center gap-3">
            <span className="text-lg">🤖</span>
            <div>
              <h2 className="text-base font-semibold text-zinc-100 flex items-center gap-2">
                Claude Code Swarm Monitor & Plugin Hub
                <span className="text-[11px] px-2 py-0.5 rounded-full bg-emerald-950/80 text-emerald-300 border border-emerald-700/50 font-mono">
                  Anthropic v1 + MCP Active
                </span>
              </h2>
              <p className="text-xs text-zinc-400">
                Subagent Auto-Routing (Flash / Argon) • Persistent SQLite Prefix Cache • Incremental NotebookLM RAG
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => fetchAllData()}
              className="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs transition-colors"
              data-testid="refresh-swarm-btn"
            >
              ↻ Refresh
            </button>
            <button
              type="button"
              onClick={handleSyncGems}
              className="px-2.5 py-1 rounded-lg bg-indigo-600/80 hover:bg-indigo-600 text-white text-xs font-medium transition-colors"
              data-testid="sync-gems-btn"
            >
              💎 Sync Gems
            </button>
            <button
              type="button"
              onClick={handleSyncRepoNotebookLM}
              className="px-2.5 py-1 rounded-lg bg-amber-600/80 hover:bg-amber-600 text-white text-xs font-medium transition-colors"
              data-testid="sync-repo-rag-btn"
            >
              📚 Sync Repo RAG
            </button>
            {onClose && (
              <button
                type="button"
                onClick={onClose}
                className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 transition-colors text-sm"
                data-testid="close-claude-swarm-btn"
                aria-label="Close Swarm Monitor"
              >
                ✕
              </button>
            )}
          </div>
        </div>

        {/* Status Toast */}
        {syncMessage && (
          <div
            className="px-5 py-2 bg-indigo-950/60 border-b border-indigo-800/50 text-indigo-200 text-xs flex items-center justify-between"
            data-testid="swarm-status-banner"
          >
            <span>{syncMessage}</span>
            <button
              type="button"
              onClick={() => setSyncMessage(null)}
              className="text-indigo-400 hover:text-indigo-200 text-xs"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Navigation Tabs */}
        <div className="px-5 pt-2.5 border-b border-zinc-800 flex items-center gap-2 bg-zinc-900/40">
          <button
            type="button"
            data-testid="tab-swarm-monitor"
            onClick={() => setActiveTab('swarm')}
            className={`px-3.5 py-2 text-xs font-medium rounded-t-lg border-b-2 transition-colors ${
              activeTab === 'swarm'
                ? 'border-blue-500 text-blue-400 bg-zinc-900'
                : 'border-transparent text-zinc-400 hover:text-zinc-200'
            }`}
          >
            ⚡ Live Swarm Monitor ({liveEvents.length})
          </button>
          <button
            type="button"
            data-testid="tab-plugins-hub"
            onClick={() => setActiveTab('plugins')}
            className={`px-3.5 py-2 text-xs font-medium rounded-t-lg border-b-2 transition-colors ${
              activeTab === 'plugins'
                ? 'border-blue-500 text-blue-400 bg-zinc-900'
                : 'border-transparent text-zinc-400 hover:text-zinc-200'
            }`}
          >
            🧩 Agents ({agents.length}), Skills ({skills.length}) & Commands ({commands.length})
          </button>
          <button
            type="button"
            data-testid="tab-workspace-installer"
            onClick={() => setActiveTab('installer')}
            className={`px-3.5 py-2 text-xs font-medium rounded-t-lg border-b-2 transition-colors ${
              activeTab === 'installer'
                ? 'border-blue-500 text-blue-400 bg-zinc-900'
                : 'border-transparent text-zinc-400 hover:text-zinc-200'
            }`}
          >
            📦 1-Click Workspace Pack Installer
          </button>
        </div>

        {/* Body Content */}
        <div className="p-5 overflow-y-auto flex-1 space-y-5">
          {loading && !telemetry ? (
            <div className="text-center py-8 text-zinc-400 text-sm">
              Loading Claude Code Swarm telemetry...
            </div>
          ) : null}

          {activeTab === 'swarm' && (
            <div className="space-y-5" data-testid="swarm-monitor-view">
              {/* Top KPI Grid */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div className="p-3.5 rounded-lg bg-zinc-900/80 border border-zinc-800">
                  <div className="text-xs text-zinc-400">SQLite Prefix Cache (M19)</div>
                  <div className="mt-1 flex items-baseline gap-2">
                    <span className="text-xl font-bold text-emerald-400" data-testid="sqlite-cache-rows">
                      {cacheStats?.sqlite_rows ?? 0}
                    </span>
                    <span className="text-xs text-zinc-400">
                      SQLite rows ({cacheStats?.ram_entries ?? 0} in RAM LRU)
                    </span>
                  </div>
                  <div className="mt-1 text-[11px] text-zinc-500 font-mono truncate">
                    Hit Rate: {cacheStats?.cache_hit_rate_pct ?? 100}% • WAL persistent
                  </div>
                </div>

                <div className="p-3.5 rounded-lg bg-zinc-900/80 border border-zinc-800">
                  <div className="text-xs text-zinc-400">Subagent Tier Routing</div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {Object.entries(telemetry?.tier_distribution || {}).length > 0 ? (
                      Object.entries(telemetry?.tier_distribution || {}).map(([tier, count]) => (
                        <span
                          key={tier}
                          className={`text-xs px-2 py-0.5 rounded font-mono ${
                            String(tier || '').includes('Flash')
                              ? 'bg-emerald-950 text-emerald-300 border border-emerald-700/50'
                              : 'bg-purple-950 text-purple-300 border border-purple-700/50'
                          }`}
                        >
                          {tier}: {count}
                        </span>
                      ))
                    ) : (
                      <span className="text-xs text-zinc-400">
                        haiku → Flash (56fdd199312815e2) • opus → Argon (95a4c0abcba24364)
                      </span>
                    )}
                  </div>
                  <div className="mt-1.5 text-[11px] text-zinc-500">
                    Auto-matches .claude/agents/*.md prompts on /v1/messages
                  </div>
                </div>

                <div className="p-3.5 rounded-lg bg-zinc-900/80 border border-zinc-800">
                  <div className="text-xs text-zinc-400">Active Workspace Pack</div>
                  <div className="mt-1 flex items-center gap-3 text-sm font-semibold text-zinc-200">
                    <span>{agents.length} Agents</span>
                    <span>•</span>
                    <span>{skills.length} Skills</span>
                    <span>•</span>
                    <span>{commands.length} Cmds</span>
                  </div>
                  <div className="mt-1 text-[11px] text-amber-400 font-mono">
                    Hooks: {telemetry?.plugins_summary?.hooks?.join(', ') || 'notebooklm_post_tool_sync.py'}
                  </div>
                </div>
              </div>

              {/* Live /v1/messages Gateway Stream */}
              <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
                <div className="px-4 py-2.5 border-b border-zinc-800 flex items-center justify-between bg-zinc-900">
                  <span className="text-xs font-semibold text-zinc-200">
                    Live Gateway Request Feed (/v1/messages)
                  </span>
                  <span className="text-[11px] text-zinc-400 font-mono">
                    Showing last {liveEvents.length} turns
                  </span>
                </div>
                {liveEvents.length === 0 ? (
                  <div className="p-4 text-xs text-zinc-400">
                    No live /v1/messages turns recorded in this session yet. Run a task in Claude Code to see real-time subagent & model routing.
                  </div>
                ) : (
                  <div className="divide-y divide-zinc-800/70 max-h-60 overflow-y-auto">
                    {liveEvents.map((ev, evIdx) => (
                      <div
                        key={`${ev.id}-${ev.timestamp}-${evIdx}`}
                        className="px-4 py-2.5 flex flex-wrap items-center justify-between gap-2 text-xs hover:bg-zinc-800/40"
                        data-testid="swarm-event-row"
                      >
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-[11px] px-2 py-0.5 rounded bg-blue-950/80 text-blue-300 border border-blue-800/60">
                            {ev.subagent}
                          </span>
                          <span
                            className={`font-mono text-[11px] px-2 py-0.5 rounded ${
                              String(ev.model_tier_label || '').includes('Flash')
                                ? 'bg-emerald-950/80 text-emerald-300 border border-emerald-800/60'
                                : 'bg-purple-950/80 text-purple-300 border border-purple-800/60'
                            }`}
                          >
                            {ev.model_tier_label}
                          </span>
                          <span
                            className={`font-mono text-[10px] px-1.5 py-0.5 rounded ${
                              ev.cache_hit
                                ? 'bg-amber-950/80 text-amber-300 border border-amber-800/60'
                                : 'bg-zinc-800 text-zinc-400'
                            }`}
                          >
                            {ev.cache_hit
                              ? `CACHE HIT (${ev.matched_turns}/${ev.total_turns})`
                              : `NEW (${ev.total_turns} turns)`}
                          </span>
                        </div>

                        <div className="flex items-center gap-2">
                          {ev.tool_calls && ev.tool_calls.length > 0 && (
                            <div className="flex flex-wrap gap-1">
                              {ev.tool_calls.map((t, idx) => (
                                <span
                                  key={`${ev.id}-t-${idx}`}
                                  className="px-1.5 py-0.5 rounded bg-zinc-800 text-amber-300 font-mono text-[10px]"
                                >
                                  🛠 {t}
                                </span>
                              ))}
                            </div>
                          )}
                          <span className="text-[11px] text-zinc-400 font-mono">
                            {ev.latency_ms}ms
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Recent Claude Code Sessions from ~/.claude/projects */}
              <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
                <div className="px-4 py-2.5 border-b border-zinc-800 flex items-center justify-between bg-zinc-900">
                  <span className="text-xs font-semibold text-zinc-200">
                    Recent Claude Code Sessions (~/.claude/projects)
                  </span>
                  <span className="text-[11px] text-zinc-400 font-mono">
                    {recentSessions.length} JSONL transcripts discovered
                  </span>
                </div>
                {recentSessions.length === 0 ? (
                  <div className="p-4 text-xs text-zinc-400">
                    No Claude Code project transcripts found.
                  </div>
                ) : (
                  <div className="divide-y divide-zinc-800/70 max-h-64 overflow-y-auto">
                    {recentSessions.map((sess) => (
                      <div
                        key={sess.session_id}
                        className="px-4 py-2.5 space-y-1 hover:bg-zinc-800/40"
                        data-testid="claude-session-row"
                      >
                        <div className="flex items-center justify-between text-xs">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-blue-400 font-medium">
                              {sess.session_id.slice(0, 12)}…
                            </span>
                            <span className="text-zinc-400">
                              {sess.turn_count} turns
                            </span>
                            {sess.subagents_count > 0 && (
                              <span className="px-1.5 py-0.5 rounded bg-purple-950/80 text-purple-300 border border-purple-800/60 font-mono text-[10px]">
                                {sess.subagents_count} subagents spawned
                              </span>
                            )}
                          </div>
                          <span className="text-[11px] text-zinc-500 font-mono">
                            {(sess.size_bytes / 1024).toFixed(1)} KB
                          </span>
                        </div>
                        {sess.last_prompt_preview && (
                          <div className="text-xs text-zinc-300 truncate">
                            “{sess.last_prompt_preview}”
                          </div>
                        )}
                        {Object.keys(sess.tool_counts || {}).length > 0 && (
                          <div className="flex flex-wrap gap-1 pt-0.5">
                            {Object.entries(sess.tool_counts || {}).map(([tname, cnt]) => (
                              <span
                                key={tname}
                                className="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 font-mono text-[10px]"
                              >
                                {tname} ×{cnt}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {activeTab === 'plugins' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5" data-testid="plugins-hub-view">
              {/* Left Column: Agents + Create Agent Form */}
              <div className="space-y-4">
                <h3 className="text-sm font-semibold text-zinc-200 border-b border-zinc-800 pb-2">
                  Subagents (.claude/agents) — {agents.length}
                </h3>
                <div className="space-y-2.5 max-h-64 overflow-y-auto pr-1">
                  {agents.map((ag) => (
                    <div
                      key={ag.name}
                      className="p-3 rounded-lg bg-zinc-900 border border-zinc-800 flex items-start justify-between gap-2"
                    >
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-sm text-zinc-100">{ag.name}</span>
                          <span
                            className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                              ag.model === 'haiku' || ag.model === 'flash'
                                ? 'bg-emerald-950 text-emerald-300 border border-emerald-700/50'
                                : 'bg-purple-950 text-purple-300 border border-purple-700/50'
                            }`}
                          >
                            {ag.model} →{' '}
                            {ag.model === 'haiku' || ag.model === 'flash' ? 'Gemini 3 Flash' : 'Gemini 4 Argon'}
                          </span>
                        </div>
                        <p className="text-xs text-zinc-400 mt-1">{ag.description}</p>
                        {normalizeTools(ag.tools).length > 0 && (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {normalizeTools(ag.tools).map((t) => (
                              <span
                                key={t}
                                className="text-[10px] px-1.5 py-0.5 bg-zinc-800 text-zinc-300 rounded font-mono"
                              >
                                {t}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                      <button
                        type="button"
                        onClick={() => handleDeleteAgent(ag.name)}
                        className="text-xs text-red-400 hover:text-red-300 px-2 py-1 rounded hover:bg-red-950/40"
                      >
                        Delete
                      </button>
                    </div>
                  ))}
                </div>

                {/* Create Subagent Form */}
                <form
                  onSubmit={handleCreateAgent}
                  className="p-3.5 rounded-lg bg-zinc-900/70 border border-zinc-800 space-y-2.5"
                >
                  <h4 className="text-xs font-semibold text-zinc-200">Create New Subagent (.claude/agents)</h4>
                  <input
                    type="text"
                    name="agent_name"
                    aria-label="Subagent Slug"
                    placeholder="Subagent Slug (e.g. security-auditor)"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    className="w-full px-2.5 py-1.5 text-xs bg-zinc-950 border border-zinc-800 rounded text-zinc-100"
                    required
                  />
                  <input
                    type="text"
                    name="agent_description"
                    aria-label="Subagent Description"
                    placeholder="Description"
                    value={newDesc}
                    onChange={(e) => setNewDesc(e.target.value)}
                    className="w-full px-2.5 py-1.5 text-xs bg-zinc-950 border border-zinc-800 rounded text-zinc-100"
                  />
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      type="text"
                      name="agent_tools"
                      aria-label="Subagent Tools"
                      placeholder="Tools (comma-separated)"
                      value={newTools}
                      onChange={(e) => setNewTools(e.target.value)}
                      className="w-full px-2.5 py-1.5 text-xs bg-zinc-950 border border-zinc-800 rounded text-zinc-100"
                    />
                    <select
                      name="agent_model"
                      aria-label="Subagent Model Tier"
                      value={newModel}
                      onChange={(e) => setNewModel(e.target.value)}
                      className="w-full px-2.5 py-1.5 text-xs bg-zinc-950 border border-zinc-800 rounded text-zinc-100"
                    >
                      <option value="haiku">haiku (→ Gemini 3 Flash)</option>
                      <option value="sonnet">sonnet (→ Gemini 4 Argon)</option>
                      <option value="opus">opus (→ Gemini 4 Argon Task)</option>
                    </select>
                  </div>
                  <textarea
                    name="agent_prompt"
                    aria-label="System Prompt Instructions"
                    placeholder="System Prompt Instructions"
                    value={newPrompt}
                    onChange={(e) => setNewPrompt(e.target.value)}
                    className="w-full px-2.5 py-1.5 text-xs bg-zinc-950 border border-zinc-800 rounded text-zinc-100 h-20"
                  />
                  <button
                    type="submit"
                    className="w-full py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded text-xs font-medium transition-colors"
                  >
                    + Save Subagent
                  </button>
                </form>
              </div>

              {/* Right Column: Skills & Slash Commands */}
              <div className="space-y-4">
                <div>
                  <h3 className="text-sm font-semibold text-zinc-200 border-b border-zinc-800 pb-2 mb-2.5">
                    Native Skills (.claude/skills) — {skills.length}
                  </h3>
                  <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
                    {skills.map((sk) => (
                      <div key={sk.name} className="p-3 rounded-lg bg-zinc-900 border border-zinc-800">
                        <div className="font-semibold text-xs text-emerald-400 font-mono">
                          Skill: {sk.name}
                        </div>
                        <p className="text-xs text-zinc-400 mt-1">{sk.description}</p>
                      </div>
                    ))}
                  </div>
                </div>

                <div>
                  <h3 className="text-sm font-semibold text-zinc-200 border-b border-zinc-800 pb-2 mb-2.5">
                    Slash Commands (.claude/commands) — {commands.length}
                  </h3>
                  <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
                    {commands.map((cmd) => (
                      <div key={cmd.name} className="p-3 rounded-lg bg-zinc-900 border border-zinc-800">
                        <div className="font-semibold text-xs text-blue-400 font-mono">
                          /{cmd.name}
                        </div>
                        <p className="text-xs text-zinc-400 mt-1">{cmd.description}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'installer' && (
            <div className="space-y-4 max-w-2xl" data-testid="workspace-installer-view">
              <div className="p-4 rounded-lg bg-zinc-900 border border-zinc-800 space-y-3">
                <h3 className="text-sm font-semibold text-zinc-100">
                  📦 1-Click .claude Workspace Pack Installer
                </h3>
                <p className="text-xs text-zinc-400">
                  Export and install our complete Claude Code orchestration pack (`.claude/agents`, `.claude/skills`, `.claude/commands`, `.claude/hooks/notebooklm_post_tool_sync.py`, `.claude/settings.json`, and `.mcp.json`) into any local project folder.
                </p>

                <form onSubmit={handleInstallWorkspace} className="space-y-3 pt-1">
                  <div>
                    <label htmlFor="workspace-target-dir" className="block text-xs text-zinc-300 mb-1">
                      Target Project Directory Path
                    </label>
                    <input
                      id="workspace-target-dir"
                      name="target_dir"
                      type="text"
                      data-testid="workspace-target-dir-input"
                      placeholder="e.g. /path/to/your/project"
                      value={targetDir}
                      onChange={(e) => setTargetDir(e.target.value)}
                      className="w-full px-3 py-2 text-xs bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 font-mono"
                      required
                    />
                  </div>

                  <div>
                    <label htmlFor="workspace-gateway-url" className="block text-xs text-zinc-300 mb-1">
                      Local Gemini Gateway URL
                    </label>
                    <input
                      id="workspace-gateway-url"
                      name="gateway_url"
                      type="text"
                      value={gatewayUrl}
                      onChange={(e) => setGatewayUrl(e.target.value)}
                      className="w-full px-3 py-1.5 text-xs bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 font-mono"
                    />
                  </div>

                  <div className="flex items-center gap-5 text-xs text-zinc-300">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        name="include_mcp_config"
                        checked={includeMcpConfig}
                        onChange={(e) => setIncludeMcpConfig(e.target.checked)}
                      />
                      Generate `.mcp.json` (`gemini-local` HTTP server)
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        name="overwrite_files"
                        checked={overwriteFiles}
                        onChange={(e) => setOverwriteFiles(e.target.checked)}
                      />
                      Overwrite existing files
                    </label>
                  </div>

                  <button
                    type="submit"
                    data-testid="install-workspace-pack-btn"
                    className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-semibold transition-colors"
                  >
                    🚀 Install .claude Workspace Pack
                  </button>
                </form>
              </div>

              {installResult && (
                <div
                  className="p-4 rounded-lg bg-emerald-950/30 border border-emerald-800/60 space-y-2"
                  data-testid="workspace-install-result"
                >
                  <div className="text-xs font-semibold text-emerald-300">
                    ✅ Installed {installResult.installed_files.length} files into {installResult.target_dir}
                  </div>
                  <div className="text-[11px] text-zinc-300 font-mono">
                    Agents: {installResult.counts.agents} • Skills: {installResult.counts.skills} • Commands:{' '}
                    {installResult.counts.commands} • Hooks: {installResult.counts.hooks} • Configs:{' '}
                    {installResult.counts.configs}
                  </div>
                  <div className="max-h-36 overflow-y-auto text-[11px] font-mono text-zinc-400 bg-zinc-950/80 p-2 rounded border border-zinc-800">
                    {installResult.installed_files.map((f) => (
                      <div key={f}>{f}</div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
export default ClaudePluginsPanel;
