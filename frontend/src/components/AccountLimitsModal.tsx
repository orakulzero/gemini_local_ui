import React, { useEffect, useState } from 'react';
import { useAccountsStore } from '../store/accountsStore';
import { AccountLimitsData, ModelTelemetry } from '../types';

export const AccountLimitsModal: React.FC = () => {
  const isLimitsModalOpen = useAccountsStore((s) => s.isLimitsModalOpen);
  const toggleLimitsModal = useAccountsStore((s) => s.toggleLimitsModal);
  const limitsData = useAccountsStore((s) => s.limitsData);
  const fetchLimits = useAccountsStore((s) => s.fetchLimits);
  const updateThresholds = useAccountsStore((s) => s.updateThresholds);
  const toggleFailover = useAccountsStore((s) => s.toggleFailover);
  const activeAccountId = useAccountsStore((s) => s.activeAccountId);

  const [selectedAccountId, setSelectedAccountId] = useState<string>('');
  const [dailyReqLimit, setDailyReqLimit] = useState<number>(500);
  const [dailyTokLimit, setDailyTokLimit] = useState<number>(2000000);
  const [hourlyReqLimit, setHourlyReqLimit] = useState<number>(60);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);

  // Poll limits on modal open
  useEffect(() => {
    if (isLimitsModalOpen) {
      fetchLimits();
      const interval = setInterval(() => {
        fetchLimits();
      }, 10000);
      return () => clearInterval(interval);
    }
  }, [isLimitsModalOpen, fetchLimits]);

  // Set default selected account
  useEffect(() => {
    if (limitsData?.accounts?.length) {
      const initialId =
        selectedAccountId && limitsData.accounts.some((a) => a.account_id === selectedAccountId)
          ? selectedAccountId
          : limitsData.active_account_id || limitsData.accounts[0].account_id;
      setSelectedAccountId(initialId);
    }
  }, [limitsData, selectedAccountId]);

  const selectedAccount: AccountLimitsData | undefined = limitsData?.accounts.find(
    (a) => a.account_id === selectedAccountId
  );

  // Sync inputs when selected account changes
  useEffect(() => {
    if (selectedAccount?.thresholds) {
      setDailyReqLimit(selectedAccount.thresholds.daily_request_threshold || 500);
      setDailyTokLimit(selectedAccount.thresholds.daily_token_threshold || 2000000);
      setHourlyReqLimit(selectedAccount.thresholds.hourly_request_threshold || 60);
      setSaveStatus(null);
    }
  }, [selectedAccount]);

  if (!isLimitsModalOpen) return null;

  const handleSaveThresholds = async () => {
    if (!selectedAccountId) return;
    setSaveStatus('Saving...');
    const ok = await updateThresholds(selectedAccountId, {
      daily_request_threshold: Number(dailyReqLimit) || 500,
      daily_token_threshold: Number(dailyTokLimit) || 2000000,
      hourly_request_threshold: Number(hourlyReqLimit) || 60,
    });
    if (ok) {
      setSaveStatus('Thresholds saved successfully!');
      setTimeout(() => setSaveStatus(null), 3000);
    } else {
      setSaveStatus('Failed to update thresholds');
    }
  };

  const canonicalModelLabels: Record<string, string> = {
    '4 Argon': 'Gemini 4 Argon (Experimental)',
    '3.1 Pro': 'Gemini 3.1 Pro (Analytical)',
    '3.8 Flash': 'Gemini 3.8 Flash (Low Latency)',
    'Task': 'Gemini Task / Deep Research',
  };

  const formatTokens = (tok: number = 0) => {
    if (tok >= 1000000) return `${(tok / 1000000).toFixed(2)}M`;
    if (tok >= 1000) return `${(tok / 1000).toFixed(1)}k`;
    return tok.toString();
  };

  const getPercentageColor = (pct: number) => {
    if (pct >= 90) return 'bg-red-500';
    if (pct >= 75) return 'bg-amber-500';
    return 'bg-blue-500';
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="fixed inset-0" onClick={() => toggleLimitsModal(false)} />
      <div
        data-testid="account-limits-modal"
        className="relative w-full max-w-4xl max-h-[90vh] flex flex-col rounded-2xl bg-zinc-900 border border-zinc-800 shadow-2xl overflow-hidden z-10 text-xs text-zinc-300"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-zinc-800 bg-zinc-950/60 shrink-0">
          <div className="flex items-center gap-2.5">
            <span className="text-blue-400 text-base">📊</span>
            <div>
              <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
                Account Limits & Quota Telemetry
                {selectedAccount && (
                  <span
                    className={`px-1.5 py-0.5 text-[10px] font-bold rounded uppercase tracking-wider ${
                      selectedAccount.tier === 'ultra'
                        ? 'bg-purple-950 text-purple-300 border border-purple-800'
                        : selectedAccount.tier === 'pro'
                        ? 'bg-blue-950 text-blue-300 border border-blue-800'
                        : 'bg-zinc-800 text-zinc-300 border border-zinc-700'
                    }`}
                  >
                    {selectedAccount.tier}
                  </span>
                )}
              </h2>
              {selectedAccount?.policy_string && (
                <p className="text-[11px] text-zinc-400 font-mono tracking-tight">
                  Policy: {selectedAccount.policy_string}
                </p>
              )}
            </div>
          </div>
          <button
            type="button"
            data-testid="limits-close-btn"
            onClick={() => toggleLimitsModal(false)}
            className="text-zinc-500 hover:text-zinc-200 transition-colors p-1 text-sm leading-none"
          >
            ✕
          </button>
        </div>

        {/* Global Summary & Account Tabs */}
        <div className="border-b border-zinc-800 bg-zinc-950/30 px-5 pt-3 pb-2 shrink-0 space-y-3">
          {limitsData?.global_summary_24h && (
            <div className="grid grid-cols-4 gap-2 text-center">
              <div className="bg-zinc-900/80 p-2 rounded-lg border border-zinc-800/80">
                <div className="text-[10px] text-zinc-500 uppercase font-semibold">24h Requests</div>
                <div className="text-sm font-bold text-zinc-200 mt-0.5 font-mono">
                  {limitsData.global_summary_24h.total_requests}
                </div>
              </div>
              <div className="bg-zinc-900/80 p-2 rounded-lg border border-zinc-800/80">
                <div className="text-[10px] text-zinc-500 uppercase font-semibold">24h Est. Tokens</div>
                <div className="text-sm font-bold text-blue-300 mt-0.5 font-mono">
                  {formatTokens(limitsData.global_summary_24h.total_tokens_est)}
                </div>
              </div>
              <div className="bg-zinc-900/80 p-2 rounded-lg border border-zinc-800/80">
                <div className="text-[10px] text-zinc-500 uppercase font-semibold">Task Mode Calls</div>
                <div className="text-sm font-bold text-purple-300 mt-0.5 font-mono">
                  {limitsData.global_summary_24h.task_mode_invocations}
                </div>
              </div>
              <div className="bg-zinc-900/80 p-2 rounded-lg border border-zinc-800/80">
                <div className="text-[10px] text-zinc-500 uppercase font-semibold">Quota Failovers</div>
                <div className="text-sm font-bold text-amber-300 mt-0.5 font-mono">
                  {limitsData.global_summary_24h.failover_events_count}
                </div>
              </div>
            </div>
          )}

          {/* Account Selector Tabs */}
          <div className="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-thin">
            {limitsData?.accounts.map((acc) => {
              const isSelected = acc.account_id === selectedAccountId;
              const isActive = acc.account_id === activeAccountId;
              return (
                <button
                  key={acc.account_id}
                  data-testid={`account-tab-${acc.account_id}`}
                  onClick={() => setSelectedAccountId(acc.account_id)}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors whitespace-nowrap ${
                    isSelected
                      ? 'bg-zinc-800 text-white border-zinc-700 shadow-sm'
                      : 'bg-zinc-900/60 text-zinc-400 border-transparent hover:bg-zinc-800/50 hover:text-zinc-200'
                  }`}
                >
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${
                      acc.cooldown?.is_active
                        ? 'bg-amber-400 animate-pulse'
                        : acc.health_status === 'healthy'
                        ? 'bg-emerald-400'
                        : 'bg-red-400'
                    }`}
                  />
                  <span>{acc.name}</span>
                  {isActive && (
                    <span className="text-[9px] px-1 bg-blue-500/20 text-blue-300 rounded font-semibold">
                      ACTIVE
                    </span>
                  )}
                  {acc.cooldown?.is_active && (
                    <span className="text-[9px] text-amber-300 font-mono">
                      {acc.cooldown.remaining_seconds}s
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* Scrollable Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {selectedAccount ? (
            <>
              {/* Active Cooldown Banner */}
              {selectedAccount.cooldown?.is_active && (
                <div className="p-3 bg-amber-950/40 border border-amber-800/60 rounded-xl flex items-center justify-between text-amber-200">
                  <div className="flex items-center gap-2">
                    <span className="text-base animate-bounce">🔥</span>
                    <div>
                      <div className="font-semibold text-xs">
                        Account Quota Limit Reached (In Cooldown)
                      </div>
                      <div className="text-[11px] text-amber-300/80">
                        Affected models: {selectedAccount.cooldown.affected_models.join(', ') || 'All models'}
                      </div>
                    </div>
                  </div>
                  <div className="font-mono text-xs px-2.5 py-1 bg-amber-900/60 rounded-lg border border-amber-700">
                    Resets in: {selectedAccount.cooldown.remaining_seconds}s
                  </div>
                </div>
              )}

              {/* Per-Model Usage & Quota Progress Bars */}
              <div className="space-y-3">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
                  Per-Model Telemetry & Quota Status
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {Object.entries(selectedAccount.per_model_24h || {}).map(([modelKey, meta]) => {
                    const label = canonicalModelLabels[modelKey] || modelKey;
                    const isCooldown = meta.quota_status === 'cooldown';
                    return (
                      <div
                        key={modelKey}
                        className="p-3 bg-zinc-950/80 border border-zinc-800/90 rounded-xl space-y-2"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-zinc-200 text-xs">{label}</span>
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase ${
                              isCooldown
                                ? 'bg-amber-950 text-amber-400 border border-amber-800'
                                : 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                            }`}
                          >
                            {meta.quota_status}
                          </span>
                        </div>
                        <div className="flex items-center justify-between text-[11px] text-zinc-400 font-mono">
                          <span>Requests: {meta.requests}</span>
                          <span>Est. Tokens: {formatTokens(meta.tokens_est)}</span>
                        </div>
                        {isCooldown && meta.cooldown_remaining_seconds > 0 && (
                          <div className="text-[10px] text-amber-400/90 font-mono flex items-center justify-between pt-1 border-t border-zinc-800">
                            <span>Cooldown remaining:</span>
                            <span className="font-bold">{meta.cooldown_remaining_seconds}s</span>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Usage Progress vs Thresholds */}
              <div className="space-y-3">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
                  Rolling 24h Threshold Consumption
                </h3>
                <div className="space-y-2.5 bg-zinc-950/80 p-4 border border-zinc-800/90 rounded-xl">
                  {/* Daily Requests */}
                  <div>
                    <div className="flex items-center justify-between text-xs mb-1">
                      <span className="text-zinc-300">Daily Requests</span>
                      <span className="font-mono text-zinc-400">
                        {selectedAccount.rolling_24h.requests} /{' '}
                        {selectedAccount.thresholds.daily_request_threshold} (
                        {selectedAccount.thresholds.daily_request_percentage}%)
                      </span>
                    </div>
                    <div className="w-full bg-zinc-800 rounded-full h-2 overflow-hidden">
                      <div
                        className={`h-full transition-all duration-500 ${getPercentageColor(
                          selectedAccount.thresholds.daily_request_percentage
                        )}`}
                        style={{
                          width: `${Math.min(
                            100,
                            selectedAccount.thresholds.daily_request_percentage
                          )}%`,
                        }}
                      />
                    </div>
                  </div>

                  {/* Daily Tokens */}
                  <div>
                    <div className="flex items-center justify-between text-xs mb-1">
                      <span className="text-zinc-300">Daily Estimated Tokens</span>
                      <span className="font-mono text-zinc-400">
                        {formatTokens(selectedAccount.rolling_24h.total_tokens_est)} /{' '}
                        {formatTokens(selectedAccount.thresholds.daily_token_threshold)} (
                        {selectedAccount.thresholds.daily_token_percentage}%)
                      </span>
                    </div>
                    <div className="w-full bg-zinc-800 rounded-full h-2 overflow-hidden">
                      <div
                        className={`h-full transition-all duration-500 ${getPercentageColor(
                          selectedAccount.thresholds.daily_token_percentage
                        )}`}
                        style={{
                          width: `${Math.min(
                            100,
                            selectedAccount.thresholds.daily_token_percentage
                          )}%`,
                        }}
                      />
                    </div>
                  </div>
                </div>
              </div>

              {/* Threshold Configuration */}
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
                    Threshold & Warning Configuration
                  </h3>
                  {saveStatus && (
                    <span className="text-[11px] text-emerald-400 font-medium">{saveStatus}</span>
                  )}
                </div>
                <div className="p-4 bg-zinc-950/80 border border-zinc-800/90 rounded-xl space-y-3">
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div>
                      <label className="block text-zinc-400 font-medium mb-1">
                        Daily Request Limit
                      </label>
                      <input
                        type="number"
                        data-testid="threshold-daily-req"
                        min={10}
                        max={100000}
                        value={dailyReqLimit}
                        onChange={(e) => setDailyReqLimit(parseInt(e.target.value, 10) || 500)}
                        className="w-full bg-zinc-900 text-zinc-100 border border-zinc-700/80 rounded-lg px-2.5 py-1.5 text-xs font-mono focus:outline-none focus:border-blue-500"
                      />
                    </div>
                    <div>
                      <label className="block text-zinc-400 font-medium mb-1">
                        Daily Token Limit
                      </label>
                      <input
                        type="number"
                        data-testid="threshold-daily-tok"
                        min={10000}
                        max={100000000}
                        step={100000}
                        value={dailyTokLimit}
                        onChange={(e) => setDailyTokLimit(parseInt(e.target.value, 10) || 2000000)}
                        className="w-full bg-zinc-900 text-zinc-100 border border-zinc-700/80 rounded-lg px-2.5 py-1.5 text-xs font-mono focus:outline-none focus:border-blue-500"
                      />
                    </div>
                    <div>
                      <label className="block text-zinc-400 font-medium mb-1">
                        Hourly Request Limit
                      </label>
                      <input
                        type="number"
                        data-testid="threshold-hourly-req"
                        min={5}
                        max={10000}
                        value={hourlyReqLimit}
                        onChange={(e) => setHourlyReqLimit(parseInt(e.target.value, 10) || 60)}
                        className="w-full bg-zinc-900 text-zinc-100 border border-zinc-700/80 rounded-lg px-2.5 py-1.5 text-xs font-mono focus:outline-none focus:border-blue-500"
                      />
                    </div>
                  </div>
                  <div className="flex justify-end pt-1">
                    <button
                      type="button"
                      data-testid="save-thresholds-btn"
                      onClick={handleSaveThresholds}
                      className="px-3.5 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium transition-colors text-xs shadow-sm"
                    >
                      Save Thresholds
                    </button>
                  </div>
                </div>
              </div>

              {/* Failover Routing & Recent Events */}
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
                    Automatic Failover Routing & History
                  </h3>
                  <label className="flex items-center gap-2 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      data-testid="failover-toggle-checkbox"
                      checked={limitsData?.failover_state.enabled ?? true}
                      onChange={(e) => toggleFailover(e.target.checked)}
                      className="rounded bg-zinc-900 border-zinc-700 text-blue-500 focus:ring-0"
                    />
                    <span className="text-xs font-medium text-zinc-300">Enable Auto-Failover</span>
                  </label>
                </div>

                <div className="p-4 bg-zinc-950/80 border border-zinc-800/90 rounded-xl space-y-3">
                  <div className="flex items-center gap-2 text-zinc-400 text-xs">
                    <span className="text-zinc-500 font-medium">Fallback Priority Chain:</span>
                    <div className="flex items-center gap-1.5 font-mono text-[11px] text-zinc-300">
                      {limitsData?.failover_state.fallback_chain?.length ? (
                        limitsData.failover_state.fallback_chain.map((accName, i) => (
                          <React.Fragment key={accName}>
                            <span className="px-1.5 py-0.5 bg-zinc-800 rounded border border-zinc-700">
                              {accName}
                            </span>
                            {i < (limitsData?.failover_state.fallback_chain.length || 0) - 1 && (
                              <span className="text-zinc-600">→</span>
                            )}
                          </React.Fragment>
                        ))
                      ) : (
                        <span className="text-zinc-500 italic">No fallback accounts configured</span>
                      )}
                    </div>
                  </div>

                  {/* Recent Failover Logs */}
                  <div className="pt-2 border-t border-zinc-800/80 space-y-1.5">
                    <div className="text-[11px] text-zinc-400 font-medium">Recent Failover Events (24h)</div>
                    {limitsData?.failover_state.recent_failovers?.length ? (
                      <div className="space-y-1">
                        {limitsData.failover_state.recent_failovers.map((evt, idx) => (
                          <div
                            key={idx}
                            className="flex items-center justify-between p-2 rounded bg-zinc-900 border border-zinc-800 text-[11px] font-mono"
                          >
                            <div className="flex items-center gap-2">
                              <span className="text-amber-400">⚡</span>
                              <span>
                                {evt.from_account_name} → {evt.to_account_name}
                              </span>
                              <span className="text-zinc-500">[{evt.model}]</span>
                            </div>
                            <div className="flex items-center gap-2 text-zinc-400">
                              <span className="text-red-400">Error {evt.error_code}</span>
                              <span>{new Date(evt.timestamp * 1000).toLocaleTimeString()}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-[11px] text-zinc-500 italic py-1">
                        No failover events recorded in the last 24 hours.
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </>
          ) : (
            <div className="p-8 text-center text-zinc-500">No account telemetry available.</div>
          )}
        </div>
      </div>
    </div>
  );
};
