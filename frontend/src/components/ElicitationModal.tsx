import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { ElicitationAction, ElicitationRequest } from '../types';
import { useChatStore } from '../store/chatStore';

export interface ElicitationModalProps {
  elicitation?: ElicitationRequest;
  onResolved?: (id: string, action: ElicitationAction, content?: Record<string, any> | null) => void;
}

export function isSafeElicitationUrl(rawUrl?: string | null): boolean {
  if (!rawUrl || typeof rawUrl !== 'string') return false;
  const trimmed = rawUrl.trim();
  if (!trimmed) return false;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol === 'https:') return Boolean(parsed.hostname);
    if (parsed.protocol === 'http:') {
      const host = parsed.hostname.toLowerCase();
      return host === '127.0.0.1' || host === 'localhost' || host === '[::1]';
    }
    return false;
  } catch {
    return false;
  }
}

function buildInitialFormState(schema?: Record<string, any> | null): Record<string, any> {
  const init: Record<string, any> = {};
  for (const [key, def] of Object.entries<any>(schema?.properties || {})) {
    init[key] = def?.default !== undefined ? def.default : def?.type === 'boolean' ? false : '';
  }
  return init;
}

export const ElicitationModal: React.FC<ElicitationModalProps> = ({ elicitation: propElicit, onResolved }) => {
  const pendingElicitations = useChatStore((s) => s.pendingElicitations);
  const activeElicitationId = useChatStore((s) => s.activeElicitationId);
  const submitElicitationResponse = useChatStore((s) => s.submitElicitationResponse);
  const clearPendingElicitation = useChatStore((s) => s.clearPendingElicitation);

  const active =
    propElicit ||
    (activeElicitationId ? pendingElicitations[activeElicitationId] : undefined) ||
    Object.values(pendingElicitations)[0];

  const [formValues, setFormValues] = useState<Record<string, any>>({});
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    setFormValues(buildInitialFormState(active?.requested_schema));
    setValidationError(null);
  }, [active?.elicitation_id]);

  if (!active) return null;

  const schemaProps = (active.requested_schema?.properties || {}) as Record<string, any>;
  const requiredFields: string[] = Array.isArray(active.requested_schema?.required) ? active.requested_schema!.required : [];
  const urlSafe = active.mode === 'url' ? isSafeElicitationUrl(active.url) : true;

  const handleAction = async (action: ElicitationAction) => {
    if (submitting) return;
    setValidationError(null);
    let payloadContent: Record<string, any> | null = null;
    if (action === 'accept' && active.mode === 'form') {
      const missing = requiredFields.filter((key) => {
        const def = schemaProps[key];
        const val = formValues[key];
        if (def?.type === 'boolean') return typeof val !== 'boolean';
        if (def?.type === 'number' || def?.type === 'integer') return val === '' || val == null || Number.isNaN(Number(val));
        return val == null || String(val).trim() === '';
      });
      if (missing.length > 0) {
        setValidationError(`Required field(s) missing: ${missing.join(', ')}`);
        return;
      }
      payloadContent = {};
      for (const [key, def] of Object.entries<any>(schemaProps)) {
        const val = formValues[key];
        if (def?.type === 'number' || def?.type === 'integer') {
          if (val !== '' && val != null) payloadContent[key] = Number(val);
        } else if (def?.type === 'boolean') payloadContent[key] = Boolean(val);
        else if (val !== '' && val != null) payloadContent[key] = val;
      }
    }
    setSubmitting(true);
    try {
      await submitElicitationResponse(active.elicitation_id, action, payloadContent);
      onResolved?.(active.elicitation_id, action, payloadContent);
    } catch (err: any) {
      setValidationError(err?.message || 'Failed to submit elicitation response');
    } finally {
      setSubmitting(false);
    }
  };

  const modal = (
    <div data-testid="elicitation-modal" className="fixed inset-0 z-[95] flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-2xl bg-zinc-900 border border-zinc-700/80 shadow-2xl overflow-hidden text-xs text-zinc-200">
        <div className="px-4 py-3 bg-zinc-950/90 border-b border-zinc-800 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className="px-2 py-0.5 rounded bg-cyan-950/60 border border-cyan-700/50 font-mono text-[10px] text-cyan-300 shrink-0">{active.connector_id}</span>
            {active.tool_name && <span className="font-mono text-zinc-300 truncate">{active.tool_name}</span>}
          </div>
          <button type="button" onClick={() => clearPendingElicitation(active.elicitation_id)} className="text-zinc-500 hover:text-zinc-200 px-1">✕</button>
        </div>

        <div className="p-4 space-y-3">
          <p className="text-sm text-zinc-100 leading-relaxed">{active.message}</p>
          {active.mode === 'form' ? (
            <div className="space-y-2.5">
              {Object.entries<any>(schemaProps).map(([key, def]) => (
                <label key={key} className="block space-y-1">
                  <span className="text-[11px] font-medium text-zinc-300">{def?.title || key}{requiredFields.includes(key) ? ' *' : ''}</span>
                  {Array.isArray(def?.enum) ? (
                    <select data-testid={`elicitation-field-${key}`} value={formValues[key] ?? ''} onChange={(e) => setFormValues((v) => ({ ...v, [key]: e.target.value }))} className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-zinc-100">
                      <option value="">-- Select --</option>
                      {def.enum.map((opt: string) => <option key={opt} value={opt}>{opt}</option>)}
                    </select>
                  ) : def?.type === 'boolean' ? (
                    <div className="flex items-center gap-2">
                      <input type="checkbox" data-testid={`elicitation-field-${key}`} checked={Boolean(formValues[key])} onChange={(e) => setFormValues((v) => ({ ...v, [key]: e.target.checked }))} className="rounded border-zinc-700 bg-zinc-950" />
                      <span className="text-zinc-400">{def?.description || key}</span>
                    </div>
                  ) : (
                    <input type={def?.type === 'number' || def?.type === 'integer' ? 'number' : 'text'} data-testid={`elicitation-field-${key}`} value={formValues[key] ?? ''} onChange={(e) => setFormValues((v) => ({ ...v, [key]: e.target.value }))} placeholder={def?.description || key} className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-zinc-100 focus:outline-none focus:border-blue-500" />
                  )}
                </label>
              ))}
            </div>
          ) : (
            <div className="space-y-2">
              <div data-testid="elicitation-url-display" className="p-2 rounded-lg bg-zinc-950 border border-zinc-800 font-mono text-[11px] text-zinc-300 break-all">{active.url || '(no URL provided)'}</div>
              {!urlSafe ? (
                <div data-testid="elicitation-unsafe-url" className="p-2 rounded-lg bg-red-950/40 border border-red-700/60 text-red-300 text-[11px]">Blocked unsafe URL scheme or non-loopback HTTP destination.</div>
              ) : (
                <a data-testid="elicitation-open-url-link" href={active.url!} target="_blank" rel="noopener noreferrer" className="inline-block px-3 py-1.5 rounded-lg bg-blue-600/20 border border-blue-500/40 text-blue-300 hover:bg-blue-600/30 font-medium">Open URL ↗</a>
              )}
            </div>
          )}

          {validationError && <div data-testid="elicitation-error" className="p-2 rounded-lg bg-red-950/40 border border-red-800/60 text-red-300 text-[11px]">{validationError}</div>}

          <div className="pt-2 border-t border-zinc-800 flex items-center justify-end gap-2">
            <button type="button" data-testid="elicitation-cancel-btn" disabled={submitting} onClick={() => handleAction('cancel')} className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300">Cancel</button>
            <button type="button" data-testid="elicitation-decline-btn" disabled={submitting} onClick={() => handleAction('decline')} className="px-3 py-1.5 rounded-lg bg-amber-900/60 hover:bg-amber-800/80 text-amber-200 border border-amber-700/50">Decline</button>
            <button type="button" data-testid="elicitation-accept-btn" disabled={submitting || (active.mode === 'url' && !urlSafe)} onClick={() => handleAction('accept')} className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white font-medium">Accept</button>
          </div>
        </div>
      </div>
    </div>
  );

  return typeof document !== 'undefined' ? createPortal(modal, document.body) : modal;
};
