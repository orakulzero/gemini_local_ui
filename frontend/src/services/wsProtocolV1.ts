import type { ElicitationAction, StreamEvent } from '../types';
import { ChatGeneratePayload, dispatchStreamEvent, StreamCallbacks } from './streaming';
import { resolveAuthenticatedWsUrl } from './desktopBridge';

export const AGENT_PROTOCOL_VERSION = 'agy.stream.v1' as const;

export interface WsRpcRequest<M extends string = string, P = Record<string, any>> {
  jsonrpc: '2.0';
  id: string | number;
  method: M;
  params: P & { protocol_version: typeof AGENT_PROTOCOL_VERSION };
}

export function buildWsPromptRequest(
  id: string | number,
  payload: ChatGeneratePayload
): WsRpcRequest<'session/prompt', ChatGeneratePayload> {
  return {
    jsonrpc: '2.0',
    id,
    method: 'session/prompt',
    params: { protocol_version: AGENT_PROTOCOL_VERSION, ...payload },
  };
}

export function buildWsCancelRequest(id: string | number): WsRpcRequest<'session/cancel', Record<string, unknown>> {
  return {
    jsonrpc: '2.0',
    id,
    method: 'session/cancel',
    params: { protocol_version: AGENT_PROTOCOL_VERSION },
  };
}

export function buildWsPermissionResponse(
  id: string | number,
  approvalId: string,
  decision: 'allow_once' | 'allow_session' | 'deny'
): WsRpcRequest<'session/respond_permission', { approval_id: string; decision: string }> {
  return {
    jsonrpc: '2.0',
    id,
    method: 'session/respond_permission',
    params: { protocol_version: AGENT_PROTOCOL_VERSION, approval_id: approvalId, decision },
  };
}

export function buildWsElicitationResponse(
  id: string | number,
  elicitationId: string,
  action: ElicitationAction,
  content?: Record<string, any> | null
): WsRpcRequest<'elicitation/respond', { elicitation_id: string; action: ElicitationAction; content: Record<string, any> | null }> {
  return {
    jsonrpc: '2.0',
    id,
    method: 'elicitation/respond',
    params: {
      protocol_version: AGENT_PROTOCOL_VERSION,
      elicitation_id: elicitationId,
      action,
      content: action === 'accept' ? (content ?? {}) : null,
    },
  };
}

export function dispatchWsServerFrame(rawJson: string, callbacks: StreamCallbacks): any {
  try {
    const frame = JSON.parse(rawJson);
    if (frame?.method === 'session/update' && frame?.params?.event) {
      dispatchStreamEvent(frame.params.event as StreamEvent, callbacks);
    } else if (frame?.error) {
      callbacks.onError?.(frame.error.message || 'WebSocket JSON-RPC error');
    }
    return frame;
  } catch (err) {
    console.warn('Failed parsing WS v1 frame:', rawJson, err);
    return null;
  }
}

export function streamChatOverWsV1(
  payload: ChatGeneratePayload,
  callbacks: StreamCallbacks,
  wsUrl?: string
): Promise<void> {
  const targetUrl = wsUrl || resolveAuthenticatedWsUrl('/api/chat/ws');

  return new Promise((resolve, reject) => {
    const ws = new WebSocket(targetUrl);
    const reqId = `turn_${Date.now()}`;
    let settled = false;
    let sawDone = false;
    let sawTerminal = false;
    let postDoneTimer: ReturnType<typeof setTimeout> | null = null;

    const cleanup = () => {
      if (postDoneTimer !== null) {
        clearTimeout(postDoneTimer);
        postDoneTimer = null;
      }
      callbacks.signal?.removeEventListener('abort', onAbort);
    };
    const finishResolve = () => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve();
    };
    const onAbort = () => {
      sawTerminal = true;
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(buildWsCancelRequest(`cancel_${Date.now()}`)));
      }
      ws.close();
      callbacks.onDone?.({ aborted: true });
      finishResolve();
    };

    callbacks.signal?.addEventListener('abort', onAbort);
    ws.onopen = () => ws.send(JSON.stringify(buildWsPromptRequest(reqId, payload)));
    ws.onmessage = (evt) => {
      const frame = dispatchWsServerFrame(String(evt.data), callbacks);
      const evType = frame?.params?.event?.type;
      if (evType === 'done') {
        sawDone = true;
        // Wait briefly for the subsequent `saved` event / final JSON-RPC response before closing.
        if (postDoneTimer === null) {
          postDoneTimer = setTimeout(() => {
            sawTerminal = true;
            ws.close();
            finishResolve();
          }, 1500);
        }
        return;
      }
      if (evType === 'saved' || evType === 'error' || (frame?.id === reqId && (frame?.result || frame?.error))) {
        sawTerminal = true;
        ws.close();
        finishResolve();
      }
    };
    ws.onerror = () => {
      if (settled) return;
      sawTerminal = true;
      settled = true;
      cleanup();
      const msg = 'WebSocket transport error';
      callbacks.onError?.(msg);
      reject(new Error(msg));
    };
    ws.onclose = () => {
      if (!settled && !sawTerminal && !sawDone) {
        callbacks.onError?.('WebSocket closed before stream completed');
      }
      finishResolve();
    };
  });
}
