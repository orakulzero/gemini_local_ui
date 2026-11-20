import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useChatStore } from '../store/chatStore';
import { useSessionStore } from '../store/sessionStore';
import { useWorkspaceStore } from '../store/workspaceStore';
import { AttachmentTray } from './AttachmentTray';

const REASONING_SHORT: Array<{ value: 1 | 4 | 2; label: string }> = [
  { value: 1, label: 'Low' },
  { value: 4, label: 'Medium' },
  { value: 2, label: 'High' },
];

const WORKSPACE_EXTENSIONS = [
  { id: 'youtube', name: 'YouTube', tag: '@YouTube', desc: 'Search & analyze videos' },
  { id: 'gmail', name: 'Gmail', tag: '@Gmail', desc: 'Read & summarize emails' },
  { id: 'google_drive', name: 'Google Drive', tag: '@Google Drive', desc: 'Files, PDFs & folders' },
  { id: 'google_docs', name: 'Google Docs', tag: '@Google Docs', desc: 'Documents' },
  { id: 'google_maps', name: 'Google Maps', tag: '@Google Maps', desc: 'Places & routes' },
  { id: 'google_flights', name: 'Google Flights', tag: '@Google Flights', desc: 'Flight schedules & fares' },
  { id: 'google_hotels', name: 'Google Hotels', tag: '@Google Hotels', desc: 'Hotels & availability' },
  { id: 'google_workspace', name: 'Google Workspace', tag: '@Google Workspace', desc: 'Gmail, Drive & Docs' },
];

export const PromptInput: React.FC = () => {
  const [text, setText] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const [showExtMenu, setShowExtMenu] = useState(false);
  const [showSkillsMenu, setShowSkillsMenu] = useState(false);
  const [skillFilter, setSkillFilter] = useState('');
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
  const useRag = useChatStore((s) => s.useRag);
  const setUseRag = useChatStore((s) => s.setUseRag);

  const featureMode = useSessionStore((s) => s.featureMode);
  const setFeatureMode = useSessionStore((s) => s.setFeatureMode);
  const reasoningLevel = useSessionStore((s) => s.reasoningLevel);
  const setReasoningLevel = useSessionStore((s) => s.setReasoningLevel);
  const selectedModel = useSessionStore((s) => s.selectedModel);
  const models = useSessionStore((s) => s.models);
  const activeModelObj = models.find((m) => m.id === selectedModel);

  const promptsEnabled = useWorkspaceStore((s) => s.promptsEnabled);
  const activePromptId = useWorkspaceStore((s) => s.activePromptId);
  const prompts = useWorkspaceStore((s) => s.prompts);
  const useGlobalPromptForChat = useWorkspaceStore((s) => s.useGlobalPromptForChat);
  const enabledSkillIds = useWorkspaceStore((s) => s.enabledSkillIds);
  const skills = useWorkspaceStore((s) => s.skills);
  const connectors = useWorkspaceStore((s) => s.connectors);
  const openHub = useWorkspaceStore((s) => s.openHub);
  const activatePrompt = useWorkspaceStore((s) => s.activatePrompt);
  const toggleSkill = useWorkspaceStore((s) => s.toggleSkill);
  const toggleConnector = useWorkspaceStore((s) => s.toggleConnector);

  const activePrompt = useMemo(
    () =>
      promptsEnabled && useGlobalPromptForChat
        ? prompts.find((p) => p.is_active || p.id === activePromptId)
        : undefined,
    [promptsEnabled, useGlobalPromptForChat, prompts, activePromptId]
  );
  const activeSkills = useMemo(
    () => skills.filter((sk) => enabledSkillIds.includes(sk.id)),
    [skills, enabledSkillIds]
  );
  const enabledConnectors = useMemo(
    () => connectors.filter((c) => c.enabled),
    [connectors]
  );
  const filteredSkills = useMemo(() => {
    const q = skillFilter.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter((sk) =>
      `${sk.name} ${sk.id} ${sk.description || ''}`.toLowerCase().includes(q)
    );
  }, [skills, skillFilter]);

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

      {(activePrompt || activeSkills.length > 0 || enabledConnectors.length > 0) && (
        <div
          data-testid="active-workspace-chips"
          className="mx-3 mt-2 flex flex-wrap items-center gap-1.5 text-[11px]"
        >
          {activePrompt && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-950/50 border border-blue-700/50 text-blue-200">
              <button type="button" onClick={() => openHub('prompts')} className="hover:underline">
                Prompt: {activePrompt.name}
              </button>
              <button
                type="button"
                aria-label={`Deactivate ${activePrompt.name}`}
                onClick={() => activatePrompt(null)}
                className="text-blue-400 hover:text-white ml-0.5"
              >
                ✕
              </button>
            </span>
          )}
          {activeSkills.map((sk) => (
            <span
              key={sk.id}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-indigo-950/50 border border-indigo-700/50 text-indigo-200"
            >
              <button type="button" onClick={() => openHub('skills')} className="hover:underline">
                /{sk.id}
              </button>
              <button
                type="button"
                aria-label={`Deactivate ${sk.name}`}
                onClick={() => toggleSkill(sk.id)}
                className="text-indigo-400 hover:text-white ml-0.5"
              >
                ✕
              </button>
            </span>
          ))}
          {enabledConnectors.map((conn) => (
            <span
              key={conn.id}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-cyan-950/50 border border-cyan-700/50 text-cyan-200"
            >
              <button type="button" onClick={() => openHub('connectors')} className="hover:underline">
                MCP: {conn.name}
              </button>
              <button
                type="button"
                aria-label={`Disable ${conn.name}`}
                onClick={() => toggleConnector(conn.id, false)}
                className="text-cyan-400 hover:text-white ml-0.5"
              >
                ✕
              </button>
            </span>
          ))}
        </div>
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
            featureMode === 'deep-research'
              ? 'Research topic... (2-Phase Deep Research with grounded citations)'
              : featureMode === 'task'
              ? 'Describe a task... (Task mode with 4 Argon / 4 Carbon)'
              : 'Ask Gemini... (Shift+Enter — new line, ↑ — history, @Extension, /Skill)'
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

          <div className="relative">
            <button
              type="button"
              data-testid="extensions-picker-btn"
              onClick={() => {
                setShowExtMenu((p) => !p);
                setShowSkillsMenu(false);
              }}
              aria-label="Workspace Extensions"
              title="Insert Google Workspace @Extension"
              className={`px-2 py-1.5 rounded-xl transition-colors text-xs font-mono leading-none ${
                showExtMenu
                  ? 'bg-blue-600/20 text-blue-300 border border-blue-500/40'
                  : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
              }`}
            >
              @
            </button>

            {showExtMenu && (
              <>
                <div
                  className="fixed inset-0 z-30"
                  onClick={() => setShowExtMenu(false)}
                />
                <div
                  data-testid="extensions-menu"
                  className="absolute left-0 bottom-full mb-2 w-64 p-1.5 rounded-xl bg-zinc-900 border border-zinc-800 shadow-2xl z-40 text-xs space-y-0.5"
                >
                  <div className="px-2 py-1 text-[10px] font-semibold uppercase text-zinc-500">
                    Workspace Extensions
                  </div>
                  {WORKSPACE_EXTENSIONS.map((ext) => (
                    <button
                      key={ext.id}
                      type="button"
                      data-testid={`ext-option-${ext.id}`}
                      onClick={() => {
                        setText((prev) =>
                          prev.includes(ext.tag)
                            ? prev
                            : prev.trim()
                            ? `${ext.tag} ${prev}`
                            : `${ext.tag} `
                        );
                        setShowExtMenu(false);
                        textareaRef.current?.focus();
                      }}
                      className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-zinc-800 flex items-center justify-between gap-2 text-zinc-200 transition-colors"
                    >
                      <span className="font-medium text-blue-300">{ext.tag}</span>
                      <span className="text-[10px] text-zinc-400 truncate">{ext.desc}</span>
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>

          <div className="relative">
            <button
              type="button"
              data-testid="skills-picker-btn"
              onClick={() => {
                setShowSkillsMenu((p) => !p);
                setShowExtMenu(false);
              }}
              aria-label="Workspace Skills"
              title="Insert /skill or toggle active skill"
              className={`px-2 py-1.5 rounded-xl transition-colors text-xs font-mono leading-none ${
                showSkillsMenu || activeSkills.length > 0
                  ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/40'
                  : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
              }`}
            >
              /
            </button>

            {showSkillsMenu && (
              <>
                <div
                  className="fixed inset-0 z-30"
                  onClick={() => setShowSkillsMenu(false)}
                />
                <div
                  data-testid="skills-menu"
                  className="absolute left-0 bottom-full mb-2 w-72 p-1.5 rounded-xl bg-zinc-900 border border-zinc-800 shadow-2xl z-40 text-xs space-y-1"
                >
                  <input
                    type="text"
                    value={skillFilter}
                    onChange={(e) => setSkillFilter(e.target.value)}
                    placeholder="Filter skills..."
                    className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-[11px] text-zinc-100 focus:outline-none focus:border-indigo-500"
                  />
                  <div className="max-h-56 overflow-y-auto space-y-0.5">
                    {filteredSkills.length === 0 ? (
                      <div className="px-2 py-2 text-center text-zinc-500 text-[11px]">No matching skills</div>
                    ) : (
                      filteredSkills.map((sk) => {
                        const slug = sk.slug || sk.id;
                        const active = enabledSkillIds.includes(sk.id);
                        return (
                          <div
                            key={sk.id}
                            className="flex items-center justify-between gap-1.5 px-2 py-1 rounded-lg hover:bg-zinc-800"
                          >
                            <button
                              type="button"
                              onClick={() => {
                                const tag = `/${slug}`;
                                setText((prev) => (prev.trim() ? `${tag} ${prev}` : `${tag} `));
                                setShowSkillsMenu(false);
                                textareaRef.current?.focus();
                              }}
                              className="flex-1 text-left min-w-0"
                            >
                              <div className="font-mono text-indigo-300 truncate">/{slug}</div>
                              {sk.description && (
                                <div className="text-[10px] text-zinc-400 truncate">{sk.description}</div>
                              )}
                            </button>
                            <button
                              type="button"
                              onClick={() => toggleSkill(sk.id)}
                              className={`px-1.5 py-0.5 rounded text-[10px] shrink-0 ${
                                active ? 'bg-indigo-600 text-white' : 'bg-zinc-800 text-zinc-400 hover:text-zinc-200'
                              }`}
                            >
                              {active ? 'On' : 'Pin'}
                            </button>
                          </div>
                        );
                      })
                    )}
                  </div>
                </div>
              </>
            )}
          </div>

          {/* Chat | Task | Research toggle matching Gemini Web */}
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
            <button
              type="button"
              data-testid="mode-research-btn"
              onClick={() => setFeatureMode('deep-research')}
              className={`flex items-center gap-1 px-3 py-1 rounded-full transition-colors ${
                featureMode === 'deep-research'
                  ? 'bg-emerald-600/30 text-emerald-200 border border-emerald-500/40 font-medium shadow-sm'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <span>Research</span>
            </button>
          </div>

          <button
            type="button"
            data-testid="rag-toggle-btn"
            aria-pressed={useRag}
            onClick={() => setUseRag(!useRag)}
            title="Augment prompt with local hybrid RAG memory"
            className={`px-2.5 py-1 rounded-full border text-xs font-medium transition-colors ${
              useRag
                ? 'bg-emerald-600/25 text-emerald-200 border-emerald-500/50 shadow-sm'
                : 'bg-zinc-950/80 text-zinc-400 border-zinc-800 hover:text-zinc-200 hover:bg-zinc-800'
            }`}
          >
            RAG
          </button>
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
