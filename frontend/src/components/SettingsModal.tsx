import React, { useState } from 'react';
import { useSessionStore } from '../store/sessionStore';
import { useChatStore } from '../store/chatStore';

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

  const [cookieInput, setCookieInput] = useState('');
  const [successMsg, setSuccessMsg] = useState('');

  if (!isSettingsOpen) return null;

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
          <h2 className="text-sm font-semibold text-zinc-100">Session & Credentials</h2>
          <button
            type="button"
            onClick={() => toggleSettings(false)}
            className="text-zinc-500 hover:text-zinc-300"
          >
            ✕
          </button>
        </div>

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
      </div>
    </div>
  );
};
