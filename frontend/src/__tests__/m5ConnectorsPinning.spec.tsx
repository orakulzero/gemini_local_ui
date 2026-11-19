import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConnectorsTab } from '../components/workspace/ConnectorsTab';
import { useWorkspaceStore } from '../store/workspaceStore';

describe('Milestone M5: MCP Connector Schema Pinning & Drift UI', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useWorkspaceStore.setState({
      connectors: [
        {
          id: 'conn_verified',
          name: 'Verified Local Utils',
          transport: 'stdio',
          enabled: true,
          is_builtin: true,
          pin_status: 'verified',
          pin_sha256: 'a1b2c3d4e5f67890abcdef1234567890abcdef1234567890abcdef1234567890',
        },
        {
          id: 'conn_drift',
          name: 'Untrusted Server',
          transport: 'stdio',
          enabled: true,
          is_builtin: false,
          pin_status: 'drift_detected',
          pin_sha256: 'f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0',
          pin_changed_tools: ['execute_arbitrary_cmd'],
        },
      ],
      testResults: {},
    });
  });

  it('renders Pinned badge for verified connector and drift warning with repin button for drifted connector', async () => {
    const repinMock = vi.fn().mockResolvedValue({ ok: true });
    useWorkspaceStore.setState({ repinConnector: repinMock });

    render(<ConnectorsTab />);

    // Verified connector pin badge
    const pinBadges = screen.getAllByTestId('connector-pin-badge');
    expect(pinBadges.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Pinned a1b2c3d4/)).toBeDefined();

    // Drifted connector badge and warning
    expect(screen.getByText(/Schema Drift!/)).toBeDefined();
    expect(screen.getByText(/execute_arbitrary_cmd/)).toBeDefined();

    // Repin button
    const repinBtn = screen.getByTestId('repin-connector-btn');
    expect(repinBtn).toBeDefined();

    fireEvent.click(repinBtn);
    await waitFor(() => {
      expect(repinMock).toHaveBeenCalledWith('conn_drift');
    });
  });
});
