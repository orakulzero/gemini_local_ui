import React, { useMemo, useState } from 'react';
import { useWorkspaceStore } from '../../store/workspaceStore';
import { useSessionStore } from '../../store/sessionStore';

type FilterMode = 'all' | 'native' | 'mcp-bridged' | 'custom' | 'ide-only';

const FILTERS: Array<{ id: FilterMode; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'native', label: 'Native' },
  { id: 'mcp-bridged', label: 'MCP-Bridged' },
  { id: 'custom', label: 'Custom' },
  { id: 'ide-only', label: 'IDE-Only' },
];

export const SkillsTab: React.FC = () => {
  const skills = useWorkspaceStore((s) => s.skills);
  const enabledSkillIds = useWorkspaceStore((s) => s.enabledSkillIds);
  const toggleSkill = useWorkspaceStore((s) => s.toggleSkill);
  const createSkill = useWorkspaceStore((s) => s.createSkill);
  const updateSkill = useWorkspaceStore((s) => s.updateSkill);
  const deleteSkill = useWorkspaceStore((s) => s.deleteSkill);
  const syncSkillToGem = useWorkspaceStore((s) => s.syncSkillToGem);

  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<FilterMode>('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [editBody, setEditBody] = useState('');
  const [showNewForm, setShowNewForm] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [newCompat, setNewCompat] = useState<'native' | 'mcp-bridged' | 'ide-only'>('native');
  const [newTriggers, setNewTriggers] = useState('');
  const [newBody, setNewBody] = useState('');
  const [syncedId, setSyncedId] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return skills.filter((sk) => {
      if (filter === 'custom' && sk.source !== 'custom') return false;
      if (filter !== 'all' && filter !== 'custom' && sk.compatibility !== filter) return false;
      if (!q) return true;
      return `${sk.name} ${sk.id} ${sk.description || ''}`.toLowerCase().includes(q);
    });
  }, [skills, query, filter]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim() || !newBody.trim()) return;
    await createSkill({
      name: newName.trim(), description: newDesc.trim(), compatibility: newCompat,
      triggers: newTriggers.split(',').map((t) => t.trim()).filter(Boolean), content: newBody.trim(),
    });
    setNewName(''); setNewDesc(''); setNewTriggers(''); setNewBody(''); setShowNewForm(false);
  };

  const handleSyncGem = async (id: string) => {
    await syncSkillToGem(id);
    setSyncedId(id);
    useSessionStore.getState().fetchModels();
    setTimeout(() => setSyncedId((cur) => (cur === id ? null : cur)), 2500);
  };

  return (
    <div data-testid="skills-tab" className="space-y-3 text-xs text-zinc-200">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <input type="text" data-testid="skills-search-input" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search skills..." className="flex-1 min-w-[180px] bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5 text-zinc-100 focus:outline-none focus:border-blue-500" />
        <button type="button" onClick={() => setShowNewForm((p) => !p)} className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium">
          {showNewForm ? 'Close Form' : '+ New Skill'}
        </button>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            data-testid={`skills-filter-${f.id}`}
            onClick={() => setFilter(f.id)}
            className={`px-2.5 py-1 rounded-full text-[11px] border transition-colors ${filter === f.id ? 'bg-blue-600/25 text-blue-300 border-blue-500/50 font-medium' : 'bg-zinc-950 text-zinc-400 border-zinc-800 hover:text-zinc-200'}`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {showNewForm && (
        <form data-testid="new-skill-form" onSubmit={handleCreate} className="p-3 rounded-xl bg-zinc-950 border border-zinc-800 space-y-2">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <input type="text" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Skill slug / name" className="bg-zinc-900 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-zinc-100" />
            <input type="text" value={newDesc} onChange={(e) => setNewDesc(e.target.value)} placeholder="Description" className="bg-zinc-900 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-zinc-100" />
            <select value={newCompat} onChange={(e) => setNewCompat(e.target.value as any)} className="bg-zinc-900 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-zinc-100">
              <option value="native">native</option>
              <option value="mcp-bridged">mcp-bridged</option>
              <option value="ide-only">ide-only</option>
            </select>
          </div>
          <input type="text" value={newTriggers} onChange={(e) => setNewTriggers(e.target.value)} placeholder="Triggers (comma-separated, e.g. /review, /audit)" className="w-full bg-zinc-900 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-zinc-100" />
          <textarea rows={3} value={newBody} onChange={(e) => setNewBody(e.target.value)} placeholder="Markdown instructions..." className="w-full bg-zinc-900 border border-zinc-800 rounded-lg p-2.5 font-mono text-[11px] text-zinc-100" />
          <div className="flex justify-end">
            <button type="submit" disabled={!newName.trim() || !newBody.trim()} className="px-3 py-1 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white font-medium">Create Skill</button>
          </div>
        </form>
      )}

      <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
        {filtered.map((sk) => {
          const isEnabled = enabledSkillIds.includes(sk.id);
          const isExpanded = expandedId === sk.id;
          const bodyText = sk.content ?? sk.body ?? '';
          return (
            <div key={sk.id} data-testid="skill-item" className={`p-3 rounded-xl border ${isEnabled ? 'bg-blue-950/20 border-blue-600/50' : 'bg-zinc-950/60 border-zinc-800'}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-semibold text-zinc-100">{sk.name}</span>
                    <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-[10px] font-mono text-zinc-300">{sk.compatibility}</span>
                    <span className="px-1.5 py-0.5 rounded bg-zinc-900 border border-zinc-800 text-[10px] text-zinc-400">{sk.source}</span>
                  </div>
                  {sk.description && <p className="text-[11px] text-zinc-400 mt-0.5">{sk.description}</p>}
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <button type="button" onClick={() => handleSyncGem(sk.id)} className="px-2 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-[11px]">
                    {syncedId === sk.id ? 'Synced!' : 'Sync to Gem'}
                  </button>
                  <button type="button" onClick={() => { setExpandedId(isExpanded ? null : sk.id); setEditBody(bodyText); }} className="px-2 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-[11px]">
                    {isExpanded ? 'Hide' : 'View'}
                  </button>
                  <button type="button" data-testid="toggle-skill-btn" onClick={() => toggleSkill(sk.id)} className={`px-2.5 py-1 rounded-lg text-[11px] font-medium ${isEnabled ? 'bg-blue-600 text-white' : 'bg-zinc-800 text-zinc-300 hover:bg-zinc-700'}`}>
                    {isEnabled ? 'Active' : 'Enable'}
                  </button>
                </div>
              </div>
              {isExpanded && (
                <div className="mt-2.5 pt-2.5 border-t border-zinc-800 space-y-2">
                  {sk.source === 'custom' ? (
                    <>
                      <textarea rows={4} value={editBody} onChange={(e) => setEditBody(e.target.value)} className="w-full bg-zinc-900 border border-zinc-800 rounded-lg p-2 font-mono text-[11px] text-zinc-100" />
                      <div className="flex justify-end gap-2">
                        <button type="button" onClick={() => deleteSkill(sk.id)} className="px-2.5 py-1 rounded bg-red-950/60 text-red-300 text-[11px]">Delete</button>
                        <button type="button" onClick={() => updateSkill(sk.id, { content: editBody })} className="px-2.5 py-1 rounded bg-blue-600 text-white text-[11px]">Save</button>
                      </div>
                    </>
                  ) : (
                    <pre className="p-2 rounded bg-zinc-900/80 border border-zinc-800 font-mono text-[11px] text-zinc-300 whitespace-pre-wrap max-h-40 overflow-y-auto">{bodyText}</pre>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
