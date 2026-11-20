import type { StateCreator } from 'zustand';
import type { Message } from '../../types';
import * as api from '../../services/api';
import { queryClient, queryKeys } from '../../api/queryClient';
import { useSessionStore } from '../sessionStore';
import { applyNodePatch, ChatState, DagSlice, parentKeyOf } from './types';

export function computeActiveBranch(
  nodesById: Record<number, Message>,
  childrenByParentId: Record<string, number[]>,
  selectedChildByParent: Record<string, number>
): Message[] {
  const branch: Message[] = [];
  const visited = new Set<number>();
  let parentKey = 'root';

  while (true) {
    const children = childrenByParentId[parentKey];
    if (!children || children.length === 0) break;

    const selectedId = selectedChildByParent[parentKey];
    const nextId =
      selectedId !== undefined && children.includes(selectedId) && nodesById[selectedId]
        ? selectedId
        : children.find((id) => Boolean(nodesById[id]));

    if (nextId === undefined || visited.has(nextId)) break;
    const node = nodesById[nextId];
    if (!node) break;

    visited.add(nextId);
    branch.push(node);
    parentKey = String(nextId);
  }
  return branch;
}

function buildDagMaps(allMessages: Message[], activeThread: Message[]) {
  const nodesById: Record<number, Message> = {};
  const allMessagesMap: Record<number, Message> = {};
  const childrenByParentId: Record<string, number[]> = {};
  const selectedChildByParent: Record<string, number> = {};

  for (const m of [...allMessages, ...activeThread]) {
    nodesById[m.id] = m;
    allMessagesMap[m.id] = m;
  }
  for (const m of Object.values(nodesById)) {
    const pKey = parentKeyOf(m.parent_id);
    if (!childrenByParentId[pKey]) childrenByParentId[pKey] = [];
    if (!childrenByParentId[pKey].includes(m.id)) childrenByParentId[pKey].push(m.id);
  }
  for (const m of activeThread) {
    selectedChildByParent[parentKeyOf(m.parent_id)] = m.id;
  }
  return { nodesById, allMessagesMap, childrenByParentId, selectedChildByParent };
}

export const createDagSlice: StateCreator<ChatState, [], [], DagSlice> = (set, get) => ({
  conversations: [],
  activeConversationId: null,
  activeLeafMessageId: null,
  activeThread: [],
  allMessagesMap: {},
  nodesById: {},
  childrenByParentId: {},
  selectedChildByParent: {},

  loadConversations: async () => {
    try {
      const convs = await api.fetchConversations();
      set({ conversations: convs });
      queryClient.setQueryData(queryKeys.conversations, convs);
    } catch (err: any) {
      set({ error: err.message });
    }
  },

  syncRemoteConversations: async () => {
    try {
      await api.triggerRemoteSync();
      await get().loadConversations();
    } catch (err: any) {
      set({ error: err.message });
    }
  },

  selectConversation: async (id, leafId) => {
    try {
      const data = await api.fetchConversationDetail(id, leafId);
      const thread = data.active_thread || [];
      const maps = buildDagMaps(Array.isArray(data.all_messages) ? data.all_messages : [], thread);
      set({
        activeConversationId: id,
        activeLeafMessageId: data.active_leaf_message_id || null,
        activeThread: thread,
        ...maps,
        error: null,
        isPinnedToBottom: true,
      });
      queryClient.setQueryData(queryKeys.conversationDetail(id, leafId ?? null), data);
    } catch (err: any) {
      set({ error: err.message });
    }
  },

  createConversation: async (title = 'New Conversation') => {
    const { selectedModel, selectedGemId } = useSessionStore.getState();
    const conv = await api.createConversation(title, selectedModel, selectedGemId);
    await get().loadConversations();
    await get().selectConversation(conv.id);
    return conv.id;
  },

  deleteConversation: async (id, remote = false) => {
    await api.deleteConversation(id, remote);
    const { activeConversationId, conversations } = get();
    if (activeConversationId === id) {
      const remaining = conversations.filter((c) => c.id !== id);
      if (remaining.length > 0) await get().selectConversation(remaining[0].id);
      else {
        set({
          activeConversationId: null,
          activeLeafMessageId: null,
          activeThread: [],
          allMessagesMap: {},
          nodesById: {},
          childrenByParentId: {},
          selectedChildByParent: {},
        });
      }
    }
    await get().loadConversations();
  },

  togglePinConversation: async (id) => {
    const conv = get().conversations.find((c) => c.id === id);
    if (!conv) return;
    const newPinned = !conv.is_pinned;
    set((s) => ({
      conversations: s.conversations.map((c) => (c.id === id ? { ...c, is_pinned: newPinned } : c)),
    }));
    try {
      await api.updateConversation(id, { is_pinned: newPinned });
      await get().loadConversations();
    } catch (err: any) {
      set({ error: err.message });
      await get().loadConversations();
    }
  },

  renameConversation: async (id, newTitle) => {
    if (!newTitle.trim()) return;
    set((s) => ({
      conversations: s.conversations.map((c) => (c.id === id ? { ...c, title: newTitle.trim() } : c)),
    }));
    try {
      await api.updateConversation(id, { title: newTitle.trim() });
      await get().loadConversations();
    } catch (err: any) {
      set({ error: err.message });
      await get().loadConversations();
    }
  },

  switchBranch: async (targetMessageId) => {
    const { activeConversationId, nodesById, childrenByParentId, selectedChildByParent } = get();
    let switchedLocally = false;

    if (nodesById[targetMessageId]) {
      const nextSelected = { ...selectedChildByParent };
      let curId: number | null | undefined = targetMessageId;
      const visitedUp = new Set<number>();
      while (curId != null && !visitedUp.has(curId)) {
        visitedUp.add(curId);
        const node: Message | undefined = nodesById[curId];
        if (!node) break;
        nextSelected[parentKeyOf(node.parent_id)] = node.id;
        curId = node.parent_id;
      }
      let downId: number | undefined = targetMessageId;
      const visitedDown = new Set<number>();
      while (downId !== undefined && !visitedDown.has(downId)) {
        visitedDown.add(downId);
        const children: number[] | undefined = childrenByParentId[String(downId)];
        if (!children || children.length === 0) break;
        const chosen: number =
          nextSelected[String(downId)] !== undefined && children.includes(nextSelected[String(downId)])
            ? nextSelected[String(downId)]
            : children[0];
        nextSelected[String(downId)] = chosen;
        downId = chosen;
      }
      const nextThread = computeActiveBranch(nodesById, childrenByParentId, nextSelected);
      if (nextThread.length > 0) {
        switchedLocally = true;
        set({
          selectedChildByParent: nextSelected,
          activeThread: nextThread,
          activeLeafMessageId: nextThread[nextThread.length - 1].id,
          isPinnedToBottom: true,
        });
      }
    }

    if (!activeConversationId || activeConversationId.startsWith('incognito_')) return;
    try {
      const data = await api.switchBranch(activeConversationId, targetMessageId);
      const nextThread: Message[] = data.active_thread || [];
      if (nextThread.length === 0) return;
      set((s) => {
        const nextNodes = { ...s.nodesById };
        const nextMap = { ...s.allMessagesMap };
        const nextChildren = { ...s.childrenByParentId };
        const nextSelected = { ...s.selectedChildByParent };
        for (const m of nextThread) {
          nextNodes[m.id] = m;
          nextMap[m.id] = m;
          const pKey = parentKeyOf(m.parent_id);
          const list = nextChildren[pKey] ? [...nextChildren[pKey]] : [];
          if (!list.includes(m.id)) list.push(m.id);
          nextChildren[pKey] = list;
          nextSelected[pKey] = m.id;
        }
        return {
          activeLeafMessageId: data.active_leaf_message_id,
          activeThread: nextThread,
          nodesById: nextNodes,
          allMessagesMap: nextMap,
          childrenByParentId: nextChildren,
          selectedChildByParent: nextSelected,
          isPinnedToBottom: true,
        };
      });
    } catch (err: any) {
      if (!switchedLocally) set({ error: err.message });
    }
  },

  updateMessageContent: (messageId, content, extraMeta) => {
    set((s) =>
      applyNodePatch(s, messageId, (m) => ({
        ...m,
        content,
        metadata: extraMeta ? { ...(m.metadata || {}), ...extraMeta } : m.metadata,
      }))
    );
  },

  setFixtureMessages: (messages) => {
    const maps = buildDagMaps(messages, messages);
    set({
      activeThread: messages,
      ...maps,
      activeLeafMessageId: messages.length > 0 ? messages[messages.length - 1].id : null,
      isPinnedToBottom: true,
    });
  },

  upsertDagNode: (node, selectInBranch = true) => {
    set((s) => {
      const pKey = parentKeyOf(node.parent_id);
      const nextNodes = { ...s.nodesById, [node.id]: node };
      const nextMap = { ...s.allMessagesMap, [node.id]: node };
      const prevList = s.childrenByParentId[pKey] || [];
      const nextChildren = {
        ...s.childrenByParentId,
        [pKey]: prevList.includes(node.id) ? prevList : [...prevList, node.id],
      };
      const nextSelected = selectInBranch
        ? { ...s.selectedChildByParent, [pKey]: node.id }
        : s.selectedChildByParent;
      const computed = computeActiveBranch(nextNodes, nextChildren, nextSelected);
      const hasNodeInComputed = computed.some((m) => m.id === node.id);
      const nextThread = hasNodeInComputed
        ? computed
        : s.activeThread.some((m) => m.id === node.id)
          ? s.activeThread.map((m) => (m.id === node.id ? node : m))
          : [...s.activeThread, node];
      return {
        nodesById: nextNodes,
        allMessagesMap: nextMap,
        childrenByParentId: nextChildren,
        selectedChildByParent: nextSelected,
        activeThread: nextThread,
      };
    });
  },

  rekeyDagNode: (oldId, newId) => {
    if (oldId === newId) return;
    set((s) => {
      const oldNode = s.nodesById[oldId] || s.allMessagesMap[oldId];
      const nextNodes = { ...s.nodesById };
      const nextMap = { ...s.allMessagesMap };
      delete nextNodes[oldId];
      delete nextMap[oldId];
      if (oldNode) {
        const rekeyed = { ...oldNode, id: newId };
        nextNodes[newId] = rekeyed;
        nextMap[newId] = rekeyed;
      }

      const nextChildren: Record<string, number[]> = {};
      for (const [pKey, list] of Object.entries(s.childrenByParentId)) {
        const targetKey = pKey === String(oldId) ? String(newId) : pKey;
        const mapped = list.map((cid) => (cid === oldId ? newId : cid));
        const merged = [...(nextChildren[targetKey] || []), ...mapped];
        nextChildren[targetKey] = Array.from(new Set(merged));
      }
      for (const cid of nextChildren[String(newId)] || []) {
        if (nextNodes[cid]) nextNodes[cid] = { ...nextNodes[cid], parent_id: newId };
        if (nextMap[cid]) nextMap[cid] = { ...nextMap[cid], parent_id: newId };
      }

      const nextSelected: Record<string, number> = {};
      for (const [pKey, cid] of Object.entries(s.selectedChildByParent)) {
        const targetKey = pKey === String(oldId) ? String(newId) : pKey;
        nextSelected[targetKey] = cid === oldId ? newId : cid;
      }

      const nextThread = s.activeThread.map((m) =>
        m.id === oldId ? { ...m, id: newId } : m.parent_id === oldId ? { ...m, parent_id: newId } : m
      );

      const nextApprovals = { ...s.pendingApprovals };
      for (const [aid, entry] of Object.entries(nextApprovals)) {
        if (entry.nodeId === oldId) nextApprovals[aid] = { ...entry, nodeId: newId };
      }

      return {
        nodesById: nextNodes,
        allMessagesMap: nextMap,
        childrenByParentId: nextChildren,
        selectedChildByParent: nextSelected,
        activeThread: nextThread,
        activeLeafMessageId: s.activeLeafMessageId === oldId ? newId : s.activeLeafMessageId,
        activeNodeId: s.activeNodeId === oldId ? newId : s.activeNodeId,
        streamingAssistantId: s.streamingAssistantId === oldId ? newId : s.streamingAssistantId,
        pendingApprovals: nextApprovals,
      };
    });
  },
});
