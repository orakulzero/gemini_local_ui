import React, { memo, useState, useCallback, useMemo, useRef, useEffect } from 'react';
import { Message } from '../types';
import { StreamingMarkdown } from './StreamingMarkdown';
import { BranchNav } from './BranchNav';
import { CodeBlock } from './CodeBlock';
import { CodeExecutionBlock } from './CodeExecutionBlock';
import { ToolApprovalCard } from './ToolApprovalCard';
import { ToolCallBlock } from './ToolCallBlock';
import { CitationPills } from './CitationPills';
import { ImageGallery } from './ImageGallery';
import { YouTubeCard } from './YouTubeCard';
import { useChatStore } from '../store/chatStore';
import * as api from '../services/api';

const MODIFY_OPTIONS = [
  { id: 'shorter', label: 'Shorter' },
  { id: 'longer', label: 'Longer' },
  { id: 'simpler', label: 'Simpler' },
  { id: 'casual', label: 'More casual' },
  { id: 'professional', label: 'More professional' },
] as const;

export interface MessageItemProps {
  message: Message;
  isStreaming?: boolean;
  onEdit?: (id: number, newContent: string) => void;
  onCopy?: (id: number) => void;
  onSelectBranch?: (id: number, branchIndex: number) => void;
}

const AVAILABLE_MODELS = [
  { id: 'gemini-4-carbon', name: '4 Carbon', desc: 'Most complex work' },
  { id: '95a4c0abcba24364', name: '4 Argon', desc: 'Complex tasks' },
  { id: 'e6fa609c3fa255c0', name: '3.1 Pro', desc: 'Reasoning' },
  { id: '56fdd199312815e2', name: '4 Hydrogen', desc: 'Fast execution' },
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
    const [showModifyMenu, setShowModifyMenu] = useState(false);
    const [selectedReasoning, setSelectedReasoning] = useState<number>(1);
    const [isModifying, setIsModifying] = useState(false);
    const [isDoubleChecking, setIsDoubleChecking] = useState(false);
    const [doubleCheckResult, setDoubleCheckResult] = useState<{
      trust_score?: number;
      verified_claims?: Array<{ claim: string; status: string; source_url: string }>;
    } | null>(null);
    const [isTtsLoading, setIsTtsLoading] = useState(false);
    const [isSpeaking, setIsSpeaking] = useState(false);
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const audioUrlRef = useRef<string | null>(null);
    const ttsSeqRef = useRef(0);
    const mountedRef = useRef(true);

    const stopAudioPlayback = useCallback(() => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.onended = null;
        audioRef.current.onerror = null;
        audioRef.current = null;
      }
      if (audioUrlRef.current) {
        URL.revokeObjectURL(audioUrlRef.current);
        audioUrlRef.current = null;
      }
      if (mountedRef.current) {
        setIsSpeaking(false);
        setIsTtsLoading(false);
      }
    }, []);

    useEffect(() => {
      mountedRef.current = true;
      return () => {
        mountedRef.current = false;
        ttsSeqRef.current += 1;
        stopAudioPlayback();
      };
    }, [stopAudioPlayback]);

    const handleToggleListen = useCallback(async () => {
      if (isSpeaking) {
        stopAudioPlayback();
        return;
      }
      if (isTtsLoading || !message.content.trim()) return;
      const seq = ++ttsSeqRef.current;
      setIsTtsLoading(true);
      try {
        const blob = await api.synthesizeSpeech(message.content);
        if (!mountedRef.current || ttsSeqRef.current !== seq) return;
        const url = URL.createObjectURL(blob);
        audioUrlRef.current = url;
        const audio = new Audio(url);
        audioRef.current = audio;
        audio.onended = () => stopAudioPlayback();
        audio.onerror = () => stopAudioPlayback();
        setIsSpeaking(true);
        setIsTtsLoading(false);
        await audio.play().catch((err) => {
          console.warn('Audio playback failed:', err);
          stopAudioPlayback();
        });
      } catch (err) {
        console.warn('TTS synthesis failed:', err);
        if (mountedRef.current && ttsSeqRef.current === seq) {
          setIsTtsLoading(false);
          setIsSpeaking(false);
        }
      }
    }, [isSpeaking, isTtsLoading, message.content, stopAudioPlayback]);

    const dedupedImages = useMemo(() => {
      const imgs = message.metadata?.images;
      if (!Array.isArray(imgs) || imgs.length === 0) return [];
      const contentStr = message.content || '';
      return imgs.filter((img: any) => {
        if (!img || !img.url) return false;
        if (contentStr.includes(img.url)) return false;
        if (img.original_url && contentStr.includes(img.original_url)) return false;
        return true;
      });
    }, [message.metadata?.images, message.content]);

    const regenerateWithModel = useChatStore((s) => s.regenerateWithModel);
    const switchBranch = useChatStore((s) => s.switchBranch);
    const updateAssistantMessageContent = useChatStore((s) => s.updateMessageContent);
    const clearPendingApproval = useChatStore((s) => s.clearPendingApproval);

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

    const handleModifyResponse = async (modifier: string) => {
      setShowModifyMenu(false);
      if (!message.content.trim() || isModifying) return;
      setIsModifying(true);
      try {
        const res = await api.modifyResponse({
          text: message.content,
          modifier,
          cid: message.metadata?.cid,
          rid: message.metadata?.rid,
          rcid: message.metadata?.rcid,
        });
        if (res.modified_text) {
          updateAssistantMessageContent(message.id, res.modified_text, {
            modified_by: modifier,
          });
        }
      } catch (err) {
        console.warn('Modify response failed:', err);
      } finally {
        setIsModifying(false);
      }
    };

    const handleDoubleCheck = async () => {
      if (!message.content.trim() || isDoubleChecking) return;
      setIsDoubleChecking(true);
      try {
        const res = await api.doubleCheckResponse({
          text: message.content,
          cid: message.metadata?.cid,
          rid: message.metadata?.rid,
          rcid: message.metadata?.rcid,
        });
        setDoubleCheckResult({
          trust_score: res.trust_score,
          verified_claims: res.verified_claims,
        });
        if (Array.isArray(res.citations) && res.citations.length > 0) {
          updateAssistantMessageContent(message.id, message.content, {
            citations: res.citations,
          });
        }
      } catch (err) {
        console.warn('Double-check failed:', err);
      } finally {
        setIsDoubleChecking(false);
      }
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
          setShowModifyMenu(false);
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
                            {([1, 4, 2] as const).map((lvl) => (
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
                                {lvl === 1 ? 'Low' : lvl === 4 ? 'Med' : 'High'}
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

        {message.attachments && message.attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2">
            {message.attachments.map((att, idx) => (
              <div
                key={`${att.filename}-${idx}`}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-zinc-800/80 border border-zinc-700/60 text-xs text-zinc-200"
              >
                {att.previewUrl && att.mime_type?.startsWith('image/') && (
                  <img
                    src={att.previewUrl}
                    alt={att.filename}
                    className="w-5 h-5 rounded object-cover"
                  />
                )}
                <span className="truncate max-w-[180px]">{att.filename}</span>
              </div>
            ))}
          </div>
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
            {message.metadata.pending_approval && (
              <ToolApprovalCard
                approval={message.metadata.pending_approval}
                onResolved={() => clearPendingApproval(message.id)}
              />
            )}
            {Array.isArray(message.metadata.tool_calls) &&
              message.metadata.tool_calls.length > 0 && (
                <ToolCallBlock toolCalls={message.metadata.tool_calls} />
              )}
            {Array.isArray(message.metadata.code_executions) &&
              message.metadata.code_executions.length > 0 && (
                <CodeExecutionBlock executions={message.metadata.code_executions} />
              )}
            {dedupedImages.length > 0 && (
              <ImageGallery images={dedupedImages} />
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

        {isAssistant && !isStreaming && !isEditing && message.content && (
          <div className="mt-2.5 pt-2 border-t border-zinc-800/50 flex flex-wrap items-center gap-2 text-[11px] text-zinc-400">
            <div className="relative">
              <button
                type="button"
                data-testid="modify-response-btn"
                disabled={isModifying}
                onClick={() => setShowModifyMenu((p) => !p)}
                className="px-2 py-1 rounded-lg hover:bg-zinc-800 hover:text-zinc-200 transition-colors disabled:opacity-50"
              >
                {isModifying ? 'Modifying...' : 'Modify response'}
              </button>
              {showModifyMenu && (
                <div
                  data-testid="modify-response-menu"
                  className="absolute left-0 bottom-full mb-1 w-44 p-1 rounded-xl bg-zinc-900 border border-zinc-700 shadow-xl z-30 space-y-0.5"
                >
                  {MODIFY_OPTIONS.map((opt) => (
                    <button
                      key={opt.id}
                      type="button"
                      data-testid={`modify-option-${opt.id}`}
                      onClick={() => handleModifyResponse(opt.id)}
                      className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-zinc-800 text-zinc-200"
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <button
              type="button"
              data-testid="double-check-btn"
              disabled={isDoubleChecking}
              onClick={handleDoubleCheck}
              className="px-2 py-1 rounded-lg hover:bg-zinc-800 hover:text-emerald-300 transition-colors disabled:opacity-50"
            >
              {isDoubleChecking ? 'Checking...' : 'G Double-check'}
            </button>

            <button
              type="button"
              data-testid="tts-listen-btn"
              disabled={isTtsLoading}
              onClick={handleToggleListen}
              className="px-2 py-1 rounded-lg hover:bg-zinc-800 hover:text-blue-300 transition-colors disabled:opacity-50"
            >
              {isTtsLoading ? 'Loading...' : isSpeaking ? 'Stop' : 'Listen'}
            </button>
          </div>
        )}

        {doubleCheckResult?.verified_claims && doubleCheckResult.verified_claims.length > 0 && (
          <div
            data-testid="double-check-results"
            className="mt-2 p-2.5 rounded-xl bg-emerald-950/20 border border-emerald-700/40 text-xs space-y-1.5"
          >
            <div className="text-[11px] font-semibold text-emerald-300">
              Fact-check trust score: {Math.round((doubleCheckResult.trust_score ?? 0.95) * 100)}%
            </div>
            {doubleCheckResult.verified_claims.slice(0, 3).map((vc, idx) => (
              <div key={idx} className="text-[11px] text-zinc-300 flex items-start gap-1.5">
                <span className="text-emerald-400 shrink-0">[{vc.status}]</span>
                <span className="truncate">{vc.claim}</span>
              </div>
            ))}
          </div>
        )}
      </article>
    );
  }
);

MessageItem.displayName = 'MessageItem';
