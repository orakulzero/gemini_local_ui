/**
 * Dedicated Web Worker for Non-Blocking Syntax Highlighting.
 * Offloads all CPU-intensive code tokenization and regex matching from the Main Thread.
 */

import hljs from 'highlight.js/lib/core';
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import python from 'highlight.js/lib/languages/python';
import bash from 'highlight.js/lib/languages/bash';
import json from 'highlight.js/lib/languages/json';
import markdown from 'highlight.js/lib/languages/markdown';
import sql from 'highlight.js/lib/languages/sql';
import rust from 'highlight.js/lib/languages/rust';
import cpp from 'highlight.js/lib/languages/cpp';
import xml from 'highlight.js/lib/languages/xml';
import css from 'highlight.js/lib/languages/css';
import yaml from 'highlight.js/lib/languages/yaml';

// Register essential languages
hljs.registerLanguage('javascript', javascript);
hljs.registerLanguage('js', javascript);
hljs.registerLanguage('typescript', typescript);
hljs.registerLanguage('ts', typescript);
hljs.registerLanguage('python', python);
hljs.registerLanguage('py', python);
hljs.registerLanguage('bash', bash);
hljs.registerLanguage('sh', bash);
hljs.registerLanguage('shell', bash);
hljs.registerLanguage('json', json);
hljs.registerLanguage('markdown', markdown);
hljs.registerLanguage('md', markdown);
hljs.registerLanguage('sql', sql);
hljs.registerLanguage('rust', rust);
hljs.registerLanguage('rs', rust);
hljs.registerLanguage('cpp', cpp);
hljs.registerLanguage('c', cpp);
hljs.registerLanguage('xml', xml);
hljs.registerLanguage('html', xml);
hljs.registerLanguage('css', css);
hljs.registerLanguage('yaml', yaml);
hljs.registerLanguage('yml', yaml);

export interface WorkerHighlightRequest {
  type: 'HIGHLIGHT_REQUEST';
  id: string;
  code: string;
  language: string;
}

export interface WorkerHighlightResponse {
  type: 'HIGHLIGHT_RESPONSE';
  id: string;
  language: string;
  html: string;
  lineCount: number;
  executionTimeMs: number;
  error?: string;
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

self.addEventListener('message', (event: MessageEvent<WorkerHighlightRequest>) => {
  const { type, id, code, language } = event.data;

  if (type !== 'HIGHLIGHT_REQUEST') return;

  const startTime = performance.now();
  const normalizedLang = (language || 'text').trim().toLowerCase();
  const lineCount = code ? code.split(/\r\n|\r|\n/).length : 0;

  try {
    let highlightedHtml: string;

    if (!code || normalizedLang === 'text' || normalizedLang === 'plain') {
      highlightedHtml = escapeHtml(code);
    } else if (hljs.getLanguage(normalizedLang)) {
      highlightedHtml = hljs.highlight(code, {
        language: normalizedLang,
        ignoreIllegals: true,
      }).value;
    } else {
      try {
        const auto = hljs.highlightAuto(code);
        highlightedHtml = auto.value;
      } catch {
        highlightedHtml = escapeHtml(code);
      }
    }

    const duration = performance.now() - startTime;

    const response: WorkerHighlightResponse = {
      type: 'HIGHLIGHT_RESPONSE',
      id,
      language: normalizedLang,
      html: highlightedHtml,
      lineCount,
      executionTimeMs: duration,
    };

    self.postMessage(response);
  } catch (err: any) {
    const response: WorkerHighlightResponse = {
      type: 'HIGHLIGHT_RESPONSE',
      id,
      language: normalizedLang,
      html: escapeHtml(code),
      lineCount,
      executionTimeMs: performance.now() - startTime,
      error: err?.message || 'Highlighting failed',
    };
    self.postMessage(response);
  }
});
