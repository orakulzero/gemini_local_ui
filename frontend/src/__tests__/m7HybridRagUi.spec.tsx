import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { queryClient } from '../services/queryClient';
import * as api from '../services/api';
import { SearchModal } from '../components/SearchModal';
import { PromptInput } from '../components/PromptInput';
import { useChatStore } from '../store/chatStore';
import { useSessionStore } from '../store/sessionStore';

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>();
  return {
    ...actual,
    fetchRagStatus: vi.fn(),
    triggerRagIndex: vi.fn(),
    searchRagHybrid: vi.fn(),
    searchMessages: vi.fn(),
  };
});

describe('M7: Hybrid RAG SearchModal, Context Injection, Reindex & PromptInput RAG Toggle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
    useChatStore.setState({
      useRag: false,
      injectedContext: '',
      activeConversationId: 'conv_m7_1',
    });
    useSessionStore.setState({
      isSearchOpen: true,
    });

    vi.mocked(api.fetchRagStatus).mockResolvedValue({
      sqlite_vec_loaded: true,
      degraded: false,
      total_chunks: 520,
      embedding_backend: 'subword_ngram_512',
      vector_dim: 512,
      worker_pid: 4321,
      win32_job_bound: true,
    } as any);

    vi.mocked(api.triggerRagIndex).mockResolvedValue({
      indexed_chunks: 12,
      skipped_unchanged: 508,
      total_chunks: 520,
    });

    vi.mocked(api.searchMessages).mockResolvedValue([]);

    vi.mocked(api.searchRagHybrid).mockResolvedValue({
      query: 'wreq transport',
      effective_mode: 'hybrid',
      degraded: false,
      duration_ms: 8.4,
      formatted_xml_context: '<untrusted_rag_context>\n  <rag_passage id="1" source="wreq.py">Wreq pool recycles at 35s</rag_passage>\n</untrusted_rag_context>',
      results: [
        {
          chunk_id: 1,
          source_type: 'workspace_file',
          source_id: 'backend/app/bridge/wreq_transport.py',
          source_title: 'wreq_transport.py',
          content: 'Wreq pool recycles at 35s idle threshold.',
          score: 0.94,
          rrf_score: 0.032,
        },
      ],
    });
  });

  const renderWithProviders = (ui: React.ReactElement) =>
    render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);

  it('renders SearchModal RAG status badge, executes Hybrid RRF search, injects untrusted XML context, and triggers reindex', async () => {
    renderWithProviders(<SearchModal />);

    expect(screen.getByTestId('search-modal')).toBeDefined();
    await waitFor(() => {
      expect(screen.getByTestId('rag-status-badge').textContent).toContain('520');
    });

    // Click Reindex button and verify triggerRagIndex call
    fireEvent.click(screen.getByTestId('rag-reindex-btn'));
    await waitFor(() => {
      expect(api.triggerRagIndex).toHaveBeenCalled();
    });

    // Switch to Hybrid RRF mode and type query
    fireEvent.click(screen.getByTestId('search-mode-hybrid'));
    fireEvent.change(screen.getByTestId('search-input'), { target: { value: 'wreq transport' } });

    await waitFor(() => {
      expect(api.searchRagHybrid).toHaveBeenCalledWith(
        expect.objectContaining({ query: 'wreq transport', mode: 'hybrid' })
      );
      expect(screen.getByTestId('rag-result-item')).toBeDefined();
    });

    expect(screen.getByTestId('rag-result-item').textContent).toContain('wreq_transport.py');

    // Attach result -> injects formatted_xml_context into chatStore and closes modal
    fireEvent.click(screen.getByTestId('rag-attach-btn'));
    await waitFor(() => {
      expect(useChatStore.getState().injectedContext).toContain('<untrusted_rag_context>');
      expect(useSessionStore.getState().isSearchOpen).toBe(false);
    });
  });

  it('toggles useChatStore.useRag when clicking rag-toggle-btn in PromptInput', () => {
    renderWithProviders(<PromptInput />);

    const ragBtn = screen.getByTestId('rag-toggle-btn');
    expect(useChatStore.getState().useRag).toBe(false);
    expect(ragBtn.getAttribute('aria-pressed')).toBe('false');

    fireEvent.click(ragBtn);
    expect(useChatStore.getState().useRag).toBe(true);
    expect(screen.getByTestId('rag-toggle-btn').getAttribute('aria-pressed')).toBe('true');

    fireEvent.click(screen.getByTestId('rag-toggle-btn'));
    expect(useChatStore.getState().useRag).toBe(false);
  });
});
