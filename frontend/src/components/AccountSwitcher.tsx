import React, { useEffect, useRef, useState } from 'react';
import { useAccountsStore } from '../store/accountsStore';
import { AccountItem } from '../types';

export const AccountSwitcher: React.FC = () => {
  const {
    accounts,
    activeAccountId,
    failoverConfig,
    fetchAccounts,
    activateAccount,
    deleteAccount,
    toggleFailover,
    toggleAddAccount,
    toggleLimitsModal,
  } = useAccountsStore();

  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchAccounts();
    const interval = setInterval(() => {
      fetchAccounts();
    }, 30000);
    return () => clearInterval(interval);
  }, [fetchAccounts]);

  useEffect(() => {
    const handleOutsideClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    if (isOpen) {
      document.addEventListener('mousedown', handleOutsideClick);
    }
    return () => document.removeEventListener('mousedown', handleOutsideClick);
  }, [isOpen]);

  const activeAccount: AccountItem | undefined =
    accounts.find((a) => a.id === activeAccountId) || accounts[0];

  const getTierBadge = (tier?: string) => {
    const t = (tier || 'free').toUpperCase();
    if (t === 'PRO') {
      return (
        <span
          data-testid="tier-badge-pro"
          className="px-1.5 py-0.5 text-[10px] font-semibold tracking-wider rounded border bg-blue-600/20 text-blue-300 border-blue-500/40"
        >
          PRO
        </span>
      );
    }
    if (t === 'ULTRA') {
      return (
        <span
          data-testid="tier-badge-ultra"
          className="px-1.5 py-0.5 text-[10px] font-semibold tracking-wider rounded border bg-purple-600/20 text-purple-300 border-purple-500/40"
        >
          ULTRA
        </span>
      );
    }
    return (
      <span
        data-testid="tier-badge-free"
        className="px-1.5 py-0.5 text-[10px] font-semibold tracking-wider rounded border bg-zinc-800 text-zinc-300 border-zinc-700"
      >
        FREE
      </span>
    );
  };

  const getHealthIndicator = (acc?: AccountItem) => {
    if (!acc) return <span className="text-zinc-500 text-xs">●</span>;
    if (acc.health_status === 'cooldown' || (acc.cooldown_until && acc.cooldown_until > Date.now() / 1000)) {
      return (
        <span className="text-amber-400 text-xs animate-pulse" title="In Cooldown">
          🔥
        </span>
      );
    }
    if (acc.health_status === 'refreshing') {
      return (
        <span className="text-amber-400 text-xs animate-spin" title="Refreshing">
          ●
        </span>
      );
    }
    if (acc.health_status === 'healthy') {
      return (
        <span className="text-emerald-400 text-xs" title="Healthy">
          ●
        </span>
      );
    }
    return (
      <span className="text-red-400 text-xs" title="Expired or Unauthenticated">
        ●
      </span>
    );
  };

  return (
    <div className="relative inline-block text-left" ref={dropdownRef}>
      <button
        type="button"
        data-testid="account-switcher-btn"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-2.5 py-1 bg-zinc-900/80 hover:bg-zinc-800 border border-zinc-800 rounded-lg text-xs text-zinc-200 transition-colors shadow-sm"
        title="Switch active Google Gemini account"
      >
        <span className="flex items-center gap-1.5 font-medium">
          {getHealthIndicator(activeAccount)}
          <span className="truncate max-w-[110px]">
            {activeAccount ? activeAccount.name : 'No Account'}
          </span>
        </span>
        {getTierBadge(activeAccount?.tier)}
        <span className="text-zinc-500 text-[10px]">▾</span>
      </button>

      {isOpen && (
        <div
          data-testid="account-switcher-dropdown"
          className="absolute left-0 mt-1.5 w-72 rounded-xl bg-zinc-900 border border-zinc-800 shadow-2xl py-1.5 z-50 backdrop-blur-md"
        >
          <div className="px-3 py-1.5 border-b border-zinc-800/80 flex items-center justify-between">
            <span className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider">
              Google Accounts
            </span>
            <button
              type="button"
              data-testid="view-limits-btn"
              onClick={() => {
                setIsOpen(false);
                toggleLimitsModal(true);
              }}
              className="text-[11px] text-blue-400 hover:text-blue-300 font-medium"
            >
              Quota Panel ↗
            </button>
          </div>

          <div className="max-h-60 overflow-y-auto py-1 divide-y divide-zinc-800/40">
            {accounts.map((acc) => {
              const isActive = acc.id === (activeAccount?.id || activeAccountId);
              return (
                <div
                  key={acc.id}
                  data-testid={`account-item-${acc.id}`}
                  className={`px-3 py-2 flex items-center justify-between hover:bg-zinc-800/50 transition-colors cursor-pointer ${
                    isActive ? 'bg-zinc-800/30' : ''
                  }`}
                  onClick={() => {
                    activateAccount(acc.id);
                    setIsOpen(false);
                  }}
                >
                  <div className="flex items-center gap-2 truncate pr-2">
                    {getHealthIndicator(acc)}
                    <div className="flex flex-col truncate">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-medium text-zinc-200 truncate">
                          {acc.name}
                        </span>
                        {isActive && (
                          <span className="text-[10px] text-emerald-400 font-semibold">✓</span>
                        )}
                      </div>
                      <span className="text-[10px] text-zinc-500 truncate font-mono">
                        {acc.health_status}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5 shrink-0">
                    {getTierBadge(acc.tier)}
                    {accounts.length > 1 && !isActive && (
                      <button
                        type="button"
                        data-testid={`delete-account-${acc.id}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (confirm(`Remove account ${acc.name}?`)) {
                            deleteAccount(acc.id);
                          }
                        }}
                        className="p-1 text-zinc-500 hover:text-red-400 rounded transition-colors text-xs"
                        title="Remove account"
                      >
                        ✕
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="px-3 py-2 border-t border-zinc-800/80 bg-zinc-950/40 flex items-center justify-between text-xs">
            <span className="text-zinc-400 text-[11px] flex items-center gap-1.5">
              <span>Auto-Failover</span>
              <span className="text-[10px] text-zinc-500">(1037/429/1061)</span>
            </span>
            <button
              type="button"
              data-testid="toggle-failover-btn"
              onClick={() => toggleFailover()}
              className={`px-2 py-0.5 text-[11px] font-medium rounded-full border transition-colors ${
                failoverConfig?.enabled
                  ? 'bg-emerald-600/20 text-emerald-300 border-emerald-500/40'
                  : 'bg-zinc-800 text-zinc-400 border-zinc-700'
              }`}
            >
              {failoverConfig?.enabled ? 'ENABLED' : 'DISABLED'}
            </button>
          </div>

          <div className="px-2 pt-1 border-t border-zinc-800/80">
            <button
              type="button"
              data-testid="add-account-btn"
              onClick={() => {
                setIsOpen(false);
                toggleAddAccount(true);
              }}
              className="w-full text-center py-1.5 text-xs font-medium text-blue-400 hover:text-blue-300 hover:bg-zinc-800/60 rounded-lg transition-colors"
            >
              + Add Google Account
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
