import React, { useState } from 'react';
import { useAccountsStore } from '../store/accountsStore';

export const AddAccountModal: React.FC = () => {
  const isAddAccountOpen = useAccountsStore((s) => s.isAddAccountOpen);
  const toggleAddAccount = useAccountsStore((s) => s.toggleAddAccount);
  const addAccount = useAccountsStore((s) => s.addAccount);
  const isLoading = useAccountsStore((s) => s.isLoading);
  const error = useAccountsStore((s) => s.error);

  const [name, setName] = useState('');
  const [cookies, setCookies] = useState('');
  const [tierOverride, setTierOverride] = useState<'free' | 'pro' | 'ultra'>('free');
  const [priority, setPriority] = useState<number>(1);
  const [useAutoImport, setUseAutoImport] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  if (!isAddAccountOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    const trimmedName = name.trim();
    if (!trimmedName) {
      setFormError('Account name is required.');
      return;
    }

    if (!useAutoImport && !cookies.trim()) {
      setFormError('Please paste session cookies or enable Auto-Import.');
      return;
    }

    const success = await addAccount({
      name: trimmedName,
      cookies: useAutoImport ? undefined : cookies.trim(),
      auto_import: useAutoImport,
      tier_override: tierOverride,
      priority: Number(priority) || 1,
    });

    if (success) {
      setName('');
      setCookies('');
      setTierOverride('free');
      setPriority(1);
      setUseAutoImport(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
      <div className="fixed inset-0" onClick={() => toggleAddAccount(false)} />
      <div
        data-testid="add-account-modal"
        className="relative w-full max-w-md rounded-2xl bg-zinc-900 border border-zinc-800 shadow-2xl overflow-hidden z-10 p-5 space-y-4 text-xs text-zinc-300"
      >
        <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
          <div className="flex items-center gap-2">
            <span className="text-blue-400 text-sm">👤</span>
            <h2 className="text-sm font-semibold text-zinc-100">Add Gemini Account</h2>
          </div>
          <button
            type="button"
            data-testid="cancel-account-btn"
            onClick={() => toggleAddAccount(false)}
            className="text-zinc-500 hover:text-zinc-300"
          >
            ✕
          </button>
        </div>

        {(formError || error) && (
          <div className="p-2 rounded bg-red-950/60 border border-red-800 text-red-300 text-[11px]">
            {formError || error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-3.5">
          <div>
            <label className="block text-zinc-400 font-medium mb-1">
              Account Label / Name
            </label>
            <input
              type="text"
              data-testid="account-name-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Work Pro, Personal Ultra, Backup"
              className="w-full bg-zinc-950 text-zinc-100 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-blue-500 font-sans"
              required
            />
          </div>

          <div className="grid grid-cols-2 gap-2.5">
            <div>
              <label className="block text-zinc-400 font-medium mb-1">
                Tier Override
              </label>
              <select
                data-testid="account-tier-select"
                value={tierOverride}
                onChange={(e) => setTierOverride(e.target.value as any)}
                className="w-full bg-zinc-950 text-zinc-100 border border-zinc-800 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:border-blue-500 font-sans capitalize"
              >
                <option value="free">FREE (Argon fallback)</option>
                <option value="pro">PRO (3.1 Pro & Task)</option>
                <option value="ultra">ULTRA (Full Quota)</option>
              </select>
            </div>

            <div>
              <label className="block text-zinc-400 font-medium mb-1">
                Failover Priority
              </label>
              <input
                type="number"
                data-testid="account-priority-input"
                min={1}
                max={99}
                value={priority}
                onChange={(e) => setPriority(parseInt(e.target.value, 10) || 1)}
                className="w-full bg-zinc-950 text-zinc-100 border border-zinc-800 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:border-blue-500 font-mono"
              />
            </div>
          </div>

          <div className="pt-1">
            <div className="flex items-center justify-between mb-1">
              <label className="text-zinc-400 font-medium">Authentication Source</label>
              <button
                type="button"
                data-testid="account-auto-import-btn"
                onClick={() => setUseAutoImport((prev) => !prev)}
                className={`text-[11px] px-2 py-0.5 rounded transition-colors ${
                  useAutoImport
                    ? 'bg-blue-600/30 text-blue-300 border border-blue-500/50'
                    : 'bg-zinc-800 text-zinc-400 hover:text-zinc-200'
                }`}
              >
                {useAutoImport ? '✓ Auto-import active' : 'Switch to Auto-Import'}
              </button>
            </div>

            {useAutoImport ? (
              <div className="p-3 bg-zinc-950/70 border border-zinc-800 rounded-lg text-zinc-400 text-[11px] space-y-1">
                <p className="text-zinc-300 font-medium">Extract from local browser cookies</p>
                <p>
                  Attempts to read <code className="font-mono text-zinc-400">__Secure-1PSID</code> and{' '}
                  <code className="font-mono text-zinc-400">__Secure-1PSIDTS</code> from default
                  Chrome/Edge profile databases.
                </p>
              </div>
            ) : (
              <textarea
                data-testid="account-cookies-input"
                value={cookies}
                onChange={(e) => setCookies(e.target.value)}
                placeholder="Paste Cookie header string (e.g., __Secure-1PSID=...; __Secure-1PSIDTS=...)"
                rows={4}
                className="w-full bg-zinc-950 text-zinc-100 border border-zinc-800 rounded-lg p-2 font-mono text-[11px] focus:outline-none focus:border-blue-500"
              />
            )}
          </div>

          <div className="flex items-center justify-end gap-2 pt-2 border-t border-zinc-800">
            <button
              type="button"
              onClick={() => toggleAddAccount(false)}
              className="px-3 py-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              data-testid="submit-account-btn"
              disabled={isLoading}
              className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded-lg font-medium transition-colors shadow-sm"
            >
              {isLoading ? 'Saving...' : 'Add Account'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
