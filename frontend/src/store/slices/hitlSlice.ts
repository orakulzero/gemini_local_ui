import type { StateCreator } from 'zustand';
import { applyNodePatch, ChatState, HitlSlice } from './types';

function nextPendingId(map: Record<string, any>, currentId: string | null): string | null {
  if (currentId && map[currentId]) return currentId;
  const keys = Object.keys(map);
  return keys.length > 0 ? keys[0] : null;
}

export const createHitlSlice: StateCreator<ChatState, [], [], HitlSlice> = (set, get) => ({
  pendingApprovals: {},
  pendingElicitations: {},
  activeElicitationId: null,

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

  setPendingElicitationForNode: (nodeId, elicitation) => {
    set((s) => ({
      pendingElicitations: {
        ...s.pendingElicitations,
        [elicitation.elicitation_id]: { ...elicitation, nodeId },
      },
      activeElicitationId: elicitation.elicitation_id,
      ...applyNodePatch(s, nodeId, (m) => ({
        ...m,
        metadata: { ...(m.metadata || {}), pending_elicitation: elicitation },
      })),
    }));
  },

  clearPendingElicitation: (idOrNodeId) => {
    set((s) => {
      const nextElicits = { ...s.pendingElicitations };
      let targetNodeId: number | null = typeof idOrNodeId === 'number' ? idOrNodeId : null;

      if (typeof idOrNodeId === 'string') {
        const entry = nextElicits[idOrNodeId];
        if (entry) targetNodeId = entry.nodeId;
        delete nextElicits[idOrNodeId];
        if (targetNodeId === null) {
          const found = Object.values(s.nodesById).find(
            (m) => m.metadata?.pending_elicitation?.elicitation_id === idOrNodeId
          );
          if (found) targetNodeId = found.id;
        }
      } else {
        const msg = s.nodesById[idOrNodeId] || s.allMessagesMap[idOrNodeId];
        const msgEid = msg?.metadata?.pending_elicitation?.elicitation_id;
        for (const [eid, entry] of Object.entries(nextElicits)) {
          if (entry.nodeId === idOrNodeId || eid === msgEid) delete nextElicits[eid];
        }
      }

      const nodePatch =
        targetNodeId !== null
          ? applyNodePatch(s, targetNodeId, (m) =>
              m.metadata?.pending_elicitation
                ? { ...m, metadata: { ...m.metadata, pending_elicitation: null } }
                : m
            )
          : {};

      return {
        pendingElicitations: nextElicits,
        activeElicitationId: nextPendingId(nextElicits, s.activeElicitationId),
        ...nodePatch,
      };
    });
  },

  submitElicitationResponse: async (elicitationId, action, content) => {
    const res = await fetch('/api/chat/elicitations/' + encodeURIComponent(elicitationId), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action,
        content: action === 'accept' ? (content ?? {}) : null,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Elicitation response failed: HTTP ${res.status}`);
    }
    get().clearPendingElicitation(elicitationId);
  },
});
