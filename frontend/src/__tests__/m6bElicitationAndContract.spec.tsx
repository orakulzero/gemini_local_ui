import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ElicitationModal, isSafeElicitationUrl } from '../components/ElicitationModal';
import { AGENT_PROTOCOL_VERSION, buildWsElicitationResponse, buildWsPromptRequest, dispatchWsServerFrame } from '../services/wsProtocolV1';
import { useChatStore } from '../store/chatStore';
import { ElicitationRequest, Message } from '../types';

describe('M6b: ElicitationModal (Form + URL), HitlSlice Elicitations, and agy.stream.v1 WS Contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({
      nodesById: {},
      childrenByParentId: {},
      selectedChildByParent: {},
      activeThread: [],
      allMessagesMap: {},
      pendingApprovals: {},
      pendingElicitations: {},
      activeElicitationId: null,
      activeConversationId: 'conv_m6b_1',
    });
  });

  it('renders dynamic form fields from requested_schema, blocks blank required field, submits accept payload, and clears store', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal('fetch', fetchMock);

    const asstMsg: Message = { id: 77, conversation_id: 'conv_m6b_1', role: 'assistant', content: 'Deploying...', parent_id: null, created_at: 100, metadata: {} };
    useChatStore.getState().upsertDagNode(asstMsg);

    const formElicitation: ElicitationRequest = {
      elicitation_id: 'el_form_1',
      connector_id: 'deploy_mcp',
      tool_name: 'deploy_service',
      mode: 'form',
      message: 'Configure deployment parameters',
      requested_schema: {
        type: 'object',
        properties: {
          service: { type: 'string', title: 'Service Name' },
          dry_run: { type: 'boolean', description: 'Dry Run' },
          region: { type: 'string', enum: ['us-east', 'eu-west'] },
        },
        required: ['service'],
      },
    };

    useChatStore.getState().setPendingElicitationForNode(77, formElicitation);
    expect(useChatStore.getState().pendingElicitations['el_form_1']).toBeDefined();
    expect(useChatStore.getState().nodesById[77]?.metadata?.pending_elicitation?.elicitation_id).toBe('el_form_1');

    render(<ElicitationModal />);
    expect(screen.getByTestId('elicitation-modal')).toBeDefined();

    fireEvent.click(screen.getByTestId('elicitation-accept-btn'));
    expect(screen.getByTestId('elicitation-error').textContent).toContain('service');
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId('elicitation-field-service'), { target: { value: 'billing-api' } });
    fireEvent.click(screen.getByTestId('elicitation-field-dry_run'));
    fireEvent.change(screen.getByTestId('elicitation-field-region'), { target: { value: 'eu-west' } });
    fireEvent.click(screen.getByTestId('elicitation-accept-btn'));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/chat/elicitations/el_form_1',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ action: 'accept', content: { service: 'billing-api', dry_run: true, region: 'eu-west' } }),
        })
      );
      expect(useChatStore.getState().pendingElicitations['el_form_1']).toBeUndefined();
      expect(useChatStore.getState().nodesById[77]?.metadata?.pending_elicitation).toBeNull();
    });
  });

  it('validates safe https:// URL & decline action vs unsafe javascript: URL blocking in URL mode', async () => {
    expect(isSafeElicitationUrl('https://auth.example.com/oauth')).toBe(true);
    expect(isSafeElicitationUrl('http://127.0.0.1:9000/callback')).toBe(true);
    expect(isSafeElicitationUrl('javascript:evil()')).toBe(false);

    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal('fetch', fetchMock);

    const safeMsg: Message = { id: 88, conversation_id: 'conv_m6b_1', role: 'assistant', content: 'Auth', parent_id: null, created_at: 101 };
    useChatStore.getState().upsertDagNode(safeMsg);
    useChatStore.getState().setPendingElicitationForNode(88, {
      elicitation_id: 'el_url_safe',
      connector_id: 'oauth_mcp',
      mode: 'url',
      message: 'Complete OAuth sign-in',
      url: 'https://auth.example.com/oauth?state=123',
    });

    const { unmount } = render(<ElicitationModal />);
    expect((screen.getByTestId('elicitation-open-url-link') as HTMLAnchorElement).getAttribute('href')).toBe('https://auth.example.com/oauth?state=123');
    expect(screen.queryByTestId('elicitation-unsafe-url')).toBeNull();

    fireEvent.click(screen.getByTestId('elicitation-decline-btn'));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/chat/elicitations/el_url_safe',
        expect.objectContaining({ method: 'POST', body: JSON.stringify({ action: 'decline', content: null }) })
      );
      expect(useChatStore.getState().pendingElicitations['el_url_safe']).toBeUndefined();
    });
    unmount();

    useChatStore.getState().setPendingElicitationForNode(88, {
      elicitation_id: 'el_url_bad',
      connector_id: 'bad_mcp',
      mode: 'url',
      message: 'Open suspicious link',
      url: 'javascript:evil()',
    });

    render(<ElicitationModal />);
    expect(screen.getByTestId('elicitation-unsafe-url')).toBeDefined();
    expect(screen.queryByTestId('elicitation-open-url-link')).toBeNull();
    expect((screen.getByTestId('elicitation-accept-btn') as HTMLButtonElement).disabled).toBe(true);
  });

  it('verifies wsProtocolV1 frozen agy.stream.v1 builders and dispatchWsServerFrame routing', () => {
    expect(AGENT_PROTOCOL_VERSION).toBe('agy.stream.v1');
    expect(buildWsPromptRequest('req_1', { prompt: 'Ping', model: 'gemini-4-carbon' })).toEqual({
      jsonrpc: '2.0',
      id: 'req_1',
      method: 'session/prompt',
      params: { protocol_version: 'agy.stream.v1', prompt: 'Ping', model: 'gemini-4-carbon' },
    });
    expect(buildWsElicitationResponse('req_2', 'el_9', 'accept', { env: 'staging' })).toEqual({
      jsonrpc: '2.0',
      id: 'req_2',
      method: 'elicitation/respond',
      params: { protocol_version: 'agy.stream.v1', elicitation_id: 'el_9', action: 'accept', content: { env: 'staging' } },
    });

    const onElicitationRequired = vi.fn();
    const onText = vi.fn();
    const onDone = vi.fn();
    const callbacks = { onElicitationRequired, onText, onDone };

    dispatchWsServerFrame(JSON.stringify({ jsonrpc: '2.0', method: 'session/update', params: { protocol_version: 'agy.stream.v1', event: { type: 'elicitation_required', metadata: { elicitation_id: 'el_9', connector_id: 'c1', mode: 'form', message: 'Env?' } } } }), callbacks);
    dispatchWsServerFrame(JSON.stringify({ jsonrpc: '2.0', method: 'session/update', params: { event: { type: 'text', delta: 'OK' } } }), callbacks);
    dispatchWsServerFrame(JSON.stringify({ jsonrpc: '2.0', method: 'session/update', params: { event: { type: 'done', metadata: { cid: 'c1' } } } }), callbacks);

    expect(onElicitationRequired).toHaveBeenCalledWith(expect.objectContaining({ elicitation_id: 'el_9' }));
    expect(onText).toHaveBeenCalledWith('OK');
    expect(onDone).toHaveBeenCalledWith({ cid: 'c1' });
  });
});
