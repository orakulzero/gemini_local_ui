import type { StateCreator } from 'zustand';
import { applyNodePatch, ChatState, HitlSlice } from './types';

export const createHitlSlice: StateCreator<ChatState, [], [], HitlSlice> = (set, get) => ({
  pendingApprovals: {},

  setPendingApprovalForNode: (nodeId, approval) => {
    set((s) => ({
      pendingApprovals: {
        ...s.pendingApprovals,
        [approval.approval_id]: { ...approval, nodeId },
      },
      ...applyNodePatch(s, nodeId, (m) => ({
        ...m,
        metadata: { ...(m.metadata || {}), pending_approval: approval },
      })),
    }));
  },

  clearPendingApproval: (messageId) => {
    set((s) => {
      const nextApprovals = { ...s.pendingApprovals };
      const msg = s.nodesById[messageId] || s.allMessagesMap[messageId];
      const msgApprovalId = msg?.metadata?.pending_approval?.approval_id;
      for (const [aid, entry] of Object.entries(nextApprovals)) {
        if (entry.nodeId === messageId || aid === msgApprovalId) {
          delete nextApprovals[aid];
        }
      }
      return {
        pendingApprovals: nextApprovals,
        ...applyNodePatch(s, messageId, (m) =>
          m.metadata?.pending_approval
            ? { ...m, metadata: { ...m.metadata, pending_approval: null } }
            : m
        ),
      };
    });
  },

  submitToolApprovalDecision: async (approvalId, decision) => {
    const res = await fetch(`/api/chat/tool-approvals/${encodeURIComponent(approvalId)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Approval failed: HTTP ${res.status}`);
    }
    const entry = get().pendingApprovals[approvalId];
    if (entry) {
      get().clearPendingApproval(entry.nodeId);
    } else {
      set((s) => {
        const nextApprovals = { ...s.pendingApprovals };
        delete nextApprovals[approvalId];
        return { pendingApprovals: nextApprovals };
      });
    }
  },
});
