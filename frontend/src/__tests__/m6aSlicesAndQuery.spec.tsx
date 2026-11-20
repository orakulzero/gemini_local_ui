import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { useChatStore } from '../store/chatStore';
import { queryClient, queryKeys, useModelsQuery } from '../services/queryClient';
import { Message } from '../types';

describe('M6a: Slices Decomposition, DAG Branching, HITL Slice, and TanStack Query', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
    useChatStore.setState({
      nodesById: {},
      childrenByParentId: {},
      selectedChildByParent: {},
      activeThread: [],
      allMessagesMap: {},
      pendingApprovals: {},
      activeConversationId: 'conv_test_1',
    });
  });

  it('verifies normalized DAG graph state and pure branch switching via computeActiveBranch', () => {
    const rootMsg: Message = { id: 1, conversation_id: 'conv_1', role: 'user', content: 'Root question', parent_id: null, created_at: 100 };
    const branchA: Message = { id: 2, conversation_id: 'conv_1', role: 'assistant', content: 'Answer A', parent_id: 1, created_at: 200 };
    const branchB: Message = { id: 3, conversation_id: 'conv_1', role: 'assistant', content: 'Answer B', parent_id: 1, created_at: 201 };

    useChatStore.getState().upsertDagNode(rootMsg);
    useChatStore.getState().upsertDagNode(branchA);
    useChatStore.getState().upsertDagNode(branchB);

    const state = useChatStore.getState();
    expect(state.nodesById[1]?.content).toBe('Root question');
    expect(state.childrenByParentId['1']).toEqual([2, 3]);

    // Switch branch to branchA (id 2)
    useChatStore.getState().switchBranch(2);
    expect(useChatStore.getState().activeThread.map(m => m.id)).toEqual([1, 2]);

    // Switch branch to branchB (id 3)
    useChatStore.getState().switchBranch(3);
    expect(useChatStore.getState().activeThread.map(m => m.id)).toEqual([1, 3]);
  });

  it('verifies streamSlice activeNodeId targeting and rekeyDagNode from tempId to serverId', () => {
    const tempId = -101;
    const serverId = 42;
    const userMsg: Message = { id: 10, conversation_id: 'conv_1', role: 'user', content: 'Compute', parent_id: null, created_at: 100 };
    const draftAsst: Message = { id: tempId, conversation_id: 'conv_1', role: 'assistant', content: 'Thinking...', parent_id: 10, created_at: 200 };

    useChatStore.getState().upsertDagNode(userMsg);
    useChatStore.getState().upsertDagNode(draftAsst);
    useChatStore.setState({ activeNodeId: tempId });

    // Rekey node from temp to server
    useChatStore.getState().rekeyDagNode(tempId, serverId);

    const state = useChatStore.getState();
    expect(state.nodesById[tempId]).toBeUndefined();
    expect(state.nodesById[serverId]?.id).toBe(serverId);
    expect(state.childrenByParentId['10']).toContain(serverId);
    expect(state.activeNodeId).toBe(serverId);
  });

  it('verifies hitlSlice setPendingApprovalForNode and clearPendingApproval synchronization', () => {
    const msg: Message = { id: 50, conversation_id: 'conv_1', role: 'assistant', content: 'Calling tool', parent_id: null, created_at: 100, metadata: {} };
    useChatStore.getState().upsertDagNode(msg);

    const approval = {
      approval_id: 'apr_99',
      connector_id: 'local_utils',
      tool_name: 'calc',
      namespaced_name: 'local_utils__calc',
      arguments: { expr: '2+2' },
    };

    useChatStore.getState().setPendingApprovalForNode(50, approval);
    const stateWithApproval = useChatStore.getState();
    expect(stateWithApproval.pendingApprovals['apr_99']).toBeDefined();
    expect(stateWithApproval.nodesById[50]?.metadata?.pending_approval?.approval_id).toBe('apr_99');

    // Clear approval
    useChatStore.getState().clearPendingApproval(50);
    const stateCleared = useChatStore.getState();
    expect(stateCleared.pendingApprovals['apr_99']).toBeUndefined();
    expect(stateCleared.nodesById[50]?.metadata?.pending_approval).toBeNull();
  });

  it('verifies TanStack Query useModelsQuery integration with QueryClientProvider', async () => {
    const mockModels = [{ id: 'gemini-4-carbon', display_name: 'Carbon', model_number: 8, thinking_flag: 2 }];
    queryClient.setQueryData(queryKeys.models, mockModels);

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useModelsQuery(), { wrapper });

    await waitFor(() => {
      expect(result.current.data).toBeDefined();
      expect(result.current.data?.[0]?.id).toBe('gemini-4-carbon');
    });
  });
});
