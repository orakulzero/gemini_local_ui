import type { ChatAttachment } from '../../types';
import type { ChatGeneratePayload } from '../../services/streaming';
import * as api from '../../services/api';
import { queryClient, queryKeys } from '../../api/queryClient';
import { useWorkspaceStore } from '../workspaceStore';
import { pendingUploadPromises } from './uiSlice';
import { applyNodePatch, ChatState } from './types';

export function getWorkspaceRequestPayload() {
  const ws = useWorkspaceStore.getState();
  return {
    use_global_prompt: Boolean(ws.promptsEnabled && ws.useGlobalPromptForChat && ws.activePromptId),
    skill_ids: ws.enabledSkillIds,
    connector_ids: ws.connectors.filter((c) => c.enabled).map((c) => c.id),
  };
}

export function selectMergedImages(prevImages: any, metaUpdate: any) {
  const incoming =
    Array.isArray(metaUpdate.images) && metaUpdate.images.length > 0
      ? metaUpdate.images
      : Array.isArray(metaUpdate.generated_images) && metaUpdate.generated_images.length > 0
        ? metaUpdate.generated_images
        : null;
  if (!incoming) return prevImages;

  const incomingHasLocal = incoming.some(
    (img: any) => typeof img?.url === 'string' && img.url.startsWith('/files/')
  );
  if (incomingHasLocal) return incoming;

  const prevHasLocal =
    Array.isArray(prevImages) &&
    prevImages.some((img: any) => typeof img?.url === 'string' && img.url.startsWith('/files/'));
  if (!prevHasLocal) return incoming;

  return incoming.map((inc: any, idx: number) => {
    const matched =
      prevImages.find(
        (p: any) =>
          (p?.original_url && p.original_url === inc?.url) ||
          (p?.image_id && p.image_id === inc?.image_id) ||
          (p?.url && p.url === inc?.url)
      ) || prevImages[idx];
    return matched?.url?.startsWith('/files/') ? { ...inc, ...matched } : inc;
  });
}

export function mergeAssistantMetadata(prevMeta: any, metaUpdate: any) {
  const base = prevMeta || {};
  const nextImages = selectMergedImages(base.images, metaUpdate);
  const nextExecutions =
    Array.isArray(metaUpdate.code_executions) && metaUpdate.code_executions.length > 0
      ? metaUpdate.code_executions
      : base.code_executions;
  const nextToolCalls =
    Array.isArray(metaUpdate.tool_calls) && metaUpdate.tool_calls.length > 0
      ? metaUpdate.tool_calls
      : base.tool_calls;
  return {
    ...base,
    ...metaUpdate,
    citations: metaUpdate.citations || base.citations,
    images: nextImages,
    youtube: metaUpdate.youtube || base.youtube,
    grounding: metaUpdate.grounding || base.grounding,
    code_executions: nextExecutions,
    tool_calls: nextToolCalls,
    pending_approval: null,
  };
}

export async function resolveOutgoingAttachments(
  initialAttachments: ChatAttachment[]
): Promise<ChatGeneratePayload['attachments']> {
  const resolved: NonNullable<ChatGeneratePayload['attachments']> = [];
  for (const a of initialAttachments) {
    let resolvedUri = a.uri;
    let resolvedMime = a.mime_type;
    let resolvedSize = a.size_bytes;

    if (!resolvedUri && a.id && pendingUploadPromises.has(a.id)) {
      try {
        const up = await pendingUploadPromises.get(a.id)!;
        resolvedUri = up.uri;
        resolvedMime = up.mime_type || resolvedMime;
        resolvedSize = up.file_size ?? resolvedSize;
      } catch {
        // Retry once below if File handle is present
      }
    }
    if (!resolvedUri && a.file) {
      const up = await api.uploadAttachment(a.file);
      resolvedUri = up.uri;
      resolvedMime = up.mime_type || resolvedMime;
      resolvedSize = up.file_size ?? resolvedSize;
    }
    resolved.push({
      uri: resolvedUri,
      filename: a.filename,
      mime_type: resolvedMime,
      file_size: resolvedSize,
      storage_path: a.storage_path,
    });
  }
  return resolved;
}

export function syncConversationDetailCache(convId: string, state: ChatState) {
  if (!convId || convId.startsWith('incognito_')) return;
  queryClient.setQueryData(queryKeys.conversationDetail(convId, null), (prev: any) => ({
    ...(prev || {}),
    id: convId,
    active_leaf_message_id: state.activeLeafMessageId,
    active_thread: state.activeThread,
    all_messages: Object.values(state.nodesById),
  }));
}

export function patchNodeFields(
  set: (fn: (s: ChatState) => Partial<ChatState>) => void,
  nodeId: number,
  metaUpdate: any
) {
  set((s) =>
    applyNodePatch(s, nodeId, (m) => ({
      ...m,
      metadata: mergeAssistantMetadata(m.metadata, metaUpdate),
    }))
  );
}
