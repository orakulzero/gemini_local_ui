import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useChatStore } from '../store/chatStore';

describe('Adversarial Stress Suite: Frontend State Decoupling & Boundary Navigation', () => {
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
      promptHistory: ['Historical prompt 1', 'Historical prompt 2', 'Historical prompt 3'],
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

  // ---------------------------------------------------------------------------
  // 1. Concurrent Context Injection While Navigating History (Up / Down)
  // ---------------------------------------------------------------------------
  it('preserves draftPrompt when context is injected at every stage of up/down history recall', () => {
    const store = useChatStore.getState();
    const userDraft = 'My in-progress draft typing...';

    // Step 1: User types and presses ArrowUp (recalled 3)
    const recalled3 = store.recallPromptUp(userDraft);
    expect(recalled3).toBe('Historical prompt 3');
    expect(useChatStore.getState().draftPrompt).toBe(userDraft);
    expect(useChatStore.getState().promptHistoryIndex).toBe(2);

    // Step 2: Context is injected while viewing historical prompt 3
    store.injectContext('<notebooklm_reference id="1">Context A</notebooklm_reference>');
    expect(useChatStore.getState().injectedContext).toBe('<notebooklm_reference id="1">Context A</notebooklm_reference>');
    // CRITICAL: user draft in store MUST remain unchanged
    expect(useChatStore.getState().draftPrompt).toBe(userDraft);

    // Step 3: User presses ArrowUp again (recalled 2)
    const recalled2 = store.recallPromptUp(recalled3);
    expect(recalled2).toBe('Historical prompt 2');
    expect(useChatStore.getState().promptHistoryIndex).toBe(1);
    expect(useChatStore.getState().draftPrompt).toBe(userDraft);

    // Step 4: Another context is injected while at historical prompt 2
    store.injectContext('<notebooklm_reference id="2">Context B</notebooklm_reference>');
    expect(useChatStore.getState().draftPrompt).toBe(userDraft);

    // Step 5: User presses ArrowDown to 3, then down past history to return to draft
    const downTo3 = store.recallPromptDown();
    expect(downTo3).toBe('Historical prompt 3');
    expect(useChatStore.getState().promptHistoryIndex).toBe(2);

    const downToDraft = store.recallPromptDown();
    // Successfully returns original uncorrupted draft!
    expect(downToDraft).toBe(userDraft);
    expect(useChatStore.getState().promptHistoryIndex).toBe(-1);

    // Step 6: Verify promptHistory list itself was never mutated
    const currentHistory = useChatStore.getState().promptHistory;
    expect(currentHistory).toEqual(['Historical prompt 1', 'Historical prompt 2', 'Historical prompt 3']);
  });

  it('handles navigation boundaries when promptHistory is empty', () => {
    useChatStore.setState({ promptHistory: [], promptHistoryIndex: -1, draftPrompt: '' });
    const store = useChatStore.getState();

    // ArrowUp with empty history returns current text and does not change index
    const resUp = store.recallPromptUp('current');
    expect(resUp).toBe('current');
    expect(useChatStore.getState().promptHistoryIndex).toBe(-1);

    // ArrowDown returns empty string
    const resDown = store.recallPromptDown();
    expect(resDown).toBe('');
    expect(useChatStore.getState().promptHistoryIndex).toBe(-1);

    // Inject context with empty history
    store.injectContext('Some context');
    expect(useChatStore.getState().injectedContext).toBe('Some context');
    expect(useChatStore.getState().draftPrompt).toBe('');
  });

  // ---------------------------------------------------------------------------
  // 2. Rapid Sequential injectContext Calls
  // ---------------------------------------------------------------------------
  it('handles burst rapid sequential injectContext calls without throwing', () => {
    const store = useChatStore.getState();

    // Rapidly fire 50 context injections in a tight synchronous loop
    for (let i = 0; i < 50; i++) {
      store.injectContext(`<ctx_payload index="${i}">Data ${i}</ctx_payload>`);
    }

    const state = useChatStore.getState();
    expect(state.injectedContext).toBe('<ctx_payload index="49">Data 49</ctx_payload>');
    expect(state.draftPrompt).toBe('');
  });

  // ---------------------------------------------------------------------------
  // 3. Empty Context Injection
  // ---------------------------------------------------------------------------
  it('safely handles empty string and whitespace-only context injection', () => {
    const store = useChatStore.getState();
    store.setDraftPrompt('Existing draft');

    // Empty string
    store.injectContext('');
    expect(useChatStore.getState().injectedContext).toBe('');
    expect(useChatStore.getState().draftPrompt).toBe('Existing draft');

    // Whitespace string
    store.injectContext('   \n\t  ');
    expect(useChatStore.getState().injectedContext).toBe('   \n\t  ');
    expect(useChatStore.getState().draftPrompt).toBe('Existing draft');
  });

  // ---------------------------------------------------------------------------
  // 4. Special Unicode Characters and Oversized Context Payloads
  // ---------------------------------------------------------------------------
  it('handles extreme unicode payloads (nulls, emojis, RTL overrides, CJK)', () => {
    const store = useChatStore.getState();
    const extremePayload = (
      'Null bytes: \u0000\u0000 | ' +
      'Astral emoji: 🦹‍♂️ 🚀 ⚛️ | ' +
      'RTL Override: \u202Ereversed\u202C | ' +
      'Zero-width: a\u200Bb\u200Cc\u200Dd | ' +
      'CJK & Math: 物理学 ∇×E = -∂B/∂t ∑_{i=0}^n'
    );

    store.injectContext(extremePayload);
    const updated = useChatStore.getState().injectedContext;
    expect(updated).toBe(extremePayload);
    expect(updated).toContain('🦹‍♂️');
    expect(updated).toContain('物理学');
    expect(updated).toContain('\u0000');
  });

  it('handles massive 100,000 character multiline XML context without performance degradation', () => {
    const store = useChatStore.getState();
    const largeChunk = '<source_item title="Chunk">\n' + 'Sample content data line.\n'.repeat(4000) + '</source_item>';
    expect(largeChunk.length).toBeGreaterThan(100000);

    const start = performance.now();
    store.injectContext(largeChunk);
    const elapsed = performance.now() - start;

    expect(useChatStore.getState().injectedContext.length).toBe(largeChunk.length);
    // Sub-millisecond or fast execution (<50ms)
    expect(elapsed).toBeLessThan(50);
  });
});
