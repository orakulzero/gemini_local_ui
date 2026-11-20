export interface GeminiDesktopConfig {
  apiBaseUrl?: string;
  apiToken?: string;
  wsBaseUrl?: string;
  isDesktop?: boolean;
}

declare global {
  interface Window {
    __GEMINI_DESKTOP_CONFIG__?: GeminiDesktopConfig;
    __TAURI_INTERNALS__?: unknown;
  }
}

export function isDesktopRuntime(): boolean {
  if (typeof window === 'undefined') return false;
  return Boolean(
    window.__GEMINI_DESKTOP_CONFIG__?.isDesktop || window.__TAURI_INTERNALS__
  );
}

export function getDesktopApiBaseUrl(): string {
  if (typeof window === 'undefined') return '';
  const raw = window.__GEMINI_DESKTOP_CONFIG__?.apiBaseUrl;
  return typeof raw === 'string' ? raw.trim().replace(/\/+$/, '') : '';
}

export function resolveApiUrl(path: string): string {
  const base = getDesktopApiBaseUrl();
  if (!base) return path;
  if (/^https?:\/\//i.test(path)) return path;
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${base}${normalizedPath}`;
}

export function getDesktopAuthHeaders(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  const token = window.__GEMINI_DESKTOP_CONFIG__?.apiToken;
  if (typeof token !== 'string' || !token.trim()) return {};
  return { 'X-Gemini-Local-Token': token.trim() };
}

function appendWsToken(url: string): string {
  if (typeof window === 'undefined') return url;
  const token = window.__GEMINI_DESKTOP_CONFIG__?.apiToken;
  if (typeof token !== 'string' || !token.trim()) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}token=${encodeURIComponent(token.trim())}`;
}

export function resolveWsUrl(path = '/api/chat/ws'): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  if (typeof window !== 'undefined') {
    const rawWsBase = window.__GEMINI_DESKTOP_CONFIG__?.wsBaseUrl;
    if (typeof rawWsBase === 'string' && rawWsBase.trim()) {
      return `${rawWsBase.trim().replace(/\/+$/, '')}${normalizedPath}`;
    }
    const apiBase = getDesktopApiBaseUrl();
    if (apiBase) {
      return `${apiBase.replace(/^http(s?):\/\//i, 'ws$1://')}${normalizedPath}`;
    }
    if (window.location?.host) {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      return `${proto}//${window.location.host}${normalizedPath}`;
    }
  }
  return `ws://127.0.0.1:8000${normalizedPath}`;
}

export function resolveAuthenticatedWsUrl(path = '/api/chat/ws'): string {
  return appendWsToken(resolveWsUrl(path));
}

export function desktopFetch(path: string, init?: RequestInit): Promise<Response> {
  if (!isDesktopRuntime()) {
    return init === undefined ? globalThis.fetch(path) : globalThis.fetch(path, init);
  }
  const url = resolveApiUrl(path);
  const authHeaders = getDesktopAuthHeaders();
  if (Object.keys(authHeaders).length === 0) {
    return init === undefined ? globalThis.fetch(url) : globalThis.fetch(url, init);
  }
  const headers = new Headers(init?.headers);
  for (const [k, v] of Object.entries(authHeaders)) {
    if (!headers.has(k)) headers.set(k, v);
  }
  return globalThis.fetch(url, { ...init, headers });
}
