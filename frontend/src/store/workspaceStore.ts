import { create } from 'zustand';
import {
  GlobalPromptItem,
  WorkspaceSkillItem,
  MCPConnectorItem,
  MCPDiscoveredTool,
  MCPSchemaPinStatus,
} from '../types';

export type WorkspaceHubTab = 'prompts' | 'skills' | 'connectors';

export interface ConnectorTestResult {
  ok: boolean;
  connector_id?: string;
  latency_ms?: number;
  tools?: MCPDiscoveredTool[];
  error?: string | null;
  pin_status?: MCPSchemaPinStatus;
  pin_sha256?: string | null;
  pin_changed_tools?: string[];
}

interface WorkspaceState {
  promptsEnabled: boolean;
  activePromptId: string | null;
  prompts: GlobalPromptItem[];
  enabledSkillIds: string[];
  skills: WorkspaceSkillItem[];
  connectors: MCPConnectorItem[];
  connectorTemplates: any[];
  testResults: Record<string, ConnectorTestResult>;
  isHubOpen: boolean;
  activeHubTab: WorkspaceHubTab;
  useGlobalPromptForChat: boolean;
  isLoading: boolean;
  error: string | null;

  openHub: (tab?: WorkspaceHubTab) => void;
  closeHub: () => void;
  setActiveHubTab: (tab: WorkspaceHubTab) => void;
  setUseGlobalPromptForChat: (enabled: boolean) => void;
  fetchWorkspaceConfig: () => Promise<void>;
  togglePromptsEnabled: (enabled: boolean) => Promise<void>;
  activatePrompt: (promptId: string | null) => Promise<void>;
  createPrompt: (payload: Partial<GlobalPromptItem>) => Promise<void>;
  updatePrompt: (id: string, payload: Partial<GlobalPromptItem>) => Promise<void>;
  deletePrompt: (id: string) => Promise<void>;
  toggleSkill: (skillId: string) => Promise<void>;
  createSkill: (payload: Partial<WorkspaceSkillItem> & { slug?: string }) => Promise<void>;
  updateSkill: (id: string, payload: Partial<WorkspaceSkillItem>) => Promise<void>;
  deleteSkill: (id: string) => Promise<void>;
  syncSkillToGem: (id: string) => Promise<any>;
  createConnector: (payload: Partial<MCPConnectorItem>) => Promise<void>;
  updateConnector: (id: string, payload: Partial<MCPConnectorItem>) => Promise<void>;
  toggleConnector: (id: string, enabled: boolean) => Promise<void>;
  deleteConnector: (id: string) => Promise<void>;
  testConnector: (id: string) => Promise<ConnectorTestResult>;
  repinConnector: (id: string) => Promise<any>;
}

async function apiJson<T = any>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Workspace request failed: HTTP ${res.status}`);
  }
  return res.json();
}

function normalizeConfig(data: any) {
  const rp = data?.prompts;
  const prompts: GlobalPromptItem[] = Array.isArray(rp) ? rp : Array.isArray(rp?.items) ? rp.items : [];
  const activePromptId = !Array.isArray(rp) && rp?.active_prompt_id !== undefined ? rp.active_prompt_id : (prompts.find((p) => p.is_active)?.id ?? null);
  const promptsEnabled = !Array.isArray(rp) && rp?.enabled !== undefined ? Boolean(rp.enabled) : true;
  const rs = data?.skills;
  const skills: WorkspaceSkillItem[] = Array.isArray(rs) ? rs : Array.isArray(rs?.items) ? rs.items : [];
  const enabledSkillIds: string[] = !Array.isArray(rs) && Array.isArray(rs?.enabled_skill_ids) ? rs.enabled_skill_ids : skills.filter((s: any) => s.is_active).map((s) => s.id);
  const rc = data?.connectors;
  const connectors: MCPConnectorItem[] = Array.isArray(rc) ? rc : Array.isArray(rc?.items) ? rc.items : [];
  const connectorTemplates = Array.isArray(data?.templates) ? data.templates : Array.isArray(rc?.templates) ? rc.templates : [];
  return { promptsEnabled, activePromptId, prompts, enabledSkillIds, skills, connectors, connectorTemplates };
}

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  promptsEnabled: true,
  activePromptId: null,
  prompts: [],
  enabledSkillIds: [],
  skills: [],
  connectors: [],
  connectorTemplates: [],
  testResults: {},
  isHubOpen: false,
  activeHubTab: 'prompts',
  useGlobalPromptForChat: true,
  isLoading: false,
  error: null,

  openHub: (tab) => set((s) => ({ isHubOpen: true, activeHubTab: tab ?? s.activeHubTab })),
  closeHub: () => set({ isHubOpen: false }),
  setActiveHubTab: (tab) => set({ activeHubTab: tab }),
  setUseGlobalPromptForChat: (enabled) => set({ useGlobalPromptForChat: enabled }),

  fetchWorkspaceConfig: async () => {
    set({ isLoading: true, error: null });
    try {
      const data = await apiJson<any>('/api/workspace/config');
      set({ ...normalizeConfig(data), isLoading: false });
    } catch (err: any) {
      set({ isLoading: false, error: err?.message || 'Failed to load workspace config' });
    }
  },
  togglePromptsEnabled: async (enabled) => {
    await apiJson('/api/workspace/prompts/activate', { method: 'POST', body: JSON.stringify({ enabled }) });
    await get().fetchWorkspaceConfig();
  },
  activatePrompt: async (promptId) => {
    await apiJson('/api/workspace/prompts/activate', { method: 'POST', body: JSON.stringify({ prompt_id: promptId }) });
    if (promptId) set({ useGlobalPromptForChat: true });
    await get().fetchWorkspaceConfig();
  },
  createPrompt: async (payload) => { await apiJson('/api/workspace/prompts', { method: 'POST', body: JSON.stringify(payload) }); await get().fetchWorkspaceConfig(); },
  updatePrompt: async (id, payload) => { await apiJson(`/api/workspace/prompts/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(payload) }); await get().fetchWorkspaceConfig(); },
  deletePrompt: async (id) => { await apiJson(`/api/workspace/prompts/${encodeURIComponent(id)}`, { method: 'DELETE' }); await get().fetchWorkspaceConfig(); },
  toggleSkill: async (skillId) => {
    const cur = get().enabledSkillIds;
    const next = cur.includes(skillId) ? cur.filter((id) => id !== skillId) : [...cur, skillId];
    set({ enabledSkillIds: next });
    try { await apiJson('/api/workspace/skills/active', { method: 'POST', body: JSON.stringify({ enabled_skill_ids: next }) }); await get().fetchWorkspaceConfig(); } catch {}
  },
  createSkill: async (payload) => { await apiJson('/api/workspace/skills', { method: 'POST', body: JSON.stringify(payload) }); await get().fetchWorkspaceConfig(); },
  updateSkill: async (id, payload) => { await apiJson(`/api/workspace/skills/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(payload) }); await get().fetchWorkspaceConfig(); },
  deleteSkill: async (id) => { await apiJson(`/api/workspace/skills/${encodeURIComponent(id)}`, { method: 'DELETE' }); await get().fetchWorkspaceConfig(); },
  syncSkillToGem: async (id) => {
    try { return await apiJson(`/api/workspace/skills/${encodeURIComponent(id)}/sync-gem`, { method: 'POST' }); } catch {
      const sk = get().skills.find((s) => s.id === id);
      if (!sk) throw new Error('Skill not found');
      return apiJson('/api/gems', { method: 'POST', body: JSON.stringify({ name: sk.name, description: sk.description || '', prompt: sk.content || sk.body || '' }) });
    }
  },
  createConnector: async (payload) => { await apiJson('/api/workspace/connectors', { method: 'POST', body: JSON.stringify(payload) }); await get().fetchWorkspaceConfig(); },
  updateConnector: async (id, payload) => { await apiJson(`/api/workspace/connectors/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(payload) }); await get().fetchWorkspaceConfig(); },
  toggleConnector: async (id, enabled) => { await apiJson(`/api/workspace/connectors/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify({ enabled }) }); await get().fetchWorkspaceConfig(); },
  deleteConnector: async (id) => { await apiJson(`/api/workspace/connectors/${encodeURIComponent(id)}`, { method: 'DELETE' }); await get().fetchWorkspaceConfig(); },
  testConnector: async (id) => {
    const res = await apiJson<ConnectorTestResult>(`/api/workspace/connectors/${encodeURIComponent(id)}/test`, { method: 'POST' });
    set((s) => ({
      testResults: { ...s.testResults, [id]: res },
      connectors: s.connectors.map((c) =>
        c.id === id && res.pin_status
          ? { ...c, pin_status: res.pin_status, pin_sha256: res.pin_sha256 ?? c.pin_sha256, pin_changed_tools: res.pin_changed_tools ?? [] }
          : c
      ),
    }));
    return res;
  },
  repinConnector: async (id) => {
    const res = await apiJson<any>(`/api/workspace/connectors/${encodeURIComponent(id)}/repin`, { method: 'POST' });
    set((s) => ({
      testResults: s.testResults[id]
        ? { ...s.testResults, [id]: { ...s.testResults[id], ...res, pin_status: res?.pin_status || 'verified', pin_changed_tools: [] } }
        : s.testResults,
    }));
    await get().fetchWorkspaceConfig();
    return res;
  },
}));
