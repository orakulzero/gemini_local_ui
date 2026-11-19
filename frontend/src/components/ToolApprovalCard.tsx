import React, { useState } from 'react';
import { ToolApprovalRequest } from '../types';

export interface ToolApprovalCardProps {
  approval: ToolApprovalRequest;
  onResolved?: (approvalId: string, decision: string) => void;
}

export const ToolApprovalCard: React.FC<ToolApprovalCardProps> = React.memo(
  ({ approval, onResolved }) => {
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const formattedArgs = (() => {
      try {
        return JSON.stringify(approval.arguments ?? {}, null, 2);
      } catch {
        return String(approval.arguments ?? '');
      }
    })();

    const submitDecision = async (decision: 'allow_once' | 'allow_session' | 'deny') => {
      if (submitting) return;
      setSubmitting(true);
      setError(null);
      try {
        const res = await fetch(
          `/api/chat/tool-approvals/${encodeURIComponent(approval.approval_id)}`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ decision }),
          }
        );
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || `Approval failed: HTTP ${res.status}`);
        }
        onResolved?.(approval.approval_id, decision);
      } catch (err: any) {
        setError(err?.message || 'Failed to submit decision');
      } finally {
        setSubmitting(false);
      }
    };

    const toolLabel = approval.namespaced_name || approval.tool_name;

    return (
      <div
        data-testid="tool-approval-card"
        className="mt-3 rounded-lg border border-amber-600/50 bg-amber-950/20 text-zinc-100 overflow-hidden shadow-sm"
      >
        <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 bg-amber-950/40 border-b border-amber-700/40 text-xs">
          <div className="flex items-center gap-2 min-w-0">
            <span className="font-semibold text-amber-300 shrink-0">Approval Required</span>
            <span className="font-mono text-zinc-100 truncate">{toolLabel}</span>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            {approval.approval_policy && (
              <span className="px-1.5 py-0.5 rounded bg-zinc-900/80 border border-amber-700/50 font-mono text-[10px] text-amber-200">
                {approval.approval_policy}
              </span>
            )}
            {approval.destructive_hint && (
              <span className="px-1.5 py-0.5 rounded bg-red-950/60 border border-red-700/50 text-[10px] text-red-300">
                destructive
              </span>
            )}
          </div>
        </div>

        <div className="p-3 space-y-2.5 text-xs">
          <pre className="p-2 rounded-md bg-zinc-950/80 border border-zinc-800 font-mono text-[11px] text-zinc-200 overflow-x-auto whitespace-pre-wrap break-words m-0">
            {formattedArgs}
          </pre>

          {error && (
            <div className="p-2 rounded bg-red-950/50 border border-red-800/60 text-[11px] text-red-300">
              {error}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              data-testid="approve-once-btn"
              disabled={submitting}
              onClick={() => submitDecision('allow_once')}
              className="px-3 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-medium transition-colors"
            >
              Allow Once
            </button>
            <button
              type="button"
              data-testid="approve-session-btn"
              disabled={submitting}
              onClick={() => submitDecision('allow_session')}
              className="px-3 py-1 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium transition-colors"
            >
              Allow Session
            </button>
            <button
              type="button"
              data-testid="deny-tool-btn"
              disabled={submitting}
              onClick={() => submitDecision('deny')}
              className="px-3 py-1 rounded-lg bg-red-900/70 hover:bg-red-800 disabled:opacity-50 text-red-100 border border-red-700/60 font-medium transition-colors"
            >
              Deny
            </button>
          </div>
        </div>
      </div>
    );
  }
);

ToolApprovalCard.displayName = 'ToolApprovalCard';
