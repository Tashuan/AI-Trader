import { useState } from 'react';
import { AlertTriangle, Loader2, X } from 'lucide-react';

export function SettingsPage() {
  const [showResetModal, setShowResetModal] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetResult, setResetResult] = useState<{ success: boolean; message: string } | null>(null);

  const handleReset = async () => {
    setResetting(true);
    setResetResult(null);
    try {
      const resp = await fetch('/api/arena/reset-portfolio', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('auth_token') || ''}`,
        },
      });
      const json = await resp.json();
      if (resp.ok) {
        setResetResult({ success: true, message: json.message || 'Portfolio reset complete.' });
      } else {
        setResetResult({ success: false, message: json.detail || 'Reset failed.' });
      }
    } catch {
      setResetResult({ success: false, message: 'Network error. Reset may have partially completed.' });
    } finally {
      setResetting(false);
    }
  };

  const closeModal = () => {
    setShowResetModal(false);
    setResetResult(null);
  };

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

      {/* Danger Zone */}
      <div className="card-base p-4 max-w-lg mt-4 border-red-500/30">
        <h2 className="text-sm font-semibold text-red-400 mb-2 flex items-center gap-1.5">
          <AlertTriangle size={14} />
          Danger Zone
        </h2>
        <p className="text-[11px] text-arena-text-secondary mb-3">
          Reset all trading data: closes all positions, clears signals & profit history, and resets every agent's cash to $10,000. Agent identities, configs, and workflows are preserved.
        </p>
        <button
          onClick={() => setShowResetModal(true)}
          className="px-3 py-1.5 text-xs font-semibold text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg hover:bg-red-500/20 transition-colors"
        >
          Reset Portfolio
        </button>
      </div>

      {/* Confirmation Modal */}
      {showResetModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={closeModal}
        >
          <div
            className="bg-arena-card border border-arena-border rounded-lg p-5 max-w-sm w-full mx-4 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-bold text-white flex items-center gap-1.5">
                <AlertTriangle size={14} className="text-red-400" />
                Reset Portfolio
              </h3>
              <button onClick={closeModal} className="text-arena-text-dim hover:text-white">
                <X size={16} />
              </button>
            </div>

            {!resetResult && (
              <>
                <p className="text-xs text-arena-text-secondary mb-4">
                  This will permanently delete all positions, trades, signals, and profit history. All agents will be reset to $100,000 cash. <strong className="text-red-400">This cannot be undone.</strong>
                </p>
                <div className="flex gap-2 justify-end">
                  <button
                    onClick={closeModal}
                    className="px-3 py-1.5 text-xs text-arena-text-dim hover:text-white transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleReset}
                    disabled={resetting}
                    className="px-3 py-1.5 text-xs font-semibold text-white bg-red-500/80 hover:bg-red-500 rounded-lg disabled:opacity-50 transition-colors flex items-center gap-1.5"
                  >
                    {resetting && <Loader2 size={12} className="animate-spin" />}
                    {resetting ? 'Resetting...' : 'Yes, Reset Everything'}
                  </button>
                </div>
              </>
            )}

            {resetResult && (
              <>
                <p className={`text-xs mb-4 ${resetResult.success ? 'text-arena-green' : 'text-red-400'}`}>
                  {resetResult.message}
                </p>
                <div className="flex justify-end">
                  <button
                    onClick={closeModal}
                    className="px-3 py-1.5 text-xs font-semibold text-white bg-arena-bg border border-arena-border rounded-lg hover:bg-arena-border transition-colors"
                  >
                    Close
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
