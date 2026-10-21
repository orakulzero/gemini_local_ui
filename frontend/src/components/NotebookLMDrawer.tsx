import React, { useState, useMemo } from 'react';
import { useNotebookLMStore } from '../store/notebooklmStore';
import { useChatStore } from '../store/chatStore';
import { NotebookSource, NotebookNote } from '../types';

export const NotebookLMDrawer: React.FC = () => {
  const isOpen = useNotebookLMStore((s) => s.isOpen);
  const setIsOpen = useNotebookLMStore((s) => s.setIsOpen);
  const notebooks = useNotebookLMStore((s) => s.notebooks);
  const activeNotebook = useNotebookLMStore((s) => s.activeNotebook);
  const isLoading = useNotebookLMStore((s) => s.isLoading);
  const error = useNotebookLMStore((s) => s.error);
  const searchQuery = useNotebookLMStore((s) => s.searchQuery);
  const setSearchQuery = useNotebookLMStore((s) => s.setSearchQuery);
  const fetchNotebooks = useNotebookLMStore((s) => s.fetchNotebooks);
  const selectNotebook = useNotebookLMStore((s) => s.selectNotebook);
  const createNotebook = useNotebookLMStore((s) => s.createNotebook);
  const deleteNotebook = useNotebookLMStore((s) => s.deleteNotebook);
  const renameNotebook = useNotebookLMStore((s) => s.renameNotebook);
  const selectedSourceIds = useNotebookLMStore((s) => s.selectedSourceIds);
  const toggleSelectSource = useNotebookLMStore((s) => s.toggleSelectSource);
  const selectAllSources = useNotebookLMStore((s) => s.selectAllSources);
  const clearSelection = useNotebookLMStore((s) => s.clearSelection);
  const attachSourceToChat = useNotebookLMStore((s) => s.attachSourceToChat);
  const attachSelectedToChat = useNotebookLMStore((s) => s.attachSelectedToChat);
  const exportChatToNotebook = useNotebookLMStore((s) => s.exportChatToNotebook);

  const activeConversationId = useChatStore((s) => s.activeConversationId);

  const [activeTab, setActiveTab] = useState<'sources' | 'notes'>('sources');
  const [newTitle, setNewTitle] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [editingNbId, setEditingNbId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [exportNotice, setExportNotice] = useState<string | null>(null);
  const [attachNotice, setAttachNotice] = useState<string | null>(null);

  const filteredNotebooks = useMemo(() => {
    if (!searchQuery.trim()) return notebooks;
    const q = searchQuery.toLowerCase();
    return notebooks.filter((n) => n.title.toLowerCase().includes(q));
  }, [notebooks, searchQuery]);

  if (!isOpen) return null;

  const handleCreate = async () => {
    if (!newTitle.trim()) return;
    const created = await createNotebook(newTitle.trim());
    if (created) {
      setNewTitle('');
      setIsCreating(false);
      selectNotebook(created.id);
    }
  };

  const handleStartRename = (id: string, currentTitle: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingNbId(id);
    setEditTitle(currentTitle);
  };

  const handleSaveRename = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (editTitle.trim()) {
      await renameNotebook(id, editTitle.trim());
    }
    setEditingNbId(null);
  };

  const handleDelete = async (id: string, title: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (window.confirm(`Delete notebook "${title}"?`)) {
      await deleteNotebook(id);
    }
  };

  const handleAttachSingle = (source: NotebookSource) => {
    attachSourceToChat(source);
    setAttachNotice(`Attached "${source.title}" to chat!`);
    setTimeout(() => setAttachNotice(null), 2500);
  };

  const handleAttachSelected = async () => {
    const formatted = await attachSelectedToChat();
    if (formatted) {
      setAttachNotice('Attached selected sources to prompt!');
      setTimeout(() => setAttachNotice(null), 2500);
    }
  };

  const handleExportChat = async () => {
    if (!activeConversationId) {
      setExportNotice('No active conversation selected to export.');
      setTimeout(() => setExportNotice(null), 3000);
      return;
    }
    const targetId = activeNotebook ? activeNotebook.id : undefined;
    const success = await exportChatToNotebook(targetId);
    if (success) {
      setExportNotice('Exported chat research to notebook!');
    } else {
      setExportNotice('Export failed.');
    }
    setTimeout(() => setExportNotice(null), 3000);
  };

  return (
    <div
      className="fixed inset-y-0 right-0 z-40 w-full sm:w-[440px] bg-zinc-900/95 border-l border-zinc-800 backdrop-blur-md shadow-2xl flex flex-col text-zinc-200 animate-in slide-in-from-right duration-200"
      data-testid="notebooklm-drawer"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800 bg-zinc-950/50 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-xl">📚</span>
          <div>
            <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
              NotebookLM Workspace
            </h2>
            <p className="text-[11px] text-zinc-400">Google Research & Source References</p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => fetchNotebooks()}
            disabled={isLoading}
            className="p-1.5 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 rounded-md text-xs transition"
            title="Refresh notebooks"
          >
            🔄
          </button>
          <button
            onClick={() => setIsOpen(false)}
            className="p-1.5 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 rounded-md text-xs transition"
            aria-label="Close drawer"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Notifications */}
      {attachNotice && (
        <div className="bg-emerald-900/40 text-emerald-300 border-b border-emerald-700/50 px-4 py-2 text-xs flex items-center justify-between">
          <span>✓ {attachNotice}</span>
          <button onClick={() => setAttachNotice(null)} className="text-emerald-400 font-bold">×</button>
        </div>
      )}
      {exportNotice && (
        <div className="bg-blue-900/40 text-blue-300 border-b border-blue-700/50 px-4 py-2 text-xs flex items-center justify-between">
          <span>ℹ {exportNotice}</span>
          <button onClick={() => setExportNotice(null)} className="text-blue-400 font-bold">×</button>
        </div>
      )}
      {error && (
        <div className="bg-red-900/40 text-red-300 border-b border-red-700/50 px-4 py-2 text-xs flex items-center justify-between">
          <span>⚠ {error}</span>
        </div>
      )}

      {/* Drawer Content */}
      {!activeNotebook ? (
        /* Notebooks List View */
        <div className="flex-1 flex flex-col min-h-0">
          <div className="p-3 border-b border-zinc-800/80 flex items-center gap-2 bg-zinc-950/20">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search notebooks..."
              className="flex-1 bg-zinc-800/60 border border-zinc-700/60 rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-blue-500"
            />
            <button
              onClick={() => setIsCreating((p) => !p)}
              className="bg-blue-600 hover:bg-blue-500 text-white rounded-lg px-2.5 py-1.5 text-xs font-medium transition shrink-0"
              title="Create new notebook"
            >
              + New
            </button>
          </div>

          {isCreating && (
            <div className="p-3 bg-zinc-800/40 border-b border-zinc-700/50 flex flex-col gap-2">
              <input
                type="text"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="Notebook title..."
                className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-blue-500"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleCreate();
                  if (e.key === 'Escape') setIsCreating(false);
                }}
              />
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setIsCreating(false)}
                  className="px-2.5 py-1 text-xs text-zinc-400 hover:text-zinc-200"
                >
                  Cancel
                </button>
                <button
                  onClick={handleCreate}
                  disabled={!newTitle.trim()}
                  className="bg-blue-600 hover:bg-blue-500 text-white rounded px-3 py-1 text-xs font-medium disabled:opacity-50"
                >
                  Create
                </button>
              </div>
            </div>
          )}

          {/* Quick Chat Export Banner */}
          <div className="p-3 bg-gradient-to-r from-blue-950/30 to-purple-950/30 border-b border-zinc-800/60 flex items-center justify-between text-xs">
            <div>
              <div className="font-medium text-zinc-200">Export Chat to Notebook</div>
              <div className="text-[11px] text-zinc-400">Save active chat turns as research text</div>
            </div>
            <button
              onClick={handleExportChat}
              disabled={!activeConversationId}
              className="bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700 rounded-md px-2.5 py-1 text-xs font-medium transition disabled:opacity-40"
            >
              Export
            </button>
          </div>

          {/* Notebooks List */}
          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            {isLoading && notebooks.length === 0 ? (
              <div className="text-center text-xs text-zinc-500 py-12">Loading notebooks...</div>
            ) : filteredNotebooks.length === 0 ? (
              <div className="text-center text-xs text-zinc-500 py-12">
                {searchQuery ? 'No notebooks match your search.' : 'No notebooks found in Google session.'}
              </div>
            ) : (
              filteredNotebooks.map((nb) => (
                <div
                  key={nb.id}
                  onClick={() => selectNotebook(nb.id)}
                  className="group bg-zinc-800/40 hover:bg-zinc-800/80 border border-zinc-700/50 hover:border-zinc-600 rounded-xl p-3 cursor-pointer transition flex flex-col gap-1.5"
                >
                  <div className="flex items-center justify-between">
                    {editingNbId === nb.id ? (
                      <div className="flex items-center gap-1 flex-1 mr-2" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="text"
                          value={editTitle}
                          onChange={(e) => setEditTitle(e.target.value)}
                          className="flex-1 bg-zinc-900 border border-blue-500 rounded px-2 py-0.5 text-xs text-zinc-100"
                          autoFocus
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') handleSaveRename(nb.id, e as any);
                            if (e.key === 'Escape') setEditingNbId(null);
                          }}
                        />
                        <button
                          onClick={(e) => handleSaveRename(nb.id, e)}
                          className="text-xs text-emerald-400 px-1"
                        >
                          ✓
                        </button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2 flex-1 min-w-0">
                        <span className="text-base shrink-0">{nb.emoji || '📓'}</span>
                        <span className="font-medium text-xs text-zinc-200 truncate group-hover:text-blue-300 transition">
                          {nb.title}
                        </span>
                      </div>
                    )}

                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition shrink-0">
                      <button
                        onClick={(e) => handleStartRename(nb.id, nb.title, e)}
                        className="p-1 text-zinc-400 hover:text-zinc-200 rounded"
                        title="Rename"
                      >
                        ✏️
                      </button>
                      <button
                        onClick={(e) => handleDelete(nb.id, nb.title, e)}
                        className="p-1 text-zinc-400 hover:text-red-300 rounded"
                        title="Delete"
                      >
                        🗑️
                      </button>
                    </div>
                  </div>

                  <div className="flex items-center justify-between text-[11px] text-zinc-500">
                    <span>{nb.source_count} {nb.source_count === 1 ? 'source' : 'sources'}</span>
                    {nb.updated_at > 0 && (
                      <span>{new Date(nb.updated_at * 1000).toLocaleDateString()}</span>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      ) : (
        /* Notebook Details / Inspector View */
        <div className="flex-1 flex flex-col min-h-0">
          {/* Breadcrumb Back */}
          <div className="px-4 py-2 border-b border-zinc-800 bg-zinc-950/40 flex items-center justify-between text-xs">
            <button
              onClick={() => selectNotebook(null)}
              className="text-blue-400 hover:text-blue-300 flex items-center gap-1 font-medium"
            >
              ← All Notebooks
            </button>
            <span className="text-zinc-500 text-[11px]">
              {activeNotebook.sources.length} sources · {activeNotebook.notes.length} notes
            </span>
          </div>

          {/* Notebook Header */}
          <div className="p-3 border-b border-zinc-800 flex items-start justify-between gap-2 bg-zinc-950/20">
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-xl shrink-0">{activeNotebook.emoji || '📓'}</span>
              <h3 className="font-semibold text-sm text-zinc-100 truncate">{activeNotebook.title}</h3>
            </div>
            <button
              onClick={handleExportChat}
              disabled={!activeConversationId}
              className="bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium shrink-0 disabled:opacity-40"
              title="Export active conversation to this notebook"
            >
              Export Chat
            </button>
          </div>

          {/* Tabs: Sources / Notes */}
          <div className="flex border-b border-zinc-800 text-xs bg-zinc-950/40">
            <button
              onClick={() => setActiveTab('sources')}
              className={`flex-1 py-2 font-medium border-b-2 text-center transition ${
                activeTab === 'sources'
                  ? 'border-blue-500 text-blue-400 bg-blue-950/20'
                  : 'border-transparent text-zinc-400 hover:text-zinc-200'
              }`}
            >
              Sources ({activeNotebook.sources.length})
            </button>
            <button
              onClick={() => setActiveTab('notes')}
              className={`flex-1 py-2 font-medium border-b-2 text-center transition ${
                activeTab === 'notes'
                  ? 'border-blue-500 text-blue-400 bg-blue-950/20'
                  : 'border-transparent text-zinc-400 hover:text-zinc-200'
              }`}
            >
              Notes ({activeNotebook.notes.length})
            </button>
          </div>

          {/* Tab Content */}
          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            {activeTab === 'sources' ? (
              activeNotebook.sources.length === 0 ? (
                <div className="text-center text-xs text-zinc-500 py-12">No sources attached to this notebook.</div>
              ) : (
                activeNotebook.sources.map((s) => {
                  const isChecked = selectedSourceIds.includes(s.id);
                  const typeLabel =
                    s.source_type === 3 ? 'PDF' : s.source_type === 2 ? 'Web' : s.source_type === 4 ? 'Video' : 'Text';

                  return (
                    <div
                      key={s.id}
                      className={`border rounded-xl p-3 transition flex flex-col gap-2 ${
                        isChecked
                          ? 'bg-blue-950/30 border-blue-500/60'
                          : 'bg-zinc-800/40 border-zinc-700/50 hover:border-zinc-600'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-start gap-2 flex-1 min-w-0">
                          <input
                            type="checkbox"
                            checked={isChecked}
                            onChange={() => toggleSelectSource(s.id)}
                            className="mt-0.5 rounded border-zinc-700 text-blue-600 focus:ring-0 focus:ring-offset-0 cursor-pointer"
                          />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1.5 flex-wrap">
                              <span className="font-medium text-xs text-zinc-100 truncate">{s.title}</span>
                              <span className="px-1.5 py-0.2 rounded text-[10px] bg-zinc-700/60 text-zinc-300 font-mono">
                                {typeLabel}
                              </span>
                            </div>
                            {s.url && (
                              <a
                                href={s.url}
                                target="_blank"
                                rel="noreferrer"
                                className="text-[11px] text-blue-400 hover:underline truncate block mt-0.5"
                              >
                                {s.url}
                              </a>
                            )}
                          </div>
                        </div>

                        <button
                          onClick={() => handleAttachSingle(s)}
                          className="bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-200 px-2 py-1 rounded text-[11px] font-medium transition shrink-0"
                          title="Attach this source summary to chat prompt"
                        >
                          Attach
                        </button>
                      </div>

                      {s.summary && (
                        <p className="text-xs text-zinc-400 line-clamp-3 bg-zinc-900/40 rounded p-2 border border-zinc-800/40">
                          {s.summary}
                        </p>
                      )}

                      <div className="flex items-center justify-between text-[10px] text-zinc-500">
                        <span>{s.token_count > 0 ? `~${s.token_count} tokens` : ''}</span>
                        {s.created_at > 0 && <span>{new Date(s.created_at * 1000).toLocaleDateString()}</span>}
                      </div>
                    </div>
                  );
                })
              )
            ) : activeNotebook.notes.length === 0 ? (
              <div className="text-center text-xs text-zinc-500 py-12">No notes created in this notebook.</div>
            ) : (
              activeNotebook.notes.map((n) => (
                <div
                  key={n.id}
                  className="bg-zinc-800/40 border border-zinc-700/50 rounded-xl p-3 flex flex-col gap-2"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-xs text-zinc-100">{n.title}</span>
                    <button
                      onClick={() => {
                        const block = `<notebooklm_reference title="${activeNotebook.title}: ${n.title}">\n${n.content}\n</notebooklm_reference>`;
                        useChatStore.getState().setDraftPrompt(block);
                        setAttachNotice(`Attached note "${n.title}" to chat!`);
                        setTimeout(() => setAttachNotice(null), 2500);
                      }}
                      className="bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-200 px-2 py-0.5 rounded text-[11px] font-medium transition"
                    >
                      Attach
                    </button>
                  </div>
                  <p className="text-xs text-zinc-300 whitespace-pre-wrap bg-zinc-900/40 rounded p-2 border border-zinc-800/40">
                    {n.content}
                  </p>
                </div>
              ))
            )}
          </div>

          {/* Batch Actions Footer */}
          {activeTab === 'sources' && activeNotebook.sources.length > 0 && (
            <div className="p-3 border-t border-zinc-800 bg-zinc-950/60 flex items-center justify-between gap-2 shrink-0">
              <div className="flex items-center gap-2 text-xs">
                <button
                  onClick={selectAllSources}
                  className="text-zinc-400 hover:text-zinc-200 underline text-[11px]"
                >
                  Select all
                </button>
                {selectedSourceIds.length > 0 && (
                  <button
                    onClick={clearSelection}
                    className="text-zinc-400 hover:text-zinc-200 underline text-[11px]"
                  >
                    Clear ({selectedSourceIds.length})
                  </button>
                )}
              </div>

              <button
                onClick={handleAttachSelected}
                disabled={isLoading}
                className="bg-blue-600 hover:bg-blue-500 text-white rounded-lg px-3 py-1.5 text-xs font-medium shadow-sm transition disabled:opacity-50"
              >
                Attach to Chat {selectedSourceIds.length > 0 ? `(${selectedSourceIds.length})` : '(All)'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
