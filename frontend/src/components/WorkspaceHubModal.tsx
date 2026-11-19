import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useWorkspaceStore, WorkspaceHubTab } from '../store/workspaceStore';
import { GlobalPromptsTab } from './workspace/GlobalPromptsTab';
import { SkillsTab } from './workspace/SkillsTab';
import { ConnectorsTab } from './workspace/ConnectorsTab';

const TABS: Array<{ id: WorkspaceHubTab; testId: string; label: string }> = [
  { id: 'prompts', testId: 'workspace-tab-prompts', label: 'Global Prompts' },
  { id: 'skills', testId: 'workspace-tab-skills', label: 'Skills' },
  { id: 'connectors', testId: 'workspace-tab-connectors', label: 'MCP Connectors' },
];

export const WorkspaceHubModal: React.FC = () => {
  const isHubOpen = useWorkspaceStore((s) => s.isHubOpen);
  const activeHubTab = useWorkspaceStore((s) => s.activeHubTab);
  const setActiveHubTab = useWorkspaceStore((s) => s.setActiveHubTab);
  const closeHub = useWorkspaceStore((s) => s.closeHub);
  const error = useWorkspaceStore((s) => s.error);

  useEffect(() => {
    if (!isHubOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeHub();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isHubOpen, closeHub]);

  if (!isHubOpen) return null;

  const modal = (
    <div
      data-testid="workspace-hub-modal"
      className="fixed inset-0 z-[90] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
      onClick={closeHub}
    >
      <div
        className="w-full max-w-3xl max-h-[88vh] flex flex-col rounded-2xl bg-zinc-900 border border-zinc-800 shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800 bg-zinc-950/70">
          <div className="flex items-center gap-3">
            <h2 className="text-sm font-semibold text-zinc-100">Workspace Hub</h2>
            <div className="flex items-center gap-1 p-0.5 rounded-lg bg-zinc-900 border border-zinc-800 text-xs">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  data-testid={t.testId}
                  onClick={() => setActiveHubTab(t.id)}
                  className={`px-2.5 py-1 rounded-md transition-colors ${
                    activeHubTab === t.id
                      ? 'bg-zinc-800 text-white font-medium'
                      : 'text-zinc-400 hover:text-zinc-200'
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>

          <button
            type="button"
            data-testid="workspace-hub-close-btn"
            onClick={closeHub}
            className="p-1 text-zinc-400 hover:text-white rounded hover:bg-zinc-800 text-sm leading-none"
            title="Close (Esc)"
          >
            ✕
          </button>
        </div>

        {error && (
          <div className="mx-4 mt-3 p-2 rounded-lg bg-red-950/50 border border-red-800/60 text-xs text-red-300">
            {error}
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-4">
          {activeHubTab === 'prompts' && <GlobalPromptsTab />}
          {activeHubTab === 'skills' && <SkillsTab />}
          {activeHubTab === 'connectors' && <ConnectorsTab />}
        </div>
      </div>
    </div>
  );

  return typeof document !== 'undefined' ? createPortal(modal, document.body) : modal;
};
