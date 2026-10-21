import React from 'react';
import { ModelSelector } from './ModelSelector';
import { AccountSwitcher } from './AccountSwitcher';
import { useSessionStore } from '../store/sessionStore';
import { useAccountsStore } from '../store/accountsStore';
import { useChatStore } from '../store/chatStore';
import { useNotebookLMStore } from '../store/notebooklmStore';

export interface HeaderProps {
  onToggleSidebar?: () => void;
  onToggleClaudeSwarm?: () => void;
  isClaudeSwarmOpen?: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  onToggleSidebar,
  onToggleClaudeSwarm,
  isClaudeSwarmOpen = false,
}) => {
  const backendStatus = useSessionStore((s) => s.backendStatus);
  const isAuthenticated = useSessionStore((s) => s.isAuthenticated);
  const toggleSearch = useSessionStore((s) => s.toggleSearch);
  const toggleSettings = useSessionStore((s) => s.toggleSettings);
  const toggleGemsManager = useSessionStore((s) => s.toggleGemsManager);
  const selectedGemId = useSessionStore((s) => s.selectedGemId);
  const toggleLimitsModal = useAccountsStore((s) => s.toggleLimitsModal);
  const isIncognito = useChatStore((s) => s.isIncognito);
  const setIncognito = useChatStore((s) => s.setIncognito);
  const toggleNotebookLM = useNotebookLMStore((s) => s.toggleDrawer);
  const isNotebookLMOpen = useNotebookLMStore((s) => s.isOpen);

  const statusTextClass =

    backendStatus === 'online' && isAuthenticated
      ? 'text-emerald-400'
      : backendStatus === 'online'
      ? 'text-amber-400'
      : 'text-red-400';

  return (
    <header className="h-12 border-b border-zinc-800/80 px-3 flex items-center justify-between select-none bg-zinc-950/70 backdrop-blur shrink-0 z-30">
      <div className="flex items-center gap-2">
        {onToggleSidebar && (
          <button
            type="button"
            data-testid="toggle-sidebar-btn"
            onClick={onToggleSidebar}
            className="p-1.5 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 rounded-lg md:hidden text-sm leading-none"
            aria-label="Toggle sidebar"
          >
            ☰
          </button>
        )}
        <span className="font-semibold text-sm tracking-tight text-blue-400">
          Gemini Local UI
        </span>
        <ModelSelector />
        <AccountSwitcher />
      </div>

      <div className="flex items-center gap-2">
        {/* Incognito Toggle Button */}
        <button
          type="button"
          data-testid="toggle-incognito-btn"
          onClick={() => setIncognito(!isIncognito)}
          className={`px-2 py-1 rounded-lg transition-colors text-xs flex items-center gap-1.5 ${
            isIncognito
              ? 'bg-purple-900/50 text-purple-300 border border-purple-500/50 font-medium'
              : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
          }`}
          title={isIncognito ? 'Disable Incognito' : 'Enable temporary chat (Incognito)'}
        >
          <span>🕶️</span>
          <span className="hidden sm:inline">Incognito</span>
        </button>

        {/* Gems Manager Button */}
        <button
          type="button"
          data-testid="open-gems-btn"
          onClick={() => toggleGemsManager(true)}
          className={`px-2 py-1 rounded-lg transition-colors text-xs flex items-center gap-1.5 ${
            selectedGemId
              ? 'bg-blue-900/40 text-blue-300 border border-blue-500/40 font-medium'
              : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
          }`}
          title="Gems Manager"
        >
          <span>💎</span>
          <span className="hidden sm:inline">Gems</span>
        </button>

        {/* NotebookLM Workspace Button */}
        <button
          type="button"
          data-testid="open-notebooklm-btn"
          onClick={() => toggleNotebookLM()}
          className={`px-2 py-1 rounded-lg transition-colors text-xs flex items-center gap-1.5 ${
            isNotebookLMOpen
              ? 'bg-amber-900/50 text-amber-300 border border-amber-500/50 font-medium'
              : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
          }`}
          title="NotebookLM Workspace (Sources & References)"
        >
          <span>📚</span>
          <span className="hidden sm:inline">NotebookLM</span>
        </button>

        {/* Claude Code Swarm Monitor Button */}
        {onToggleClaudeSwarm && (
          <button
            type="button"
            data-testid="open-claude-swarm-btn"
            onClick={onToggleClaudeSwarm}
            className={`px-2 py-1 rounded-lg transition-colors text-xs flex items-center gap-1.5 ${
              isClaudeSwarmOpen
                ? 'bg-emerald-900/50 text-emerald-300 border border-emerald-500/50 font-medium'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
            }`}
            title="Claude Code Swarm Monitor & .claude Plugin Hub"
          >
            <span>🤖</span>
            <span className="hidden sm:inline">Claude Swarm</span>
          </button>
        )}

        <button
          type="button"
          data-testid="open-limits-btn"

          onClick={() => toggleLimitsModal(true)}
          className="p-1.5 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 rounded-lg transition-colors text-xs flex items-center gap-1"
          aria-label="Account Limits & Telemetry"
          title="Account Limits & Telemetry"
        >
          📊
        </button>

        <button
          type="button"
          data-testid="open-search-btn"
          onClick={() => toggleSearch(true)}
          className="p-1.5 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 rounded-lg transition-colors text-xs"
          aria-label="Search"
          title="Search conversations"
        >
          ⌕
        </button>

        <button
          type="button"
          data-testid="open-settings-btn"
          onClick={() => toggleSettings(true)}
          className={`flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs hover:bg-zinc-800/60 transition-colors font-mono ${statusTextClass}`}
          title="Session & Credentials"
        >
          ● {isAuthenticated ? 'Connected' : 'Auth'}
        </button>
      </div>
    </header>
  );
};
