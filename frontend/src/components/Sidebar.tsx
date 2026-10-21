import React, { useState } from 'react';
import { useChatStore } from '../store/chatStore';
import { triggerRemoteSync } from '../services/api';
import { ConversationSummary } from '../types';

export interface SidebarProps {
  isOpen: boolean;
  onClose: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ isOpen, onClose }) => {
  const conversations = useChatStore((s) => s.conversations);
  const activeConversationId = useChatStore((s) => s.activeConversationId);
  const selectConversation = useChatStore((s) => s.selectConversation);
  const createConversation = useChatStore((s) => s.createConversation);
  const deleteConversation = useChatStore((s) => s.deleteConversation);
  const togglePinConversation = useChatStore((s) => s.togglePinConversation);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const loadConversations = useChatStore((s) => s.loadConversations);

  const [isSyncing, setIsSyncing] = useState(false);
  const [editingConvId, setEditingConvId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');
  const [deleteTargetConv, setDeleteTargetConv] = useState<ConversationSummary | null>(null);

  const handleSync = async () => {
    setIsSyncing(true);
    try {
      await triggerRemoteSync();
      await loadConversations();
    } catch (e) {
      console.warn('Sync failed', e);
    } finally {
      setIsSyncing(false);
    }
  };

  const startRename = (conv: ConversationSummary, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingConvId(conv.id);
    setEditingTitle(conv.title || '');
  };

  const handleRenameSubmit = async (convId: string) => {
    if (editingConvId !== convId) return;
    if (editingTitle.trim()) {
      await renameConversation(convId, editingTitle.trim());
    }
    setEditingConvId(null);
  };

  const pinnedConversations = conversations.filter((c) => c.is_pinned);
  const recentConversations = conversations.filter((c) => !c.is_pinned);

  const renderConvItem = (conv: ConversationSummary) => {
    const isActive = conv.id === activeConversationId;
    const isEditing = editingConvId === conv.id;

    return (
      <div
        key={conv.id}
        data-testid={`conversation-item-${conv.id}`}
        className={`group relative flex items-center justify-between px-2.5 py-2 rounded-xl text-xs cursor-pointer transition-colors ${
          isActive
            ? 'bg-zinc-800 text-white font-medium'
            : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'
        }`}
        onClick={() => {
          if (!isEditing) {
            selectConversation(conv.id);
            onClose();
          }
        }}
        onDoubleClick={(e) => startRename(conv, e)}
      >
        {isEditing ? (
          <input
            type="text"
            value={editingTitle}
            autoFocus
            onChange={(e) => setEditingTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleRenameSubmit(conv.id);
              if (e.key === 'Escape') setEditingConvId(null);
            }}
            onBlur={() => handleRenameSubmit(conv.id)}
            onClick={(e) => e.stopPropagation()}
            className="w-full bg-zinc-900 text-white text-xs px-1.5 py-0.5 rounded border border-blue-500 focus:outline-none"
          />
        ) : (
          <span className="truncate flex-1 pr-2">{conv.title || 'Untitled'}</span>
        )}

        {!isEditing && (
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            {/* Pin Toggle */}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                togglePinConversation(conv.id);
              }}
              title={conv.is_pinned ? 'Unpin' : 'Pin'}
              className={`p-1 text-xs rounded hover:bg-zinc-700/50 ${
                conv.is_pinned ? 'text-yellow-400' : 'text-zinc-400'
              }`}
            >
              📌
            </button>

            {/* Rename button */}
            <button
              type="button"
              onClick={(e) => startRename(conv, e)}
              title="Rename"
              className="p-1 text-xs text-zinc-400 hover:text-zinc-200 rounded hover:bg-zinc-700/50"
            >
              ✏️
            </button>

            {/* Delete button (opens modal) */}
            <button
              type="button"
              data-testid={`delete-conversation-${conv.id}`}
              onClick={(e) => {
                e.stopPropagation();
                setDeleteTargetConv(conv);
              }}
              className="px-1 text-zinc-400 hover:text-red-400 rounded transition-colors"
              title="Delete"
            >
              ×
            </button>
          </div>
        )}
      </div>
    );
  };

  return (
    <>
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-30 md:hidden"
          onClick={onClose}
        />
      )}

      <aside
        data-testid="app-sidebar"
        className={`fixed md:static inset-y-0 left-0 z-40 w-64 bg-zinc-950 border-r border-zinc-800/80 flex flex-col transition-transform duration-200 ease-in-out shrink-0 ${
          isOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'
        }`}
      >
        <div className="p-3 border-b border-zinc-800/80 flex items-center justify-between">
          <button
            type="button"
            data-testid="new-chat-btn"
            onClick={async () => {
              await createConversation();
              onClose();
            }}
            className="flex-1 py-1.5 px-3 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium shadow-sm transition-colors"
          >
            + New Chat
          </button>
          <button
            type="button"
            data-testid="sync-chats-btn"
            onClick={handleSync}
            disabled={isSyncing}
            title="Sync with Gemini account"
            className={`p-1.5 ml-2 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 rounded-lg transition-colors text-sm leading-none ${
              isSyncing ? 'animate-spin text-blue-400' : ''
            }`}
          >
            ↻
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-3">
          {/* Pinned Section */}
          {pinnedConversations.length > 0 && (
            <div className="space-y-0.5">
              <div className="px-2 py-1 text-[11px] font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
                <span>📌</span>
                <span>Pinned</span>
              </div>
              {pinnedConversations.map(renderConvItem)}
            </div>
          )}

          {/* Recent Section */}
          <div className="space-y-0.5">
            {pinnedConversations.length > 0 && (
              <div className="px-2 py-1 text-[11px] font-semibold text-zinc-400 uppercase tracking-wider">
                Recent
              </div>
            )}
            {recentConversations.length === 0 && pinnedConversations.length === 0 ? (
              <div className="px-3 py-6 text-center text-xs text-zinc-500">
                No saved chats
              </div>
            ) : (
              recentConversations.map(renderConvItem)
            )}
          </div>
        </div>
      </aside>

      {/* Deletion Modal */}
      {deleteTargetConv && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-150"
          onClick={() => setDeleteTargetConv(null)}
        >
          <div
            className="w-full max-w-sm bg-zinc-900 border border-zinc-800 rounded-2xl p-5 shadow-2xl text-zinc-200 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div>
              <h3 className="text-sm font-semibold text-white">Delete chat?</h3>
              <p className="mt-1 text-xs text-zinc-400 truncate">
                «{deleteTargetConv.title || 'Untitled'}»
              </p>
            </div>

            <div className="flex flex-col gap-2 pt-2">
              <button
                type="button"
                onClick={async () => {
                  const id = deleteTargetConv.id;
                  setDeleteTargetConv(null);
                  await deleteConversation(id, false);
                }}
                className="w-full py-2 px-3 text-xs font-medium rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition"
              >
                Delete locally only
              </button>

              <button
                type="button"
                onClick={async () => {
                  const id = deleteTargetConv.id;
                  setDeleteTargetConv(null);
                  await deleteConversation(id, true);
                }}
                className="w-full py-2 px-3 text-xs font-medium rounded-xl bg-red-600/90 hover:bg-red-600 text-white transition"
              >
                Delete everywhere (Google + locally)
              </button>

              <button
                type="button"
                onClick={() => setDeleteTargetConv(null)}
                className="w-full py-1.5 px-3 text-xs text-zinc-400 hover:text-zinc-200 transition"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
