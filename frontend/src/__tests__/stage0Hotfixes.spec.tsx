import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ToolApprovalCard } from '../components/ToolApprovalCard';
import { ToolCallBlock } from '../components/ToolCallBlock';
import { ToolApprovalRequest } from '../types';

describe('Stage 0 Hotfixes: HITL ToolApprovalCard and Denied ToolCallBlock Badge', () => {
  const mockApproval: ToolApprovalRequest = {
    approval_id: 'apr_1',
    connector_id: 'builtin_local_utils',
    tool_name: 'calculate_expression',
    namespaced_name: 'builtin_local_utils__calculate_expression',
    arguments: { expression: '100 / 4' },
    approval_policy: 'always_ask',
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders ToolApprovalCard, displays tool name & arguments, and posts allow_once on button click', async () => {
    const onResolved = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true, approval_id: 'apr_1', decision: 'allow_once' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ToolApprovalCard approval={mockApproval} onResolved={onResolved} />);

    expect(screen.getByTestId('tool-approval-card')).toBeDefined();
    expect(screen.getByText(/builtin_local_utils__calculate_expression|calculate_expression/)).toBeDefined();
    expect(screen.getByText(/100 \/ 4/)).toBeDefined();

    fireEvent.click(screen.getByTestId('approve-once-btn'));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/api/chat/tool-approvals/apr_1'),
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ decision: 'allow_once' }),
        })
      );
      expect(onResolved).toHaveBeenCalledWith('apr_1', 'allow_once');
    });
  });

  it('posts deny decision when deny button is clicked', async () => {
    const onResolved = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true, approval_id: 'apr_1', decision: 'deny' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ToolApprovalCard approval={mockApproval} onResolved={onResolved} />);
    fireEvent.click(screen.getByTestId('deny-tool-btn'));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/api/chat/tool-approvals/apr_1'),
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ decision: 'deny' }),
        })
      );
      expect(onResolved).toHaveBeenCalledWith('apr_1', 'deny');
    });
  });

  it('renders ToolCallBlock with status="denied" showing DENIED or ERROR status badge', () => {
    const toolCalls = [
      {
        id: 'tc-denied-1',
        namespaced_name: 'builtin_local_utils__calculate_expression',
        arguments: { expression: '100 / 4' },
        result: 'Tool execution denied by user.',
        status: 'denied' as const,
      },
    ];

    render(<ToolCallBlock toolCalls={toolCalls} />);
    expect(screen.getByTestId('tool-call-block')).toBeDefined();
    const statusBadge = screen.getByTestId('tool-call-status');
    expect(statusBadge.textContent?.toUpperCase()).toMatch(/DENIED|ERROR/);
    expect(screen.getByTestId('tool-call-result').textContent).toContain('denied by user');
  });
});
