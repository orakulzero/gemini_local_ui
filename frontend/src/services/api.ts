import {
  ConversationSummary,
  ConversationDetail,
  ModelOption,
  GemOption,
  SessionHealthStatus,
  SearchResultItem,
  AccountItem,
  FailoverConfig,
  LimitsResponse,
  NotebookInfo,
  NotebookDetail,
  NotebookSource,
  NotebookAttachContextResponse,
} from '../types';


export async function fetchSessionStatus(): Promise<{
  authenticated: boolean;
  health: SessionHealthStatus | null;
}> {
  const res = await fetch('/api/session/status');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return {
    authenticated: Boolean(data.authenticated),
    health: data.health || null,
  };
}

export async function submitCredentials(
  cookies?: string,
  autoImport = false
): Promise<boolean> {
  const res = await fetch('/api/session/credentials', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cookies: autoImport ? undefined : cookies, auto_import: autoImport }),
  });
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new Error(errData.detail || 'Failed to update credentials');
  }
  const data = await res.json();
  return Boolean(data.authenticated);
}

export async function clearCredentials(): Promise<void> {
  const res = await fetch('/api/session/credentials', { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to clear credentials');
}

export async function rotateSession(): Promise<boolean> {
  const res = await fetch('/api/session/rotate', { method: 'POST' });
  if (!res.ok) return false;
  const data = await res.json();
  return Boolean(data.success);
}

export async function fetchModels(): Promise<ModelOption[]> {
  const res = await fetch('/api/models');
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data.models) ? data.models : [];
}

export async function fetchGems(type = 'all'): Promise<GemOption[]> {
  const res = await fetch(`/api/gems?type=${type}`);
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data.gems)
    ? data.gems.map((g: any) => ({
        id: g.id,
        title: g.title || g.name || g.id,
        description: g.description || '',
        system_prompt: g.system_prompt || g.prompt || '',
        icon: g.icon,
        is_builtin: Boolean(g.is_builtin),
        created_at: g.created_at,
      }))
    : [];
}

export async function fetchDetailedGems(type = 'all'): Promise<{
  gems: GemOption[];
  custom_gems: GemOption[];
  system_gems: GemOption[];
}> {
  const res = await fetch(`/api/gems?type=${type}`);
  if (!res.ok) return { gems: [], custom_gems: [], system_gems: [] };
  const data = await res.json();
  const mapper = (g: any): GemOption => ({
    id: g.id,
    title: g.title || g.name || g.id,
    description: g.description || '',
    system_prompt: g.system_prompt || g.prompt || '',
    icon: g.icon,
    is_builtin: Boolean(g.is_builtin),
    created_at: g.created_at,
  });
  return {
    gems: Array.isArray(data.gems) ? data.gems.map(mapper) : [],
    custom_gems: Array.isArray(data.custom_gems) ? data.custom_gems.map(mapper) : [],
    system_gems: Array.isArray(data.system_gems) ? data.system_gems.map(mapper) : [],
  };
}

export async function updateConversation(
  id: string,
  update: { title?: string; is_pinned?: boolean; system_prompt?: string }
): Promise<any> {
  const res = await fetch(`/api/conversations/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(update),
  });
  if (!res.ok) throw new Error('Failed to update conversation');
  return res.json();
}

export async function fetchConversations(
  limit = 50,
  pinnedFirst = true
): Promise<ConversationSummary[]> {
  const res = await fetch(
    `/api/conversations?limit=${limit}&pinned_first=${pinnedFirst}`
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return data.conversations || [];
}

export async function fetchConversationDetail(
  id: string,
  leafId?: number
): Promise<ConversationDetail> {
  const url = leafId
    ? `/api/conversations/${id}?tree=true&leaf_id=${leafId}`
    : `/api/conversations/${id}?tree=true`;
  const res = await fetch(url);
  if (!res.ok) throw new Error('Failed to load conversation');
  return res.json();
}

export async function createConversation(
  title = 'New Conversation',
  model = '56fdd199312815e2',
  gemId?: string | null
): Promise<{ id: string; title: string }> {
  const res = await fetch('/api/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, model, gem_id: gemId }),
  });
  if (!res.ok) throw new Error('Failed to create conversation');
  return res.json();
}

export async function deleteConversation(
  id: string,
  remote = false
): Promise<void> {
  const res = await fetch(`/api/conversations/${id}?remote=${remote}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error('Failed to delete conversation');
}

export async function switchBranch(
  conversationId: string,
  targetMessageId: number
): Promise<{ active_leaf_message_id: number; active_thread: any[] }> {
  const res = await fetch(`/api/conversations/${conversationId}/branch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_message_id: targetMessageId }),
  });
  if (!res.ok) throw new Error('Failed to switch branch');
  return res.json();
}

export async function editMessage(
  conversationId: string,
  messageId: number,
  content: string,
  model?: string
): Promise<{ created_message: any; active_thread: any[] }> {
  const res = await fetch(
    `/api/conversations/${conversationId}/messages/${messageId}/edit`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, model }),
    }
  );
  if (!res.ok) throw new Error('Failed to edit message');
  return res.json();
}

export async function searchMessages(query: string): Promise<SearchResultItem[]> {
  const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
  if (!res.ok) throw new Error('Search failed');
  const data = await res.json();
  return data.results || [];
}

export async function triggerRemoteSync(): Promise<{ synced_count: number }> {
  const res = await fetch('/api/conversations/sync', { method: 'POST' });
  if (!res.ok) throw new Error('Remote sync failed');
  return res.json();
}

export async function fetchAccounts(): Promise<{
  active_account_id: string;
  accounts: AccountItem[];
  failover_config: FailoverConfig;
}> {
  const res = await fetch('/api/accounts');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function createAccount(req: {
  name: string;
  cookies?: string;
  auto_import?: boolean;
  tier_override?: string;
  priority?: number;
}): Promise<{ success: boolean; account_id: string; account: AccountItem }> {
  const res = await fetch('/api/accounts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new Error(errData.detail || 'Failed to create account');
  }
  return res.json();
}

export async function activateAccount(accountId: string): Promise<boolean> {
  const res = await fetch(`/api/accounts/${encodeURIComponent(accountId)}/activate`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error('Failed to activate account');
  const data = await res.json();
  return Boolean(data.success);
}

export async function deleteAccount(accountId: string): Promise<boolean> {
  const res = await fetch(`/api/accounts/${encodeURIComponent(accountId)}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error('Failed to delete account');
  const data = await res.json();
  return Boolean(data.success);
}

export async function updateFailoverConfig(
  config: Partial<FailoverConfig>
): Promise<boolean> {
  const res = await fetch('/api/accounts/failover-config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
  if (!res.ok) throw new Error('Failed to update failover config');
  const data = await res.json();
  return Boolean(data.success);
}

export async function fetchLimits(accountId?: string): Promise<LimitsResponse> {
  const url = accountId ? `/api/limits?account_id=${encodeURIComponent(accountId)}` : '/api/limits';
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function updateThresholds(
  accountId: string,
  thresholds: {
    daily_request_threshold: number;
    daily_token_threshold: number;
    hourly_request_threshold: number;
  }
): Promise<boolean> {
  const res = await fetch(`/api/limits/${encodeURIComponent(accountId)}/thresholds`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(thresholds),
  });
  if (!res.ok) throw new Error('Failed to update thresholds');
  const data = await res.json();
  return Boolean(data.success);
}

// NotebookLM API Client (Feature F50 & F51)
export async function fetchNotebooks(): Promise<NotebookInfo[]> {
  const res = await fetch('/api/notebooklm/notebooks');
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to fetch notebooks: HTTP ${res.status}`);
  }
  return res.json();
}

export async function fetchNotebookDetail(id: string): Promise<NotebookDetail> {
  const res = await fetch(`/api/notebooklm/notebooks/${encodeURIComponent(id)}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to fetch notebook detail: HTTP ${res.status}`);
  }
  return res.json();
}

export async function createNotebook(title: string): Promise<NotebookInfo> {
  const res = await fetch('/api/notebooklm/notebooks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to create notebook: HTTP ${res.status}`);
  }
  return res.json();
}

export async function deleteNotebook(id: string): Promise<{ success: boolean; id: string }> {
  const res = await fetch(`/api/notebooklm/notebooks/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to delete notebook: HTTP ${res.status}`);
  }
  return res.json();
}

export async function renameNotebook(id: string, title: string): Promise<{ success: boolean; id: string; title: string }> {
  const res = await fetch(`/api/notebooklm/notebooks/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to rename notebook: HTTP ${res.status}`);
  }
  return res.json();
}

export async function attachNotebookContext(req: {
  notebook_id: string;
  source_ids?: string[];
  note_ids?: string[];
  include_notes?: boolean;
}): Promise<NotebookAttachContextResponse> {
  const res = await fetch('/api/notebooklm/attach-context', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to attach notebook context: HTTP ${res.status}`);
  }
  return res.json();
}

export async function exportToNotebook(req: {
  notebook_id?: string;
  new_notebook_title?: string;
  conversation_id?: string;
  message_ids?: number[];
  title?: string;
  content?: string;
}): Promise<{ success: boolean; notebook_id: string; source: NotebookSource }> {
  const res = await fetch('/api/notebooklm/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to export to notebook: HTTP ${res.status}`);
  }
  return res.json();
}


