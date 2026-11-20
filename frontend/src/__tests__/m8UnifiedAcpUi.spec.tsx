import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { queryClient } from '../services/queryClient';
import * as api from '../services/api';
import * as wsProtocol from '../services/wsProtocolV1';
import { SettingsModal } from '../components/SettingsModal';
import { useChatStore } from '../store/chatStore';
import { useSessionStore } from '../store/sessionStore';

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>();
  return {
    ...actual,
    fetchAcpStatus: vi.fn(),
    switchAcpEngine: vi.fn(),
    fetchSavedInfo: vi.fn().mockResolvedValue([]),
  };
});

describe('M8: Unified Rust ACP Engine & WebSocket v1 Transport Feature-Flag UI', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
    useChatStore.setState({
      agentEngine: 'python',
      streamTransport: 'sse',
      isGenerating: false,
      activeConversationId: 'conv_m8_1',
      nodesById: {},
      childrenByParentId: {},
      selectedChildByParent: {},
      activeThread: [],
      allMessagesMap: {},
    });
    useSessionStore.setState({
      isSettingsOpen: true,
      selectedModel: 'gemini-4-carbon',
    });

    vi.mocked(api.fetchAcpStatus).mockResolvedValue({
      available: true,
      binary_path: 'agy-ultra-acp.exe',
      active_engine: 'python',
      ws_url: 'ws://127.0.0.1:8765',
      protocol_version: 'agy.stream.v1',
      win32_job_bound: true,
    });

    vi.mocked(api.switchAcpEngine).mockImplementation(async (engine) => ({
      available: true,
      binary_path: 'agy-ultra-acp.exe',
      active_engine: engine,
      ws_url: 'ws://127.0.0.1:8765',
      protocol_version: 'agy.stream.v1',
      win32_job_bound: true,
    }));
  });

  afterEach(() => {
    useChatStore.setState({
      agentEngine: 'python',
      streamTransport: 'sse',
      isGenerating: false,
    });
  });

  it('renders SettingsModal acp-runtime-badge and switches agentEngine to rust_acp & streamTransport to ws', async () => {
    render(
      <QueryClientProvider client={queryClient}>
        <SettingsModal />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('acp-runtime-badge').textContent).toContain('agy.stream.v1');
    });

    fireEvent.click(screen.getByTestId('engine-rust-btn'));
    expect(useChatStore.getState().agentEngine).toBe('rust_acp');
    await waitFor(() => {
      expect(api.switchAcpEngine).toHaveBeenCalledWith('rust_acp');
    });

    fireEvent.click(screen.getByTestId('transport-ws-btn'));
    expect(useChatStore.getState().streamTransport).toBe('ws');

    // Restore via UI buttons
    fireEvent.click(screen.getByTestId('engine-python-btn'));
    fireEvent.click(screen.getByTestId('transport-sse-btn'));
    expect(useChatStore.getState().agentEngine).toBe('python');
    expect(useChatStore.getState().streamTransport).toBe('sse');
  });

  it('dispatches sendMessage through streamChatOverWsV1 with engine="rust_acp" when streamTransport="ws"', async () => {
    const wsSpy = vi.spyOn(wsProtocol, 'streamChatOverWsV1').mockImplementation(async (_payload, callbacks) => {
      callbacks.onStart?.('conv_m8_1', 101);
      callbacks.onText?.('Hello from Rust ACP WS!');
      callbacks.onSaved?.(102);
      callbacks.onDone?.({ cid: 'c_m8', rid: 'r_m8', rcid: 'rc_m8' });
    });

    useChatStore.setState({
      agentEngine: 'rust_acp',
      streamTransport: 'ws',
    });

    await useChatStore.getState().sendMessage('Hello over WS');

    expect(wsSpy).toHaveBeenCalledTimes(1);
    expect(wsSpy.mock.calls[0][0]).toEqual(
      expect.objectContaining({
        prompt: 'Hello over WS',
        engine: 'rust_acp',
      })
    );

    wsSpy.mockRestore();
  });
});
