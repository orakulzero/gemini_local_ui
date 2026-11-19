import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { WorkspaceHubModal } from '../components/WorkspaceHubModal';
import { ToolCallBlock } from '../components/ToolCallBlock';
import { LightboxModal } from '../components/LightboxModal';
import { useWorkspaceStore } from '../store/workspaceStore';

describe('Workspace Hub: Modal Navigation, ToolCallBlock, and LightboxModal Portal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useWorkspaceStore.setState({
      isHubOpen: true,
      activeHubTab: 'prompts',
      prompts: [
        {
          id: 'p1',
          name: 'Systems Architect',
          description: 'Design distributed architectures',
          content: 'You are an architect',
          apply_mode: 'turn0',
          is_active: true,
          is_builtin: true,
        },
      ],
      skills: [
        {
          id: 'sk1',
          name: 'caveman-concise',
          description: 'Direct concise answers',
          compatibility: 'native',
          source: 'gemini-pack',
          triggers: ['/caveman-concise'],
          content: 'Talk short',
        },
        {
          id: 'sk2',
          name: 'custom-mcp-tool',
          description: 'Custom bridged skill',
          compatibility: 'mcp-bridged',
          source: 'custom',
          triggers: [],
          content: 'Run tool',
        },
      ],
      connectors: [
        {
          id: 'conn1',
          name: 'Local Builtin Utils',
          transport: 'stdio',
          enabled: true,
          is_builtin: true,
          command: 'python',
          args: [],
        },
      ],
      enabledSkillIds: ['sk1'],
      testResults: {
        conn1: { ok: true, latency_ms: 15, tools: [{ name: 'calculate_expression', description: 'Evaluates math' }] },
      },
    });
  });

  it('renders WorkspaceHubModal across prompts, skills, and connectors tabs with testids', () => {
    render(<WorkspaceHubModal />);

    expect(screen.getByTestId('workspace-hub-modal')).toBeDefined();
    expect(screen.getByText('Systems Architect')).toBeDefined();

    // Switch to Skills tab
    fireEvent.click(screen.getByTestId('workspace-tab-skills'));
    expect(screen.getByTestId('skills-filter-native')).toBeDefined();
    expect(screen.getByTestId('skills-filter-all')).toBeDefined();
    const skillItems = screen.getAllByTestId('skill-item');
    expect(skillItems.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('caveman-concise')).toBeDefined();

    // Switch to Connectors tab
    fireEvent.click(screen.getByTestId('workspace-tab-connectors'));
    expect(screen.getByTestId('test-connector-btn')).toBeDefined();
    expect(screen.getByText('Local Builtin Utils')).toBeDefined();
  });

  it('renders ToolCallBlock with OK status badge, tool name, arguments, and result string', () => {
    const toolCalls = [
      {
        id: 'tc-calc-1',
        namespaced_name: 'builtin_local_utils__calculate_expression',
        arguments: { expression: '6 * 7' },
        result: '42',
        status: 'ok' as const,
        duration_ms: 24,
      },
    ];

    render(<ToolCallBlock toolCalls={toolCalls} />);

    expect(screen.getByTestId('tool-call-block')).toBeDefined();
    expect(screen.getByText('builtin_local_utils__calculate_expression')).toBeDefined();
    const statusBadge = screen.getByTestId('tool-call-status');
    expect(statusBadge.textContent).toBe('OK');
    const resultPre = screen.getByTestId('tool-call-result');
    expect(resultPre.textContent).toContain('42');
  });

  it('renders LightboxModal inside document.body portal container with z-[100] and functioning close button', () => {
    const handleClose = vi.fn();
    render(
      <LightboxModal
        image={{ url: '/files/test_cube.png', alt: 'Neon Blue Cube', prompt: 'Render cube' }}
        onClose={handleClose}
      />
    );

    const closeBtn = screen.getByTestId('lightbox-close-btn');
    expect(closeBtn).toBeDefined();
    const modal = screen.getByTestId('lightbox-modal');
    expect(modal.className).toContain('z-[100]');
    expect(document.body.contains(closeBtn)).toBe(true);

    fireEvent.click(closeBtn);
    expect(handleClose).toHaveBeenCalledTimes(1);
  });
});
