import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useChatStore } from '../store/chatStore';
import { useSessionStore } from '../store/sessionStore';
import { AttachmentTray } from './AttachmentTray';

const REASONING_SHORT: Array<{ value: 1 | 4 | 2; label: string }> = [
  { value: 1, label: 'Low' },
  { value: 4, label: 'Medium' },
  { value: 2, label: 'High' },
];

export const PromptInput: React.FC = () => {
  const [text, setText] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const isGenerating = useChatStore((s) => s.isGenerating);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const abortGeneration = useChatStore((s) => s.abortGeneration);
  const recallPromptUp = useChatStore((s) => s.recallPromptUp);
  const recallPromptDown = useChatStore((s) => s.recallPromptDown);
  const pendingAttachments = useChatStore((s) => s.pendingAttachments);
  const addAttachment = useChatStore((s) => s.addAttachment);
  const removeAttachment = useChatStore((s) => s.removeAttachment);
  const isIncognito = useChatStore((s) => s.isIncognito);
  const setIncognito = useChatStore((s) => s.setIncognito);
  const injectedContext = useChatStore((s) => s.injectedContext);
  const injectContext = useChatStore((s) => s.injectContext);

  const featureMode = useSessionStore((s) => s.featureMode);
  const setFeatureMode = useSessionStore((s) => s.setFeatureMode);
  const reasoningLevel = useSessionStore((s) => s.reasoningLevel);
  const setReasoningLevel = useSessionStore((s) => s.setReasoningLevel);
  const selectedModel = useSessionStore((s) => s.selectedModel);
  const models = useSessionStore((s) => s.models);
  const activeModelObj = models.find((m) => m.id === selectedModel);

  const adjustHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const newHeight = Math.min(el.scrollHeight, 240);
    el.style.height = `${Math.max(44, newHeight)}px`;
  }, []);

  useEffect(() => {
    adjustHeight();
  }, [text, adjustHeight]);

  useEffect(() => {
    if (injectedContext) {
      setText((prev) => (prev.trim() ? `${prev.trim()}\n\n${injectedContext}` : injectedContext));
      injectContext('');
      if (textareaRef.current) {
        textareaRef.current.focus();
      }
    }
  }, [injectedContext, injectContext]);


  const handleSubmit = async () => {
    if (isGenerating) return;
    const trimmed = text.trim();
    if (!trimmed && pendingAttachments.length === 0) return;

    setText('');
    if (textareaRef.current) {
      textareaRef.current.style.height = '44px';
    }
    await sendMessage(trimmed);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
      return;
    }

    if (e.key === 'ArrowUp') {
      const isAtStart = textareaRef.current?.selectionStart === 0 && textareaRef.current?.selectionEnd === 0;
      if (text === '' || isAtStart) {
        e.preventDefault();
        const recalled = recallPromptUp(text);
        setText(recalled);
      }
      return;
    }

    if (e.key === 'ArrowDown') {
      const isAtEnd = textareaRef.current?.selectionStart === text.length;
      if (isAtEnd) {
        e.preventDefault();
        const recalled = recallPromptDown();
        setText(recalled);
      }
      return;
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files) {
      for (let i = 0; i < files.length; i++) {
        addAttachment(files[i]);
      }
    }
    e.target.value = '';
  };

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    for (let i = 0; i < items.length; i++) {
      if (items[i].kind === 'file') {
        const file = items[i].getAsFile();
        if (file) {
          e.preventDefault();
          addAttachment(file);
        }
      }
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      for (let i = 0; i < files.length; i++) {
        addAttachment(files[i]);
      }
    }
  };

  return (
    <div
      data-testid="prompt-input-container"
      onDragOver={(e) => {
        e.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
      className={`relative w-full max-w-4xl mx-auto rounded-2xl bg-zinc-900 border transition-all ${
        isDragging
          ? 'border-blue-500 bg-zinc-800/80 shadow-lg'
          : featureMode === 'task'
          ? 'border-blue-500/40 shadow-[0_0_20px_-8px_rgba(59,130,246,0.3)]'
          : 'border-zinc-800'
      }`}
      style={{ contain: 'layout' }}
    >
      {isIncognito && (
        <div
          data-testid="incognito-banner"
          className="mx-3 mt-2.5 px-3 py-1.5 bg-purple-950/40 border border-purple-500/30 rounded-xl flex items-center justify-between text-xs text-purple-200"
        >
          <div className="flex items-center gap-2">
            <span className="text-sm">🕶️</span>
            <span>
              Temporary chat enabled: messages are not saved in Google history or written to the local database.
            </span>
          </div>
          <button
            type="button"
            onClick={() => setIncognito(false)}
            className="text-[11px] text-purple-400 hover:text-purple-200 ml-2 font-medium"
          >
            ✕
          </button>
        </div>
      )}

      {pendingAttachments.length > 0 && (
        <AttachmentTray attachments={pendingAttachments} onRemove={removeAttachment} />
      )}

      <div className="px-3.5 pt-2.5 pb-1">
        <textarea
          ref={textareaRef}
          data-testid="prompt-input-textarea"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          placeholder={
            featureMode === 'task'
              ? 'Describe a task... (Task mode with 4 Argon — 60 FPS streaming without freezes)'
              : 'Ask Gemini... (Shift+Enter — new line, ↑ — history)'
          }
          rows={1}
          className="w-full bg-transparent text-zinc-100 placeholder-zinc-500 text-sm md:text-base resize-none focus:outline-none min-h-[40px] max-h-[240px] py-1.5 leading-normal"
        />
      </div>

      <div className="flex items-center justify-between gap-2 px-3 pb-2.5 pt-1">
        <div className="flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={handleFileChange}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            aria-label="Attach file"
            title="Attach files"
            className="p-2 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 rounded-xl transition-colors text-sm leading-none"
          >
            +
          </button>

          {/* Chat | Task toggle matching Gemini 4 chat-spark-toggle */}
          <div
            data-testid="chat-spark-toggle"
            className="flex items-center p-0.5 rounded-full bg-zinc-950/90 border border-zinc-800 text-xs"
          >
            <button
              type="button"
              data-testid="mode-chat-btn"
              onClick={() => setFeatureMode('chat')}
              className={`px-3 py-1 rounded-full transition-colors ${
                featureMode === 'chat'
                  ? 'bg-zinc-800 text-zinc-100 font-medium shadow-sm'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              Chat
            </button>
            <button
              type="button"
              data-testid="mode-task-btn"
              onClick={() => setFeatureMode('task')}
              className={`flex items-center gap-1 px-3 py-1 rounded-full transition-colors ${
                featureMode === 'task'
                  ? 'bg-blue-600/30 text-blue-200 border border-blue-500/40 font-medium shadow-sm'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <span>✦</span>
              <span>Task</span>
            </button>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Quick Reasoning Level selector */}
          <div
            data-testid="prompt-reasoning-selector"
            className="hidden sm:flex items-center gap-0.5 p-0.5 rounded-lg bg-zinc-950/80 border border-zinc-800/80 text-[11px]"
            title="Reasoning Level"
          >
            {REASONING_SHORT.map((r) => (
              <button
                key={r.value}
                type="button"
                onClick={() => setReasoningLevel(r.value)}
                className={`px-2 py-0.5 rounded-md transition-colors ${
                  reasoningLevel === r.value
                    ? 'bg-zinc-800 text-blue-300 font-medium'
                    : 'text-zinc-500 hover:text-zinc-300'
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>

          <span className="text-xs text-zinc-400 hidden md:inline px-1.5">
            {activeModelObj?.display_name || selectedModel}
          </span>

          {isGenerating ? (
            <button
              type="button"
              data-testid="stop-generation-button"
              onClick={abortGeneration}
              aria-label="Stop generating"
              className="p-2.5 bg-red-600 hover:bg-red-500 text-white rounded-xl transition-colors shadow-md text-xs leading-none"
            >
              ■
            </button>
          ) : (
            <button
              type="button"
              data-testid="send-prompt-button"
              onClick={handleSubmit}
              disabled={!text.trim() && pendingAttachments.length === 0}
              aria-label="Send message"
              className={`p-2.5 rounded-xl transition-all text-xs leading-none ${
                text.trim() || pendingAttachments.length > 0
                  ? 'bg-blue-600 hover:bg-blue-500 text-white shadow-md cursor-pointer'
                  : 'bg-zinc-800 text-zinc-600 cursor-not-allowed'
              }`}
            >
              ↑
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
