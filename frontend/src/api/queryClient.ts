import { QueryClient, useQuery } from '@tanstack/react-query';
import { apiClient } from './typedClient';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

export const queryKeys = {
  conversations: ['conversations'] as const,
  conversationDetail: (id: string, leafId?: number | null) =>
    ['conversation', id, leafId ?? null] as const,
  models: ['models'] as const,
  sessionStatus: ['sessionStatus'] as const,
  workspaceConfig: ['workspaceConfig'] as const,
};

export function useConversationsQuery(limit = 50, pinnedFirst = true) {
  return useQuery({
    queryKey: queryKeys.conversations,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/conversations', {
        params: { query: { limit, pinned_first: pinnedFirst } },
      });
      if (error) throw new Error('Failed to fetch conversations');
      return (data as any)?.conversations || [];
    },
  });
}

export function useConversationDetailQuery(id: string | null, leafId?: number | null) {
  return useQuery({
    queryKey: queryKeys.conversationDetail(id || '', leafId),
    enabled: Boolean(id && !id.startsWith('incognito_')),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/conversations/{id}', {
        params: {
          path: { id: id! },
          query: { tree: true, ...(leafId != null ? { leaf_id: leafId } : {}) },
        },
      });
      if (error) throw new Error('Failed to load conversation');
      return data as any;
    },
  });
}

export function useModelsQuery() {
  return useQuery({
    queryKey: queryKeys.models,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/models');
      if (error) throw new Error('Failed to fetch models');
      return Array.isArray((data as any)?.models) ? (data as any).models : [];
    },
  });
}

export function useSessionStatusQuery() {
  return useQuery({
    queryKey: queryKeys.sessionStatus,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/session/status');
      if (error) throw new Error('Failed to fetch session status');
      return {
        authenticated: Boolean((data as any)?.authenticated),
        health: (data as any)?.health || null,
      };
    },
  });
}

export function useWorkspaceConfigQuery() {
  return useQuery({
    queryKey: queryKeys.workspaceConfig,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/workspace/config');
      if (error) throw new Error('Failed to fetch workspace config');
      return data as any;
    },
  });
}
