import type { ChatAttachment, ConversationSummary, Message, ToolApprovalRequest } from '../../types';

export interface DagSlice {
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  activeLeafMessageId: number | null;
  activeThread: Message[];
  allMessagesMap: Record<number, Message>;
  nodesById: Record<number, Message>;
  childrenByParentId: Record<string, number[]>;
  selectedChildByParent: Record<string, number>;

  loadConversations: () => Promise<void>;
  syncRemoteConversations: () => Promise<void>;
  selectConversation: (id: string, leafId?: number) => Promise<void>;
  createConversation: (title?: string) => Promise<string>;
  deleteConversation: (id: string, remote?: boolean) => Promise<void>;
  togglePinConversation: (id: string) => Promise<void>;
  renameConversation: (id: string, newTitle: string) => Promise<void>;
  switchBranch: (targetMessageId: number) => Promise<void>;
  updateMessageContent: (messageId: number, content: string, extraMeta?: any) => void;
  setFixtureMessages: (messages: Message[]) => void;
  upsertDagNode: (node: Message, selectInBranch?: boolean) => void;
  rekeyDagNode: (oldId: number, newId: number) => void;
}

export interface StreamSlice {
  isGenerating: boolean;
  streamingThought: string;
  streamingText: string;
  streamingAssistantId: number | null;
  activeNodeId: number | null;
  activeAbortController: AbortController | null;

  sendMessage: (prompt: string) => Promise<void>;
  regenerateWithModel: (messageId: number, model: string, reasoningLevel?: number) => Promise<void>;
  editMessageAndBranch: (messageId: number, newContent: string) => Promise<void>;
  abortGeneration: () => void;
}

export type PendingApprovalEntry = ToolApprovalRequest & { nodeId: number };

export interface HitlSlice {
  pendingApprovals: Record<string, PendingApprovalEntry>;

  setPendingApprovalForNode: (nodeId: number, approval: ToolApprovalRequest) => void;
  clearPendingApproval: (messageId: number) => void;
  submitToolApprovalDecision: (
    approvalId: string,
    decision: 'allow_once' | 'allow_session' | 'deny'
  ) => Promise<void>;
}

export interface UiSlice {
  isIncognito: boolean;
  isPinnedToBottom: boolean;
  promptHistory: string[];
  promptHistoryIndex: number;
  draftPrompt: string;
  injectedContext: string;
  pendingAttachments: ChatAttachment[];
  error: string | null;

  setIncognito: (incognito: boolean) => void;
  setIsPinnedToBottom: (pinned: boolean) => void;
  recallPromptUp: (currentText: string) => string;
  recallPromptDown: () => string;
  setDraftPrompt: (draft: string) => void;
  injectContext: (context: string) => void;
  addAttachment: (file: File) => void;
  removeAttachment: (index: number) => void;
  clearAttachments: () => void;
}

export type ChatState = DagSlice & StreamSlice & HitlSlice & UiSlice;

export function parentKeyOf(parentId: number | null | undefined): string {
  return parentId === null || parentId === undefined ? 'root' : String(parentId);
}

export function applyNodePatch(
  s: ChatState,
  nodeId: number,
  updater: (msg: Message) => Message
): Partial<ChatState> {
  const existing = s.nodesById[nodeId] || s.allMessagesMap[nodeId] || s.activeThread.find((m) => m.id === nodeId);
  if (!existing) return {};
  const updated = updater(existing);
  const nextNodes = { ...s.nodesById, [nodeId]: updated };
  const nextMap = { ...s.allMessagesMap, [nodeId]: updated };
  const nextThread = s.activeThread.map((m) => (m.id === nodeId ? updated : m));
  return { nodesById: nextNodes, allMessagesMap: nextMap, activeThread: nextThread };
}
