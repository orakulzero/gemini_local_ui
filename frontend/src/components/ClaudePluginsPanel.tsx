import React, { useState, useEffect } from 'react';

export const ClaudePluginsPanel: React.FC = () => {
  const [plugins, setPlugins] = useState<{agents: any[], commands: any[]}>({agents: [], commands: []});
  const [newName, setNewName] = useState('');
  const [newPrompt, setNewPrompt] = useState('');
  const [newDescription, setNewDescription] = useState('');

  const loadPlugins = () => {
    // Assuming an endpoint exists or will be fetched from a combined endpoint
    fetch('/api/claude-plugins')
      .then(res => res.json())
      .then(data => setPlugins(data))
      .catch(console.error);
  };

  useEffect(() => {
    loadPlugins();
  }, []);

  const handleCreateAgent = async () => {
    const payload = {
      name: newName,
      description: newDescription,
      prompt: newPrompt,
      tools: [],
      model: 'sonnet'
    };
    await fetch('/api/claude-plugins/agents', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    setNewName('');
    setNewPrompt('');
    setNewDescription('');
    loadPlugins();
  };

  const handleDeleteAgent = async (name: string) => {
    await fetch(`/api/claude-plugins/agents/${name}`, { method: 'DELETE' });
    loadPlugins();
  };

  const handleSyncGems = async () => {
    const res = await fetch('/api/claude-plugins/sync-gems', { method: 'POST' });
    const data = await res.json();
    alert(`Synced ${data.synced_count ?? 0} Claude agents to Custom Gems`);
  };

  return (
    <div className="p-6 bg-gray-50 text-gray-800 min-h-screen">
      <h1 className="text-2xl font-bold mb-4">Claude Plugins & Agents Hub</h1>

      <button
        onClick={handleSyncGems}
        className="bg-blue-600 text-white px-4 py-2 rounded mb-6"
      >
        Sync Plugin Gems (MCP)
      </button>

      <div className="grid grid-cols-2 gap-8">
        <div>
          <h2 className="text-xl font-semibold mb-2">Local Agents</h2>
          <ul className="space-y-2">
            {plugins.agents?.map(a => (
              <li key={a.name} className="p-3 bg-white shadow rounded flex justify-between">
                <div>
                  <strong>{a.name}</strong> - {a.description}
                </div>
                <button
                  onClick={() => handleDeleteAgent(a.name)}
                  className="text-red-500 hover:text-red-700"
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>

          <h2 className="text-xl font-semibold mt-6 mb-2">Local Commands</h2>
          <ul className="space-y-2">
            {plugins.commands?.map(c => (
              <li key={c.name} className="p-3 bg-white shadow rounded">
                <strong>{c.name}</strong> - {c.description}
              </li>
            ))}
          </ul>
        </div>

        <div className="bg-white p-4 shadow rounded">
          <h2 className="text-xl font-semibold mb-4">Create New Agent</h2>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium">Name (e.g. data-analyst)</label>
              <input
                value={newName}
                onChange={e => setNewName(e.target.value)}
                className="w-full border p-2 rounded"
              />
            </div>
            <div>
              <label className="block text-sm font-medium">Description</label>
              <input
                value={newDescription}
                onChange={e => setNewDescription(e.target.value)}
                className="w-full border p-2 rounded"
              />
            </div>
            <div>
              <label className="block text-sm font-medium">Prompt</label>
              <textarea
                value={newPrompt}
                onChange={e => setNewPrompt(e.target.value)}
                className="w-full border p-2 rounded h-32"
              />
            </div>
            <button
              onClick={handleCreateAgent}
              className="bg-green-600 text-white px-4 py-2 rounded w-full"
            >
              Save Agent to .claude/agents/
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
