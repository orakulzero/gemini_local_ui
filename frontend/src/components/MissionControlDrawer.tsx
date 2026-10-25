import React, { useState, useEffect, useCallback } from 'react';

export interface MissionControlDrawerProps {
  isOpen: boolean;
  onClose: () => void;
}

interface TierInfo {
  id?: string;
  name?: string;
  status?: string;
  description?: string;
}

interface PluginInfo {
  name: string;
  dir: string;
  disabled: boolean;
  isGlobal: boolean;
  isBuiltin: boolean;
  skills: string[];
  agents: string[];
  rules: string[];
  hooks: boolean;
  mcp: string[];
  customizationLink: string;
}

interface OverviewPayload {
  ok: boolean;
  tiers: Record<string, TierInfo | string[]>;
  golden_bus: {
    slots: string[];
    slot_names: Record<string, string>;
    last_benchmark?: {
      avg_duration_ms: number;
      min_duration_ms: number;
      max_duration_ms: number;
      iterations: number;
    } | null;
  };
  plugins_count: number;
  pipelines: Record<
    string,
    {
      pipeline_id: string;
      phase: string;
      baseline_id: string;
      valid: boolean;
      updated_at: string;
    }
  >;
  brain_summary: {
    indexed_sessions?: number;
    results_count?: number;
    child_reports_count: number;
  };
}

export const MissionControlDrawer: React.FC<MissionControlDrawerProps> = ({
  isOpen,
  onClose,
}) => {
  const [activeTab, setActiveTab] = useState<
    'bus' | 'plugins' | 'iso9000' | 'brain' | 'diff-tour' | 'federation'
  >('bus');
  const [overview, setOverview] = useState<OverviewPayload | null>(null);
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [brainQuery, setBrainQuery] = useState('');
  const [brainResults, setBrainResults] = useState<
    Array<{ conversation_id: string; label?: string; reference: string; snippet: string }>
  >([]);
  const [childReports, setChildReports] = useState<Array<Record<string, unknown>>>([]);
  const [benchmarkResult, setBenchmarkResult] = useState<{
    avg_duration_ms: number;
    min_duration_ms: number;
    max_duration_ms: number;
    iterations: number;
  } | null>(null);
  const [isoPipelineId, setIsoPipelineId] = useState('M28-FLAGSHIP');
  const [isoPhase, setIsoPhase] = useState('SPEC');
  const [isoStatusMsg, setIsoStatusMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Diff-Tour State
  const [dropRevealingAsks, setDropRevealingAsks] = useState(false);
  const [diffTourPreview, setDiffTourPreview] = useState<string>('');
  const [diffTourLoading, setDiffTourLoading] = useState(false);

  // Plugin Drift State
  const [reconcileMsg, setReconcileMsg] = useState<string | null>(null);

  // Federation Wave State
  const [federationLoading, setFederationLoading] = useState(false);
  const [federationResult, setFederationResult] = useState<Record<string, unknown> | null>(null);

  const fetchOverview = useCallback(async () => {
    try {
      const res = await fetch('/api/mission-control/overview');
      if (res.ok) {
        const data = await res.json();
        setOverview(data);
        if (data.golden_bus?.last_benchmark) {
          setBenchmarkResult(data.golden_bus.last_benchmark);
        }
      }
    } catch {
      // ignore offline state during initial render
    }
  }, []);

  const fetchPlugins = useCallback(async () => {
    try {
      const res = await fetch('/api/mission-control/plugins');
      if (res.ok) {
        const data = await res.json();
        setPlugins(data.plugins || []);
      }
    } catch {
      // ignore offline
    }
  }, []);

  const fetchBrain = useCallback(async (q: string) => {
    try {
      const res = await fetch(`/api/mission-control/brain/search?q=${encodeURIComponent(q)}`);
      if (res.ok) {
        const data = await res.json();
        setBrainResults(data.results || []);
        setChildReports(data.child_reports || []);
      }
    } catch {
      // ignore offline
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      fetchOverview();
      fetchPlugins();
      fetchBrain('');
    }
  }, [isOpen, fetchOverview, fetchPlugins, fetchBrain]);

  const handleRunBenchmark = async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/mission-control/bus/benchmark', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ iterations: 50 }),
      });
      if (res.ok) {
        const data = await res.json();
        setBenchmarkResult(data);
        fetchOverview();
      }
    } finally {
      setLoading(false);
    }
  };

  const handleTogglePlugin = async (dir: string, enabled: boolean) => {
    const res = await fetch('/api/mission-control/plugins/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target: dir, enabled }),
    });
    if (res.ok) {
      fetchPlugins();
      fetchOverview();
    }
  };

  const handleReconcileDrift = async () => {
    setReconcileMsg(null);
    try {
      const res = await fetch('/api/mission-control/plugins/reconcile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_version: '1.1.0' }),
      });
      const data = await res.json();
      if (res.ok) {
        setReconcileMsg(`Reconciled ${data.reconciled_count} plugins to v1.1.0`);
        fetchPlugins();
        fetchOverview();
      } else {
        setReconcileMsg(`Error: ${data.detail || 'Reconciliation failed'}`);
      }
    } catch {
      setReconcileMsg('Reconciliation request failed');
    }
  };

  const handleAdvanceIsoGate = async () => {
    setIsoStatusMsg(null);
    const res = await fetch('/api/mission-control/iso9000/gate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pipeline_id: isoPipelineId,
        target_phase: isoPhase,
        baseline_id: 'BL-20261023-M28-L4',
      }),
    });
    const data = await res.json();
    if (res.ok) {
      setIsoStatusMsg(`Gate ${data.phase} sealed (${data.baseline_id})`);
      fetchOverview();
    } else {
      setIsoStatusMsg(`Gate Error: ${data.detail || 'Validation failed'}`);
    }
  };

  const handleGenerateDiffTour = async () => {
    setDiffTourLoading(true);
    try {
      const res = await fetch('/api/mission-control/diff-tour/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: 'Senior Engineer Architecture Tour v1',
          drop_revealing_asks: dropRevealingAsks,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setDiffTourPreview(data.tui_preview || JSON.stringify(data.tour, null, 2));
      }
    } finally {
      setDiffTourLoading(false);
    }
  };

  const handleRunFederationWaves = async () => {
    setFederationLoading(true);
    try {
      const res = await fetch('/api/mission-control/federation/waves/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workflow_name: 'l4_l5_station_audit_dag',
          tasks: [
            {
              task_id: 'sec_scan',
              station_id: 'agy-secret-shield',
              action: 'audit_secrets',
              payload: { profile: 'clean_repo' },
              depends_on: [],
            },
            {
              task_id: 'pe_triage',
              station_id: 'pe-reversing-lab',
              action: 'triage_pe',
              payload: { profile: 'clean' },
              depends_on: [],
            },
            {
              task_id: 'eskd_check',
              station_id: 'eskd-vision-station',
              action: 'inspect_geometry',
              payload: { sheet_format: 'A4' },
              depends_on: ['sec_scan', 'pe_triage'],
            },
          ],
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setFederationResult(data);
      }
    } finally {
      setFederationLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div
      data-testid="mission-control-drawer"
      className="fixed inset-y-0 right-0 z-50 w-full max-w-2xl bg-zinc-950 border-l border-zinc-800 shadow-2xl flex flex-col text-zinc-100"
    >
      {/* Header */}
      <div className="px-4 py-3 border-b border-zinc-800 flex items-center justify-between bg-zinc-900/60">
        <div>
          <h2 className="text-sm font-semibold text-cyan-400 tracking-wide">
            AGY Mission Control (L1-L5 Unified Command Center)
          </h2>
          <p className="text-xs text-zinc-400">
            Golden Stack P10-P50 Bus • LSRPC Plugin Hub • ISO 9001 Agile V • Diff-Tour v1 • Federation DAG
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-300"
        >
          Close
        </button>
      </div>

      {/* 5-Tier Architecture Status Strip (L1..L5) */}
      <div className="grid grid-cols-5 gap-2 p-3 bg-zinc-900/30 border-b border-zinc-800 text-xs">
        {['L1', 'L2', 'L3', 'L4', 'L5'].map((tierKey) => {
          const rawTier = overview?.tiers?.[tierKey];
          const isArr = Array.isArray(rawTier);
          const tierObj = !isArr && rawTier ? (rawTier as TierInfo) : null;
          const tierName = tierObj?.name || (tierKey === 'L5' ? 'Federation Mesh' : isArr && (rawTier as string[])[0] ? (rawTier as string[])[0] : tierKey);
          const tierStatus = tierObj?.status || 'online';

          return (
            <div
              key={tierKey}
              data-testid={`mc-tier-${tierKey}`}
              className="p-2 rounded border border-zinc-800 bg-zinc-900/70 flex flex-col justify-between"
            >
              <div className="flex items-center justify-between">
                <span className="font-mono font-bold text-cyan-400">{tierKey}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-700/40">
                  {tierStatus}
                </span>
              </div>
              <div className="mt-1 font-medium text-zinc-200 truncate">
                {tierName}
              </div>
            </div>
          );
        })}
      </div>

      {/* Navigation Tabs */}
      <div className="flex border-b border-zinc-800 bg-zinc-900/40 px-3 pt-2 gap-2 text-xs overflow-x-auto">
        <button
          type="button"
          data-testid="mc-tab-bus"
          onClick={() => setActiveTab('bus')}
          className={`px-3 py-1.5 rounded-t font-medium border-b-2 transition-colors whitespace-nowrap ${
            activeTab === 'bus'
              ? 'border-cyan-400 text-cyan-300 bg-zinc-900'
              : 'border-transparent text-zinc-400 hover:text-zinc-200'
          }`}
        >
          P10-P50 Golden Bus
        </button>
        <button
          type="button"
          data-testid="mc-tab-plugins"
          onClick={() => setActiveTab('plugins')}
          className={`px-3 py-1.5 rounded-t font-medium border-b-2 transition-colors whitespace-nowrap ${
            activeTab === 'plugins'
              ? 'border-cyan-400 text-cyan-300 bg-zinc-900'
              : 'border-transparent text-zinc-400 hover:text-zinc-200'
          }`}
        >
          LSRPC Plugins ({overview?.plugins_count ?? plugins.length})
        </button>
        <button
          type="button"
          data-testid="mc-tab-diff-tour"
          onClick={() => setActiveTab('diff-tour')}
          className={`px-3 py-1.5 rounded-t font-medium border-b-2 transition-colors whitespace-nowrap ${
            activeTab === 'diff-tour'
              ? 'border-cyan-400 text-cyan-300 bg-zinc-900'
              : 'border-transparent text-zinc-400 hover:text-zinc-200'
          }`}
        >
          Diff-Tour v1
        </button>
        <button
          type="button"
          data-testid="mc-tab-federation"
          onClick={() => setActiveTab('federation')}
          className={`px-3 py-1.5 rounded-t font-medium border-b-2 transition-colors whitespace-nowrap ${
            activeTab === 'federation'
              ? 'border-cyan-400 text-cyan-300 bg-zinc-900'
              : 'border-transparent text-zinc-400 hover:text-zinc-200'
          }`}
        >
          Federation DAG (L4-L5)
        </button>
        <button
          type="button"
          data-testid="mc-tab-iso9000"
          onClick={() => setActiveTab('iso9000')}
          className={`px-3 py-1.5 rounded-t font-medium border-b-2 transition-colors whitespace-nowrap ${
            activeTab === 'iso9000'
              ? 'border-cyan-400 text-cyan-300 bg-zinc-900'
              : 'border-transparent text-zinc-400 hover:text-zinc-200'
          }`}
        >
          ISO 9001 Gates
        </button>
        <button
          type="button"
          data-testid="mc-tab-brain"
          onClick={() => setActiveTab('brain')}
          className={`px-3 py-1.5 rounded-t font-medium border-b-2 transition-colors whitespace-nowrap ${
            activeTab === 'brain'
              ? 'border-cyan-400 text-cyan-300 bg-zinc-900'
              : 'border-transparent text-zinc-400 hover:text-zinc-200'
          }`}
        >
          Brain & Subagents
        </button>
      </div>

      {/* Tab Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
        {activeTab === 'bus' && (
          <div className="space-y-4">
            <div className="flex items-center justify-between p-3 rounded border border-zinc-800 bg-zinc-900/50">
              <div>
                <div className="font-semibold text-zinc-200">
                  Single-Process Priority Hook Multiplexer
                </div>
                <div className="text-zinc-400">
                  Executes P10 → P20 → P30 → P40 → P50 in-memory (&lt;1ms) with circuit breaker
                </div>
              </div>
              <button
                type="button"
                data-testid="mc-run-benchmark-btn"
                onClick={handleRunBenchmark}
                disabled={loading}
                className="px-3 py-1.5 rounded bg-cyan-600 hover:bg-cyan-500 text-white font-medium disabled:opacity-50"
              >
                {loading ? 'Running...' : 'Run Benchmark'}
              </button>
            </div>

            {benchmarkResult && (
              <div className="grid grid-cols-3 gap-2 p-3 rounded border border-cyan-800/40 bg-cyan-950/20">
                <div>
                  <span className="text-zinc-400 block">Avg 5-Slot Latency</span>
                  <span className="text-sm font-mono font-bold text-emerald-400">
                    {benchmarkResult.avg_duration_ms.toFixed(4)} ms
                  </span>
                </div>
                <div>
                  <span className="text-zinc-400 block">Min Latency</span>
                  <span className="text-sm font-mono text-zinc-200">
                    {benchmarkResult.min_duration_ms.toFixed(4)} ms
                  </span>
                </div>
                <div>
                  <span className="text-zinc-400 block">Max Latency</span>
                  <span className="text-sm font-mono text-zinc-200">
                    {benchmarkResult.max_duration_ms.toFixed(4)} ms
                  </span>
                </div>
              </div>
            )}

            <div className="space-y-2">
              {['P10', 'P20', 'P30', 'P40', 'P50'].map((slot) => (
                <div
                  key={slot}
                  className="p-2.5 rounded border border-zinc-800 bg-zinc-900/40 flex items-center justify-between"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-bold text-cyan-400">{slot}</span>
                    <span className="text-zinc-200 font-medium">
                      {overview?.golden_bus?.slot_names?.[slot] || slot}
                    </span>
                  </div>
                  <span className="text-emerald-400 font-mono">ACTIVE</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === 'plugins' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between p-3 rounded border border-zinc-800 bg-zinc-900/50">
              <div>
                <div className="font-semibold text-zinc-200">Plugin Version Drift Reconciliation</div>
                <div className="text-zinc-400">Reconcile installed plugin manifests to target baseline v1.1.0</div>
              </div>
              <button
                type="button"
                data-testid="mc-reconcile-drift-btn"
                onClick={handleReconcileDrift}
                className="px-3 py-1.5 rounded bg-cyan-600 hover:bg-cyan-500 text-white font-medium"
              >
                Reconcile to v1.1.0
              </button>
            </div>
            {reconcileMsg && (
              <div className="p-2 rounded bg-emerald-950/40 border border-emerald-800/40 text-emerald-300 font-mono text-[11px]">
                {reconcileMsg}
              </div>
            )}
            <div className="space-y-2">
              {plugins.map((pl) => (
                <div
                  key={pl.dir}
                  className="p-3 rounded border border-zinc-800 bg-zinc-900/40 flex items-center justify-between"
                >
                  <div>
                    <div className="font-semibold text-zinc-200">{pl.name}</div>
                    <div className="text-zinc-400 font-mono text-[11px]">
                      dir: {pl.dir} • skills: {pl.skills.length} • agents: {pl.agents.length}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleTogglePlugin(pl.dir, pl.disabled)}
                    className={`px-2.5 py-1 rounded font-medium ${
                      pl.disabled
                        ? 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700'
                        : 'bg-emerald-900/50 text-emerald-300 border border-emerald-600/40'
                    }`}
                  >
                    {pl.disabled ? 'Disabled' : 'Enabled'}
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === 'diff-tour' && (
          <div className="space-y-4">
            <div className="p-3 rounded border border-zinc-800 bg-zinc-900/50 space-y-3">
              <div className="font-semibold text-zinc-200">
                Senior Engineer Diff-Tour v1 Visualizer
              </div>
              <p className="text-zinc-400 text-xs">
                Inspect architecture across 8 dimensions with epistemic basis classification and TUI preview.
              </p>

              {/* 8 Review Dimension Pills */}
              <div className="flex flex-wrap gap-1.5">
                {[
                  'L1_PROMISES',
                  'L2_FAILURE',
                  'L3_OPERATIONS',
                  'L4_TRUST',
                  'L5_SCALE',
                  'L6_DESIGN',
                  'L7_TESTS',
                  'L8_AI_TRAPS',
                ].map((dim) => (
                  <span
                    key={dim}
                    className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-cyan-950/60 text-cyan-300 border border-cyan-800/40"
                  >
                    {dim}
                  </span>
                ))}
              </div>

              {/* Epistemic Basis Badges */}
              <div className="flex items-center gap-2 pt-1">
                <span className="text-zinc-400 text-[11px]">Epistemic Basis:</span>
                <span className="px-2 py-0.5 rounded text-[10px] bg-emerald-950 text-emerald-300 border border-emerald-800/40 font-mono">
                  fact
                </span>
                <span className="px-2 py-0.5 rounded text-[10px] bg-amber-950 text-amber-300 border border-amber-800/40 font-mono">
                  inferred
                </span>
                <span className="px-2 py-0.5 rounded text-[10px] bg-purple-950 text-purple-300 border border-purple-800/40 font-mono">
                  opinion
                </span>
              </div>

              {/* Controls */}
              <div className="flex items-center justify-between pt-2 border-t border-zinc-800">
                <label className="flex items-center gap-2 cursor-pointer text-zinc-300">
                  <input
                    type="checkbox"
                    data-testid="mc-diff-tour-drop-asks"
                    checked={dropRevealingAsks}
                    onChange={(e) => setDropRevealingAsks(e.target.checked)}
                    className="rounded bg-zinc-900 border-zinc-700 text-cyan-500 focus:ring-0"
                  />
                  <span>Drop Revealing Asks</span>
                </label>

                <button
                  type="button"
                  data-testid="mc-generate-diff-tour-btn"
                  onClick={handleGenerateDiffTour}
                  disabled={diffTourLoading}
                  className="px-3 py-1.5 rounded bg-cyan-600 hover:bg-cyan-500 text-white font-medium disabled:opacity-50"
                >
                  {diffTourLoading ? 'Generating...' : 'Generate Diff-Tour v1'}
                </button>
              </div>
            </div>

            {/* TUI Preview */}
            <div className="space-y-1">
              <span className="text-zinc-400 font-mono text-[11px]">TUI Preview Box Output</span>
              <pre
                data-testid="mc-diff-tour-preview"
                className="p-3 rounded border border-zinc-800 bg-zinc-900/90 text-zinc-200 font-mono text-[11px] overflow-x-auto whitespace-pre leading-relaxed min-h-[140px]"
              >
                {diffTourPreview || 'Click "Generate Diff-Tour v1" to view deterministic TUI box preview...'}
              </pre>
            </div>
          </div>
        )}

        {activeTab === 'federation' && (
          <div className="space-y-4">
            <div className="p-3 rounded border border-zinc-800 bg-zinc-900/50 space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-semibold text-zinc-200">
                    L4-L5 Stations & Topological Wave DAG Orchestrator
                  </div>
                  <div className="text-zinc-400 text-xs">
                    Parallel execution waves across Secret Shield, PE Lab, and ESKD stations
                  </div>
                </div>
                <button
                  type="button"
                  data-testid="mc-run-federation-waves-btn"
                  onClick={handleRunFederationWaves}
                  disabled={federationLoading}
                  className="px-3 py-1.5 rounded bg-cyan-600 hover:bg-cyan-500 text-white font-medium disabled:opacity-50"
                >
                  {federationLoading ? 'Executing Waves...' : 'Run Federation Waves'}
                </button>
              </div>

              {/* Topological Wave Badges */}
              <div className="flex gap-2 pt-1">
                <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-blue-950 text-blue-300 border border-blue-800/40">
                  Wave 0: Parallel Station Scanners
                </span>
                <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-indigo-950 text-indigo-300 border border-indigo-800/40">
                  Wave 1: Synthesis & Cross-Station Dossier
                </span>
              </div>
            </div>

            {/* Station Cards */}
            <div className="grid grid-cols-3 gap-2">
              <div
                data-testid="mc-station-secret-shield"
                className="p-3 rounded border border-zinc-800 bg-zinc-900/60 flex flex-col justify-between space-y-2"
              >
                <div>
                  <div className="font-semibold text-zinc-200">Secret Shield v1.1.0</div>
                  <div className="text-[11px] text-zinc-400 mt-1">
                    OASIS SARIF 2.1.0 & Windows DPAPI quarantine envelope rotation
                  </div>
                </div>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-700/40 w-fit font-mono">
                  READY
                </span>
              </div>

              <div
                data-testid="mc-station-pe-lab"
                className="p-3 rounded border border-zinc-800 bg-zinc-900/60 flex flex-col justify-between space-y-2"
              >
                <div>
                  <div className="font-semibold text-zinc-200">PE Reversing Lab v1.1.0</div>
                  <div className="text-[11px] text-zinc-400 mt-1">
                    Rich Header, EAT symbols, TLS callbacks, RWX anomalies & YARA
                  </div>
                </div>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-700/40 w-fit font-mono">
                  READY
                </span>
              </div>

              <div
                data-testid="mc-station-eskd"
                className="p-3 rounded border border-zinc-800 bg-zinc-900/60 flex flex-col justify-between space-y-2"
              >
                <div>
                  <div className="font-semibold text-zinc-200">ESKD Vision Station v1.1.0</div>
                  <div className="text-[11px] text-zinc-400 mt-1">
                    GOST 2.104/2.301, 300 DPI geometry & 2D CCW line crossings
                  </div>
                </div>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-700/40 w-fit font-mono">
                  READY
                </span>
              </div>
            </div>

            {/* Federation Execution Result */}
            {federationResult && (
              <div className="p-3 rounded border border-cyan-800/40 bg-zinc-900/80 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-cyan-300">Wave Execution Outcome</span>
                  <span className="font-mono text-emerald-400 text-xs font-bold">
                    {String(federationResult.status || 'COMPLETED')}
                  </span>
                </div>
                <div className="font-mono text-[11px] text-zinc-400">
                  Mesh Digest: {String(federationResult.mesh_digest || '').slice(0, 24)}...
                </div>
                <div className="font-mono text-[11px] text-zinc-400">
                  Tasks Executed: {String(federationResult.tasks_executed || 0)}
                </div>
              </div>
            )}
          </div>
        )}

        {activeTab === 'iso9000' && (
          <div className="space-y-4">
            <div className="p-3 rounded border border-zinc-800 bg-zinc-900/50 space-y-3">
              <div className="font-semibold text-zinc-200">
                ISO 9001:2015 Agile V Gate Controller (P30 Self-Grading Bias Shield)
              </div>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={isoPipelineId}
                  onChange={(e) => setIsoPipelineId(e.target.value)}
                  className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-100 flex-1"
                  placeholder="Pipeline ID"
                />
                <select
                  value={isoPhase}
                  onChange={(e) => setIsoPhase(e.target.value)}
                  className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-100"
                >
                  <option value="SPEC">SPEC</option>
                  <option value="TEST_DESIGN">TEST_DESIGN</option>
                  <option value="SYNTHESIS">SYNTHESIS</option>
                  <option value="VERIFICATION">VERIFICATION</option>
                  <option value="RELEASE">RELEASE</option>
                </select>
                <button
                  type="button"
                  onClick={handleAdvanceIsoGate}
                  className="px-3 py-1 rounded bg-cyan-600 hover:bg-cyan-500 text-white font-medium"
                >
                  Advance Gate
                </button>
              </div>
              {isoStatusMsg && (
                <div className="text-emerald-400 font-mono">{isoStatusMsg}</div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'brain' && (
          <div className="space-y-3">
            <div className="flex gap-2">
              <input
                type="text"
                value={brainQuery}
                onChange={(e) => setBrainQuery(e.target.value)}
                placeholder="Search cross-session artifacts & child testbench reports..."
                className="bg-zinc-900 border border-zinc-700 rounded px-2.5 py-1.5 text-zinc-100 flex-1"
              />
              <button
                type="button"
                onClick={() => fetchBrain(brainQuery)}
                className="px-3 py-1.5 rounded bg-cyan-600 hover:bg-cyan-500 text-white font-medium"
              >
                Search
              </button>
            </div>
            {childReports.length > 0 && (
              <div className="p-2.5 rounded border border-pink-800/40 bg-pink-950/20">
                <div className="font-semibold text-pink-300 mb-1">
                  Child Testbench Telemetry Reports ({childReports.length})
                </div>
                {childReports.map((rep, idx) => (
                  <div key={idx} className="font-mono text-[11px] text-zinc-300">
                    [{String(rep.status || 'PASS')}] {String(rep.summary || '')}
                  </div>
                ))}
              </div>
            )}
            <div className="space-y-2">
              {brainResults.map((item) => (
                <div
                  key={item.conversation_id}
                  className="p-2.5 rounded border border-zinc-800 bg-zinc-900/40"
                >
                  <div className="font-mono text-cyan-400">{item.reference}</div>
                  <div className="text-zinc-400 mt-1 line-clamp-2">{item.snippet}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
