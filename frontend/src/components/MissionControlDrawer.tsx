import React, { useState, useEffect, useCallback } from 'react';

export interface MissionControlDrawerProps {
  isOpen: boolean;
  onClose: () => void;
}

interface TierInfo {
  id: string;
  name: string;
  status: string;
  description: string;
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
  tiers: Record<string, TierInfo>;
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
    results_count: number;
    child_reports_count: number;
  };
}

export const MissionControlDrawer: React.FC<MissionControlDrawerProps> = ({
  isOpen,
  onClose,
}) => {
  const [activeTab, setActiveTab] = useState<'bus' | 'plugins' | 'iso9000' | 'brain'>('bus');
  const [overview, setOverview] = useState<OverviewPayload | null>(null);
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [brainQuery, setBrainQuery] = useState('');
  const [brainResults, setBrainResults] = useState<
    Array<{ conversation_id: string; label: string; reference: string; snippet: string }>
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
            AGY Mission Control (L1-L4 Unified Command Center)
          </h2>
          <p className="text-xs text-zinc-400">
            Golden Stack P10-P50 Bus • LSRPC Plugin Hub • ISO 9001 Agile V • Brain Memory
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

      {/* 4-Tier Architecture Status Strip */}
      <div className="grid grid-cols-4 gap-2 p-3 bg-zinc-900/30 border-b border-zinc-800 text-xs">
        {['L1', 'L2', 'L3', 'L4'].map((tierKey) => {
          const tier = overview?.tiers?.[tierKey];
          return (
            <div
              key={tierKey}
              className="p-2 rounded border border-zinc-800 bg-zinc-900/70 flex flex-col justify-between"
            >
              <div className="flex items-center justify-between">
                <span className="font-mono font-bold text-cyan-400">{tierKey}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-700/40">
                  {tier?.status || 'online'}
                </span>
              </div>
              <div className="mt-1 font-medium text-zinc-200 truncate">
                {tier?.name || tierKey}
              </div>
            </div>
          );
        })}
      </div>

      {/* Navigation Tabs */}
      <div className="flex border-b border-zinc-800 bg-zinc-900/40 px-3 pt-2 gap-2 text-xs">
        <button
          type="button"
          data-testid="mc-tab-bus"
          onClick={() => setActiveTab('bus')}
          className={`px-3 py-1.5 rounded-t font-medium border-b-2 transition-colors ${
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
          className={`px-3 py-1.5 rounded-t font-medium border-b-2 transition-colors ${
            activeTab === 'plugins'
              ? 'border-cyan-400 text-cyan-300 bg-zinc-900'
              : 'border-transparent text-zinc-400 hover:text-zinc-200'
          }`}
        >
          LSRPC Plugins ({overview?.plugins_count ?? plugins.length})
        </button>
        <button
          type="button"
          data-testid="mc-tab-iso9000"
          onClick={() => setActiveTab('iso9000')}
          className={`px-3 py-1.5 rounded-t font-medium border-b-2 transition-colors ${
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
          className={`px-3 py-1.5 rounded-t font-medium border-b-2 transition-colors ${
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
