import React, { useState, useCallback, useMemo } from 'react';
import { useCanvasStore } from '../store/canvasStore';
import { useChatStore } from '../store/chatStore';

const EXTENSION_MAP: Record<string, string> = {
  python: 'py',
  py: 'py',
  javascript: 'js',
  js: 'js',
  typescript: 'ts',
  ts: 'ts',
  tsx: 'tsx',
  jsx: 'jsx',
  html: 'html',
  htm: 'html',
  css: 'css',
  svg: 'svg',
  json: 'json',
  markdown: 'md',
  md: 'md',
  rust: 'rs',
  rs: 'rs',
  go: 'go',
  sql: 'sql',
  sh: 'sh',
  bash: 'sh',
  c: 'c',
  cpp: 'cpp',
};

const MIME_MAP: Record<string, string> = {
  html: 'text/html',
  svg: 'image/svg+xml',
  json: 'application/json',
  markdown: 'text/markdown',
  md: 'text/markdown',
  css: 'text/css',
  javascript: 'application/javascript',
  typescript: 'application/typescript',
  python: 'text/x-python',
  py: 'text/x-python',
};

export const CanvasPanel: React.FC = () => {
  const isOpen = useCanvasStore((s) => s.isOpen);
  const activeArtifact = useCanvasStore((s) => s.activeArtifact);
  const activeTab = useCanvasStore((s) => s.activeTab);
  const closeCanvas = useCanvasStore((s) => s.closeCanvas);
  const updateContent = useCanvasStore((s) => s.updateContent);
  const setActiveTab = useCanvasStore((s) => s.setActiveTab);
  const injectContext = useChatStore((s) => s.injectContext);

  const [copied, setCopied] = useState(false);

  const language = (activeArtifact?.language || 'text').toLowerCase();
  const ext = EXTENSION_MAP[language] || 'txt';
  const mime = MIME_MAP[language] || 'text/plain';

  const handleCopy = useCallback(async () => {
    if (!activeArtifact) return;
    try {
      await navigator.clipboard.writeText(activeArtifact.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (e) {
      console.error('Failed to copy artifact content', e);
    }
  }, [activeArtifact]);

  const handleDownload = useCallback(() => {
    if (!activeArtifact) return;
    try {
      const filename = activeArtifact.title.includes('.')
        ? activeArtifact.title
        : `${activeArtifact.title || 'artifact'}.${ext}`;

      const blob = new Blob([activeArtifact.content], { type: `${mime};charset=utf-8` });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error('Failed to download artifact', e);
    }
  }, [activeArtifact, ext, mime]);

  const handleInsertIntoPrompt = useCallback(() => {
    if (!activeArtifact) return;
    const snippet = `Here is my updated version of ${activeArtifact.title || 'the code'}:\n\`\`\`${activeArtifact.language}\n${activeArtifact.content}\n\`\`\`\n`;
    injectContext(snippet);
  }, [activeArtifact, injectContext]);

  const lines = useMemo(() => {
    return (activeArtifact?.content || '').split('\n');
  }, [activeArtifact?.content]);

  if (!isOpen || !activeArtifact) {
    return null;
  }

  const isHtml = language === 'html' || language === 'htm';
  const isSvg = language === 'svg';
  const isMarkdown = language === 'markdown' || language === 'md';

  return (
    <div
      data-testid="canvas-panel"
      className="w-full h-full flex flex-col bg-zinc-950 border-l border-zinc-800 text-zinc-100 z-20 overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3.5 py-2.5 border-b border-zinc-800 bg-zinc-900/90 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-semibold text-zinc-200 truncate" title={activeArtifact.title}>
            {activeArtifact.title || 'Artifact'}
          </span>
          <span className="px-1.5 py-0.5 rounded text-[10px] font-mono uppercase bg-zinc-800 text-zinc-400 border border-zinc-700/60">
            {activeArtifact.language}
          </span>
        </div>

        {/* Tab switcher */}
        <div className="flex items-center bg-zinc-950 p-0.5 rounded-lg border border-zinc-800 text-xs">
          <button
            type="button"
            data-testid="canvas-tab-code"
            onClick={() => setActiveTab('code')}
            className={`px-2.5 py-1 rounded-md transition-colors font-medium ${
              activeTab === 'code'
                ? 'bg-zinc-800 text-white shadow-sm'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            Code
          </button>
          <button
            type="button"
            data-testid="canvas-tab-preview"
            onClick={() => setActiveTab('preview')}
            className={`px-2.5 py-1 rounded-md transition-colors font-medium ${
              activeTab === 'preview'
                ? 'bg-zinc-800 text-white shadow-sm'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            Preview
          </button>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            data-testid="canvas-insert-prompt-btn"
            onClick={handleInsertIntoPrompt}
            title="Insert into Prompt input"
            className="px-2 py-1 text-xs rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white transition-colors"
          >
            Insert in Prompt
          </button>
          <button
            type="button"
            data-testid="canvas-copy-btn"
            onClick={handleCopy}
            title="Copy code"
            className="p-1.5 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded transition-colors text-xs"
          >
            {copied ? '✓' : '📋'}
          </button>
          <button
            type="button"
            data-testid="canvas-download-btn"
            onClick={handleDownload}
            title="1-Click Download file"
            className="p-1.5 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded transition-colors text-xs"
          >
            💾
          </button>
          <button
            type="button"
            data-testid="canvas-close-btn"
            onClick={closeCanvas}
            title="Close Canvas"
            className="p-1.5 text-zinc-400 hover:text-red-400 hover:bg-zinc-800 rounded transition-colors text-sm leading-none"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 min-h-0 relative overflow-hidden bg-zinc-950">
        {activeTab === 'code' ? (
          <div className="flex h-full w-full font-mono text-xs overflow-auto">
            {/* Line numbers gutter */}
            <div className="select-none py-3 px-2 text-right bg-zinc-900/60 border-r border-zinc-800 text-zinc-600 font-mono text-xs min-w-[3rem] shrink-0">
              {lines.map((_, i) => (
                <div key={i} className="leading-relaxed">
                  {i + 1}
                </div>
              ))}
            </div>

            {/* Editable code textarea */}
            <textarea
              data-testid="canvas-editor-textarea"
              value={activeArtifact.content}
              onChange={(e) => updateContent(e.target.value)}
              spellCheck={false}
              className="flex-1 w-full h-full p-3 bg-transparent text-zinc-200 resize-none focus:outline-none font-mono text-xs md:text-sm leading-relaxed whitespace-pre"
            />
          </div>
        ) : (
          <div className="w-full h-full flex items-center justify-center overflow-auto bg-zinc-900/30 p-2">
            {isHtml ? (
              <iframe
                data-testid="canvas-preview-iframe"
                sandbox="allow-scripts"
                srcDoc={activeArtifact.content}
                className="w-full h-full border-0 rounded-lg bg-white shadow-md"
                title="Canvas HTML Preview"
              />
            ) : isSvg ? (
              <iframe
                data-testid="canvas-preview-svg"
                sandbox="allow-scripts"
                srcDoc={activeArtifact.content}
                className="w-full h-full border-0 rounded-lg bg-zinc-900/80 shadow-md"
                title="Canvas SVG Preview"
              />
            ) : isMarkdown ? (
              <div
                data-testid="canvas-preview-markdown"
                className="w-full h-full p-6 bg-zinc-900 rounded-lg border border-zinc-800 overflow-auto prose prose-invert prose-sm max-w-none whitespace-pre-wrap font-sans"
              >
                {activeArtifact.content}
              </div>
            ) : (
              <div
                data-testid="canvas-preview-code"
                className="w-full h-full p-4 font-mono text-xs text-zinc-300 bg-zinc-950 rounded-lg border border-zinc-800 overflow-auto whitespace-pre"
              >
                {activeArtifact.content}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
