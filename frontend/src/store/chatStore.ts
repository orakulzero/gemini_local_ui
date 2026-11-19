import { create } from 'zustand';
import { Message, ConversationSummary, ChatAttachment } from '../types';
import { streamChatGenerate } from '../services/streaming';
import * as api from '../services/api';
import { useSessionStore } from './sessionStore';
import { useWorkspaceStore } from './workspaceStore';

const pendingUploadPromises = new Map<
  string,
  Promise<{ uri: string; mime_type: string; file_size: number }>
>();

function getWorkspaceRequestPayload() {
  const ws = useWorkspaceStore.getState();
  return {
    use_global_prompt: Boolean(ws.promptsEnabled && ws.useGlobalPromptForChat && ws.activePromptId),
    skill_ids: ws.enabledSkillIds,
    connector_ids: ws.connectors.filter((c) => c.enabled).map((c) => c.id),
  };
}

function selectMergedImages(prevImages: any, metaUpdate: any) {
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

function mergeAssistantMetadata(prevMeta: any, metaUpdate: any) {
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

interface ChatState {
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  activeLeafMessageId: number | null;
  activeThread: Message[];
  allMessagesMap: Record<number, Message>;
  isGenerating: boolean;
  streamingThought: string;
  streamingText: string;
  streamingAssistantId: number | null;
  promptHistory: string[];
  promptHistoryIndex: number;
  draftPrompt: string;
  injectedContext: string;
  pendingAttachments: ChatAttachment[];
  activeAbortController: AbortController | null;
  isPinnedToBottom: boolean;
  isIncognito: boolean;
  error: string | null;

  // Actions
  loadConversations: () => Promise<void>;
  syncRemoteConversations: () => Promise<void>;
  selectConversation: (id: string, leafId?: number) => Promise<void>;
  createConversation: (title?: string) => Promise<string>;
  deleteConversation: (id: string, remote?: boolean) => Promise<void>;
  togglePinConversation: (id: string) => Promise<void>;
  renameConversation: (id: string, newTitle: string) => Promise<void>;
  regenerateWithModel: (messageId: number, model: string, reasoningLevel?: number) => Promise<void>;
  setIncognito: (incognito: boolean) => void;
  switchBranch: (targetMessageId: number) => Promise<void>;
  editMessageAndBranch: (messageId: number, newContent: string) => Promise<void>;
  updateMessageContent: (messageId: number, content: string, extraMeta?: any) => void;
  clearPendingApproval: (messageId: number) => void;
  sendMessage: (prompt: string) => Promise<void>;
  abortGeneration: () => void;
  addAttachment: (file: File) => void;
  removeAttachment: (index: number) => void;
  clearAttachments: () => void;
  recallPromptUp: (currentText: string) => string;
  recallPromptDown: () => string;
  setDraftPrompt: (draft: string) => void;
  injectContext: (context: string) => void;
  setIsPinnedToBottom: (pinned: boolean) => void;
  setFixtureMessages: (messages: Message[]) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  activeConversationId: null,
  activeLeafMessageId: null,
  activeThread: [],
  allMessagesMap: {},
  isGenerating: false,
  streamingThought: '',
  streamingText: '',
  streamingAssistantId: null,
  promptHistory: [],
  promptHistoryIndex: -1,
  draftPrompt: '',
  injectedContext: '',
  pendingAttachments: [],
  activeAbortController: null,
  isPinnedToBottom: true,
  isIncognito: false,
  error: null,

  loadConversations: async () => {
    try {
      const convs = await api.fetchConversations();
      set({ conversations: convs });
    } catch (err: any) {
      set({ error: err.message });
    }
  },

  syncRemoteConversations: async () => {
    try {
      await api.triggerRemoteSync();
      await get().loadConversations();
    } catch (err: any) {
      set({ error: err.message });
    }
  },

  selectConversation: async (id: string, leafId?: number) => {
    try {
      const data = await api.fetchConversationDetail(id, leafId);
      const msgMap: Record<number, Message> = {};
      if (Array.isArray(data.all_messages)) {
        for (const m of data.all_messages) {
          msgMap[m.id] = m;
        }
      }

      set({
        activeConversationId: id,
        activeLeafMessageId: data.active_leaf_message_id || null,
        activeThread: data.active_thread || [],
        allMessagesMap: msgMap,
        error: null,
        isPinnedToBottom: true,
      });
    } catch (err: any) {
      set({ error: err.message });
    }
  },

  createConversation: async (title = 'New Conversation') => {
    const selectedModel = useSessionStore.getState().selectedModel;
    const selectedGemId = useSessionStore.getState().selectedGemId;

    const conv = await api.createConversation(title, selectedModel, selectedGemId);
    await get().loadConversations();
    await get().selectConversation(conv.id);
    return conv.id;
  },

  deleteConversation: async (id: string, remote = false) => {
    await api.deleteConversation(id, remote);
    const { activeConversationId, conversations } = get();
    if (activeConversationId === id) {
      const remaining = conversations.filter((c) => c.id !== id);
      if (remaining.length > 0) {
        await get().selectConversation(remaining[0].id);
      } else {
        set({
          activeConversationId: null,
          activeLeafMessageId: null,
          activeThread: [],
          allMessagesMap: {},
        });
      }
    }
    await get().loadConversations();
  },

  togglePinConversation: async (id: string) => {
    const conv = get().conversations.find((c) => c.id === id);
    if (!conv) return;
    const newPinned = !conv.is_pinned;
    set((s) => ({
      conversations: s.conversations.map((c) => (c.id === id ? { ...c, is_pinned: newPinned } : c)),
    }));
    try {
      await api.updateConversation(id, { is_pinned: newPinned });
      await get().loadConversations();
    } catch (err: any) {
      set({ error: err.message });
      await get().loadConversations();
    }
  },

  renameConversation: async (id: string, newTitle: string) => {
    if (!newTitle.trim()) return;
    set((s) => ({
      conversations: s.conversations.map((c) => (c.id === id ? { ...c, title: newTitle.trim() } : c)),
    }));
    try {
      await api.updateConversation(id, { title: newTitle.trim() });
      await get().loadConversations();
    } catch (err: any) {
      set({ error: err.message });
      await get().loadConversations();
    }
  },

  setIncognito: (incognito: boolean) => {
    set({ isIncognito: incognito });
    if (incognito) {
      set({
        activeConversationId: `incognito_${Date.now()}`,
        activeLeafMessageId: null,
        activeThread: [],
        allMessagesMap: {},
      });
    }
  },

  regenerateWithModel: async (messageId: number, model: string, reasoningLevel?: number) => {
    const { activeConversationId, activeThread, allMessagesMap, isGenerating } = get();
    if (isGenerating || !activeConversationId) return;

    const targetMsg = allMessagesMap[messageId] || activeThread.find((m) => m.id === messageId);
    if (!targetMsg) return;

    let userMsg: Message | null = null;
    if (targetMsg.role === 'assistant') {
      const parentId = targetMsg.parent_id;
      if (parentId) {
        userMsg = allMessagesMap[parentId] || activeThread.find((m) => m.id === parentId) || null;
      }
    } else {
      userMsg = targetMsg;
    }

    if (!userMsg) return;

    const effectiveReasoning = reasoningLevel ?? 1;
    const effectiveModel = `${model}@thinking=${effectiveReasoning}@mode=chat`;
    const tempAsstId = Date.now();
    const abortController = new AbortController();

    const userMsgIdx = activeThread.findIndex((m) => m.id === userMsg!.id);
    const baseThread = userMsgIdx !== -1 ? activeThread.slice(0, userMsgIdx + 1) : activeThread;

    const newAsstMsg: Message = {
      id: tempAsstId,
      conversation_id: activeConversationId,
      parent_id: userMsg.id,
      role: 'assistant',
      content: '',
      thoughts: '',
      model: model,
      created_at: tempAsstId,
      attachments: [],
    };

    set({
      activeThread: [...baseThread, newAsstMsg],
      isGenerating: true,
      streamingThought: '',
      streamingText: '',
      streamingAssistantId: tempAsstId,
      activeAbortController: abortController,
      error: null,
      isPinnedToBottom: true,
    });

    let activeAsstId = tempAsstId;
    let pendingThoughtDelta = '';
    let pendingTextDelta = '';
    let rafHandle: number | null = null;

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
      set((s) => {
        const updatedThought = tDelta ? s.streamingThought + tDelta : s.streamingThought;
        const updatedText = cDelta ? s.streamingText + cDelta : s.streamingText;
        return {
          streamingThought: updatedThought,
          streamingText: updatedText,
          activeThread: s.activeThread.map((m) =>
            m.id === activeAsstId ? { ...m, thoughts: updatedThought, content: updatedText } : m
          ),
        };
      });
    };

    const scheduleFlush = () => {
      if (rafHandle === null) {
        rafHandle = requestAnimationFrame(flushDeltas);
      }
    };

    const updateAsstMeta = (metaUpdate: any) => {
      if (!metaUpdate) return;
      set((s) => ({
        activeThread: s.activeThread.map((m) =>
          m.id === activeAsstId
            ? {
                ...m,
                metadata: mergeAssistantMetadata(m.metadata, metaUpdate),
              }
            : m
        ),
      }));
    };

    const appendToolCalls = (metaUpdate: any) => {
      if (!metaUpdate?.tool_calls?.length) return;
      set((s) => ({
        activeThread: s.activeThread.map((m) => {
          if (m.id !== activeAsstId) return m;
          const prev = Array.isArray(m.metadata?.tool_calls) ? m.metadata.tool_calls : [];
          return {
            ...m,
            metadata: {
              ...(m.metadata || {}),
              pending_approval: null,
              tool_calls: [...prev, ...metaUpdate.tool_calls],
            },
          };
        }),
      }));
    };

    const setPendingApproval = (approvalMeta: any) => {
      if (!approvalMeta) return;
      set((s) => ({
        activeThread: s.activeThread.map((m) =>
          m.id === activeAsstId
            ? { ...m, metadata: { ...(m.metadata || {}), pending_approval: approvalMeta } }
            : m
        ),
      }));
    };

    try {
      await streamChatGenerate(
        {
          conversation_id: activeConversationId,
          parent_message_id: userMsg.id,
          existing_user_message_id: userMsg.id,
          prompt: userMsg.content,
          model: effectiveModel,
          ...getWorkspaceRequestPayload(),
        },
        {
          signal: abortController.signal,
          onThought: (delta) => {
            pendingThoughtDelta += delta;
            scheduleFlush();
          },
          onText: (delta) => {
            pendingTextDelta += delta;
            scheduleFlush();
          },
          onCitation: updateAsstMeta,
          onMedia: updateAsstMeta,
          onToolCall: appendToolCalls,
          onToolApprovalRequired: setPendingApproval,
          onSaved: (serverAsstMsgId) => {
            flushDeltas();
            if (serverAsstMsgId) {
              activeAsstId = serverAsstMsgId;
              set((s) => ({
                activeLeafMessageId: serverAsstMsgId,
                activeThread: s.activeThread.map((m) =>
                  m.id === tempAsstId ? { ...m, id: serverAsstMsgId } : m
                ),
              }));
            }
          },
          onError: (errMsg) => {
            flushDeltas();
            set((s) => ({
              error: errMsg,
              activeThread: s.activeThread.map((m) =>
                m.id === activeAsstId ? { ...m, is_error: true, content: `Error: ${errMsg}` } : m
              ),
            }));
          },
          onDone: (metadata) => {
            flushDeltas();
            if (metadata) updateAsstMeta(metadata);
            set({ isGenerating: false, activeAbortController: null });
            get().selectConversation(activeConversationId);
            get().loadConversations();
          },
        }
      );
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        set({ error: err.message });
      }
    } finally {
      flushDeltas();
      set({ isGenerating: false, activeAbortController: null });
    }
  },

  switchBranch: async (targetMessageId: number) => {
    const { activeConversationId } = get();
    if (!activeConversationId) return;

    try {
      const data = await api.switchBranch(activeConversationId, targetMessageId);
      set({
        activeLeafMessageId: data.active_leaf_message_id,
        activeThread: data.active_thread || [],
        isPinnedToBottom: true,
      });
    } catch (err: any) {
      set({ error: err.message });
    }
  },

  editMessageAndBranch: async (messageId: number, newContent: string) => {
    const { activeConversationId, isGenerating } = get();
    if (!activeConversationId || isGenerating || !newContent.trim()) return;

    try {
      const { selectedModel, reasoningLevel, featureMode, selectedGemId } = useSessionStore.getState();
      const effectiveModel = `${selectedModel}@thinking=${reasoningLevel}@mode=${featureMode}`;
      const data = await api.editMessage(activeConversationId, messageId, newContent, effectiveModel);
      const createdUserMsg = data.created_message;
      const tempAsstId = Date.now();
      const abortController = new AbortController();

      const updatedThread = [
        ...(data.active_thread || []),
        {
          id: tempAsstId,
          conversation_id: activeConversationId,
          parent_id: createdUserMsg.id,
          role: 'assistant' as const,
          content: '',
          thoughts: '',
          model: selectedModel,
          created_at: tempAsstId,
          attachments: [],
        },
      ];

      set({
        activeThread: updatedThread,
        activeLeafMessageId: createdUserMsg.id,
        isGenerating: true,
        streamingThought: '',
        streamingText: '',
        streamingAssistantId: tempAsstId,
        activeAbortController: abortController,
        error: null,
        isPinnedToBottom: true,
      });

      let activeAsstId = tempAsstId;
      let pendingThoughtDelta = '';
      let pendingTextDelta = '';
      let rafHandle: number | null = null;

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
        set((s) => {
          const updatedThought = tDelta ? s.streamingThought + tDelta : s.streamingThought;
          const updatedText = cDelta ? s.streamingText + cDelta : s.streamingText;
          return {
            streamingThought: updatedThought,
            streamingText: updatedText,
            activeThread: s.activeThread.map((m) =>
              m.id === activeAsstId ? { ...m, thoughts: updatedThought, content: updatedText } : m
            ),
          };
        });
      };

      const scheduleFlush = () => {
        if (rafHandle === null) {
          rafHandle = requestAnimationFrame(flushDeltas);
        }
      };

      await streamChatGenerate(
        {
          conversation_id: activeConversationId,
          parent_message_id: createdUserMsg.parent_id ?? null,
          existing_user_message_id: createdUserMsg.id,
          prompt: newContent,
          model: effectiveModel,
          feature_mode: featureMode,
          gem_id: selectedGemId,
        },
        {
          signal: abortController.signal,
          onThought: (delta) => {
            pendingThoughtDelta += delta;
            scheduleFlush();
          },
          onText: (delta) => {
            pendingTextDelta += delta;
            scheduleFlush();
          },
          onSaved: (serverAsstMsgId) => {
            flushDeltas();
            if (serverAsstMsgId) {
              activeAsstId = serverAsstMsgId;
              set((s) => ({
                activeLeafMessageId: serverAsstMsgId,
                activeThread: s.activeThread.map((m) =>
                  m.id === tempAsstId ? { ...m, id: serverAsstMsgId } : m
                ),
              }));
            }
          },
          onError: (errMsg) => {
            flushDeltas();
            set((s) => ({
              error: errMsg,
              activeThread: s.activeThread.map((m) =>
                m.id === activeAsstId ? { ...m, is_error: true, content: `Error: ${errMsg}` } : m
              ),
            }));
          },
          onDone: () => {
            flushDeltas();
            set({ isGenerating: false, activeAbortController: null });
            get().loadConversations();
          },
        }
      );
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        set({ error: err.message });
      }
    } finally {
      set({ isGenerating: false, activeAbortController: null });
    }
  },

  sendMessage: async (prompt: string) => {
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
        promptHistoryIndex: -1,
        draftPrompt: '',
      }));
    }

    const abortController = new AbortController();
    const tempUserId = Date.now();
    const tempAsstId = tempUserId + 1;

    const initialAttachments = [...pendingAttachments];
    const newThread = [...get().activeThread];
    if (prompt.trim() || initialAttachments.length > 0) {
      newThread.push({
        id: tempUserId,
        conversation_id: convId,
        parent_id: parentId,
        role: 'user',
        content: prompt,
        model: selectedModel,
        created_at: tempUserId,
        attachments: initialAttachments,
      });
    }

    newThread.push({
      id: tempAsstId,
      conversation_id: convId,
      parent_id: tempUserId,
      role: 'assistant',
      content: '',
      thoughts: '',
      model: selectedModel,
      created_at: tempAsstId,
      attachments: [],
    });

    set({
      activeThread: newThread,
      isGenerating: true,
      streamingThought: '',
      streamingText: '',
      streamingAssistantId: tempAsstId,
      activeAbortController: abortController,
      pendingAttachments: [],
      error: null,
      isPinnedToBottom: true,
    });

    let activeAsstId = tempAsstId;
    let pendingThoughtDelta = '';
    let pendingTextDelta = '';
    let rafHandle: number | null = null;

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
      set((s) => {
        const updatedThought = tDelta ? s.streamingThought + tDelta : s.streamingThought;
        const updatedText = cDelta ? s.streamingText + cDelta : s.streamingText;
        return {
          streamingThought: updatedThought,
          streamingText: updatedText,
          activeThread: s.activeThread.map((m) =>
            m.id === activeAsstId ? { ...m, thoughts: updatedThought, content: updatedText } : m
          ),
        };
      });
    };

    const scheduleFlush = () => {
      if (rafHandle === null) {
        rafHandle = requestAnimationFrame(flushDeltas);
      }
    };

    const updateAsstMeta = (metaUpdate: any) => {
      if (!metaUpdate) return;
      set((s) => ({
        activeThread: s.activeThread.map((m) =>
          m.id === activeAsstId
            ? {
                ...m,
                metadata: mergeAssistantMetadata(m.metadata, metaUpdate),
              }
            : m
        ),
      }));
    };

    const appendToolCalls = (metaUpdate: any) => {
      if (!metaUpdate?.tool_calls?.length) return;
      set((s) => ({
        activeThread: s.activeThread.map((m) => {
          if (m.id !== activeAsstId) return m;
          const prev = Array.isArray(m.metadata?.tool_calls) ? m.metadata.tool_calls : [];
          return {
            ...m,
            metadata: {
              ...(m.metadata || {}),
              pending_approval: null,
              tool_calls: [...prev, ...metaUpdate.tool_calls],
            },
          };
        }),
      }));
    };

    const setPendingApproval = (approvalMeta: any) => {
      if (!approvalMeta) return;
      set((s) => ({
        activeThread: s.activeThread.map((m) =>
          m.id === activeAsstId
            ? { ...m, metadata: { ...(m.metadata || {}), pending_approval: approvalMeta } }
            : m
        ),
      }));
    };

    try {
      const resolvedAttachments: Array<{
        uri?: string;
        filename: string;
        mime_type: string;
        file_size: number;
        storage_path?: string;
      }> = [];

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

        resolvedAttachments.push({
          uri: resolvedUri,
          filename: a.filename,
          mime_type: resolvedMime,
          file_size: resolvedSize,
          storage_path: a.storage_path,
        });
      }

      await streamChatGenerate(
        {
          conversation_id: convId,
          parent_message_id: parentId,
          prompt,
          model: effectiveModel,
          feature_mode: featureMode,
          gem_id: selectedGemId,
          attachments: resolvedAttachments,
          incognito: isIncognito,
          ...getWorkspaceRequestPayload(),
        },
        {
          signal: abortController.signal,
          onStart: (_serverConvId, serverUserMsgId) => {
            if (serverUserMsgId) {
              set((s) => ({
                activeThread: s.activeThread.map((m) =>
                  m.id === tempUserId ? { ...m, id: serverUserMsgId } : m
                ),
              }));
            }
          },
          onThought: (delta) => {
            pendingThoughtDelta += delta;
            scheduleFlush();
          },
          onText: (delta) => {
            pendingTextDelta += delta;
            scheduleFlush();
          },
          onCitation: updateAsstMeta,
          onMedia: updateAsstMeta,
          onToolCall: appendToolCalls,
          onToolApprovalRequired: setPendingApproval,
          onSaved: (serverAsstMsgId) => {
            flushDeltas();
            if (serverAsstMsgId) {
              activeAsstId = serverAsstMsgId;
              set((s) => ({
                activeLeafMessageId: serverAsstMsgId,
                activeThread: s.activeThread.map((m) =>
                  m.id === tempAsstId ? { ...m, id: serverAsstMsgId } : m
                ),
              }));
            }
          },
          onError: (errMsg) => {
            flushDeltas();
            set((s) => ({
              error: errMsg,
              activeThread: s.activeThread.map((m) =>
                m.id === activeAsstId ? { ...m, is_error: true, content: `Error: ${errMsg}` } : m
              ),
            }));
          },
          onDone: (metadata) => {
            flushDeltas();
            if (metadata) updateAsstMeta(metadata);
            set({ isGenerating: false, activeAbortController: null });
            if (!isIncognito) {
              get().loadConversations();
            }
          },
        }
      );
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        set({ error: err.message });
      }
    } finally {
      flushDeltas();
      set({ isGenerating: false, activeAbortController: null });
    }
  },

  abortGeneration: () => {
    const { activeAbortController } = get();
    if (activeAbortController) {
      activeAbortController.abort();
      set({ isGenerating: false, activeAbortController: null });
    }
  },

  addAttachment: (file: File) => {
    const clientId = `att_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    const previewUrl = file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined;
    const item: ChatAttachment = {
      id: clientId,
      filename: file.name,
      mime_type: file.type || 'application/octet-stream',
      size_bytes: file.size,
      previewUrl,
      uploading: true,
      file,
    };
    set((s) => ({ pendingAttachments: [...s.pendingAttachments, item] }));

    const uploadPromise = api.uploadAttachment(file).then(
      (res) => {
        pendingUploadPromises.delete(clientId);
        set((s) => ({
          pendingAttachments: s.pendingAttachments.map((a) =>
            a.id === clientId
              ? {
                  ...a,
                  uri: res.uri,
                  mime_type: res.mime_type || a.mime_type,
                  size_bytes: res.file_size ?? a.size_bytes,
                  uploading: false,
                  error: undefined,
                }
              : a
          ),
        }));
        return res;
      },
      (err: any) => {
        pendingUploadPromises.delete(clientId);
        set((s) => ({
          pendingAttachments: s.pendingAttachments.map((a) =>
            a.id === clientId
              ? { ...a, uploading: false, error: err?.message || 'Upload failed' }
              : a
          ),
        }));
        throw err;
      }
    );
    pendingUploadPromises.set(clientId, uploadPromise);
  },

  removeAttachment: (index: number) => {
    set((s) => {
      const att = s.pendingAttachments[index];
      if (att?.id) {
        pendingUploadPromises.delete(att.id);
      }
      if (att && att.previewUrl) {
        URL.revokeObjectURL(att.previewUrl);
      }
      return {
        pendingAttachments: s.pendingAttachments.filter((_, i) => i !== index),
      };
    });
  },

  clearAttachments: () => {
    const { pendingAttachments } = get();
    for (const a of pendingAttachments) {
      if (a.id) pendingUploadPromises.delete(a.id);
      if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
    }
    set({ pendingAttachments: [] });
  },

  updateMessageContent: (messageId: number, content: string, extraMeta?: any) => {
    set((s) => {
      const patchMsg = (m: Message): Message =>
        m.id === messageId
          ? {
              ...m,
              content,
              metadata: extraMeta ? { ...(m.metadata || {}), ...extraMeta } : m.metadata,
            }
          : m;
      const updatedMap = { ...s.allMessagesMap };
      if (updatedMap[messageId]) {
        updatedMap[messageId] = patchMsg(updatedMap[messageId]);
      }
      return {
        activeThread: s.activeThread.map(patchMsg),
        allMessagesMap: updatedMap,
      };
    });
  },

  clearPendingApproval: (messageId: number) => {
    set((s) => {
      const patchMsg = (m: Message): Message =>
        m.id === messageId && m.metadata?.pending_approval
          ? { ...m, metadata: { ...m.metadata, pending_approval: null } }
          : m;
      const updatedMap = { ...s.allMessagesMap };
      if (updatedMap[messageId]) {
        updatedMap[messageId] = patchMsg(updatedMap[messageId]);
      }
      return {
        activeThread: s.activeThread.map(patchMsg),
        allMessagesMap: updatedMap,
      };
    });
  },

  recallPromptUp: (currentText: string) => {
    const { promptHistory, promptHistoryIndex } = get();
    if (promptHistory.length === 0) return currentText;

    if (promptHistoryIndex === -1) {
      set({ draftPrompt: currentText });
      const newIdx = promptHistory.length - 1;
      set({ promptHistoryIndex: newIdx });
      return promptHistory[newIdx];
    } else if (promptHistoryIndex > 0) {
      const newIdx = promptHistoryIndex - 1;
      set({ promptHistoryIndex: newIdx });
      return promptHistory[newIdx];
    }
    return promptHistory[0];
  },

  recallPromptDown: () => {
    const { promptHistory, promptHistoryIndex, draftPrompt } = get();
    if (promptHistoryIndex === -1) return '';

    if (promptHistoryIndex < promptHistory.length - 1) {
      const newIdx = promptHistoryIndex + 1;
      set({ promptHistoryIndex: newIdx });
      return promptHistory[newIdx];
    } else {
      set({ promptHistoryIndex: -1 });
      return draftPrompt;
    }
  },

  setDraftPrompt: (draft: string) => set({ draftPrompt: draft }),
  injectContext: (context: string) => set({ injectedContext: context }),

  setIsPinnedToBottom: (pinned: boolean) => set({ isPinnedToBottom: pinned }),

  setFixtureMessages: (messages: Message[]) => {
    const msgMap: Record<number, Message> = {};
    for (const m of messages) msgMap[m.id] = m;
    set({
      activeThread: messages,
      allMessagesMap: msgMap,
      activeLeafMessageId: messages.length > 0 ? messages[messages.length - 1].id : null,
      isPinnedToBottom: true,
    });
  },
}));
