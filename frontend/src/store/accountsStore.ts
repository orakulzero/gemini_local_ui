import { create } from 'zustand';
import { AccountItem, FailoverConfig, LimitsResponse } from '../types';
import * as api from '../services/api';

interface AccountsState {
  accounts: AccountItem[];
  activeAccountId: string;
  failoverConfig: FailoverConfig | null;
  limitsData: LimitsResponse | null;
  isLimitsModalOpen: boolean;
  isAddAccountOpen: boolean;
  isLoading: boolean;
  error: string | null;

  // Actions
  fetchAccounts: () => Promise<void>;
  activateAccount: (accountId: string) => Promise<boolean>;
  addAccount: (req: {
    name: string;
    cookies?: string;
    auto_import?: boolean;
    tier_override?: string;
    priority?: number;
  }) => Promise<boolean>;
  deleteAccount: (accountId: string) => Promise<boolean>;
  toggleFailover: (enabled?: boolean) => Promise<void>;
  fetchLimits: (accountId?: string) => Promise<void>;
  updateThresholds: (
    accountId: string,
    thresholds: {
      daily_request_threshold: number;
      daily_token_threshold: number;
      hourly_request_threshold: number;
    }
  ) => Promise<boolean>;
  toggleLimitsModal: (open?: boolean) => void;
  toggleAddAccount: (open?: boolean) => void;
}

export const useAccountsStore = create<AccountsState>((set, get) => ({
  accounts: [],
  activeAccountId: 'default',
  failoverConfig: null,
  limitsData: null,
  isLimitsModalOpen: false,
  isAddAccountOpen: false,
  isLoading: false,
  error: null,

  fetchAccounts: async () => {
    try {
      set({ isLoading: true, error: null });
      const data = await api.fetchAccounts();
      set({
        accounts: data.accounts,
        activeAccountId: data.active_account_id,
        failoverConfig: data.failover_config,
        isLoading: false,
      });
    } catch (err: any) {
      set({ isLoading: false, error: err.message || 'Failed to load accounts' });
    }
  },

  activateAccount: async (accountId: string) => {
    try {
      const ok = await api.activateAccount(accountId);
      if (ok) {
        set({ activeAccountId: accountId });
        await get().fetchAccounts();
      }
      return ok;
    } catch (err: any) {
      set({ error: err.message });
      return false;
    }
  },

  addAccount: async (req) => {
    try {
      set({ isLoading: true, error: null });
      await api.createAccount(req);
      await get().fetchAccounts();
      set({ isLoading: false, isAddAccountOpen: false });
      return true;
    } catch (err: any) {
      set({ isLoading: false, error: err.message });
      return false;
    }
  },

  deleteAccount: async (accountId: string) => {
    try {
      const ok = await api.deleteAccount(accountId);
      if (ok) {
        await get().fetchAccounts();
      }
      return ok;
    } catch (err: any) {
      set({ error: err.message });
      return false;
    }
  },

  toggleFailover: async (enabled) => {
    const current = get().failoverConfig;
    const nextVal = enabled !== undefined ? enabled : !current?.enabled;
    try {
      await api.updateFailoverConfig({ enabled: nextVal });
      if (current) {
        set({ failoverConfig: { ...current, enabled: nextVal } });
      } else {
        await get().fetchAccounts();
      }
    } catch (err: any) {
      set({ error: err.message });
    }
  },

  fetchLimits: async (accountId) => {
    try {
      const data = await api.fetchLimits(accountId);
      set({ limitsData: data });
    } catch (err: any) {
      console.warn('Failed to fetch limits telemetry', err);
    }
  },

  updateThresholds: async (accountId, thresholds) => {
    try {
      const ok = await api.updateThresholds(accountId, thresholds);
      if (ok) {
        await get().fetchLimits(accountId);
      }
      return ok;
    } catch (err: any) {
      set({ error: err.message });
      return false;
    }
  },

  toggleLimitsModal: (open) =>
    set((s) => ({ isLimitsModalOpen: open !== undefined ? open : !s.isLimitsModalOpen })),

  toggleAddAccount: (open) =>
    set((s) => ({ isAddAccountOpen: open !== undefined ? open : !s.isAddAccountOpen })),
}));
