import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useChatStore } from '../store/chatStore';

describe('useChatStore - State Decoupling & History Recall', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({
      conversations: [],
      activeConversationId: null,
      activeLeafMessageId: null,
      activeThread: [],
      allMessagesMap: {},
      isGenerating: false,
      streamingThought: '',
      streamingText: '',
      streamingAssistantId: null,
      promptHistory: ['First prompt', 'Second prompt', 'Third prompt'],
      promptHistoryIndex: -1,
      draftPrompt: '',
      injectedContext: '',
      pendingAttachments: [],
      activeAbortController: null,
      isPinnedToBottom: true,
      isIncognito: false,
      error: null,
    });
  });

  it('injectContext updates injectedContext without modifying draftPrompt or promptHistory', () => {
    const store = useChatStore.getState();
    expect(store.injectedContext).toBe('');
    expect(store.draftPrompt).toBe('');

    store.injectContext('<notebooklm_reference title="Ref">\nNotes\n</notebooklm_reference>');

    const updated = useChatStore.getState();
    expect(updated.injectedContext).toBe('<notebooklm_reference title="Ref">\nNotes\n</notebooklm_reference>');
    expect(updated.draftPrompt).toBe('');
    expect(updated.promptHistory.length).toBe(3);
  });

  it('recallPromptUp saves current input into draftPrompt and returns last prompt', () => {
    const store = useChatStore.getState();
    const currentTyped = 'My unfinished draft';

    const recalled1 = store.recallPromptUp(currentTyped);

    const stateAfterUp1 = useChatStore.getState();
    expect(stateAfterUp1.draftPrompt).toBe('My unfinished draft');
    expect(recalled1).toBe('Third prompt');
    expect(stateAfterUp1.promptHistoryIndex).toBe(2);

    const recalled2 = store.recallPromptUp(recalled1);
    const stateAfterUp2 = useChatStore.getState();
    expect(recalled2).toBe('Second prompt');
    expect(stateAfterUp2.promptHistoryIndex).toBe(1);
    // draftPrompt is preserved
    expect(stateAfterUp2.draftPrompt).toBe('My unfinished draft');
  });

  it('recallPromptDown returns to saved draftPrompt when descending past newest prompt', () => {
    const store = useChatStore.getState();
    const currentTyped = 'Draft to recover';

    store.recallPromptUp(currentTyped); // index 2 -> 'Third prompt'
    store.recallPromptUp('Third prompt'); // index 1 -> 'Second prompt'

    const down1 = store.recallPromptDown(); // returns to index 2
    expect(down1).toBe('Third prompt');
    expect(useChatStore.getState().promptHistoryIndex).toBe(2);

    const down2 = store.recallPromptDown(); // returns past history -> draftPrompt
    expect(down2).toBe('Draft to recover');
    expect(useChatStore.getState().promptHistoryIndex).toBe(-1);
  });

  it('injectedContext does not overwrite draftPrompt during active history navigation', () => {
    const store = useChatStore.getState();
    store.recallPromptUp('Navigating draft');

    expect(useChatStore.getState().draftPrompt).toBe('Navigating draft');

    // External context injection occurs (e.g. user clicks attach source from NotebookLM)
    store.injectContext('<notebooklm_reference>Attached</notebooklm_reference>');

    expect(useChatStore.getState().injectedContext).toBe('<notebooklm_reference>Attached</notebooklm_reference>');
    expect(useChatStore.getState().draftPrompt).toBe('Navigating draft');

    // Navigating back down should still safely restore the original draft
    const downResult = store.recallPromptDown();
    expect(downResult).toBe('Navigating draft');
    expect(useChatStore.getState().promptHistoryIndex).toBe(-1);
  });

  it('setFixtureMessages populates activeThread and leaf id correctly', () => {
    const fixtures = [
      { id: 101, conversation_id: 'c1', parent_id: null, role: 'user' as const, content: 'Hello' },
      { id: 102, conversation_id: 'c1', parent_id: 101, role: 'assistant' as const, content: 'Hi there' },
    ];

    useChatStore.getState().setFixtureMessages(fixtures as any);

    const state = useChatStore.getState();
    expect(state.activeThread.length).toBe(2);
    expect(state.activeLeafMessageId).toBe(102);
    expect(state.allMessagesMap[101].content).toBe('Hello');
    expect(state.allMessagesMap[102].content).toBe('Hi there');
  });

  it('setIncognito updates incognito flag', () => {
    expect(useChatStore.getState().isIncognito).toBe(false);
    useChatStore.getState().setIncognito(true);
    expect(useChatStore.getState().isIncognito).toBe(true);
  });
});
