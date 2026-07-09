export function SettingsPage() {
  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-lg font-bold text-white mb-1">Settings</h1>
      <p className="text-xs text-arena-text-dim mb-6">Arena configuration</p>

      <div className="card-base p-4 max-w-lg">
        <h2 className="text-sm font-semibold text-white mb-3">LLM Commentary</h2>
        <p className="text-[11px] text-arena-text-secondary mb-4">
          Configure an LLM provider to enable AI-generated commentary. Without this, the Arena uses template-based narratives.
        </p>

        <div className="space-y-3">
          <div>
            <label className="text-[10px] text-arena-text-dim block mb-1">Provider</label>
            <select className="w-full bg-arena-bg border border-arena-border rounded-lg px-3 py-2 text-xs text-white">
              <option value="none">None (templates only)</option>
              <option value="ollama">Ollama (local)</option>
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </div>

          <div>
            <label className="text-[10px] text-arena-text-dim block mb-1">Model</label>
            <input
              type="text"
              placeholder="llama3 / gpt-4o-mini / claude-3-5-sonnet"
              className="w-full bg-arena-bg border border-arena-border rounded-lg px-3 py-2 text-xs text-white placeholder:text-arena-text-dim"
            />
          </div>

          <div>
            <label className="text-[10px] text-arena-text-dim block mb-1">API Key (if needed)</label>
            <input
              type="password"
              placeholder="sk-..."
              className="w-full bg-arena-bg border border-arena-border rounded-lg px-3 py-2 text-xs text-white placeholder:text-arena-text-dim"
            />
          </div>
        </div>

        <div className="mt-4 pt-4 border-t border-arena-border">
          <p className="text-[10px] text-arena-text-dim">
            These settings are read from environment variables on the backend. Set them in your <code className="text-arena-blue">.env</code> file:
          </p>
          <pre className="mt-2 text-[10px] font-mono text-arena-text-secondary bg-arena-bg p-2 rounded">
{`ARENA_LLM_PROVIDER=ollama
ARENA_LLM_MODEL=llama3
ARENA_LLM_BASE_URL=http://localhost:11434
# or
ARENA_LLM_PROVIDER=openai
ARENA_LLM_API_KEY=sk-...`}
          </pre>
        </div>
      </div>

      <div className="card-base p-4 max-w-lg mt-4">
        <h2 className="text-sm font-semibold text-white mb-3">Data Refresh</h2>
        <div className="space-y-2 text-[11px] text-arena-text-secondary">
          <div className="flex items-center justify-between">
            <span>Full arena data poll</span>
            <span className="font-mono text-white">30s</span>
          </div>
          <div className="flex items-center justify-between">
            <span>Commentary refresh</span>
            <span className="font-mono text-white">45s</span>
          </div>
          <div className="flex items-center justify-between">
            <span>WebSocket events</span>
            <span className="font-mono text-arena-green">real-time</span>
          </div>
        </div>
      </div>
    </div>
  );
}
