import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';
import { useChatStore } from './store/chatStore';
import { useSessionStore } from './store/sessionStore';
import { Message } from './types';

// Expose benchmark harness functions on window
if (typeof window !== 'undefined') {
  (window as any).__chatStore = useChatStore;
  (window as any).__sessionStore = useSessionStore;

  const loadMessages = (messages: Message[]) => {
    useChatStore.getState().setFixtureMessages(messages);
  };

  (window as any).__loadBenchmarkMessages = loadMessages;
  (window as any).__loadFixture = loadMessages;

  (window as any).__simulateHighSpeedStream = (rateHz = 60) => {
    const intervalMs = Math.max(5, Math.floor(1000 / rateHz));
    return (window as any).__startMockStreaming(60, intervalMs);
  };

  (window as any).__startMockStreaming = (count = 60, intervalMs = 20): Promise<void> => {
    return new Promise((resolve) => {
      const store = useChatStore.getState();
      const assistantId = 999999;
      const currentThread = [...store.activeThread];

      // Add streaming placeholder
      currentThread.push({
        id: assistantId,
        conversation_id: store.activeConversationId || 'conv_bench',
        parent_id: currentThread.length > 0 ? currentThread[currentThread.length - 1].id : null,
        role: 'assistant',
        content: '',
        thoughts: 'Streaming thought trace...',
        model: 'gemini-2.5-pro',
        created_at: Date.now(),
      });

      useChatStore.setState({
        activeThread: currentThread,
        isGenerating: true,
        streamingText: '',
      });

      let currentStep = 0;
      const tokens = [
        ' The', ' quantum', ' algorithm', ' achieves', ' quadratic', ' speedup',
        ' over', ' classical', ' brute', ' force', ' methods.', ' Specifically,',
        ' Grover', ' search', ' executes', ' in', ' O(sqrt(N))', ' steps.',
        ' Given', ' an', ' unstructured', ' database', ' of', ' size', ' N,',
        ' amplitude', ' amplification', ' rotates', ' the', ' state', ' vector.',
        ' \n\n```python\ndef grover_step(state):\n    state = oracle(state)\n    return diffusion(state)\n```\n\n',
        ' This', ' guarantees', ' convergence', ' with', ' high', ' probability.',
      ];

      const timer = setInterval(() => {
        currentStep++;
        const token = tokens[(currentStep - 1) % tokens.length];

        useChatStore.setState((s) => {
          const newText = s.streamingText + token;
          return {
            streamingText: newText,
            activeThread: s.activeThread.map((m) =>
              m.id === assistantId ? { ...m, content: newText } : m
            ),
          };
        });

        if (currentStep >= count) {
          clearInterval(timer);
          useChatStore.setState({ isGenerating: false });
          resolve();
        }
      }, intervalMs);
    });
  };
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
