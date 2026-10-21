import React, { useState } from 'react';
import { useSessionStore } from '../store/sessionStore';

const REASONING_LEVELS: Array<{ value: 1 | 4 | 2; label: string; sub: string }> = [
  { value: 1, label: 'Low', sub: 'Fast and efficient' },
  { value: 4, label: 'Medium', sub: 'Balanced' },
  { value: 2, label: 'High', sub: 'Deep' },
];

export const ModelSelector: React.FC = () => {
  const [isOpen, setIsOpen] = useState(false);
  const models = useSessionStore((s) => s.models);
  const selectedModel = useSessionStore((s) => s.selectedModel);
  const setSelectedModel = useSessionStore((s) => s.setSelectedModel);
  const reasoningLevel = useSessionStore((s) => s.reasoningLevel);
  const setReasoningLevel = useSessionStore((s) => s.setReasoningLevel);
  const featureMode = useSessionStore((s) => s.featureMode);
  const setFeatureMode = useSessionStore((s) => s.setFeatureMode);
  const gems = useSessionStore((s) => s.gems);
  const selectedGemId = useSessionStore((s) => s.selectedGemId);
  const setSelectedGemId = useSessionStore((s) => s.setSelectedGemId);

  const visibleModels =
    featureMode === 'task'
      ? models.filter((m) => m.id === '56fdd199312815e2' || m.id === '95a4c0abcba24364' || m.display_name.includes('Argon') || m.display_name.includes('3.8'))
      : models;

  const activeModelObj = models.find((m) => m.id === selectedModel);
  const activeGemObj = gems.find((g) => g.id === selectedGemId);
  const activeReasoningObj = REASONING_LEVELS.find((r) => r.value === reasoningLevel) || REASONING_LEVELS[0];

  return (
    <div className="relative flex items-center gap-2">
      <div
        data-testid="header-mode-toggle"
        className="flex items-center p-0.5 rounded-full bg-zinc-900 border border-zinc-800 text-[11px]"
      >
        <button
          type="button"
          onClick={() => setFeatureMode('chat')}
          className={`px-2.5 py-0.5 rounded-full transition-colors ${
            featureMode === 'chat'
              ? 'bg-zinc-800 text-zinc-100 font-medium shadow-sm'
              : 'text-zinc-400 hover:text-zinc-200'
          }`}
        >
          Chat
        </button>
        <button
          type="button"
          onClick={() => setFeatureMode('task')}
          className={`flex items-center gap-1 px-2.5 py-0.5 rounded-full transition-colors ${
            featureMode === 'task'
              ? 'bg-blue-600/30 text-blue-200 border border-blue-500/40 font-medium shadow-sm'
              : 'text-zinc-400 hover:text-zinc-200'
          }`}
        >
          <span>✦</span>
          <span>Task</span>
        </button>
      </div>

      <button
        type="button"
        data-testid="model-selector-btn"
        onClick={() => setIsOpen((prev) => !prev)}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-zinc-800/80 hover:bg-zinc-700/80 border border-zinc-700/50 text-xs font-medium text-zinc-200 transition-colors"
      >
        <span>
          {activeGemObj ? `Gem: ${activeGemObj.title}` : activeModelObj?.display_name || selectedModel}
        </span>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-900/90 text-zinc-400 border border-zinc-700/50">
          {activeReasoningObj.label}
        </span>
        <svg className="w-3 h-3 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {isOpen && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setIsOpen(false)}
          />
          <div
            data-testid="model-selector-dropdown"
            className="absolute left-0 top-full mt-1.5 w-72 p-1.5 rounded-xl bg-zinc-900 border border-zinc-800 shadow-2xl z-50 text-xs space-y-1"
          >
            <div className="px-2 py-1 text-[10px] font-semibold uppercase text-zinc-500">
              {featureMode === 'task' ? 'Task Models' : 'Models'}
            </div>
            {(visibleModels.length > 0 ? visibleModels : models).map((m) => {
              const isSelected = selectedModel === m.id && !selectedGemId;
              return (
                <button
                  key={m.id}
                  type="button"
                  data-testid={`model-option-${m.id}`}
                  onClick={() => {
                    setSelectedModel(m.id);
                    setSelectedGemId(null);
                    setIsOpen(false);
                  }}
                  className={`w-full text-left px-2.5 py-1.5 rounded-lg flex items-center justify-between transition-colors ${
                    isSelected
                      ? 'bg-blue-600 text-white font-medium'
                      : 'text-zinc-200 hover:bg-zinc-800'
                  }`}
                >
                  <div className="flex flex-col min-w-0 pr-2">
                    <span className="truncate font-medium">{m.display_name}</span>
                    {m.description && (
                      <span
                        className={`text-[10px] truncate ${
                          isSelected ? 'text-blue-100/90' : 'text-zinc-400'
                        }`}
                      >
                        {m.description}
                      </span>
                    )}
                  </div>
                  {m.id === '95a4c0abcba24364' || m.id === 'gemini-4-argon' ? (
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0 ${
                        isSelected ? 'bg-blue-500 text-white' : 'bg-zinc-800 text-zinc-300'
                      }`}
                    >
                      New
                    </span>
                  ) : m.supports_thinking ? (
                    <span className="text-[10px] opacity-75 font-mono shrink-0">Thinking</span>
                  ) : null}
                </button>
              );
            })}

            <div className="px-2 pt-2 pb-1 text-[10px] font-semibold uppercase text-zinc-500 border-t border-zinc-800">
              Reasoning Level
            </div>
            <div className="grid grid-cols-3 gap-1 px-1 pb-1">
              {REASONING_LEVELS.map((r) => {
                const active = reasoningLevel === r.value;
                return (
                  <button
                    key={r.value}
                    type="button"
                    data-testid={`reasoning-level-${r.value}`}
                    title={r.sub}
                    onClick={() => setReasoningLevel(r.value)}
                    className={`px-2 py-1.5 rounded-lg text-center transition-colors border ${
                      active
                        ? 'bg-blue-600/20 border-blue-500 text-blue-300 font-medium'
                        : 'bg-zinc-950/60 border-zinc-800 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
                    }`}
                  >
                    <div className="text-[11px]">{r.label}</div>
                    <div className="text-[9px] opacity-70 truncate">{r.sub.split(' ')[0]}</div>
                  </button>
                );
              })}
            </div>

            {gems.length > 0 && (
              <>
                <div className="px-2 pt-2 pb-1 text-[10px] font-semibold uppercase text-zinc-500 border-t border-zinc-800">
                  Custom Gems
                </div>
                {gems.map((g) => (
                  <button
                    key={g.id}
                    type="button"
                    data-testid={`gem-option-${g.id}`}
                    onClick={() => {
                      setSelectedGemId(g.id);
                      setIsOpen(false);
                    }}
                    className={`w-full text-left px-2.5 py-1.5 rounded-lg flex items-center justify-between ${
                      selectedGemId === g.id
                        ? 'bg-blue-600 text-white font-medium'
                        : 'text-zinc-300 hover:bg-zinc-800'
                    }`}
                  >
                    <span className="truncate">{g.title}</span>
                  </button>
                ))}
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
};
