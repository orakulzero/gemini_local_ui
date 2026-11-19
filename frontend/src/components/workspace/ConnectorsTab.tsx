import React, { useState } from 'react';
import { ApprovalPolicy } from '../../types';
import { useWorkspaceStore } from '../../store/workspaceStore';

type KVRow = { key: string; value: string };

const APPROVAL_POLICIES: Array<{ value: ApprovalPolicy; label: string }> = [
  { value: 'auto_allow_readonly', label: 'Auto-allow read-only' },
  { value: 'always_ask', label: 'Always ask' },
  { value: 'auto_allow_all', label: 'Auto-allow all' },
  { value: 'deny', label: 'Deny' },
];

const PRESET_TEMPLATES = [
  { id: 'local_utils', label: 'Local Utils', transport: 'stdio' as const, name: 'Local Utils', command: 'python', args: '-u -m backend.app.mcp.builtin_tools_server', url: '' },
  { id: 'stdio', label: 'Stdio MCP', transport: 'stdio' as const, name: 'Custom Stdio MCP', command: 'npx', args: '-y @modelcontextprotocol/server-filesystem .', url: '' },
  { id: 'http', label: 'HTTP/SSE MCP', transport: 'streamable-http' as const, name: 'Remote MCP Server', command: '', args: '', url: 'http://127.0.0.1:8080/mcp' },
];

export const ConnectorsTab: React.FC = () => {
  const connectors = useWorkspaceStore((s) => s.connectors);
  const testResults = useWorkspaceStore((s) => s.testResults);
  const toggleConnector = useWorkspaceStore((s) => s.toggleConnector);
  const updateConnector = useWorkspaceStore((s) => s.updateConnector);
  const testConnector = useWorkspaceStore((s) => s.testConnector);
  const repinConnector = useWorkspaceStore((s) => s.repinConnector);
  const createConnector = useWorkspaceStore((s) => s.createConnector);
  const deleteConnector = useWorkspaceStore((s) => s.deleteConnector);

  const [testingId, setTestingId] = useState<string | null>(null);
  const [repinningId, setRepinningId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [transport, setTransport] = useState<'stdio' | 'streamable-http' | 'sse'>('stdio');
  const [command, setCommand] = useState('');
  const [argsText, setArgsText] = useState('');
  const [url, setUrl] = useState('');
  const [timeoutSec, setTimeoutSec] = useState(15);
  const [approvalPolicy, setApprovalPolicy] = useState<ApprovalPolicy>('auto_allow_readonly');
  const [kvRows, setKvRows] = useState<KVRow[]>([{ key: '', value: '' }]);

  const applyPreset = (p: (typeof PRESET_TEMPLATES)[number]) => {
    setName(p.name);
    setTransport(p.transport);
    setCommand(p.command);
    setArgsText(p.args);
    setUrl(p.url);
  };

  const handleTest = async (id: string) => {
    setTestingId(id);
    try { await testConnector(id); } finally { setTestingId((c) => (c === id ? null : c)); }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    const secrets = Object.fromEntries(kvRows.filter((r) => r.key.trim()).map((r) => [r.key.trim(), r.value]));
    await createConnector({
      name: name.trim(),
      transport,
      enabled: true,
      timeout_seconds: timeoutSec,
      approval_policy: approvalPolicy,
      ...(transport === 'stdio'
        ? { command: command.trim(), args: argsText.trim() ? argsText.trim().split(/\s+/) : [], env: secrets }
        : { url: url.trim(), headers: secrets }),
    });
    setName(''); setCommand(''); setArgsText(''); setUrl(''); setApprovalPolicy('auto_allow_readonly'); setKvRows([{ key: '', value: '' }]);
    setShowForm(false);
  };

  return (
    <div data-testid="connectors-tab" className="space-y-3 text-xs text-zinc-200">
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-zinc-400">Connect local stdio or remote HTTP/SSE MCP tool servers. Secrets stay encrypted at rest.</span>
        <button type="button" onClick={() => setShowForm((p) => !p)} className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium shrink-0">
          {showForm ? 'Close Form' : '+ Add Connector'}
        </button>
      </div>

      {showForm && (
        <form data-testid="new-connector-form" onSubmit={handleCreate} className="p-3 rounded-xl bg-zinc-950 border border-zinc-800 space-y-2.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] text-zinc-400 mr-1">Templates:</span>
            {PRESET_TEMPLATES.map((p) => (
              <button key={p.id} type="button" onClick={() => applyPreset(p)} className="px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-[11px]">{p.label}</button>
            ))}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-4 gap-2">
            <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="Connector name" className="bg-zinc-900 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-zinc-100" />
            <select value={transport} onChange={(e) => setTransport(e.target.value as any)} className="bg-zinc-900 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-zinc-100">
              <option value="stdio">stdio</option>
              <option value="streamable-http">streamable-http</option>
              <option value="sse">sse</option>
            </select>
            <select data-testid="new-connector-approval-policy" value={approvalPolicy} onChange={(e) => setApprovalPolicy(e.target.value as ApprovalPolicy)} className="bg-zinc-900 border border-zinc-800 rounded-lg px-2 py-1.5 text-zinc-100">
              {APPROVAL_POLICIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
            </select>
            <input type="number" min={3} max={120} value={timeoutSec} onChange={(e) => setTimeoutSec(Number(e.target.value) || 15)} placeholder="Timeout (s)" className="bg-zinc-900 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-zinc-100" />
          </div>
          {transport === 'stdio' ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <input type="text" value={command} onChange={(e) => setCommand(e.target.value)} placeholder="Command (e.g. python, npx)" className="bg-zinc-900 border border-zinc-800 rounded-lg px-2.5 py-1.5 font-mono text-zinc-100" />
              <input type="text" value={argsText} onChange={(e) => setArgsText(e.target.value)} placeholder="Arguments (space-separated)" className="bg-zinc-900 border border-zinc-800 rounded-lg px-2.5 py-1.5 font-mono text-zinc-100" />
            </div>
          ) : (
            <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Endpoint URL (https://...)" className="w-full bg-zinc-900 border border-zinc-800 rounded-lg px-2.5 py-1.5 font-mono text-zinc-100" />
          )}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-[11px] text-zinc-400">
              <span>{transport === 'stdio' ? 'Environment Variables (masked)' : 'HTTP Headers (masked)'}</span>
              <button type="button" onClick={() => setKvRows((r) => [...r, { key: '', value: '' }])} className="text-blue-400 hover:text-blue-300">+ Add pair</button>
            </div>
            {kvRows.map((row, idx) => (
              <div key={idx} className="flex gap-2">
                <input type="text" value={row.key} onChange={(e) => setKvRows((r) => r.map((x, i) => (i === idx ? { ...x, key: e.target.value } : x)))} placeholder="KEY" className="w-1/2 bg-zinc-900 border border-zinc-800 rounded-lg px-2 py-1 font-mono text-zinc-100" />
                <input type="password" value={row.value} onChange={(e) => setKvRows((r) => r.map((x, i) => (i === idx ? { ...x, value: e.target.value } : x)))} placeholder="••••••••" className="w-1/2 bg-zinc-900 border border-zinc-800 rounded-lg px-2 py-1 font-mono text-zinc-100" />
              </div>
            ))}
          </div>
          <div className="flex justify-end">
            <button type="submit" disabled={!name.trim()} className="px-3.5 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white font-medium">Save Connector</button>
          </div>
        </form>
      )}

      <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
        {connectors.map((conn) => {
          const testRes = testResults[conn.id];
          const tools = testRes?.tools || conn.discovered_tools || [];
          const secretKeys = conn.env_keys || conn.header_keys || [];
          const pinStatus = testRes?.pin_status ?? conn.pin_status;
          const pinSha = testRes?.pin_sha256 ?? conn.pin_sha256;
          const changedTools = testRes?.pin_changed_tools ?? conn.pin_changed_tools ?? [];
          const isDrift = pinStatus === 'drift_detected';
          const isPinned = pinStatus === 'pinned' || pinStatus === 'verified';
          return (
            <div key={conn.id} data-testid="connector-item" className={`p-3 rounded-xl border ${conn.enabled ? 'bg-cyan-950/15 border-cyan-600/50' : 'bg-zinc-950/60 border-zinc-800'}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-semibold text-zinc-100">{conn.name}</span>
                    <span className="px-1.5 py-0.5 rounded bg-zinc-800 font-mono text-[10px] text-cyan-300">{conn.transport}</span>
                    {isPinned && (
                      <span data-testid="connector-pin-badge" className="px-1.5 py-0.5 rounded bg-emerald-950/60 border border-emerald-700/50 font-mono text-[10px] text-emerald-300">
                        Pinned{pinSha ? ` ${pinSha.slice(0, 8)}` : ''}
                      </span>
                    )}
                    {isDrift && (
                      <span data-testid="connector-pin-badge" className="px-1.5 py-0.5 rounded bg-red-950/60 border border-red-700/50 text-[10px] font-semibold text-red-300">
                        Schema Drift!
                      </span>
                    )}
                    {secretKeys.length > 0 && <span className="px-1.5 py-0.5 rounded bg-zinc-900 border border-zinc-800 text-[10px] text-zinc-400">{secretKeys.length} secret(s)</span>}
                  </div>
                  <div className="text-[11px] font-mono text-zinc-400 mt-0.5 truncate">
                    {conn.transport === 'stdio' ? `${conn.command || ''} ${(conn.args || []).join(' ')}`.trim() : conn.url}
                  </div>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <select data-testid="connector-approval-policy" value={conn.approval_policy || 'auto_allow_readonly'} onChange={(e) => updateConnector(conn.id, { approval_policy: e.target.value as ApprovalPolicy })} className="bg-zinc-900 border border-zinc-800 rounded-lg px-2 py-1 text-[11px] text-zinc-200" title="Tool approval policy">
                    {APPROVAL_POLICIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                  </select>
                  {isDrift && (
                    <button
                      type="button"
                      data-testid="repin-connector-btn"
                      disabled={repinningId === conn.id}
                      onClick={async () => {
                        setRepinningId(conn.id);
                        try { await repinConnector(conn.id); } finally { setRepinningId((c) => (c === conn.id ? null : c)); }
                      }}
                      className="px-2.5 py-1 rounded-lg bg-red-700 hover:bg-red-600 disabled:opacity-50 text-white text-[11px] font-medium"
                    >
                      {repinningId === conn.id ? 'Re-pinning...' : 'Re-pin Schema'}
                    </button>
                  )}
                  <button type="button" data-testid="test-connector-btn" disabled={testingId === conn.id} onClick={() => handleTest(conn.id)} className="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-[11px]">
                    {testingId === conn.id ? 'Testing...' : 'Test'}
                  </button>
                  <button type="button" data-testid="toggle-connector-btn" onClick={() => toggleConnector(conn.id, !conn.enabled)} className={`px-2.5 py-1 rounded-lg text-[11px] font-medium ${conn.enabled ? 'bg-cyan-600 text-white' : 'bg-zinc-800 text-zinc-300 hover:bg-zinc-700'}`}>
                    {conn.enabled ? 'Enabled' : 'Disabled'}
                  </button>
                  {!conn.is_builtin && (
                    <button type="button" onClick={() => deleteConnector(conn.id)} className="px-2 py-1 rounded-lg hover:bg-red-950/60 text-zinc-400 hover:text-red-300 text-[11px]">Delete</button>
                  )}
                </div>
              </div>
              {isDrift && (
                <div className="mt-2 p-2 rounded-lg border bg-red-950/30 border-red-800/60 text-[11px] text-red-200">
                  Schema drift detected — tool invocation blocked until re-pinned.{changedTools.length > 0 ? ` Changed tools: ${changedTools.join(', ')}` : ''}
                </div>
              )}
              {testRes && (
                <div className={`mt-2 p-2 rounded-lg border text-[11px] ${testRes.ok ? 'bg-emerald-950/20 border-emerald-800/50 text-emerald-300' : 'bg-red-950/30 border-red-800/50 text-red-300'}`}>
                  {testRes.ok ? `Connected (${testRes.latency_ms ?? 0} ms) — ${tools.length} tool(s)` : `Error: ${testRes.error || 'Connection failed'}`}
                </div>
              )}
              {tools.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {tools.map((t) => (
                    <span key={t.name} title={t.description || t.name} className="px-2 py-0.5 rounded bg-zinc-900 border border-zinc-800 font-mono text-[10px] text-zinc-300">{t.name}</span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
