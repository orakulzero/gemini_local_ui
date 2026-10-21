/**
 * Singleton Worker Client and LRU Memory Cache for Syntax Highlighting.
 */

import type { WorkerHighlightRequest, WorkerHighlightResponse } from '../workers/highlight.worker';

class SimpleLRUCache<K, V> {
  private capacity: number;
  private map: Map<K, V> = new Map();

  constructor(capacity = 500) {
    this.capacity = capacity;
  }

  get(key: K): V | undefined {
    if (!this.map.has(key)) return undefined;
    const val = this.map.get(key)!;
    this.map.delete(key);
    this.map.set(key, val);
    return val;
  }

  set(key: K, value: V): void {
    if (this.map.has(key)) {
      this.map.delete(key);
    } else if (this.map.size >= this.capacity) {
      const firstKey = this.map.keys().next().value;
      if (firstKey !== undefined) this.map.delete(firstKey);
    }
    this.map.set(key, value);
  }

  has(key: K): boolean {
    return this.map.has(key);
  }
}

export interface HighlightResult {
  html: string;
  lineCount: number;
  language: string;
}

type Resolver = (result: HighlightResult) => void;

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

class HighlightClient {
  private worker: Worker | null = null;
  private cache = new SimpleLRUCache<string, HighlightResult>(500);
  private pendingRequests = new Map<string, Resolver[]>();

  constructor() {
    this.initWorker();
  }

  private initWorker() {
    if (typeof window === 'undefined' || typeof Worker === 'undefined') return;

    try {
      this.worker = new Worker(
        new URL('../workers/highlight.worker.ts', import.meta.url),
        { type: 'module' }
      );

      this.worker.onmessage = (event: MessageEvent<WorkerHighlightResponse>) => {
        const { id, html, lineCount, language } = event.data;
        const result: HighlightResult = { html, lineCount, language };

        this.cache.set(id, result);

        const resolvers = this.pendingRequests.get(id);
        if (resolvers) {
          this.pendingRequests.delete(id);
          for (const resolve of resolvers) {
            resolve(result);
          }
        }
      };

      this.worker.onerror = (err) => {
        console.error('Highlight worker encountered error:', err);
        this.worker?.terminate();
        this.initWorker();
      };
    } catch (e) {
      console.warn('Web Worker initialization failed, falling back to synchronous escape', e);
    }
  }

  public generateKey(language: string, code: string): string {
    let hash = 0;
    const str = `${language}:${code}`;
    for (let i = 0; i < str.length; i++) {
      hash = ((hash << 5) - hash) + str.charCodeAt(i);
      hash |= 0;
    }
    return `hl_${hash}_${code.length}`;
  }

  public getCached(language: string, code: string): HighlightResult | undefined {
    const key = this.generateKey(language, code);
    return this.cache.get(key);
  }

  public highlight(language: string, code: string): Promise<HighlightResult> {
    const key = this.generateKey(language, code);
    const cached = this.cache.get(key);
    if (cached) {
      return Promise.resolve(cached);
    }

    if (!this.worker) {
      const fallbackResult: HighlightResult = {
        html: escapeHtml(code),
        lineCount: code ? code.split(/\r\n|\r|\n/).length : 0,
        language: language || 'text',
      };
      this.cache.set(key, fallbackResult);
      return Promise.resolve(fallbackResult);
    }

    return new Promise((resolve) => {
      const existing = this.pendingRequests.get(key);
      if (existing) {
        existing.push(resolve);
        return;
      }
      this.pendingRequests.set(key, [resolve]);

      const request: WorkerHighlightRequest = {
        type: 'HIGHLIGHT_REQUEST',
        id: key,
        code,
        language,
      };

      this.worker!.postMessage(request);
    });
  }
}

export const highlightClient = new HighlightClient();
