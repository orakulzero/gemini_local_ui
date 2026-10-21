import React, { memo, useState, useCallback } from 'react';
import { Message } from '../types';
import { StreamingMarkdown } from './StreamingMarkdown';
import { BranchNav } from './BranchNav';
import { CodeBlock } from './CodeBlock';
import { CitationPills } from './CitationPills';
import { ImageGallery } from './ImageGallery';
import { YouTubeCard } from './YouTubeCard';
import { useChatStore } from '../store/chatStore';

export interface MessageItemProps {
  message: Message;
  isStreaming?: boolean;
  onEdit?: (id: number, newContent: string) => void;
  onCopy?: (id: number) => void;
  onSelectBranch?: (id: number, branchIndex: number) => void;
}

const AVAILABLE_MODELS = [
  { id: '95a4c0abcba24364', name: '4 Argon', desc: 'Complex tasks' },
  { id: 'e6fa609c3fa255c0', name: '3.1 Pro', desc: 'Reasoning' },
  { id: '56fdd199312815e2', name: '3.8 Flash', desc: 'Versatile' },
  { id: '8c46e95b1a07cecc', name: '3.5 Flash-Lite', desc: 'Fast responses' },
];

export const MessageItem: React.FC<MessageItemProps> = memo(
  ({ message, isStreaming = false, onEdit, onCopy, onSelectBranch }) => {
    const isAssistant = message.role === 'assistant';
    const [copied, setCopied] = useState(false);
    const [isEditing, setIsEditing] = useState(false);
    const [isHovered, setIsHovered] = useState(false);
    const [editDraft, setEditDraft] = useState(message.content);
    const [showRegenMenu, setShowRegenMenu] = useState(false);
    const [selectedReasoning, setSelectedReasoning] = useState<number>(1);

    const regenerateWithModel = useChatStore((s) => s.regenerateWithModel);
    const switchBranch = useChatStore((s) => s.switchBranch);

    const handleCopy = useCallback(() => {
      navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      onCopy?.(message.id);
    }, [message.content, message.id, onCopy]);

    const handleEditSubmit = useCallback(() => {
      if (editDraft.trim() && editDraft !== message.content) {
        onEdit?.(message.id, editDraft);
      }
      setIsEditing(false);
    }, [editDraft, message.content, message.id, onEdit]);

    const handleRegenerate = (modelId: string) => {
      setShowRegenMenu(false);
      regenerateWithModel(message.id, modelId, selectedReasoning);
    };

    const thoughtContent = message.thoughts || (message as any).thought;

    const siblingCount = message.sibling_count ?? message.branchInfo?.total ?? 1;
    const siblingIndex = message.sibling_index ?? message.branchInfo?.current ?? 1;
    const siblingIds = message.sibling_ids ?? message.branchInfo?.sibling_ids ?? [];

    const roleLabel = isAssistant
      ? message.model
        ? `Gemini (${message.model})`
        : 'Gemini'
      : 'You';
    const timeLabel = new Date(message.created_at || Date.now()).toLocaleTimeString();

    return (
      <article
        data-testid="message-item"
        data-role={message.role}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => {
          setIsHovered(false);
          setShowRegenMenu(false);
        }}
        className={`msg-row relative flex flex-col justify-center min-h-[76px] px-4 py-3.5 border-b border-zinc-800/40 ${
          isAssistant ? 'bg-zinc-900/30' : 'bg-transparent'
        }`}
      >
        <header
          className={`flex items-center justify-between mb-1.5 text-xs select-none ${
            isAssistant ? 'text-blue-400' : 'text-emerald-400'
          }`}
        >
          <span>
            {roleLabel} • {timeLabel}
          </span>

          <div className="flex items-center gap-2 text-zinc-400">
            {(isHovered || copied || siblingCount > 1 || showRegenMenu) && (
              <>
                <button
                  type="button"
                  onClick={handleCopy}
                  className="hover:text-zinc-200 transition-colors"
                >
                  {copied ? 'Copied!' : 'Copy'}
                </button>

                {!isAssistant && !isEditing && (
                  <button
                    type="button"
                    onClick={() => setIsEditing(true)}
                    className="hover:text-zinc-200 transition-colors"
                  >
                    Edit
                  </button>
                )}

                {isAssistant && !isStreaming && (
                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => setShowRegenMenu(!showRegenMenu)}
                      className="hover:text-blue-300 text-zinc-400 flex items-center gap-1 transition-colors"
                      title="Regenerate with another model"
                    >
                      <span>↻</span>
                      <span className="text-[11px]">Switch model</span>
                    </button>

                    {showRegenMenu && (
                      <div
                        className="absolute right-0 top-full mt-1.5 z-30 w-64 bg-zinc-900 border border-zinc-700/80 rounded-xl shadow-xl p-2 text-zinc-200 space-y-2 animate-in fade-in duration-150"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <div className="text-[11px] font-semibold text-zinc-400 px-1">
                          Regenerate with model:
                        </div>

                        {/* Reasoning level toggle */}
                        <div className="flex items-center justify-between px-1 py-1 bg-zinc-800/60 rounded-lg text-[10px]">
                          <span className="text-zinc-400">Thinking:</span>
                          <div className="flex gap-1">
                            {([1, 2, 4] as const).map((lvl) => (
                              <button
                                key={lvl}
                                type="button"
                                onClick={() => setSelectedReasoning(lvl)}
                                className={`px-1.5 py-0.5 rounded ${
                                  selectedReasoning === lvl
                                    ? 'bg-blue-600 text-white font-medium'
                                    : 'text-zinc-400 hover:text-zinc-200'
                                }`}
                              >
                                {lvl === 1 ? 'Low' : lvl === 2 ? 'Med' : 'High'}
                              </button>
                            ))}
                          </div>
                        </div>

                        <div className="space-y-0.5">
                          {AVAILABLE_MODELS.map((m) => (
                            <button
                              key={m.id}
                              type="button"
                              onClick={() => handleRegenerate(m.id)}
                              className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-zinc-800 flex items-center justify-between text-xs transition"
                            >
                              <span className="font-medium text-zinc-100">{m.name}</span>
                              <span className="text-[10px] text-zinc-400">{m.desc}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {siblingCount > 1 && (
                  <BranchNav
                    messageId={message.id}
                    siblingIndex={siblingIndex}
                    siblingCount={siblingCount}
                    siblingIds={siblingIds}
                    onSwitchBranch={(targetId) => {
                      if (onSelectBranch) {
                        onSelectBranch(targetId, 0);
                      } else {
                        switchBranch(targetId);
                      }
                    }}
                  />
                )}
              </>
            )}
          </div>
        </header>

        {thoughtContent && (
          <details className="mb-2.5 px-3 py-2 rounded-lg bg-zinc-900/80 border border-zinc-800 text-xs text-zinc-400 whitespace-pre-wrap font-mono">
            <summary className="cursor-pointer font-sans font-medium hover:text-zinc-300 select-none mb-1">
              ▸ Thinking Process
            </summary>
            {thoughtContent}
          </details>
        )}

        {isEditing ? (
          <div className="mt-1">
            <textarea
              value={editDraft}
              onChange={(e) => setEditDraft(e.target.value)}
              className="w-full bg-zinc-900 text-zinc-100 border border-zinc-700 rounded-lg p-2.5 text-sm focus:outline-none focus:border-blue-500"
              rows={3}
            />
            <div className="flex gap-2 mt-2">
              <button
                type="button"
                onClick={handleEditSubmit}
                className="px-3 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded text-xs font-medium"
              >
                Save & Submit
              </button>
              <button
                type="button"
                onClick={() => setIsEditing(false)}
                className="px-3 py-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded text-xs"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <StreamingMarkdown content={message.content} isStreaming={isStreaming} />
        )}

        {/* Media & Citations for Assistant Responses */}
        {isAssistant && message.metadata && (
          <>
            {message.metadata.citations && (
              <CitationPills citations={message.metadata.citations} />
            )}
            {message.metadata.images && (
              <ImageGallery images={message.metadata.images} />
            )}
            {message.metadata.youtube && (
              <YouTubeCard videos={message.metadata.youtube} />
            )}
          </>
        )}

        {message.codeBlocks &&
          message.codeBlocks.map((cb, idx) => (
            <CodeBlock key={idx} language={cb.language} code={cb.code} />
          ))}
      </article>
    );
  }
);

MessageItem.displayName = 'MessageItem';
