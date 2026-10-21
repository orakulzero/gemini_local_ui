import React, { useEffect, useState, useCallback } from 'react';
import { Header } from './components/Header';
import { Sidebar } from './components/Sidebar';
import { VirtualChatList } from './components/VirtualChatList';
import { PromptInput } from './components/PromptInput';
import { SearchModal } from './components/SearchModal';
import { SettingsModal } from './components/SettingsModal';
import { AccountLimitsModal } from './components/AccountLimitsModal';
import { AddAccountModal } from './components/AddAccountModal';
import { CanvasPanel } from './components/CanvasPanel';
import { GemsManagerModal } from './components/GemsManagerModal';
import { NotebookLMDrawer } from './components/NotebookLMDrawer';
import { ClaudePluginsPanel } from './components/ClaudePluginsPanel';
import { useChatStore } from './store/chatStore';
import { useSessionStore } from './store/sessionStore';
import { useCanvasStore } from './store/canvasStore';

export const App: React.FC = () => {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [claudeSwarmOpen, setClaudeSwarmOpen] = useState(false);

  const activeThread = useChatStore((s) => s.activeThread);
  const isGenerating = useChatStore((s) => s.isGenerating);
  const switchBranch = useChatStore((s) => s.switchBranch);
  const editMessageAndBranch = useChatStore((s) => s.editMessageAndBranch);
  const loadConversations = useChatStore((s) => s.loadConversations);
  const fetchSessionStatus = useSessionStore((s) => s.fetchSessionStatus);
  const fetchModels = useSessionStore((s) => s.fetchModels);
  const isCanvasOpen = useCanvasStore((s) => s.isOpen);

  // Initialize session and conversation history
  useEffect(() => {
    fetchSessionStatus();
    fetchModels();
    loadConversations();
  }, [fetchSessionStatus, fetchModels, loadConversations]);

  const handleEditMessage = useCallback(
    (id: number, newContent: string) => {
      editMessageAndBranch(id, newContent);
    },
    [editMessageAndBranch]
  );

  const handleSelectBranch = useCallback(
    (targetId: number) => {
      switchBranch(targetId);
    },
    [switchBranch]
  );

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-zinc-950 text-zinc-100">
      <Sidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      {/* Main chat viewport (60% split when Canvas is open, or full width) */}
      <div
        className={`flex flex-col h-full min-w-0 overflow-hidden relative transition-all duration-200 ${
          isCanvasOpen ? 'w-full md:w-[60%]' : 'flex-1'
        }`}
      >
        <Header
          onToggleSidebar={() => setSidebarOpen((prev) => !prev)}
          onToggleClaudeSwarm={() => setClaudeSwarmOpen((prev) => !prev)}
          isClaudeSwarmOpen={claudeSwarmOpen}
        />

        <main className="flex-1 min-h-0 relative">
          <VirtualChatList
            messages={activeThread}
            isStreaming={isGenerating}
            onEditMessage={handleEditMessage}
            onSelectBranch={handleSelectBranch}
          />
        </main>

        <footer className="p-3 bg-zinc-950/80 backdrop-blur border-t border-zinc-800/60 shrink-0">
          <PromptInput />
        </footer>
      </div>

      {/* Canvas / Artifacts Side-Panel (40% split on desktop) */}
      {isCanvasOpen && (
        <div className="fixed md:static inset-y-0 right-0 z-30 w-full md:w-[40%] h-full shrink-0 shadow-2xl md:shadow-none">
          <CanvasPanel />
        </div>
      )}

      <SearchModal />
      <SettingsModal />
      <AccountLimitsModal />
      <AddAccountModal />
      <GemsManagerModal />
      <NotebookLMDrawer />
      <ClaudePluginsPanel
        isOpen={claudeSwarmOpen}
        onClose={() => setClaudeSwarmOpen(false)}
      />
    </div>
  );

};

export default App;
