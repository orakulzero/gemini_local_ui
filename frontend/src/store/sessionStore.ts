import { create } from 'zustand';
import { ModelOption, GemOption, SessionHealthStatus } from '../types';
import * as api from '../services/api';
import { useAccountsStore } from './accountsStore';

interface SessionState {
  isAuthenticated: boolean;
  health: SessionHealthStatus | null;
  models: ModelOption[];
  selectedModel: string;
  reasoningLevel: 1 | 4 | 2;
  featureMode: 'chat' | 'task';
  gems: GemOption[];
  selectedGemId: string | null;
  isSettingsOpen: boolean;
  isSearchOpen: boolean;
  isGemsManagerOpen: boolean;
  backendStatus: 'online' | 'offline' | 'checking';
  isLoading: boolean;
  error: string | null;

  // Actions
  fetchSessionStatus: () => Promise<void>;
  submitCredentials: (cookies?: string, autoImport?: boolean) => Promise<boolean>;
  clearCredentials: () => Promise<void>;
  rotateSession: () => Promise<boolean>;
  fetchModels: () => Promise<void>;
  setSelectedModel: (modelId: string) => void;
  setReasoningLevel: (level: 1 | 4 | 2) => void;
  setFeatureMode: (mode: 'chat' | 'task') => void;
  setSelectedGemId: (gemId: string | null) => void;
  toggleSettings: (open?: boolean) => void;
  toggleSearch: (open?: boolean) => void;
  toggleGemsManager: (open?: boolean) => void;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  isAuthenticated: false,
  health: null,
  models: [
    {
      id: '8c46e95b1a07cecc',
      display_name: '3.5 Flash-Lite',
      description: 'Fastest responses',
      supports_thinking: true,
      is_default: false,
    },
    {
      id: '56fdd199312815e2',
      display_name: '3.8 Flash',
      description: 'All-round help',
      supports_thinking: true,
      is_default: true,
    },
    {
      id: 'e6fa609c3fa255c0',
      display_name: '3.1 Pro',
      description: 'Advanced reasoning capabilities',
      supports_thinking: true,
      is_default: false,
    },
    {
      id: '95a4c0abcba24364',
      display_name: '4 Argon',
      description: 'Complex tasks · New',
      supports_thinking: true,
      is_default: false,
    },
  ],
  selectedModel: '56fdd199312815e2',
  reasoningLevel: 1,
  featureMode: 'chat',
  gems: [],
  selectedGemId: null,
  isSettingsOpen: false,
  isSearchOpen: false,
  isGemsManagerOpen: false,
  backendStatus: 'checking',
  isLoading: false,
  error: null,

  fetchSessionStatus: async () => {
    try {
      const { authenticated, health } = await api.fetchSessionStatus();
      set({
        isAuthenticated: authenticated,
        health,
        backendStatus: 'online',
        error: null,
      });
      // Synchronize multi-account profiles
      try {
        useAccountsStore.getState().fetchAccounts();
      } catch {
        // accountsStore optional
      }
    } catch (err: any) {
      set({
        isAuthenticated: false,
        backendStatus: 'offline',
        error: err.message || 'Cannot connect to backend',
      });
    }
  },

  submitCredentials: async (cookies?: string, autoImport = false) => {
    set({ isLoading: true, error: null });
    try {
      const auth = await api.submitCredentials(cookies, autoImport);
      await get().fetchSessionStatus();
      await get().fetchModels();
      set({ isLoading: false });
      return auth;
    } catch (err: any) {
      set({ isLoading: false, error: err.message });
      return false;
    }
  },

  clearCredentials: async () => {
    try {
      await api.clearCredentials();
      set({ isAuthenticated: false, health: null });
    } catch (err: any) {
      set({ error: err.message });
    }
  },

  rotateSession: async () => {
    try {
      const success = await api.rotateSession();
      await get().fetchSessionStatus();
      return success;
    } catch {
      return false;
    }
  },

  fetchModels: async () => {
    try {
      const models = await api.fetchModels();
      const gems = await api.fetchGems();
      if (models.length > 0) {
        const current = get().selectedModel;
        const exists = models.some((m) => m.id === current);
        const defModel = models.find((m) => m.is_default)?.id || models[0].id;
        set({
          models,
          selectedModel: exists ? current : defModel,
        });
      }
      if (gems.length > 0) {
        set({ gems });
      }
    } catch (err) {
      console.warn('Failed fetching models list', err);
    }
  },

  setSelectedModel: (modelId: string) => set({ selectedModel: modelId }),
  setReasoningLevel: (level: 1 | 4 | 2) => set({ reasoningLevel: level }),
  setFeatureMode: (mode: 'chat' | 'task') =>
    set((s) => {
      if (mode === 'task' && s.selectedModel !== '95a4c0abcba24364' && s.selectedModel !== '56fdd199312815e2') {
        return { featureMode: mode, selectedModel: '95a4c0abcba24364' };
      }
      return { featureMode: mode };
    }),
  setSelectedGemId: (gemId: string | null) => set({ selectedGemId: gemId }),
  toggleSettings: (open) => set((s) => ({ isSettingsOpen: open ?? !s.isSettingsOpen })),
  toggleSearch: (open) => set((s) => ({ isSearchOpen: open ?? !s.isSearchOpen })),
  toggleGemsManager: (open) => set((s) => ({ isGemsManagerOpen: open ?? !s.isGemsManagerOpen })),
}));
