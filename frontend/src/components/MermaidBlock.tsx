import React, { useEffect, useState, useCallback, useRef } from 'react';
import { useCanvasStore } from '../store/canvasStore';

export interface MermaidBlockProps {
  code: string;
}

let mermaidInitialized = false;
let mermaidSeq = 0;

export const MermaidBlock: React.FC<MermaidBlockProps> = React.memo(({ code }) => {
  const [viewMode, setViewMode] = useState<'diagram' | 'code'>('diagram');
  const [svgHtml, setSvgHtml] = useState<string>('');
  const [parseError, setParseError] = useState<string | null>(null);
  const [isRendering, setIsRendering] = useState<boolean>(true);
  const [copied, setCopied] = useState<boolean>(false);
  const diagramContainerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    const trimmed = code.trim();

    if (!trimmed) {
      setSvgHtml('');
      setParseError('Empty Mermaid diagram');
      setIsRendering(false);
      return;
    }

    setIsRendering(true);
    setParseError(null);

    const renderDiagram = async () => {
      try {
        const mermaid = (await import('mermaid')).default;
        if (!mermaidInitialized) {
          mermaid.initialize({
            startOnLoad: false,
            theme: 'dark',
            securityLevel: 'strict',
          });
          mermaidInitialized = true;
        }

        const uniqueId = `mermaid_diagram_${++mermaidSeq}`;
        const result = await mermaid.render(uniqueId, trimmed);
        if (cancelled) return;

        setSvgHtml(result.svg || '');
        setParseError(null);
        setIsRendering(false);

        if (result.bindFunctions && diagramContainerRef.current) {
          result.bindFunctions(diagramContainerRef.current);
        }
      } catch (err: any) {
        if (cancelled) return;
        const msg = err?.message || 'Mermaid syntax parse error';
        setSvgHtml('');
        setParseError(msg);
        setIsRendering(false);
      }
    };

    renderDiagram();

    return () => {
      cancelled = true;
    };
  }, [code]);

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
      console.error('Failed to copy Mermaid code', err);
    }
  }, [code]);

  const handleOpenCanvas = useCallback(() => {
    useCanvasStore.getState().openCanvas({
      title: 'diagram.mmd',
      language: 'mermaid',
      content: code,
      originalContent: code,
    });
  }, [code]);

  const showRawFallback = viewMode === 'code' || Boolean(parseError);

  return (
    <div className="relative my-2.5 rounded-lg border border-zinc-700/60 bg-zinc-900/90 text-zinc-100 overflow-hidden shadow-sm">
      <div className="flex items-center justify-between px-3 py-1.5 bg-zinc-800/80 border-b border-zinc-700/50 text-xs font-mono text-zinc-300 select-none">
        <div className="flex items-center gap-2">
          <span>MERMAID</span>
          {parseError && (
            <span
              data-testid="mermaid-parse-error-badge"
              title={parseError}
              className="px-1.5 py-0.5 rounded bg-amber-950/60 border border-amber-700/50 text-[10px] text-amber-300 font-sans"
            >
              Parse error
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          <button
            type="button"
            data-testid="mermaid-toggle-view-btn"
            onClick={() => setViewMode((v) => (v === 'diagram' ? 'code' : 'diagram'))}
            className="px-2 py-0.5 rounded text-[11px] bg-zinc-900/70 border border-zinc-700/50 hover:bg-zinc-700/60 transition-colors font-sans"
            title="Toggle Diagram / Code view"
          >
            <span className={viewMode === 'diagram' ? 'text-white font-semibold' : 'text-zinc-400'}>
              Diagram
            </span>
            <span className="text-zinc-600 mx-1">|</span>
            <span className={viewMode === 'code' ? 'text-white font-semibold' : 'text-zinc-400'}>
              Code
            </span>
          </button>

          <button
            type="button"
            data-testid="mermaid-canvas-btn"
            onClick={handleOpenCanvas}
            className="px-2 py-0.5 rounded text-[11px] text-zinc-400 hover:text-white hover:bg-zinc-700/60 transition-colors flex items-center gap-1 font-sans"
            title="Open in Canvas side-panel"
          >
            <span>⧉</span> Canvas
          </button>

          <button
            type="button"
            data-testid="mermaid-copy-btn"
            onClick={handleCopy}
            className="px-2 py-0.5 rounded text-[11px] text-zinc-300 hover:text-white hover:bg-zinc-700/60 transition-colors font-sans"
            title="Copy Mermaid source"
          >
            {copied ? 'Copied!' : 'Copy'}
          </button>
        </div>
      </div>

      {showRawFallback ? (
        <div className="p-3 overflow-x-auto bg-zinc-950/60">
          {parseError && (
            <div className="mb-2 text-[11px] text-amber-300/90 font-mono truncate" title={parseError}>
              {parseError}
            </div>
          )}
          <pre className="text-xs font-mono text-zinc-200 whitespace-pre-wrap break-words m-0">
            <code>{code}</code>
          </pre>
        </div>
      ) : (
        <div className="p-4 overflow-x-auto flex items-center justify-center bg-zinc-950/40 min-h-[80px]">
          {isRendering && !svgHtml ? (
            <span className="text-xs text-zinc-400 animate-pulse">Rendering diagram...</span>
          ) : (
            <div
              ref={diagramContainerRef}
              data-testid="mermaid-diagram"
              className="w-full flex justify-center [&_svg]:max-w-full [&_svg]:h-auto"
              dangerouslySetInnerHTML={{ __html: svgHtml }}
            />
          )}
        </div>
      )}
    </div>
  );
});

MermaidBlock.displayName = 'MermaidBlock';
