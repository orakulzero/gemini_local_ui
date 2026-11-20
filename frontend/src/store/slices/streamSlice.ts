import type { StateCreator } from 'zustand';
import type { Message } from '../../types';
import { ChatGeneratePayload, streamChatGenerate } from '../../services/streaming';
import * as api from '../../services/api';
import { useSessionStore } from '../sessionStore';
import { applyNodePatch, ChatState, StreamSlice } from './types';
import {
  getWorkspaceRequestPayload,
  mergeAssistantMetadata,
  patchNodeFields,
  resolveOutgoingAttachments,
  selectMergedImages,
  syncConversationDetailCache,
} from './streamHelpers';

export { mergeAssistantMetadata, selectMergedImages };

interface TurnStreamOpts {
  convId: string;
  tempAsstId: number;
  tempUserId?: number;
  abortController: AbortController;
  payload: ChatGeneratePayload;
  set: (partial: Partial<ChatState> | ((s: ChatState) => Partial<ChatState>)) => void;
  get: () => ChatState;
}

async function runAssistantTurnStream({
  convId,
  tempAsstId,
  tempUserId,
  abortController,
  payload,
  set,
  get,
}: TurnStreamOpts) {
  let activeAsstId = tempAsstId;
  let pendingThoughtDelta = '';
  let pendingTextDelta = '';
  let rafHandle: number | null = null;

  const targetId = () => get().activeNodeId ?? activeAsstId;

  const flushDeltas = () => {
    if (rafHandle !== null) {
      cancelAnimationFrame(rafHandle);
      rafHandle = null;
    }
    if (!pendingThoughtDelta && !pendingTextDelta) return;
    const tDelta = pendingThoughtDelta;
    const cDelta = pendingTextDelta;
    pendingThoughtDelta = '';
    pendingTextDelta = '';
    const nodeId = targetId();
    set((s) => {
      const updatedThought = tDelta ? s.streamingThought + tDelta : s.streamingThought;
      const updatedText = cDelta ? s.streamingText + cDelta : s.streamingText;
      return {
        streamingThought: updatedThought,
        streamingText: updatedText,
        ...applyNodePatch(s, nodeId, (m) => ({ ...m, thoughts: updatedThought, content: updatedText })),
      };
    });
  };

  const scheduleFlush = () => {
    if (rafHandle === null) rafHandle = requestAnimationFrame(flushDeltas);
  };

  const updateAsstMeta = (metaUpdate: any) => {
    if (!metaUpdate) return;
    patchNodeFields(set, targetId(), metaUpdate);
  };

  try {
    await streamChatGenerate(payload, {
      signal: abortController.signal,
      onStart: (_serverConvId, serverUserMsgId) => {
        if (tempUserId !== undefined && serverUserMsgId) {
          get().rekeyDagNode(tempUserId, serverUserMsgId);
        }
      },
      onThought: (delta) => { pendingThoughtDelta += delta; scheduleFlush(); },
      onText: (delta) => { pendingTextDelta += delta; scheduleFlush(); },
      onCitation: updateAsstMeta,
      onMedia: updateAsstMeta,
      onToolCall: (metaUpdate: any) => {
        if (!metaUpdate?.tool_calls?.length) return;
        const nodeId = targetId();
        get().clearPendingApproval(nodeId);
        get().clearPendingElicitation(nodeId);
        set((s) =>
          applyNodePatch(s, nodeId, (m) => {
            const prev = Array.isArray(m.metadata?.tool_calls) ? m.metadata.tool_calls : [];
            return {
              ...m,
              metadata: { ...(m.metadata || {}), pending_approval: null, tool_calls: [...prev, ...metaUpdate.tool_calls] },
            };
          })
        );
      },
      onToolApprovalRequired: (approvalMeta) => {
        if (approvalMeta) get().setPendingApprovalForNode(targetId(), approvalMeta);
      },
      onElicitationRequired: (elicitMeta) => {
        if (elicitMeta) get().setPendingElicitationForNode(targetId(), elicitMeta);
      },
      onSaved: (serverAsstMsgId) => {
        flushDeltas();
        if (serverAsstMsgId) {
          const oldId = activeAsstId;
          activeAsstId = serverAsstMsgId;
          get().rekeyDagNode(oldId, serverAsstMsgId);
          set({ activeNodeId: serverAsstMsgId, streamingAssistantId: serverAsstMsgId, activeLeafMessageId: serverAsstMsgId });
        }
      },
      onError: (errMsg) => {
        flushDeltas();
        const nodeId = targetId();
        set((s) => ({
          error: errMsg,
          ...applyNodePatch(s, nodeId, (m) => ({ ...m, is_error: true, content: `Error: ${errMsg}` })),
        }));
      },
      onDone: (metadata) => {
        flushDeltas();
        const nodeId = targetId();
        if (metadata) updateAsstMeta(metadata);
        get().clearPendingApproval(nodeId);
        get().clearPendingElicitation(nodeId);
        set({ isGenerating: false, activeAbortController: null, activeNodeId: null });
        syncConversationDetailCache(convId, get());
        if (!get().isIncognito) get().loadConversations();
      },
    });
  } catch (err: any) {
    if (err.name !== 'AbortError') set({ error: err.message });
  } finally {
    flushDeltas();
    set({ isGenerating: false, activeAbortController: null, activeNodeId: null });
  }
}

export const createStreamSlice: StateCreator<ChatState, [], [], StreamSlice> = (set, get) => ({
  isGenerating: false,
  streamingThought: '',
  streamingText: '',
  streamingAssistantId: null,
  activeNodeId: null,
  activeAbortController: null,

  regenerateWithModel: async (messageId, model, reasoningLevel) => {
    const { activeConversationId, activeThread, allMessagesMap, nodesById, isGenerating } = get();
    if (isGenerating || !activeConversationId) return;

    const targetMsg = nodesById[messageId] || allMessagesMap[messageId] || activeThread.find((m) => m.id === messageId);
    if (!targetMsg) return;

    const userMsg: Message | null =
      targetMsg.role === 'assistant' && targetMsg.parent_id != null
        ? nodesById[targetMsg.parent_id] || allMessagesMap[targetMsg.parent_id] || activeThread.find((m) => m.id === targetMsg.parent_id) || null
        : targetMsg.role === 'user'
          ? targetMsg
          : null;
    if (!userMsg) return;

    const effectiveModel = `${model}@thinking=${reasoningLevel ?? 1}@mode=chat`;
    const tempAsstId = Date.now();
    const abortController = new AbortController();

    get().upsertDagNode({
      id: tempAsstId, conversation_id: activeConversationId, parent_id: userMsg.id,
      role: 'assistant', content: '', thoughts: '', model, created_at: tempAsstId, attachments: [],
    }, true);

    set({
      isGenerating: true, streamingThought: '', streamingText: '',
      streamingAssistantId: tempAsstId, activeNodeId: tempAsstId,
      activeAbortController: abortController, error: null, isPinnedToBottom: true,
    });

    await runAssistantTurnStream({
      convId: activeConversationId, tempAsstId, abortController, set, get,
      payload: {
        conversation_id: activeConversationId, parent_message_id: userMsg.id,
        existing_user_message_id: userMsg.id, prompt: userMsg.content, model: effectiveModel,
        ...getWorkspaceRequestPayload(),
      },
    });
  },

  editMessageAndBranch: async (messageId, newContent) => {
    const { activeConversationId, isGenerating } = get();
    if (!activeConversationId || isGenerating || !newContent.trim()) return;
    try {
      const { selectedModel, reasoningLevel, featureMode, selectedGemId } = useSessionStore.getState();
      const effectiveModel = `${selectedModel}@thinking=${reasoningLevel}@mode=${featureMode}`;
      const data = await api.editMessage(activeConversationId, messageId, newContent, effectiveModel);
      const createdUserMsg = data.created_message;
      for (const m of data.active_thread || []) get().upsertDagNode(m, true);

      const tempAsstId = Date.now();
      const abortController = new AbortController();
      get().upsertDagNode({
        id: tempAsstId, conversation_id: activeConversationId, parent_id: createdUserMsg.id,
        role: 'assistant', content: '', thoughts: '', model: selectedModel, created_at: tempAsstId, attachments: [],
      }, true);

      set({
        activeLeafMessageId: createdUserMsg.id, isGenerating: true, streamingThought: '', streamingText: '',
        streamingAssistantId: tempAsstId, activeNodeId: tempAsstId,
        activeAbortController: abortController, error: null, isPinnedToBottom: true,
      });

      await runAssistantTurnStream({
        convId: activeConversationId, tempAsstId, abortController, set, get,
        payload: {
          conversation_id: activeConversationId, parent_message_id: createdUserMsg.parent_id ?? null,
          existing_user_message_id: createdUserMsg.id, prompt: newContent, model: effectiveModel,
          feature_mode: featureMode, gem_id: selectedGemId, ...getWorkspaceRequestPayload(),
        },
      });
    } catch (err: any) {
      if (err.name !== 'AbortError') set({ error: err.message });
    } finally {
      set({ isGenerating: false, activeAbortController: null, activeNodeId: null });
    }
  },

  sendMessage: async (prompt) => {
    const { activeConversationId, activeThread, pendingAttachments, isGenerating, isIncognito } = get();
    if (isGenerating) return;

    let convId = activeConversationId;
    if (!convId) {
      if (isIncognito) {
        convId = `incognito_${Date.now()}`;
        set({ activeConversationId: convId });
      } else {
        convId = await get().createConversation(prompt.slice(0, 40) || 'New Conversation');
      }
    }

    const parentId = activeThread.length > 0 ? activeThread[activeThread.length - 1].id : null;
    const { selectedModel, reasoningLevel, featureMode, selectedGemId } = useSessionStore.getState();
    const effectiveModel = `${selectedModel}@thinking=${reasoningLevel}@mode=${featureMode}`;

    if (prompt.trim()) {
      set((s) => ({
        promptHistory: [...s.promptHistory.filter((p) => p !== prompt), prompt],
        promptHistoryIndex: -1, draftPrompt: '',
      }));
    }

    const abortController = new AbortController();
    const tempUserId = Date.now();
    const tempAsstId = tempUserId + 1;
    const initialAttachments = [...pendingAttachments];
    const hasUserMsg = Boolean(prompt.trim() || initialAttachments.length > 0);

    if (hasUserMsg) {
      get().upsertDagNode({
        id: tempUserId, conversation_id: convId, parent_id: parentId, role: 'user',
        content: prompt, model: selectedModel, created_at: tempUserId, attachments: initialAttachments,
      }, true);
    }

    get().upsertDagNode({
      id: tempAsstId, conversation_id: convId, parent_id: hasUserMsg ? tempUserId : parentId,
      role: 'assistant', content: '', thoughts: '', model: selectedModel, created_at: tempAsstId, attachments: [],
    }, true);

    set({
      isGenerating: true, streamingThought: '', streamingText: '',
      streamingAssistantId: tempAsstId, activeNodeId: tempAsstId,
      activeAbortController: abortController, pendingAttachments: [], error: null, isPinnedToBottom: true,
    });

    try {
      const resolvedAttachments = await resolveOutgoingAttachments(initialAttachments);
      await runAssistantTurnStream({
        convId, tempAsstId, tempUserId: hasUserMsg ? tempUserId : undefined, abortController, set, get,
        payload: {
          conversation_id: convId, parent_message_id: parentId, prompt, model: effectiveModel,
          feature_mode: featureMode, gem_id: selectedGemId, attachments: resolvedAttachments,
          incognito: isIncognito, ...getWorkspaceRequestPayload(),
        },
      });
    } catch (err: any) {
      if (err.name !== 'AbortError') set({ error: err.message });
      set({ isGenerating: false, activeAbortController: null, activeNodeId: null });
    }
  },

  abortGeneration: () => {
    const { activeAbortController } = get();
    if (activeAbortController) {
      activeAbortController.abort();
      set({ isGenerating: false, activeAbortController: null, activeNodeId: null });
    }
  },
});
