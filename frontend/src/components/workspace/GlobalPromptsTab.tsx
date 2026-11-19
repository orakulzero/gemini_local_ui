import React, { useState } from 'react';
import { GlobalPromptItem } from '../../types';
import { useWorkspaceStore } from '../../store/workspaceStore';
import { useSessionStore } from '../../store/sessionStore';

export const GlobalPromptsTab: React.FC = () => {
  const prompts = useWorkspaceStore((s) => s.prompts);
  const promptsEnabled = useWorkspaceStore((s) => s.promptsEnabled);
  const activePromptId = useWorkspaceStore((s) => s.activePromptId);
  const togglePromptsEnabled = useWorkspaceStore((s) => s.togglePromptsEnabled);
  const activatePrompt = useWorkspaceStore((s) => s.activatePrompt);
  const createPrompt = useWorkspaceStore((s) => s.createPrompt);
  const updatePrompt = useWorkspaceStore((s) => s.updatePrompt);
  const deletePrompt = useWorkspaceStore((s) => s.deletePrompt);
  const closeHub = useWorkspaceStore((s) => s.closeHub);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [content, setContent] = useState('');
  const [applyMode, setApplyMode] = useState<'turn0' | 'every_turn'>('turn0');
  const [saving, setSaving] = useState(false);

  const resetForm = () => {
    setEditingId(null); setName(''); setDescription(''); setContent(''); setApplyMode('turn0');
  };

  const startEdit = (item: GlobalPromptItem) => {
    setEditingId(item.id); setName(item.name); setDescription(item.description || '');
    setContent(item.content); setApplyMode(item.apply_mode || 'turn0');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !content.trim() || saving) return;
    setSaving(true);
    try {
      const payload = { name: name.trim(), description: description.trim(), content: content.trim(), apply_mode: applyMode };
      if (editingId) await updatePrompt(editingId, payload);
      else await createPrompt(payload);
      resetForm();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div data-testid="global-prompts-tab" className="space-y-4 text-xs text-zinc-200">
      <div className="flex flex-wrap items-center justify-between gap-2 p-3 rounded-xl bg-zinc-950/70 border border-zinc-800">
        <div>
          <div className="font-semibold text-zinc-100">Global System Prompts</div>
          <div className="text-[11px] text-zinc-400">Inject an active persona or instruction rule into new or ongoing chats.</div>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => { closeHub(); useSessionStore.getState().toggleSettings(true); }} className="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-[11px]">
            Saved Info
          </button>
          <button
            type="button"
            data-testid="global-prompts-master-toggle"
            onClick={() => togglePromptsEnabled(!promptsEnabled)}
            className={`px-3 py-1 rounded-lg font-medium border transition-colors ${promptsEnabled ? 'bg-emerald-950/60 text-emerald-300 border-emerald-700/60' : 'bg-zinc-800 text-zinc-400 border-zinc-700'}`}
          >
            {promptsEnabled ? 'Enabled' : 'Disabled'}
          </button>
        </div>
      </div>

      <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
        {prompts.map((item) => {
          const isActive = item.is_active || item.id === activePromptId;
          return (
            <div key={item.id} data-testid="global-prompt-item" className={`p-3 rounded-xl border transition-colors ${isActive ? 'bg-blue-950/25 border-blue-600/50' : 'bg-zinc-950/60 border-zinc-800'}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-zinc-100 truncate">{item.name}</span>
                    <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-[10px] font-mono text-zinc-400">{item.apply_mode || 'turn0'}</span>
                    {isActive && <span className="px-1.5 py-0.5 rounded bg-blue-600/30 text-blue-300 text-[10px] font-medium">Active</span>}
                  </div>
                  {item.description && <p className="text-[11px] text-zinc-400 mt-0.5">{item.description}</p>}
                  <p className="text-[11px] text-zinc-500 mt-1 line-clamp-2 font-mono">{item.content}</p>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <button
                    type="button"
                    data-testid="activate-prompt-btn"
                    onClick={() => activatePrompt(isActive ? null : item.id)}
                    className={`px-2.5 py-1 rounded-lg text-[11px] font-medium ${isActive ? 'bg-zinc-800 text-zinc-300 hover:bg-zinc-700' : 'bg-blue-600 hover:bg-blue-500 text-white'}`}
                  >
                    {isActive ? 'Deactivate' : 'Activate'}
                  </button>
                  <button type="button" onClick={() => startEdit(item)} className="px-2 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-[11px]">Edit</button>
                  {!item.is_builtin && (
                    <button type="button" onClick={() => deletePrompt(item.id)} className="px-2 py-1 rounded-lg hover:bg-red-950/60 text-zinc-400 hover:text-red-300 text-[11px]">Delete</button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <form data-testid="new-prompt-form" onSubmit={handleSubmit} className="p-3 rounded-xl bg-zinc-950/80 border border-zinc-800 space-y-2.5">
        <div className="flex items-center justify-between">
          <span className="font-semibold text-zinc-200">{editingId ? 'Edit Prompt' : 'New Global Prompt'}</span>
          {editingId && <button type="button" onClick={resetForm} className="text-[11px] text-zinc-400 hover:text-zinc-200">Cancel Edit</button>}
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="Prompt name" className="bg-zinc-900 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-zinc-100 focus:outline-none focus:border-blue-500" />
          <input type="text" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Short description" className="bg-zinc-900 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-zinc-100 focus:outline-none focus:border-blue-500" />
          <select value={applyMode} onChange={(e) => setApplyMode(e.target.value as 'turn0' | 'every_turn')} className="bg-zinc-900 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-zinc-100 focus:outline-none focus:border-blue-500">
            <option value="turn0">Turn 0 only</option>
            <option value="every_turn">Every turn</option>
          </select>
        </div>
        <textarea rows={3} value={content} onChange={(e) => setContent(e.target.value)} placeholder="System instruction content..." className="w-full bg-zinc-900 border border-zinc-800 rounded-lg p-2.5 font-mono text-[11px] text-zinc-100 focus:outline-none focus:border-blue-500" />
        <div className="flex justify-end">
          <button type="submit" disabled={saving || !name.trim() || !content.trim()} className="px-3.5 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white font-medium">
            {saving ? 'Saving...' : editingId ? 'Save Changes' : 'Create Prompt'}
          </button>
        </div>
      </form>
    </div>
  );
};
