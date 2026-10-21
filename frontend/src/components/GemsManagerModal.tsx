import React, { useState, useEffect, useMemo } from 'react';
import { useSessionStore } from '../store/sessionStore';
import { GemOption } from '../types';
import * as api from '../services/api';

const PINNED_GEMS_KEY = 'gemini_pinned_gems';

export const GemsManagerModal: React.FC = () => {
  const isGemsManagerOpen = useSessionStore((s) => s.isGemsManagerOpen);
  const toggleGemsManager = useSessionStore((s) => s.toggleGemsManager);
  const selectedGemId = useSessionStore((s) => s.selectedGemId);
  const setSelectedGemId = useSessionStore((s) => s.setSelectedGemId);

  const [activeTab, setActiveTab] = useState<'all' | 'custom' | 'system' | 'pinned'>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [allGems, setAllGems] = useState<GemOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [pinnedGemIds, setPinnedGemIds] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem(PINNED_GEMS_KEY);
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });

  useEffect(() => {
    if (!isGemsManagerOpen) return;
    setLoading(true);
    api
      .fetchDetailedGems('all')
      .then((res) => {
        setAllGems(res.gems || []);
      })
      .catch((err) => {
        console.warn('Failed loading detailed gems', err);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [isGemsManagerOpen]);

  const togglePinGem = (gemId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setPinnedGemIds((prev) => {
      const next = prev.includes(gemId) ? prev.filter((id) => id !== gemId) : [...prev, gemId];
      try {
        localStorage.setItem(PINNED_GEMS_KEY, JSON.stringify(next));
      } catch {
        // ignore storage errors
      }
      return next;
    });
  };

  const filteredGems = useMemo(() => {
    return allGems.filter((gem) => {
      // Tab filter
      if (activeTab === 'custom' && gem.is_builtin) return false;
      if (activeTab === 'system' && !gem.is_builtin) return false;
      if (activeTab === 'pinned' && !pinnedGemIds.includes(gem.id)) return false;

      // Search filter
      if (!searchQuery.trim()) return true;
      const q = searchQuery.toLowerCase();
      const name = (gem.name || '').toLowerCase();
      const desc = (gem.description || '').toLowerCase();
      const prompt = (gem.prompt || '').toLowerCase();
      return name.includes(q) || desc.includes(q) || prompt.includes(q);
    });
  }, [allGems, activeTab, searchQuery, pinnedGemIds]);

  if (!isGemsManagerOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-200"
      onClick={() => toggleGemsManager(false)}
    >
      <div
        className="w-full max-w-3xl max-h-[85vh] bg-zinc-900 text-zinc-200 border border-zinc-800 rounded-2xl shadow-2xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-zinc-800">
          <div className="flex items-center gap-3">
            <span className="text-2xl">💎</span>
            <div>
              <h2 className="text-lg font-semibold text-zinc-100">Gems Manager</h2>
              <p className="text-xs text-zinc-400">Specialized experts and system instructions</p>
            </div>
          </div>
          <button
            onClick={() => toggleGemsManager(false)}
            className="p-1.5 text-zinc-400 hover:text-zinc-200 rounded-lg hover:bg-zinc-800 transition"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* Filter bar & Search */}
        <div className="p-4 border-b border-zinc-800 space-y-3 bg-zinc-950/60">
          <div className="flex items-center justify-between gap-4">
            {/* Tabs */}
            <div className="flex bg-zinc-900 rounded-lg p-0.5 border border-zinc-800 text-xs font-medium">
              {(
                [
                  { id: 'all', label: 'All' },
                  { id: 'custom', label: 'Custom' },
                  { id: 'system', label: 'System' },
                  { id: 'pinned', label: 'Pinned' },
                ] as const
              ).map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`px-3 py-1.5 rounded-md transition ${
                    activeTab === tab.id
                      ? 'bg-blue-600 text-white shadow-sm'
                      : 'text-zinc-400 hover:text-zinc-200'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* Clear Selection */}
            {selectedGemId && (
              <button
                onClick={() => setSelectedGemId(null)}
                className="text-xs text-red-400 hover:text-red-300 transition"
              >
                Reset selected Gem
              </button>
            )}
          </div>

          {/* Search Input */}
          <div className="relative">
            <input
              type="text"
              placeholder="Search by name, description, or instructions..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-zinc-900 border border-zinc-700 rounded-xl px-4 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="absolute right-3 top-2.5 text-xs text-zinc-400 hover:text-zinc-200"
              >
                ✕
              </button>
            )}
          </div>
        </div>

        {/* Gems List Grid */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3 max-h-[500px]">
          {loading ? (
            <div className="py-12 text-center text-sm text-zinc-400">
              <span className="inline-block animate-spin mr-2">⚙</span> Loading Gems...
            </div>
          ) : filteredGems.length === 0 ? (
            <div className="py-12 text-center text-sm text-zinc-400">
              {searchQuery ? 'No Gems matched your search' : 'Gems list is empty'}
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {filteredGems.map((gem) => {
                const isSelected = selectedGemId === gem.id;
                const isPinned = pinnedGemIds.includes(gem.id);
                return (
                  <div
                    key={gem.id}
                    onClick={() => {
                      setSelectedGemId(isSelected ? null : gem.id);
                      toggleGemsManager(false);
                    }}
                    className={`cursor-pointer group relative flex flex-col justify-between p-3.5 rounded-xl border transition ${
                      isSelected
                        ? 'border-blue-500 bg-blue-950/20 shadow-md'
                        : 'border-zinc-800 bg-zinc-850 bg-zinc-800/50 hover:border-zinc-700 hover:bg-zinc-800/80'
                    }`}
                  >
                    <div>
                      {/* Top row: Icon + Title + Pin */}
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-2.5">
                          <span className="text-xl">{gem.icon || '💎'}</span>
                          <div>
                            <h3 className="text-sm font-medium text-zinc-100 group-hover:text-blue-400 transition">
                              {gem.name}
                            </h3>
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 border border-zinc-700/50">
                              {gem.is_builtin ? 'System' : 'Custom'}
                            </span>
                          </div>
                        </div>

                        <button
                          onClick={(e) => togglePinGem(gem.id, e)}
                          title={isPinned ? 'Unpin' : 'Pin'}
                          className={`p-1 rounded text-xs transition ${
                            isPinned
                              ? 'text-yellow-400 hover:text-yellow-300'
                              : 'text-zinc-500 opacity-40 group-hover:opacity-100 hover:text-zinc-300'
                          }`}
                        >
                          📌
                        </button>
                      </div>

                      {/* Description */}
                      {gem.description && (
                        <p className="mt-2 text-xs text-zinc-400 line-clamp-2 leading-relaxed">
                          {gem.description}
                        </p>
                      )}

                      {/* Prompt / Instructions Preview */}
                      {gem.prompt && (
                        <div className="mt-2 p-2 rounded bg-black/30 border border-zinc-800 text-[11px] text-zinc-400 font-mono line-clamp-2">
                          {gem.prompt}
                        </div>
                      )}
                    </div>

                    {/* Footer / Apply */}
                    <div className="mt-3 pt-2 border-t border-zinc-800 flex items-center justify-between">
                      <span className="text-[10px] text-zinc-500">ID: {gem.id.slice(0, 10)}...</span>
                      <button
                        className={`text-xs px-2.5 py-1 rounded-md font-medium transition ${
                          isSelected
                            ? 'bg-blue-600 text-white'
                            : 'bg-zinc-800 text-zinc-300 hover:bg-zinc-700'
                        }`}
                      >
                        {isSelected ? '✓ Active' : 'Select'}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
