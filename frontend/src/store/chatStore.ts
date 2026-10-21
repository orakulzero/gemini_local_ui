import { create } from 'zustand';
import { Message, ConversationSummary, ChatAttachment } from '../types';
import { streamChatGenerate } from '../services/streaming';
import * as api from '../services/api';
import { useSessionStore } from './sessionStore';

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
            m.id === tempAsstId ? { ...m, thoughts: updatedThought, content: updatedText } : m
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
          m.id === tempAsstId
            ? {
                ...m,
                metadata: {
                  ...(m.metadata || {}),
                  ...metaUpdate,
                  citations: metaUpdate.citations || m.metadata?.citations,
                  images: metaUpdate.images || metaUpdate.generated_images || m.metadata?.images,
                  youtube: metaUpdate.youtube || m.metadata?.youtube,
                  grounding: metaUpdate.grounding || m.metadata?.grounding,
                },
              }
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
          onSaved: (serverAsstMsgId) => {
            flushDeltas();
            if (serverAsstMsgId) {
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
                m.id === tempAsstId ? { ...m, is_error: true, content: `Error: ${errMsg}` } : m
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
              m.id === tempAsstId ? { ...m, thoughts: updatedThought, content: updatedText } : m
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
                m.id === tempAsstId ? { ...m, is_error: true, content: `Error: ${errMsg}` } : m
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

    const newThread = [...get().activeThread];
    if (prompt.trim() || pendingAttachments.length > 0) {
      newThread.push({
        id: tempUserId,
        conversation_id: convId,
        parent_id: parentId,
        role: 'user',
        content: prompt,
        model: selectedModel,
        created_at: tempUserId,
        attachments: [...pendingAttachments],
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
            m.id === tempAsstId ? { ...m, thoughts: updatedThought, content: updatedText } : m
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
          m.id === tempAsstId
            ? {
                ...m,
                metadata: {
                  ...(m.metadata || {}),
                  ...metaUpdate,
                  citations: metaUpdate.citations || m.metadata?.citations,
                  images: metaUpdate.images || metaUpdate.generated_images || m.metadata?.images,
                  youtube: metaUpdate.youtube || m.metadata?.youtube,
                  grounding: metaUpdate.grounding || m.metadata?.grounding,
                },
              }
            : m
        ),
      }));
    };

    try {
      await streamChatGenerate(
        {
          conversation_id: convId,
          parent_message_id: parentId,
          prompt,
          model: effectiveModel,
          gem_id: selectedGemId,
          incognito: isIncognito,
          attachments: pendingAttachments.map((a) => ({
            uri: a.uri,
            filename: a.filename,
            mime_type: a.mime_type,
            file_size: a.size_bytes,
            storage_path: a.storage_path,
          })),
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
          onSaved: (serverAsstMsgId) => {
            flushDeltas();
            if (serverAsstMsgId) {
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
                m.id === tempAsstId ? { ...m, is_error: true, content: `Error: ${errMsg}` } : m
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
    const previewUrl = file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined;
    const item: ChatAttachment = {
      filename: file.name,
      mime_type: file.type || 'application/octet-stream',
      size_bytes: file.size,
      previewUrl,
    };
    set((s) => ({ pendingAttachments: [...s.pendingAttachments, item] }));
  },

  removeAttachment: (index: number) => {
    set((s) => {
      const att = s.pendingAttachments[index];
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
      if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
    }
    set({ pendingAttachments: [] });
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
