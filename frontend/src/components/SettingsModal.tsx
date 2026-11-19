import React, { useEffect, useState } from 'react';
import { useSessionStore } from '../store/sessionStore';
import { useChatStore } from '../store/chatStore';
import * as api from '../services/api';

export const SettingsModal: React.FC = () => {
  const isSettingsOpen = useSessionStore((s) => s.isSettingsOpen);
  const toggleSettings = useSessionStore((s) => s.toggleSettings);
  const health = useSessionStore((s) => s.health);
  const submitCredentials = useSessionStore((s) => s.submitCredentials);
  const clearCredentials = useSessionStore((s) => s.clearCredentials);
  const rotateSession = useSessionStore((s) => s.rotateSession);
  const isLoading = useSessionStore((s) => s.isLoading);
  const error = useSessionStore((s) => s.error);
  const syncRemoteConversations = useChatStore((s) => s.syncRemoteConversations);

  const [activeTab, setActiveTab] = useState<'session' | 'saved_info'>('session');
  const [cookieInput, setCookieInput] = useState('');
  const [successMsg, setSuccessMsg] = useState('');
  const [memories, setMemories] = useState<Array<{ id: string; content: string; category: string }>>([]);
  const [newMemory, setNewMemory] = useState('');
  const [memoryLoading, setMemoryLoading] = useState(false);

  useEffect(() => {
    if (!isSettingsOpen || activeTab !== 'saved_info') return;
    setMemoryLoading(true);
    api
      .fetchSavedInfo()
      .then((items) => setMemories(items))
      .catch((err) => console.warn('Failed loading saved info:', err))
      .finally(() => setMemoryLoading(false));
  }, [isSettingsOpen, activeTab]);

  if (!isSettingsOpen) return null;

  const handleAddMemory = async () => {
    if (!newMemory.trim()) return;
    setMemoryLoading(true);
    try {
      await api.addSavedInfo(newMemory.trim());
      setNewMemory('');
      setMemories(await api.fetchSavedInfo());
    } finally {
      setMemoryLoading(false);
    }
  };

  const handleDeleteMemory = async (id: string) => {
    setMemoryLoading(true);
    try {
      await api.deleteSavedInfo(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
    } finally {
      setMemoryLoading(false);
    }
  };

  const handleSubmit = async (autoImport = false) => {
    setSuccessMsg('');
    const ok = await submitCredentials(autoImport ? undefined : cookieInput, autoImport);
    if (ok) {
      setSuccessMsg(autoImport ? 'Credentials auto-imported successfully!' : 'Credentials saved successfully!');
      setCookieInput('');
      syncRemoteConversations();
    }
  };

  const handleRotate = async () => {
    setSuccessMsg('');
    const ok = await rotateSession();
    if (ok) setSuccessMsg('Session token rotated successfully!');
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
      <div className="fixed inset-0" onClick={() => toggleSettings(false)} />
      <div
        data-testid="settings-modal"
        className="relative w-full max-w-md rounded-2xl bg-zinc-900 border border-zinc-800 shadow-2xl overflow-hidden z-10 p-5 space-y-4 text-xs text-zinc-300"
      >
        <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-zinc-100">Settings</h2>
            <div className="flex bg-zinc-950 rounded-lg p-0.5 border border-zinc-800 text-[11px]">
              <button
                type="button"
                data-testid="settings-tab-session"
                onClick={() => setActiveTab('session')}
                className={`px-2 py-0.5 rounded-md ${
                  activeTab === 'session' ? 'bg-zinc-800 text-zinc-100 font-medium' : 'text-zinc-400'
                }`}
              >
                Session
              </button>
              <button
                type="button"
                data-testid="settings-tab-saved-info"
                onClick={() => setActiveTab('saved_info')}
                className={`px-2 py-0.5 rounded-md ${
                  activeTab === 'saved_info' ? 'bg-blue-600 text-white font-medium' : 'text-zinc-400'
                }`}
              >
                Saved Info
              </button>
            </div>
          </div>
          <button
            type="button"
            onClick={() => toggleSettings(false)}
            className="text-zinc-500 hover:text-zinc-300"
          >
            ✕
          </button>
        </div>

        {activeTab === 'saved_info' ? (
          <div className="space-y-3" data-testid="saved-info-panel">
            <p className="text-[11px] text-zinc-400">
              Persistent preferences & instructions Gemini remembers across conversations.
            </p>
            <div className="flex gap-2">
              <input
                type="text"
                data-testid="saved-info-input"
                value={newMemory}
                onChange={(e) => setNewMemory(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleAddMemory();
                }}
                placeholder="e.g. Always respond with concise Rust examples"
                className="flex-1 bg-zinc-950 text-zinc-100 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-blue-500"
              />
              <button
                type="button"
                data-testid="saved-info-add-btn"
                disabled={memoryLoading || !newMemory.trim()}
                onClick={handleAddMemory}
                className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded-lg font-medium"
              >
                Add
              </button>
            </div>
            <div className="max-h-56 overflow-y-auto space-y-1.5">
              {memoryLoading && memories.length === 0 ? (
                <div className="text-center text-zinc-500 py-4">Loading Saved Info...</div>
              ) : memories.length === 0 ? (
                <div className="text-center text-zinc-500 py-4">No Saved Info items yet</div>
              ) : (
                memories.map((m) => (
                  <div
                    key={m.id}
                    data-testid="saved-info-item"
                    className="flex items-start justify-between gap-2 p-2 rounded-lg bg-zinc-950 border border-zinc-800"
                  >
                    <span className="text-zinc-200 leading-relaxed">{m.content}</span>
                    <button
                      type="button"
                      onClick={() => handleDeleteMemory(m.id)}
                      className="text-zinc-500 hover:text-red-400 shrink-0 px-1"
                      title="Delete memory"
                    >
                      ✕
                    </button>
                  </div>
                ))
              )}
            </div>
          </div>
        ) : (
          <>

        {/* Health status */}
        <div className="p-3 rounded-xl bg-zinc-950 border border-zinc-800/80 space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-zinc-400">Status:</span>
            <span
              className={`font-semibold capitalize ${
                health?.is_authenticated ? 'text-emerald-400' : 'text-amber-400'
              }`}
            >
              {health?.status || 'Unknown'}
            </span>
          </div>
          {health?.account_tier && (
            <div className="flex items-center justify-between">
              <span className="text-zinc-400">Account Tier:</span>
              <span className="uppercase font-mono text-zinc-200">{health.account_tier}</span>
            </div>
          )}
          <div className="flex items-center justify-between">
            <span className="text-zinc-400">1PSID Cookie:</span>
            <span>{health?.has_psid ? '✓ Available' : '✗ Missing'}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-zinc-400">1PSIDTS Cookie:</span>
            <span>{health?.has_psidts ? '✓ Available' : '✗ Missing'}</span>
          </div>
          {health?.error_message && (
            <div className="pt-1 text-[11px] text-amber-400/90 break-words">
              {health.error_message}
            </div>
          )}
        </div>

        {error && <div className="p-2 rounded bg-red-950/60 border border-red-800 text-red-300">{error}</div>}
        {successMsg && <div className="p-2 rounded bg-emerald-950/60 border border-emerald-800 text-emerald-300">{successMsg}</div>}

        {/* Manual Cookie Import */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <label className="text-zinc-400 block font-medium">Manual Cookies Input (Chrome Pro / JSON / Header)</label>
          </div>
          <textarea
            data-testid="credentials-input"
            value={cookieInput}
            onChange={(e) => setCookieInput(e.target.value)}
            placeholder="Paste Cookie header from DevTools Network tab (or __Secure-1PSID=...; __Secure-1PSIDTS=...)"
            rows={3}
            className="w-full bg-zinc-950 text-zinc-100 border border-zinc-800 rounded-lg p-2 font-mono text-[11px] focus:outline-none focus:border-blue-500"
          />
          <div className="flex gap-2">
            <button
              type="button"
              data-testid="save-credentials-btn"
              disabled={isLoading || !cookieInput.trim()}
              onClick={() => handleSubmit(false)}
              className="flex-1 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded-lg font-medium transition-colors"
            >
              Save Credentials
            </button>
            <button
              type="button"
              data-testid="auto-import-btn"
              disabled={isLoading}
              onClick={() => handleSubmit(true)}
              className="py-1.5 px-3 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg transition-colors"
            >
              Auto-Import from Browser
            </button>
          </div>
        </div>

        {/* Rotation & Clear */}
        <div className="pt-2 border-t border-zinc-800 flex justify-between">
          <button
            type="button"
            data-testid="rotate-session-btn"
            disabled={isLoading || !health?.is_authenticated}
            onClick={handleRotate}
            className="py-1 px-3 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors"
          >
            Rotate Tokens
          </button>
          <button
            type="button"
            data-testid="clear-credentials-btn"
            disabled={isLoading}
            onClick={clearCredentials}
            className="py-1 px-3 text-red-400 hover:bg-red-950/40 rounded-lg transition-colors"
          >
            Clear All
          </button>
        </div>
          </>
        )}
      </div>
    </div>
  );
};
