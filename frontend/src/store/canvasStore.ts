import { create } from 'zustand';

export interface Artifact {
  id: string;
  title: string;
  language: string;
  content: string;
  originalContent: string;
}

export interface CanvasState {
  isOpen: boolean;
  activeArtifact: Artifact | null;
  activeTab: 'code' | 'preview';

  openCanvas: (artifact: Omit<Artifact, 'id'>) => void;
  closeCanvas: () => void;
  updateContent: (newContent: string) => void;
  setActiveTab: (tab: 'code' | 'preview') => void;
  revertContent: () => void;
}

export const useCanvasStore = create<CanvasState>((set) => ({
  isOpen: false,
  activeArtifact: null,
  activeTab: 'code',

  openCanvas: (artifact) =>
    set({
      isOpen: true,
      activeArtifact: {
        id: `art_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
        ...artifact,
      },
      // Default to preview tab for HTML, SVG, Markdown, otherwise code
      activeTab: ['html', 'svg', 'xml', 'markdown', 'md'].includes(
        (artifact.language || '').toLowerCase()
      )
        ? 'preview'
        : 'code',
    }),

  closeCanvas: () =>
    set({
      isOpen: false,
      activeArtifact: null,
    }),

  updateContent: (newContent: string) =>
    set((state) => {
      if (!state.activeArtifact) return state;
      return {
        activeArtifact: {
          ...state.activeArtifact,
          content: newContent,
        },
      };
    }),

  setActiveTab: (tab) => set({ activeTab: tab }),

  revertContent: () =>
    set((state) => {
      if (!state.activeArtifact) return state;
      return {
        activeArtifact: {
          ...state.activeArtifact,
          content: state.activeArtifact.originalContent,
        },
      };
    }),
}));
