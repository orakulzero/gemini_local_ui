import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { streamChatOverWsV1 } from '../services/wsProtocolV1';
import { resolveAuthenticatedWsUrl, resolveWsUrl } from '../services/desktopBridge';
import { segmentStreamingMarkdown } from '../components/StreamingMarkdown';
import { SearchModal } from '../components/SearchModal';
import { ModelSelector } from '../components/ModelSelector';
import { FlowStudioDrawer } from '../components/FlowStudioDrawer';
import { useSessionStore } from '../store/sessionStore';
import { resolveRenderableImageUrl } from '../utils/imageUrl';

class FakeWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static instances: FakeWebSocket[] = [];
  readyState = FakeWebSocket.OPEN;
  url: string;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
    queueMicrotask(() => this.onopen?.());
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.closed = true;
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  emit(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

describe('Deep Verification Invariants (Frontend)', () => {
  const origWS = globalThis.WebSocket;
  const origFetch = globalThis.fetch;

  beforeEach(() => {
    FakeWebSocket.instances = [];
    (globalThis as any).WebSocket = FakeWebSocket;
    delete (window as any).__GEMINI_DESKTOP_CONFIG__;
    delete (window as any).__TAURI_INTERNALS__;
  });

  afterEach(() => {
    (globalThis as any).WebSocket = origWS;
    globalThis.fetch = origFetch;
    vi.restoreAllMocks();
  });

  it('wsProtocolV1 keeps socket open after done until saved/result arrives and surfaces premature disconnect', async () => {
    const onDone = vi.fn();
    const onSaved = vi.fn();
    const onError = vi.fn();

    const p = streamChatOverWsV1(
      { conversation_id: 'c1', prompt: 'hi', model: 'gemini-3.8-flash' },
      { onDone, onSaved, onError },
      'ws://127.0.0.1:8000/api/chat/ws'
    );

    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));
    const ws = FakeWebSocket.instances[0];

    // Backend emits `done` BEFORE `saved`! Socket must stay open for `saved`.
    ws.emit({ jsonrpc: '2.0', method: 'session/update', params: { event: { type: 'done', metadata: { full_text: 'OK' } } } });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(ws.closed).toBe(false);

    ws.emit({ jsonrpc: '2.0', method: 'session/update', params: { event: { type: 'saved', assistant_message_id: 777 } } });
    expect(onSaved).toHaveBeenCalledWith(777);
    expect(ws.closed).toBe(true);
    await p;
    expect(onError).not.toHaveBeenCalled();

    // Premature disconnect before done/saved must surface an error instead of silent hang
    const onErr2 = vi.fn();
    const p2 = streamChatOverWsV1(
      { conversation_id: 'c1', prompt: 'hi2', model: 'gemini-3.8-flash' },
      { onError: onErr2 },
      'ws://127.0.0.1:8000/api/chat/ws'
    );
    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(2));
    FakeWebSocket.instances[1].close();
    await p2;
    expect(onErr2).toHaveBeenCalledTimes(1);
  });

  it('resolveAuthenticatedWsUrl appends ?token= while resolveWsUrl stays clean in desktop mode', () => {
    (window as any).__GEMINI_DESKTOP_CONFIG__ = {
      isDesktop: true,
      apiBaseUrl: 'http://127.0.0.1:49152',
      apiToken: 'tok_abc_123',
    };
    expect(resolveWsUrl('/api/chat/ws')).toBe('ws://127.0.0.1:49152/api/chat/ws');
    expect(resolveAuthenticatedWsUrl('/api/chat/ws')).toBe('ws://127.0.0.1:49152/api/chat/ws?token=tok_abc_123');
  });

  it('segmentStreamingMarkdown strips raw <FollowUp .../> tags cleanly', () => {
    const raw = 'Hello world.\n\n<FollowUp label="Want more?" query="Expand this"/>';
    const seg = segmentStreamingMarkdown(raw);
    const combined = [...seg.immutableBlocks.map((b) => b.content), seg.tailBlock].join('\n');
    expect(combined).toContain('Hello world.');
    expect(combined).not.toContain('<FollowUp');
  });

  it('SearchModal escapes HTML safely (no XSS) and closes on Escape key', async () => {
    useSessionStore.setState({ isSearchOpen: true });
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/rag/status')) {
        return new Response(JSON.stringify({ total_chunks: 10, sqlite_vec_loaded: true, degraded: false }), { status: 200 });
      }
      if (url.includes('/api/search')) {
        return new Response(
          JSON.stringify({
            results: [
              {
                message_id: 1,
                conversation_id: 'c1',
                conversation_title: 'Test',
                role: 'assistant',
                snippet: '<img src=x onerror=alert(1)> <mark class="search-highlight">safe</mark>',
              },
            ],
          }),
          { status: 200 }
        );
      }
      return new Response(JSON.stringify({}), { status: 200 });
    }) as any;

    render(<SearchModal />);
    const input = screen.getByTestId('search-input');
    fireEvent.change(input, { target: { value: 'safe' } });
    const item = await screen.findByTestId('search-result-item');
    // Must render <mark> for highlight, NEVER an actual <img> DOM element!
    expect(item.querySelector('mark')).not.toBeNull();
    expect(item.querySelector('img')).toBeNull();

    // Pressing Escape must close SearchModal
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(useSessionStore.getState().isSearchOpen).toBe(false);
  });

  it('ModelSelector closes dropdown on Escape key', () => {
    useSessionStore.setState({
      models: [{ id: 'gemini-3.8-flash', display_name: '3.8 Flash', description: 'Fast' } as any],
      selectedModel: 'gemini-3.8-flash',
    });
    render(<ModelSelector />);
    fireEvent.click(screen.getByTestId('model-selector-btn'));
    expect(screen.getByTestId('model-selector-dropdown')).toBeTruthy();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByTestId('model-selector-dropdown')).toBeNull();
  });

  it('resolveRenderableImageUrl proxies cross-site Google FIFE URLs same-origin and leaves safe URLs intact', () => {
    const fife = 'https://work.fife.usercontent.google.com/rd-gg-dl/abc123?foo=bar';
    const lh3 = 'https://lh3.googleusercontent.com/gg-dl/xyz789';
    expect(resolveRenderableImageUrl(fife)).toBe(`/api/images/proxy?url=${encodeURIComponent(fife)}`);
    expect(resolveRenderableImageUrl(lh3)).toBe(`/api/images/proxy?url=${encodeURIComponent(lh3)}`);
    expect(resolveRenderableImageUrl('/files/abc123.png')).toBe('/files/abc123.png');
    expect(resolveRenderableImageUrl('/api/images/proxy?url=already')).toBe('/api/images/proxy?url=already');
    expect(resolveRenderableImageUrl('https://example.com/chart.png')).toBe('https://example.com/chart.png');
  });

  it('FlowStudioDrawer loads and selects saved projects from /api/flow/projects', async () => {
    const savedProj = {
      project_id: 'flow_saved_01',
      topic: 'Polyphase FFT Spectrometer',
      language: 'en',
      target_audience: 'university',
      aspect_ratio: '16:9',
      model_key: 'veo_3_1_t2v_fast_audio',
      status: 'assembled',
      assembled_video_url: '/api/flow/media/flow_saved_01_lesson.mp4',
      scenes: [
        {
          scene_id: 'sc_1',
          scene_number: 1,
          title: 'Intro',
          narration_text: 'Polyphase channels.',
          visual_prompt: 'DSP diagram',
          duration_sec: 5,
          camera_motion: 'slow push-in',
          on_screen_bullets: ['FFT'],
          status: 'completed',
        },
      ],
    };
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/flow/status')) {
        return new Response(JSON.stringify({ ok: true, authenticated: true }), { status: 200 });
      }
      if (url.includes('/api/flow/projects')) {
        return new Response(JSON.stringify({ ok: true, count: 1, projects: [savedProj] }), { status: 200 });
      }
      return new Response(JSON.stringify({}), { status: 200 });
    }) as any;

    render(<FlowStudioDrawer isOpen={true} onClose={() => {}} />);
    const itemBtn = await screen.findByTestId('flow-saved-project-flow_saved_01');
    expect(itemBtn.textContent).toContain('Polyphase FFT Spectrometer');
    fireEvent.click(itemBtn);
    await waitFor(() => {
      expect(screen.getByText(/Storyboard: Polyphase FFT Spectrometer/)).toBeTruthy();
      expect(screen.getByText(/Assembled Educational Video Lesson/)).toBeTruthy();
    });
  });
});
