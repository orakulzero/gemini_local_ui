import React from 'react';
import { ToolCallRecord } from '../types';

export interface ToolCallBlockProps {
  toolCalls: ToolCallRecord[];
}

function getToolStatus(tc: ToolCallRecord): 'RUNNING' | 'OK' | 'ERROR' | 'DENIED' {
  const status = (tc.status || '').toLowerCase();
  if (status === 'running' || status === 'pending') return 'RUNNING';
  if (status === 'denied') return 'DENIED';
  if (tc.is_error === true || tc.ok === false || status === 'error' || status === 'failed') {
    return 'ERROR';
  }
  return 'OK';
}

function formatArgs(args: ToolCallRecord['arguments']): string {
  if (args === undefined || args === null) return '{}';
  if (typeof args === 'string') return args;
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return String(args);
  }
}

export const ToolCallBlock: React.FC<ToolCallBlockProps> = React.memo(({ toolCalls }) => {
  if (!Array.isArray(toolCalls) || toolCalls.length === 0) {
    return null;
  }

  return (
    <div data-testid="tool-call-block" className="mt-3 space-y-2">
      {toolCalls.map((tc, idx) => {
        const status = getToolStatus(tc);
        const label =
          tc.namespaced_name ||
          (tc.connector_id && tc.tool_name
            ? `${tc.connector_id}__${tc.tool_name}`
            : tc.tool_name || tc.name || 'MCP Tool');
        const formattedArgs = formatArgs(tc.arguments);

        const badgeClass =
          status === 'RUNNING'
            ? 'bg-blue-950/60 text-blue-300 border-blue-700/50 animate-pulse'
            : status === 'DENIED'
              ? 'bg-amber-950/60 text-amber-300 border-amber-700/50'
              : status === 'ERROR'
                ? 'bg-red-950/60 text-red-300 border-red-700/50'
                : 'bg-emerald-950/60 text-emerald-300 border-emerald-700/50';

        return (
          <div
            key={tc.id || `${label}-${idx}`}
            className="rounded-lg border border-zinc-700/60 bg-zinc-900/90 text-zinc-100 overflow-hidden shadow-sm"
          >
            <div className="flex items-center justify-between px-3 py-1.5 bg-zinc-800/80 border-b border-zinc-700/50 text-xs select-none">
              <div className="flex items-center gap-2 min-w-0">
                <span className="font-mono font-semibold text-cyan-300 truncate">{label}</span>
                {tc.connector_id && (
                  <span className="px-1.5 py-0.5 rounded bg-zinc-900/80 border border-zinc-700/60 font-mono text-[10px] text-zinc-400 shrink-0">
                    {tc.connector_id}
                  </span>
                )}
              </div>

              <div className="flex items-center gap-2 shrink-0">
                {typeof tc.duration_ms === 'number' && (
                  <span className="font-mono text-[10px] text-zinc-400">
                    {Math.round(tc.duration_ms)} ms
                  </span>
                )}
                <span
                  data-testid="tool-call-status"
                  className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border ${badgeClass}`}
                >
                  {status}
                </span>
              </div>
            </div>

            <div className="p-2.5 space-y-2 text-xs">
              <details open className="rounded-md border border-zinc-800 bg-zinc-950/60">
                <summary className="cursor-pointer px-2.5 py-1 text-[11px] font-medium text-zinc-400 hover:text-zinc-200 select-none">
                  Arguments
                </summary>
                <pre className="px-2.5 pb-2 pt-1 overflow-x-auto font-mono text-[11px] text-zinc-300 border-t border-zinc-800/80 whitespace-pre-wrap break-words m-0">
                  {formattedArgs}
                </pre>
              </details>

              {tc.result !== undefined && tc.result !== '' && (
                <details open className="rounded-md border border-zinc-800 bg-zinc-950/80">
                  <summary className="cursor-pointer px-2.5 py-1 text-[11px] font-medium text-zinc-400 hover:text-zinc-200 select-none">
                    Result
                  </summary>
                  <pre
                    data-testid="tool-call-result"
                    className={`px-2.5 pb-2 pt-1 overflow-x-auto font-mono text-[11px] border-t border-zinc-800/80 whitespace-pre-wrap break-words m-0 ${
                      status === 'ERROR'
                        ? 'text-red-300'
                        : status === 'DENIED'
                          ? 'text-amber-300'
                          : 'text-emerald-200'
                    }`}
                  >
                    {tc.result}
                  </pre>
                </details>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
});

ToolCallBlock.displayName = 'ToolCallBlock';
