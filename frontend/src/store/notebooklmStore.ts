import { create } from 'zustand';
import { NotebookInfo, NotebookDetail, NotebookSource, NotebookNote } from '../types';
import * as api from '../services/api';
import { useChatStore } from './chatStore';

export interface NotebookLMState {
  isOpen: boolean;
  notebooks: NotebookInfo[];
  activeNotebookId: string | null;
  activeNotebook: NotebookDetail | null;
  isLoading: boolean;
  error: string | null;
  searchQuery: string;
  selectedSourceIds: string[];
  selectedNoteIds: string[];
  includeNotes: boolean;

  // Actions
  setIsOpen: (open: boolean) => void;
  toggleDrawer: () => void;
  setSearchQuery: (query: string) => void;
  fetchNotebooks: () => Promise<void>;
  selectNotebook: (id: string | null) => Promise<void>;
  createNotebook: (title: string) => Promise<NotebookInfo | null>;
  deleteNotebook: (id: string) => Promise<boolean>;
  renameNotebook: (id: string, newTitle: string) => Promise<boolean>;
  toggleSelectSource: (sourceId: string) => void;
  toggleSelectNote: (noteId: string) => void;
  selectAllSources: () => void;
  clearSelection: () => void;
  setIncludeNotes: (include: boolean) => void;
  attachSourceToChat: (source: NotebookSource) => void;
  attachSelectedToChat: () => Promise<string | null>;
  exportChatToNotebook: (
    notebookId?: string,
    newTitle?: string,
    conversationId?: string
  ) => Promise<boolean>;
}

export const useNotebookLMStore = create<NotebookLMState>((set, get) => ({
  isOpen: false,
  notebooks: [],
  activeNotebookId: null,
  activeNotebook: null,
  isLoading: false,
  error: null,
  searchQuery: '',
  selectedSourceIds: [],
  selectedNoteIds: [],
  includeNotes: true,

  setIsOpen: (open: boolean) => {
    set({ isOpen: open });
    if (open && get().notebooks.length === 0) {
      get().fetchNotebooks();
    }
  },

  toggleDrawer: () => {
    const next = !get().isOpen;
    get().setIsOpen(next);
  },

  setSearchQuery: (query: string) => set({ searchQuery: query }),

  fetchNotebooks: async () => {
    set({ isLoading: true, error: null });
    try {
      const list = await api.fetchNotebooks();
      set({ notebooks: list, isLoading: false });
    } catch (err: any) {
      set({ error: err.message || 'Failed to fetch notebooks', isLoading: false });
    }
  },

  selectNotebook: async (id: string | null) => {
    if (!id) {
      set({ activeNotebookId: null, activeNotebook: null, selectedSourceIds: [], selectedNoteIds: [] });
      return;
    }
    set({ activeNotebookId: id, isLoading: true, error: null, selectedSourceIds: [], selectedNoteIds: [] });
    try {
      const detail = await api.fetchNotebookDetail(id);
      set({ activeNotebook: detail, isLoading: false });
    } catch (err: any) {
      set({ error: err.message || 'Failed to load notebook detail', isLoading: false });
    }
  },

  createNotebook: async (title: string) => {
    const trimmed = title.trim();
    if (!trimmed) return null;
    set({ isLoading: true, error: null });
    try {
      const nb = await api.createNotebook(trimmed);
      set((s) => ({ notebooks: [nb, ...s.notebooks], isLoading: false }));
      return nb;
    } catch (err: any) {
      set({ error: err.message || 'Failed to create notebook', isLoading: false });
      return null;
    }
  },

  deleteNotebook: async (id: string) => {
    set({ isLoading: true, error: null });
    try {
      await api.deleteNotebook(id);
      set((s) => ({
        notebooks: s.notebooks.filter((n) => n.id !== id),
        activeNotebookId: s.activeNotebookId === id ? null : s.activeNotebookId,
        activeNotebook: s.activeNotebook?.id === id ? null : s.activeNotebook,
        isLoading: false,
      }));
      return true;
    } catch (err: any) {
      set({ error: err.message || 'Failed to delete notebook', isLoading: false });
      return false;
    }
  },

  renameNotebook: async (id: string, newTitle: string) => {
    const trimmed = newTitle.trim();
    if (!trimmed) return false;
    set({ isLoading: true, error: null });
    try {
      await api.renameNotebook(id, trimmed);
      set((s) => ({
        notebooks: s.notebooks.map((n) => (n.id === id ? { ...n, title: trimmed } : n)),
        activeNotebook:
          s.activeNotebook && s.activeNotebook.id === id
            ? { ...s.activeNotebook, title: trimmed }
            : s.activeNotebook,
        isLoading: false,
      }));
      return true;
    } catch (err: any) {
      set({ error: err.message || 'Failed to rename notebook', isLoading: false });
      return false;
    }
  },

  toggleSelectSource: (sourceId: string) => {
    set((s) => {
      const exists = s.selectedSourceIds.includes(sourceId);
      return {
        selectedSourceIds: exists
          ? s.selectedSourceIds.filter((id) => id !== sourceId)
          : [...s.selectedSourceIds, sourceId],
      };
    });
  },

  toggleSelectNote: (noteId: string) => {
    set((s) => {
      const exists = s.selectedNoteIds.includes(noteId);
      return {
        selectedNoteIds: exists
          ? s.selectedNoteIds.filter((id) => id !== noteId)
          : [...s.selectedNoteIds, noteId],
      };
    });
  },

  selectAllSources: () => {
    const active = get().activeNotebook;
    if (!active) return;
    const allIds = active.sources.map((s) => s.id);
    set({ selectedSourceIds: allIds });
  },

  clearSelection: () => {
    set({ selectedSourceIds: [], selectedNoteIds: [] });
  },

  setIncludeNotes: (include: boolean) => set({ includeNotes: include }),

  attachSourceToChat: (source: NotebookSource) => {
    const summary = source.summary || source.title;
    const block = `<notebooklm_reference title="${source.title}">\n${summary}\n</notebooklm_reference>`;
    useChatStore.getState().injectContext(block);
  },

  attachSelectedToChat: async () => {
    const { activeNotebook, selectedSourceIds, selectedNoteIds, includeNotes } = get();
    if (!activeNotebook) return null;

    set({ isLoading: true, error: null });
    try {
      const res = await api.attachNotebookContext({
        notebook_id: activeNotebook.id,
        source_ids: selectedSourceIds.length > 0 ? selectedSourceIds : undefined,
        note_ids: selectedNoteIds.length > 0 ? selectedNoteIds : undefined,
        include_notes: includeNotes,
      });

      if (res && res.formatted_context) {
        useChatStore.getState().injectContext(res.formatted_context);
        set({ isLoading: false });
        return res.formatted_context;
      }
      set({ isLoading: false });
      return null;
    } catch (err: any) {
      // Fallback: manually format if network call fails
      const sourcesToAttach =
        selectedSourceIds.length > 0
          ? activeNotebook.sources.filter((s) => selectedSourceIds.includes(s.id))
          : activeNotebook.sources;

      const lines = [`<notebooklm_reference title="${activeNotebook.title}">`];
      for (const s of sourcesToAttach) {
        lines.push(`  <source title="${s.title}">\n    ${s.summary || s.title}\n  </source>`);
      }
      lines.push('</notebooklm_reference>');
      const fallbackBlock = lines.join('\n');
      useChatStore.getState().injectContext(fallbackBlock);
      set({ isLoading: false });
      return fallbackBlock;
    }
  },

  exportChatToNotebook: async (notebookId?: string, newTitle?: string, conversationId?: string) => {
    const convId = conversationId || useChatStore.getState().activeConversationId;
    if (!convId) {
      set({ error: 'No active conversation to export' });
      return false;
    }

    set({ isLoading: true, error: null });
    try {
      const targetNbId = notebookId || get().activeNotebookId || undefined;
      await api.exportToNotebook({
        notebook_id: targetNbId,
        new_notebook_title: newTitle || undefined,
        conversation_id: convId,
      });
      await get().fetchNotebooks();
      if (targetNbId) {
        await get().selectNotebook(targetNbId);
      }
      set({ isLoading: false });
      return true;
    } catch (err: any) {
      set({ error: err.message || 'Failed to export conversation', isLoading: false });
      return false;
    }
  },
}));
