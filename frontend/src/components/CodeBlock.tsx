import React, { useState, useEffect, useMemo, useCallback, useRef, useLayoutEffect } from 'react';
import { highlightClient, HighlightResult } from '../utils/highlightClient';
import { useCanvasStore } from '../store/canvasStore';

export interface CodeBlockProps {
  code: string;
  language?: string;
  isStreaming?: boolean;
}

const HLJS_SHADOW_CSS = `
:host {
  display: block;
  white-space: pre;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  color: #e4e4e7;
}
.hljs-keyword, .hljs-selector-tag, .hljs-literal, .hljs-section, .hljs-link { color: #c084fc; }
.hljs-function .hljs-keyword { color: #60a5fa; }
.hljs-subst { color: #e4e4e7; }
.hljs-string, .hljs-title, .hljs-name, .hljs-type, .hljs-attribute, .hljs-symbol, .hljs-bullet, .hljs-addition, .hljs-variable, .hljs-template-tag, .hljs-template-variable { color: #4ade80; }
.hljs-comment, .hljs-quote, .hljs-deletion, .hljs-meta { color: #71717a; font-style: italic; }
.hljs-number, .hljs-regexp, .hljs-literal, .hljs-built_in { color: #fb923c; }
.hljs-title.function_, .hljs-title.class_ { color: #60a5fa; }
`;

let sharedHljsSheet: CSSStyleSheet | null = null;
function getHljsStyleSheet(): CSSStyleSheet | null {
  if (typeof window === 'undefined' || typeof CSSStyleSheet === 'undefined') return null;
  if (!sharedHljsSheet) {
    try {
      sharedHljsSheet = new CSSStyleSheet();
      sharedHljsSheet.replaceSync(HLJS_SHADOW_CSS);
    } catch {
      sharedHljsSheet = null;
    }
  }
  return sharedHljsSheet;
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export const CodeBlock: React.FC<CodeBlockProps> = React.memo(({
  code,
  language = 'text',
  isStreaming = false,
}) => {
  const normalizedLang = useMemo(() => (language || 'TEXT').toUpperCase(), [language]);
  const [copied, setCopied] = useState(false);
  const shadowHostRef = useRef<HTMLSpanElement | null>(null);

  const initialCache = useMemo(
    () => highlightClient.getCached(language, code),
    [language, code]
  );

  const [highlighted, setHighlighted] = useState<HighlightResult | null>(initialCache || null);

  useEffect(() => {
    if (isStreaming) {
      return;
    }

    let isSubscribed = true;
    highlightClient.highlight(language, code).then((result) => {
      if (isSubscribed) {
        setHighlighted(result);
      }
    });

    return () => {
      isSubscribed = false;
    };
  }, [code, language, isStreaming]);

  useLayoutEffect(() => {
    const host = shadowHostRef.current;
    if (!host) return;

    let shadow = host.shadowRoot;
    if (!shadow) {
      shadow = host.attachShadow({ mode: 'open' });
      const sheet = getHljsStyleSheet();
      if (sheet && 'adoptedStyleSheets' in shadow) {
        shadow.adoptedStyleSheets = [sheet];
      }
    }

    shadow.innerHTML = highlighted ? highlighted.html : escapeHtml(code);
  }, [highlighted, code]);

  const handleCopy = useCallback(async () => {
    try {
      const cleanCode = code.replace(/\r\n/g, '\n');
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(cleanCode);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = cleanCode;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy to clipboard', err);
    }
  }, [code]);

  return (
    <div className="relative my-2.5 rounded-lg border border-zinc-700/60 bg-zinc-900/90 text-zinc-100 overflow-hidden shadow-sm">
      <div className="flex items-center justify-between px-3 py-1.5 bg-zinc-800/80 border-b border-zinc-700/50 text-xs font-mono text-zinc-300 select-none">
        {normalizedLang}
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            data-testid="open-canvas-button"
            onClick={() => {
              useCanvasStore.getState().openCanvas({
                title: `${normalizedLang.toLowerCase()}_artifact`,
                language: normalizedLang.toLowerCase(),
                content: code,
                originalContent: code,
              });
            }}
            className="px-2 py-0.5 rounded text-[11px] text-zinc-400 hover:text-white hover:bg-zinc-700/60 transition-colors flex items-center gap-1"
            title="Open in Canvas side-panel"
          >
            <span>⧉</span> Canvas
          </button>
          <button
            type="button"
            data-testid="copy-code-button"
            onClick={handleCopy}
            className="px-2 py-0.5 rounded text-[11px] text-zinc-300 hover:text-white hover:bg-zinc-700/60 transition-colors"
            title="Copy code to clipboard"
          >
            {copied ? 'Copied!' : 'Copy'}
          </button>
        </div>
      </div>

      <pre className="m-0 p-3 overflow-x-auto bg-transparent font-mono text-xs md:text-sm leading-relaxed">
        <code className="hljs block whitespace-pre font-mono text-zinc-200 code-lines">
          <span ref={shadowHostRef} />
        </code>
      </pre>
    </div>
  );
});

CodeBlock.displayName = 'CodeBlock';
