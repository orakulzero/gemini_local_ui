import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useNotebookLMStore } from '../store/notebooklmStore';
import { useChatStore } from '../store/chatStore';
import * as api from '../services/api';

vi.mock('../services/api');

describe('useNotebookLMStore', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useNotebookLMStore.setState({
      isOpen: false,
      notebooks: [],
      activeNotebookId: null,
      activeNotebook: null,
      selectedSourceIds: [],
      selectedNoteIds: [],
      includeNotes: false,
      searchQuery: '',
      isLoading: false,
      error: null,
    });
    useChatStore.setState({
      injectedContext: '',
      draftPrompt: '',
    });
  });

  it('toggles drawer state correctly', () => {
    const store = useNotebookLMStore.getState();
    expect(store.isOpen).toBe(false);

    store.setIsOpen(true);
    expect(useNotebookLMStore.getState().isOpen).toBe(true);

    useNotebookLMStore.getState().toggleDrawer();
    expect(useNotebookLMStore.getState().isOpen).toBe(false);
  });

  it('selects and deselects sources', () => {
    const store = useNotebookLMStore.getState();
    useNotebookLMStore.setState({
      activeNotebook: {
        id: 'nb_1',
        title: 'Quantum Notes',
        created_at: 1000,
        updated_at: 2000,
        source_count: 2,
        sources: [
          {
            id: 'src_1',
            title: 'QFT Paper',
            source_type: 3,
            url: null,
            summary: 'Summary of QFT',
            token_count: 500,
            created_at: 1000,
          },
          {
            id: 'src_2',
            title: 'Shor Notes',
            source_type: 1,
            url: null,
            summary: 'Summary of Shor',
            token_count: 300,
            created_at: 1100,
          },
        ],
        notes: [],
      },
    });

    store.toggleSelectSource('src_1');
    expect(useNotebookLMStore.getState().selectedSourceIds).toEqual(['src_1']);

    store.toggleSelectSource('src_2');
    expect(useNotebookLMStore.getState().selectedSourceIds).toEqual(['src_1', 'src_2']);

    store.toggleSelectSource('src_1');
    expect(useNotebookLMStore.getState().selectedSourceIds).toEqual(['src_2']);

    store.selectAllSources();
    expect(useNotebookLMStore.getState().selectedSourceIds).toEqual(['src_1', 'src_2']);

    store.clearSelection();
    expect(useNotebookLMStore.getState().selectedSourceIds).toEqual([]);
  });

  it('attachSourceToChat delegates to useChatStore.injectContext with XML reference', () => {
    const source = {
      id: 'src_qft',
      title: 'Quantum Fourier Transform',
      source_type: 3,
      url: 'https://arxiv.org/abs/1234.5678',
      summary: 'Detailed explanation of quantum phase estimation and QFT.',
      token_count: 1200,
      created_at: 1729000000,
    };

    useNotebookLMStore.getState().attachSourceToChat(source);

    const chatState = useChatStore.getState();
    expect(chatState.injectedContext).toContain('<notebooklm_reference title="Quantum Fourier Transform">');
    expect(chatState.injectedContext).toContain('Detailed explanation of quantum phase estimation and QFT.');
    expect(chatState.injectedContext).toContain('</notebooklm_reference>');
    // Ensure draftPrompt was NOT modified
    expect(chatState.draftPrompt).toBe('');
  });

  it('attachSelectedToChat invokes api and injects formatted XML context into chatStore', async () => {
    useNotebookLMStore.setState({
      activeNotebook: {
        id: 'nb_ml',
        title: 'Machine Learning',
        created_at: 1000,
        updated_at: 2000,
        source_count: 1,
        sources: [
          {
            id: 'src_trans',
            title: 'Attention Is All You Need',
            source_type: 3,
            url: null,
            summary: 'Transformers paper summary',
            token_count: 800,
            created_at: 1000,
          },
        ],
        notes: [],
      },
      selectedSourceIds: ['src_trans'],
    });

    const formattedContext = '<notebooklm_reference title="Machine Learning">\n  <source title="Attention Is All You Need">\n    Transformers paper summary\n  </source>\n</notebooklm_reference>';
    vi.mocked(api.attachNotebookContext).mockResolvedValueOnce({
      formatted_context: formattedContext,
      estimated_tokens: 250,
      notebook_id: 'nb_ml',
      notebook_title: 'Machine Learning',
      source_count: 1,
      note_count: 0,
    });

    const result = await useNotebookLMStore.getState().attachSelectedToChat();

    expect(result).toBe(formattedContext);
    expect(useChatStore.getState().injectedContext).toBe(formattedContext);
    expect(useChatStore.getState().draftPrompt).toBe('');
  });

  it('exportChatToNotebook sends active conversation turn bundle to API', async () => {
    useChatStore.setState({ activeConversationId: 'conv_12345' });
    vi.mocked(api.exportToNotebook).mockResolvedValueOnce({
      success: true,
      notebook_id: 'nb_exported',
      source_id: 'src_exported_turn',
    } as any);
    vi.mocked(api.fetchNotebooks).mockResolvedValueOnce([
      { id: 'nb_exported', title: 'Exported Research', created_at: 1, updated_at: 1, source_count: 1 } as any,
    ]);

    const success = await useNotebookLMStore.getState().exportChatToNotebook(undefined, 'Exported Research');

    expect(success).toBe(true);
    expect(api.exportToNotebook).toHaveBeenCalledWith({
      notebook_id: undefined,
      new_notebook_title: 'Exported Research',
      conversation_id: 'conv_12345',
    });
  });
});
