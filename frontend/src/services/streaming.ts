import { StreamEvent } from '../types';

export interface ChatGeneratePayload {
  conversation_id?: string;
  parent_message_id?: number | null;
  existing_user_message_id?: number | null;
  prompt: string;
  model: string;
  gem_id?: string | null;
  incognito?: boolean;
  system_instruction?: string;
  attachments?: Array<{
    uri?: string;
    filename: string;
    mime_type: string;
    file_size: number;
    storage_path?: string;
  }>;
}

export interface StreamCallbacks {
  signal?: AbortSignal;
  onStart?: (conversationId: string, userMessageId?: number) => void;
  onThought?: (delta: string) => void;
  onText?: (delta: string) => void;
  onCitation?: (metadata: any) => void;
  onMedia?: (metadata: any) => void;
  onSaved?: (assistantMessageId?: number) => void;
  onDone?: (metadata?: any) => void;
  onError?: (error: string) => void;
}

function dispatchSseLine(rawLine: string, callbacks: StreamCallbacks) {
  const line = rawLine.trim();
  if (!line || !line.startsWith('data:')) return;

  const jsonStr = line.slice(5).trim();
  if (!jsonStr) return;

  try {
    const event: StreamEvent = JSON.parse(jsonStr);

    switch (event.type) {
      case 'start':
        callbacks.onStart?.(event.conversation_id, event.user_message_id);
        break;
      case 'thought':
        callbacks.onThought?.(event.delta);
        break;
      case 'text':
        callbacks.onText?.(event.delta);
        break;
      case 'citation':
      case 'grounding':
        callbacks.onCitation?.(event.metadata);
        break;
      case 'media':
        callbacks.onMedia?.(event.metadata);
        callbacks.onCitation?.(event.metadata);
        break;
      case 'saved':
        callbacks.onSaved?.(event.assistant_message_id);
        break;
      case 'done':
        callbacks.onDone?.(event.metadata);
        break;
      case 'error':
        callbacks.onError?.(event.error);
        break;
    }
  } catch (parseErr) {
    console.warn('Failed parsing SSE JSON line:', jsonStr, parseErr);
  }
}

/**
 * Buffered SSE streaming client handling chunked TCP fragmentation and UTF-8 boundaries.
 */
export async function streamChatGenerate(
  payload: ChatGeneratePayload,
  callbacks: StreamCallbacks
): Promise<void> {
  const response = await fetch('/api/chat/generate', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(payload),
    signal: callbacks.signal,
  });

  if (!response.ok) {
    const errorBody = await response.text().catch(() => '');
    throw new Error(`HTTP ${response.status}: ${errorBody || response.statusText}`);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error('ReadableStream not supported by browser environment');
  }

  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      // Retain the trailing incomplete line in the buffer
      buffer = lines.pop() || '';

      for (const rawLine of lines) {
        dispatchSseLine(rawLine, callbacks);
      }
    }

    buffer += decoder.decode();
    if (buffer.trim()) {
      for (const rawLine of buffer.split('\n')) {
        dispatchSseLine(rawLine, callbacks);
      }
    }
  } catch (err: any) {
    if (err.name === 'AbortError') {
      callbacks.onDone?.({ aborted: true });
      return;
    }
    callbacks.onError?.(err.message || 'Stream connection severed');
    throw err;
  } finally {
    reader.releaseLock();
  }
}
