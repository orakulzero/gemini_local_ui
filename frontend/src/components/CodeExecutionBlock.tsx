import React from 'react';

export interface CodeExecutionItem {
  language?: string;
  code?: string;
  stdout?: string;
  stderr?: string;
  outcome?: string;
}

export interface CodeExecutionBlockProps {
  executions: CodeExecutionItem[];
}

function isExecutionError(exec: CodeExecutionItem): boolean {
  const outcomeNorm = (exec.outcome || '').toUpperCase();
  if (
    outcomeNorm.includes('FAIL') ||
    outcomeNorm.includes('ERR') ||
    outcomeNorm.includes('TIMEOUT') ||
    outcomeNorm.includes('DEADLINE') ||
    outcomeNorm.includes('CANCEL')
  ) {
    return true;
  }
  if (
    outcomeNorm === 'OK' ||
    outcomeNorm === 'OUTCOME_OK' ||
    outcomeNorm === 'SUCCESS'
  ) {
    return false;
  }
  return Boolean(exec.stderr && exec.stderr.trim());
}

export const CodeExecutionBlock: React.FC<CodeExecutionBlockProps> = React.memo(
  ({ executions }) => {
    if (!Array.isArray(executions) || executions.length === 0) {
      return null;
    }

    return (
      <div
        data-testid="code-execution-block"
        className="mt-3 space-y-2.5"
      >
        {executions.map((exec, idx) => {
          const isError = isExecutionError(exec);
          const statusLabel = isError ? 'ERROR' : 'OK';
          const langLabel = (exec.language || 'PYTHON').toUpperCase();

          return (
            <div
              key={idx}
              className="rounded-lg border border-zinc-700/60 bg-zinc-900/90 text-zinc-100 overflow-hidden shadow-sm"
            >
              <div className="flex items-center justify-between px-3 py-1.5 bg-zinc-800/80 border-b border-zinc-700/50 text-xs select-none">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-zinc-200">
                    Cloud Python Sandbox
                  </span>
                  <span className="px-1.5 py-0.5 rounded bg-zinc-900/80 border border-zinc-700/60 font-mono text-[10px] text-zinc-400">
                    {langLabel}
                  </span>
                </div>
                <span
                  data-testid="code-execution-status"
                  className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border ${
                    isError
                      ? 'bg-red-950/60 text-red-300 border-red-700/50'
                      : 'bg-emerald-950/60 text-emerald-300 border-emerald-700/50'
                  }`}
                >
                  {statusLabel}
                </span>
              </div>

              <div className="p-3 space-y-2.5 text-xs">
                {exec.code && (
                  <details className="rounded-md border border-zinc-800 bg-zinc-950/60">
                    <summary className="cursor-pointer px-2.5 py-1.5 text-[11px] font-medium text-zinc-400 hover:text-zinc-200 select-none">
                      Code ({langLabel})
                    </summary>
                    <pre className="px-2.5 pb-2.5 pt-1 overflow-x-auto font-mono text-xs text-zinc-200 border-t border-zinc-800/80 whitespace-pre-wrap break-words m-0">
                      <code>{exec.code}</code>
                    </pre>
                  </details>
                )}

                {exec.stdout !== undefined && exec.stdout !== '' && (
                  <div>
                    <div className="text-[10px] uppercase tracking-wider font-semibold text-zinc-400 mb-1">
                      stdout
                    </div>
                    <pre
                      data-testid="code-execution-stdout"
                      className="p-2.5 rounded-md bg-zinc-950/80 border border-zinc-800 font-mono text-xs text-emerald-200 overflow-x-auto whitespace-pre-wrap break-words m-0"
                    >
                      {exec.stdout}
                    </pre>
                  </div>
                )}

                {exec.stderr !== undefined && exec.stderr !== '' && (
                  <div>
                    <div className="text-[10px] uppercase tracking-wider font-semibold text-red-400/90 mb-1">
                      stderr
                    </div>
                    <pre
                      data-testid="code-execution-stderr"
                      className="p-2.5 rounded-md bg-red-950/30 border border-red-900/50 font-mono text-xs text-red-300 overflow-x-auto whitespace-pre-wrap break-words m-0"
                    >
                      {exec.stderr}
                    </pre>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    );
  }
);

CodeExecutionBlock.displayName = 'CodeExecutionBlock';
