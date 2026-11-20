import type { StateCreator } from 'zustand';
import type { ChatAttachment } from '../../types';
import * as api from '../../services/api';
import { ChatState, UiSlice } from './types';

export const pendingUploadPromises = new Map<
  string,
  Promise<{ uri: string; mime_type: string; file_size: number }>
>();

export const createUiSlice: StateCreator<ChatState, [], [], UiSlice> = (set, get) => ({
  isIncognito: false,
  isPinnedToBottom: true,
  promptHistory: [],
  promptHistoryIndex: -1,
  draftPrompt: '',
  injectedContext: '',
  pendingAttachments: [],
  error: null,

  setIncognito: (incognito) => {
    set({ isIncognito: incognito });
    if (incognito) {
      set({
        activeConversationId: `incognito_${Date.now()}`,
        activeLeafMessageId: null,
        activeThread: [],
        allMessagesMap: {},
        nodesById: {},
        childrenByParentId: {},
        selectedChildByParent: {},
      });
    }
  },

  setIsPinnedToBottom: (pinned) => set({ isPinnedToBottom: pinned }),

  recallPromptUp: (currentText) => {
    const { promptHistory, promptHistoryIndex } = get();
    if (promptHistory.length === 0) return currentText;

    if (promptHistoryIndex === -1) {
      const newIdx = promptHistory.length - 1;
      set({ draftPrompt: currentText, promptHistoryIndex: newIdx });
      return promptHistory[newIdx];
    }
    if (promptHistoryIndex > 0) {
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
    }
    set({ promptHistoryIndex: -1 });
    return draftPrompt;
  },

  setDraftPrompt: (draft) => set({ draftPrompt: draft }),
  injectContext: (context) => set({ injectedContext: context }),

  addAttachment: (file) => {
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

  removeAttachment: (index) => {
    set((s) => {
      const att = s.pendingAttachments[index];
      if (att?.id) pendingUploadPromises.delete(att.id);
      if (att?.previewUrl) URL.revokeObjectURL(att.previewUrl);
      return {
        pendingAttachments: s.pendingAttachments.filter((_, i) => i !== index),
      };
    });
  },

  clearAttachments: () => {
    for (const a of get().pendingAttachments) {
      if (a.id) pendingUploadPromises.delete(a.id);
      if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
    }
    set({ pendingAttachments: [] });
  },
});
