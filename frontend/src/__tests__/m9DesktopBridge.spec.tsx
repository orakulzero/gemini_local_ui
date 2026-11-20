import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { queryClient } from '../services/queryClient';
import {
  getDesktopApiBaseUrl,
  getDesktopAuthHeaders,
  isDesktopRuntime,
  resolveApiUrl,
  resolveWsUrl,
} from '../services/desktopBridge';
import { Header } from '../components/Header';

describe('M9: Tauri v2 Desktop Runtime Bridge & Header Runtime Badge', () => {
  afterEach(() => {
    delete (window as any).__GEMINI_DESKTOP_CONFIG__;
    delete (window as any).__TAURI_INTERNALS__;
  });

  it('preserves standard browser URLs and empty auth headers when desktop config is absent', () => {
    delete (window as any).__GEMINI_DESKTOP_CONFIG__;
    delete (window as any).__TAURI_INTERNALS__;

    expect(isDesktopRuntime()).toBe(false);
    expect(getDesktopApiBaseUrl()).toBe('');
    expect(resolveApiUrl('/api/models')).toBe('/api/models');
    expect(getDesktopAuthHeaders()).toEqual({});

    const { unmount } = render(
      <QueryClientProvider client={queryClient}>
        <Header />
      </QueryClientProvider>
    );
    expect(screen.queryByTestId('desktop-runtime-badge')).toBeNull();
    unmount();
  });

  it('resolves sidecar base URL, X-Gemini-Local-Token header, WS URL, and renders desktop-runtime-badge in desktop mode', () => {
    (window as any).__GEMINI_DESKTOP_CONFIG__ = {
      apiBaseUrl: 'http://127.0.0.1:54321/',
      apiToken: 'tok_m9_secret',
      isDesktop: true,
    };

    expect(isDesktopRuntime()).toBe(true);
    expect(getDesktopApiBaseUrl()).toBe('http://127.0.0.1:54321');
    expect(resolveApiUrl('/api/models')).toBe('http://127.0.0.1:54321/api/models');
    expect(getDesktopAuthHeaders()['X-Gemini-Local-Token']).toBe('tok_m9_secret');
    expect(resolveWsUrl('/api/chat/ws')).toBe('ws://127.0.0.1:54321/api/chat/ws');

    render(
      <QueryClientProvider client={queryClient}>
        <Header />
      </QueryClientProvider>
    );

    const badge = screen.getByTestId('desktop-runtime-badge');
    expect(badge).toBeDefined();
    expect(badge.textContent).toContain('Desktop');
  });
});
