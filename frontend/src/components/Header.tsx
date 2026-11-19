import React, { useState } from 'react';
import { ModelSelector } from './ModelSelector';
import { AccountSwitcher } from './AccountSwitcher';
import { useSessionStore } from '../store/sessionStore';
import { useAccountsStore } from '../store/accountsStore';
import { useChatStore } from '../store/chatStore';
import { useNotebookLMStore } from '../store/notebooklmStore';
import { useWorkspaceStore } from '../store/workspaceStore';
import { exportConversationUrl } from '../services/api';

export interface HeaderProps {
  onToggleSidebar?: () => void;
  onToggleClaudeSwarm?: () => void;
  isClaudeSwarmOpen?: boolean;
  onToggleFlowStudio?: () => void;
  isFlowStudioOpen?: boolean;
  onToggleMissionControl?: () => void;
  isMissionControlOpen?: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  onToggleSidebar,
  onToggleClaudeSwarm,
  isClaudeSwarmOpen = false,
  onToggleFlowStudio,
  isFlowStudioOpen = false,
  onToggleMissionControl,
  isMissionControlOpen = false,
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
  const activeConversationId = useChatStore((s) => s.activeConversationId);
  const activeLeafMessageId = useChatStore((s) => s.activeLeafMessageId);
  const [showExportMenu, setShowExportMenu] = useState(false);
  const toggleNotebookLM = useNotebookLMStore((s) => s.toggleDrawer);
  const isNotebookLMOpen = useNotebookLMStore((s) => s.isOpen);
  const openWorkspaceHub = useWorkspaceStore((s) => s.openHub);
  const isWorkspaceHubOpen = useWorkspaceStore((s) => s.isHubOpen);
  const promptsEnabled = useWorkspaceStore((s) => s.promptsEnabled);
  const activePromptId = useWorkspaceStore((s) => s.activePromptId);
  const useGlobalPromptForChat = useWorkspaceStore((s) => s.useGlobalPromptForChat);
  const enabledSkillIds = useWorkspaceStore((s) => s.enabledSkillIds);
  const connectors = useWorkspaceStore((s) => s.connectors);
  const activeWorkspaceCount =
    (promptsEnabled && useGlobalPromptForChat && activePromptId ? 1 : 0) +
    enabledSkillIds.length +
    connectors.filter((c) => c.enabled).length;

  const statusTextClass =
    backendStatus === 'online' && isAuthenticated
      ? 'text-emerald-400'
      : backendStatus === 'online'
      ? 'text-amber-400'
      : 'text-red-400';

  return (
    <header className="relative h-12 border-b border-zinc-800/80 px-2.5 flex items-center justify-between gap-2 select-none bg-zinc-950/95 shrink-0 z-30">
      <div className="flex items-center gap-1.5 min-w-0 shrink-0">
        {onToggleSidebar && (
          <button
            type="button"
            data-testid="toggle-sidebar-btn"
            onClick={onToggleSidebar}
            className="p-1.5 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 rounded-lg md:hidden text-sm leading-none shrink-0"
            aria-label="Toggle sidebar"
          >
            ☰
          </button>
        )}
        <span className="hidden xl:inline font-semibold text-sm tracking-tight text-blue-400 whitespace-nowrap">
          Gemini Local UI
        </span>
        <ModelSelector />
        <AccountSwitcher />
      </div>

      <div className="flex items-center gap-1 shrink-0">
        {/* Incognito Toggle Button */}
        <button
          type="button"
          data-testid="toggle-incognito-btn"
          onClick={() => setIncognito(!isIncognito)}
          className={`px-1.5 py-1 rounded-lg transition-colors text-xs flex items-center gap-1 whitespace-nowrap ${
            isIncognito
              ? 'bg-purple-900/50 text-purple-300 border border-purple-500/50 font-medium'
              : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
          }`}
          aria-label="Incognito"
          title={isIncognito ? 'Disable Incognito' : 'Enable temporary chat (Incognito)'}
        >
          <span>🕶️</span>
          <span className="hidden 2xl:inline">Incognito</span>
        </button>

        {/* Gems Manager Button */}
        <button
          type="button"
          data-testid="open-gems-btn"
          onClick={() => toggleGemsManager(true)}
          className={`px-1.5 py-1 rounded-lg transition-colors text-xs flex items-center gap-1 whitespace-nowrap ${
            selectedGemId
              ? 'bg-blue-900/40 text-blue-300 border border-blue-500/40 font-medium'
              : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
          }`}
          aria-label="Gems Manager"
          title="Gems Manager"
        >
          <span>💎</span>
          <span className="hidden 2xl:inline">Gems</span>
        </button>

        {/* NotebookLM Workspace Button */}
        <button
          type="button"
          data-testid="open-notebooklm-btn"
          onClick={() => toggleNotebookLM()}
          className={`px-1.5 py-1 rounded-lg transition-colors text-xs flex items-center gap-1 whitespace-nowrap ${
            isNotebookLMOpen
              ? 'bg-amber-900/50 text-amber-300 border border-amber-500/50 font-medium'
              : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
          }`}
          aria-label="NotebookLM Workspace"
          title="NotebookLM Workspace (Sources & References)"
        >
          <span>📚</span>
          <span className="hidden 2xl:inline">NotebookLM</span>
        </button>

        {/* Google Flow Educational Video Studio Button */}
        <button
          type="button"
          data-testid="open-flow-studio-btn"
          onClick={onToggleFlowStudio}
          className={`px-1.5 py-1 rounded-lg transition-colors text-xs flex items-center gap-1 whitespace-nowrap ${
            isFlowStudioOpen
              ? 'bg-pink-900/50 text-pink-300 border border-pink-500/50 font-medium'
              : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
          }`}
          aria-label="Flow Studio"
          title="Google Flow Educational Video Studio (Veo 3.1)"
        >
          <span>🎬</span>
          <span className="hidden 2xl:inline">Flow Studio</span>
        </button>

        {/* AGY Mission Control Button */}
        <button
          type="button"
          data-testid="open-mission-control-btn"
          onClick={onToggleMissionControl}
          className={`px-1.5 py-1 rounded-lg transition-colors text-xs flex items-center gap-1 whitespace-nowrap ${
            isMissionControlOpen
              ? 'bg-cyan-900/50 text-cyan-300 border border-cyan-500/50 font-medium'
              : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
          }`}
          aria-label="Mission Control"
          title="AGY Mission Control (L1-L4 Unified Bus & Plugin Hub)"
        >
          <span>🛰️</span>
          <span className="hidden 2xl:inline">Mission Control</span>
        </button>

        {/* Claude Code Swarm Monitor Button */}
        {onToggleClaudeSwarm && (
          <button
            type="button"
            data-testid="open-claude-swarm-btn"
            onClick={onToggleClaudeSwarm}
            className={`px-1.5 py-1 rounded-lg transition-colors text-xs flex items-center gap-1 whitespace-nowrap ${
              isClaudeSwarmOpen
                ? 'bg-emerald-900/50 text-emerald-300 border border-emerald-500/50 font-medium'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
            }`}
            aria-label="Claude Swarm"
            title="Claude Code Swarm Monitor & .claude Plugin Hub"
          >
            <span>🤖</span>
            <span className="hidden 2xl:inline">Claude Swarm</span>
          </button>
        )}

        {/* Workspace Hub Button */}
        <button
          type="button"
          data-testid="open-workspace-hub-btn"
          onClick={() => openWorkspaceHub()}
          className={`px-2 py-1 rounded-lg transition-colors text-xs flex items-center gap-1.5 whitespace-nowrap ${
            isWorkspaceHubOpen || activeWorkspaceCount > 0
              ? 'bg-indigo-900/40 text-indigo-300 border border-indigo-500/40 font-medium'
              : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
          }`}
          aria-label="Workspace Hub"
          title="Workspace Hub (Global Prompts, Skills & MCP Connectors)"
        >
          <span>🧩</span>
          <span className="hidden xl:inline">Workspace</span>
          {activeWorkspaceCount > 0 && (
            <span className="px-1.5 py-0.2 rounded-full bg-indigo-600/80 text-white text-[10px] font-mono leading-tight">
              {activeWorkspaceCount}
            </span>
          )}
        </button>

        {activeConversationId && !activeConversationId.startsWith('incognito_') && (
          <div className="relative shrink-0">
            <button
              type="button"
              data-testid="export-chat-menu-btn"
              onClick={() => setShowExportMenu((p) => !p)}
              className="p-1.5 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 rounded-lg transition-colors text-xs flex items-center gap-1"
              aria-label="Export conversation"
              title="Export chat (Markdown / JSON)"
            >
              ⇩
            </button>
            {showExportMenu && (
              <>
                <div
                  className="fixed inset-0 z-40"
                  onClick={() => setShowExportMenu(false)}
                />
                <div
                  data-testid="export-chat-menu"
                  className="absolute right-0 top-full mt-1.5 w-44 p-1.5 rounded-xl bg-zinc-900 border border-zinc-800 shadow-2xl z-50 text-xs space-y-1"
                >
                  <a
                    data-testid="export-chat-md-btn"
                    href={
                      activeLeafMessageId
                        ? `${exportConversationUrl(activeConversationId, 'markdown')}&leaf_id=${activeLeafMessageId}`
                        : exportConversationUrl(activeConversationId, 'markdown')
                    }
                    download
                    onClick={() => setShowExportMenu(false)}
                    className="block w-full text-left px-2.5 py-1.5 rounded-lg text-zinc-200 hover:bg-zinc-800"
                  >
                    Export Markdown (.md)
                  </a>
                  <a
                    data-testid="export-chat-json-btn"
                    href={
                      activeLeafMessageId
                        ? `${exportConversationUrl(activeConversationId, 'json')}&leaf_id=${activeLeafMessageId}`
                        : exportConversationUrl(activeConversationId, 'json')
                    }
                    download
                    onClick={() => setShowExportMenu(false)}
                    className="block w-full text-left px-2.5 py-1.5 rounded-lg text-zinc-200 hover:bg-zinc-800"
                  >
                    Export JSON (.json)
                  </a>
                </div>
              </>
            )}
          </div>
        )}

        <button
          type="button"
          data-testid="open-limits-btn"
          onClick={() => toggleLimitsModal(true)}
          className="p-1.5 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 rounded-lg transition-colors text-xs flex items-center gap-1 shrink-0"
          aria-label="Account Limits & Telemetry"
          title="Account Limits & Telemetry"
        >
          📊
        </button>

        <button
          type="button"
          data-testid="open-search-btn"
          onClick={() => toggleSearch(true)}
          className="p-1.5 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 rounded-lg transition-colors text-xs shrink-0"
          aria-label="Search"
          title="Search conversations"
        >
          ⌕
        </button>

        <button
          type="button"
          data-testid="open-settings-btn"
          onClick={() => toggleSettings(true)}
          className={`flex items-center gap-1 px-2 py-1 rounded-lg text-xs hover:bg-zinc-800/60 transition-colors font-mono whitespace-nowrap shrink-0 ${statusTextClass}`}
          title="Session & Credentials"
        >
          ● {isAuthenticated ? 'Connected' : 'Auth'}
        </button>
      </div>
    </header>
  );
};
