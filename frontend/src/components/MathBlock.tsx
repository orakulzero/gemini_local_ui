/// <reference types="vite/client" />
import React, { useMemo, useRef, useLayoutEffect } from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import katexCssText from 'katex/dist/katex.min.css?inline';

export interface MathBlockProps {
  formula: string;
  displayMode?: boolean;
}

const mathRenderCache = new Map<string, string>();

let sharedKatexSheet: CSSStyleSheet | null = null;
function getKatexStyleSheet(): CSSStyleSheet | null {
  if (typeof window === 'undefined' || typeof CSSStyleSheet === 'undefined') return null;
  if (!sharedKatexSheet) {
    try {
      sharedKatexSheet = new CSSStyleSheet();
      sharedKatexSheet.replaceSync(katexCssText);
    } catch {
      sharedKatexSheet = null;
    }
  }
  return sharedKatexSheet;
}

export const MathBlock: React.FC<MathBlockProps> = React.memo(({
  formula,
  displayMode = false,
}) => {
  const hostRef = useRef<HTMLElement | null>(null);

  const renderedHtml = useMemo(() => {
    const trimmed = formula.trim();
    if (!trimmed) return '';

    const cacheKey = `${displayMode ? 'disp' : 'inline'}:${trimmed}`;
    if (mathRenderCache.has(cacheKey)) {
      return mathRenderCache.get(cacheKey)!;
    }

    try {
      const html = katex.renderToString(trimmed, {
        displayMode,
        throwOnError: false,
        errorColor: '#ef4444',
        output: 'html',
        strict: false,
      });

      if (mathRenderCache.size >= 500) {
        const first = mathRenderCache.keys().next().value;
        if (first) mathRenderCache.delete(first);
      }
      mathRenderCache.set(cacheKey, html);

      return html;
    } catch (err) {
      console.warn('KaTeX rendering error for formula:', trimmed, err);
      return '';
    }
  }, [formula, displayMode]);

  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host || !renderedHtml) return;

    let shadow = host.shadowRoot;
    if (!shadow) {
      shadow = host.attachShadow({ mode: 'open' });
      const sheet = getKatexStyleSheet();
      if (sheet && 'adoptedStyleSheets' in shadow) {
        shadow.adoptedStyleSheets = [sheet];
      }
    }
    shadow.innerHTML = renderedHtml;
  }, [renderedHtml]);

  if (!formula.trim()) {
    return null;
  }

  if (!renderedHtml) {
    return displayMode ? (
      <div className="my-2 p-2 rounded bg-red-950/40 border border-red-800/40 font-mono text-xs text-red-300">
        $${formula}$$
      </div>
    ) : (
      <span className="px-1 py-0.5 rounded bg-red-950/30 text-red-300 font-mono text-xs">
        ${formula}$
      </span>
    );
  }

  return displayMode ? (
    <div
      ref={(el) => {
        hostRef.current = el;
      }}
      data-testid="math-block-display"
      className="my-2.5 overflow-x-auto text-center py-1.5"
    />
  ) : (
    <span
      ref={(el) => {
        hostRef.current = el;
      }}
      data-testid="math-block-inline"
      className="inline-math inline-block px-0.5"
    />
  );
});

MathBlock.displayName = 'MathBlock';
