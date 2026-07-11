import { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Copy, Check, ChevronDown, ChevronUp, Bot, Play, Square, Wifi, WifiOff, Circle } from 'lucide-react';
import { Tooltip } from '../components/Tooltip';

interface PersonalityData {
  name: string;
  tagline: string;
  bio: string;
  goal: string;
  strategy_type: string;
  risk_tolerance: string;
  voice: string;
  quirks: string[];
  watchlist: string[];
  emoji_frequency: string;
  trash_talk: boolean;
  confidence_threshold: number;
}

interface AgentEntry {
  fileName: string;
  name: string;
}

interface BotStatus {
  running: boolean;
  pid: number | null;
}

interface RegisteredAgent {
  agent_id: number;
  name: string;
  online: boolean;
}

const ALL_AGENTS: AgentEntry[] = [
  { fileName: 'NewsHound', name: 'NewsHound' },
  { fileName: 'ChartMaster', name: 'ChartMaster' },
  { fileName: 'FadeMaster', name: 'FadeMaster' },
  { fileName: 'MomentumRider', name: 'MomentumRider' },
  { fileName: 'BlitzTrader', name: 'BlitzTrader' },
  { fileName: 'CopyCat', name: 'CopyCat' },
  { fileName: 'EventMaster', name: 'EventMaster' },
  { fileName: 'OpenSniper', name: 'OpenSniper' },
  { fileName: 'Prophet', name: 'Prophet' },
  { fileName: 'RangeRider', name: 'RangeRider' },
  { fileName: 'SpreadMaster', name: 'SpreadMaster' },
];

function buildAgentPrompt(agentsDir: string, fileName: string): string {
  return `Read the file ${agentsDir}/AGENT_INSTRUCTIONS_${fileName}.md and follow the instructions. Do NOT create Python scripts. Use curl commands to interact with the API and reason about each trade yourself. Register on the platform and run one trading cycle. Tell me what you found and did.`;
}

export function AgentsPage() {
  const [personalities, setPersonalities] = useState<Record<string, PersonalityData>>({});
  const [agentsDir, setAgentsDir] = useState<string>('');
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [botStatuses, setBotStatuses] = useState<Record<string, BotStatus>>({});
  const [registeredAgents, setRegisteredAgents] = useState<Record<string, RegisteredAgent>>({});
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchData = useCallback(() => {
    fetch('/api/arena/personalities')
      .then(r => r.json())
      .then(data => {
        setPersonalities(data.personalities || {});
        setAgentsDir(data.agents_dir || '');
        setLoading(false);
      })
      .catch(() => setLoading(false));

    fetch('/api/arena/bots')
      .then(r => r.json())
      .then(data => setBotStatuses(data.bots || {}))
      .catch(() => {});

    fetch('/api/arena/full')
      .then(r => r.json())
      .then(data => {
        const agents: Record<string, RegisteredAgent> = {};
        (data.agents || []).forEach((a: any) => {
          agents[a.name] = { agent_id: a.agent_id, name: a.name, online: a.online };
        });
        setRegisteredAgents(agents);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 15000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleCopy = (key: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 2000);
  };

  const handleStartBot = async (agentKey: string) => {
    setActionLoading(`start-${agentKey}`);
    try {
      await fetch(`/api/arena/bot/${agentKey}/start`, { method: 'POST' });
      fetchData();
    } catch (e) {
      console.error('Failed to start bot:', e);
    }
    setActionLoading(null);
  };

  const handleStopBot = async (agentKey: string) => {
    setActionLoading(`stop-${agentKey}`);
    try {
      await fetch(`/api/arena/bot/${agentKey}/stop`, { method: 'POST' });
      fetchData();
    } catch (e) {
      console.error('Failed to stop bot:', e);
    }
    setActionLoading(null);
  };

  const handleDisconnect = async (agentId: number, agentName: string) => {
    setActionLoading(`disconnect-${agentName}`);
    try {
      await fetch(`/api/arena/agent/${agentId}/disconnect`, { method: 'POST' });
      fetchData();
    } catch (e) {
      console.error('Failed to disconnect agent:', e);
    }
    setActionLoading(null);
  };

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-lg font-bold text-white mb-1">Agents</h1>
      <p className="text-xs text-arena-text-dim mb-6">
        Launch Python bots from the UI or copy the agent prompt into a new AI chat. When an AI agent logs in, it automatically takes over from any running bot.
      </p>

      {/* Legend */}
      <div className="flex items-center gap-4 mb-4 text-[10px] text-arena-text-dim">
        <div className="flex items-center gap-1.5">
          <Circle size={8} className="fill-arena-green text-arena-green" />
          <span>Bot running</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Wifi size={10} className="text-arena-blue" />
          <span>AI agent online</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Circle size={8} className="fill-arena-text-dim text-arena-text-dim" />
          <span>Offline</span>
        </div>
      </div>

      {/* Agent cards */}
      <div className="space-y-2">
        {ALL_AGENTS.map(agent => {
          const expanded = expandedKey === agent.name;
          const personality = personalities[agent.name];
          const prompt = buildAgentPrompt(agentsDir, agent.fileName);
          const copyKey = `prompt-${agent.name}`;
          const agentKey = agent.name.toLowerCase();
          const botStatus = botStatuses[agentKey];
          const botRunning = botStatus?.running || false;
          const registered = registeredAgents[agent.name];
          const aiOnline = registered?.online || false;
          const hasPersonality = !!personality;

          // Status indicator
          let statusColor = '#8B92A5';
          let statusLabel = 'Offline';
          if (botRunning) { statusColor = '#34D399'; statusLabel = 'Bot running'; }
          if (aiOnline) { statusColor = '#60A5FA'; statusLabel = 'AI agent online'; }

          return (
            <div key={agent.name} className="card-base overflow-hidden">
              {/* Agent header row */}
              <div
                className="p-4 flex items-center justify-between cursor-pointer card-hover"
                onClick={() => setExpandedKey(expanded ? null : agent.name)}
              >
                <div className="flex items-center gap-3">
                  <div className="relative">
                    <Avatar name={agent.name} />
                    <Tooltip text={statusLabel} side="right">
                      <div
                        className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-arena-card"
                        style={{ background: statusColor }}
                      />
                    </Tooltip>
                  </div>
                  <div>
                    <div className="text-sm font-semibold text-white">{agent.name}</div>
                    <div className="text-[10px] text-arena-text-dim">
                      {personality?.tagline || 'Agent instruction file available'}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  {/* Status badges */}
                  <div className="flex items-center gap-1.5">
                    {botRunning && (
                      <Tooltip text={`Python bot running (PID: ${botStatus.pid})`} side="bottom">
                        <span className="text-[9px] px-2 py-0.5 rounded-full bg-arena-green/15 text-arena-green flex items-center gap-1">
                          <Bot size={8} /> BOT
                        </span>
                      </Tooltip>
                    )}
                    {aiOnline && (
                      <Tooltip text="AI agent is online and actively trading" side="bottom">
                        <span className="text-[9px] px-2 py-0.5 rounded-full bg-arena-blue/15 text-arena-blue flex items-center gap-1">
                          <Wifi size={8} /> AI
                        </span>
                      </Tooltip>
                    )}
                  </div>

                  {/* Quick actions (don't expand on click) */}
                  <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    {hasPersonality && !botRunning && (
                      <Tooltip text="Launch Python bot for this agent" side="bottom">
                        <button
                          onClick={() => handleStartBot(agentKey)}
                          disabled={actionLoading === `start-${agentKey}`}
                          className="p-1.5 rounded-lg bg-arena-green/15 text-arena-green hover:bg-arena-green/25 transition-colors disabled:opacity-50"
                        >
                          <Play size={12} />
                        </button>
                      </Tooltip>
                    )}
                    {botRunning && (
                      <Tooltip text="Stop the running Python bot" side="bottom">
                        <button
                          onClick={() => handleStopBot(agentKey)}
                          disabled={actionLoading === `stop-${agentKey}`}
                          className="p-1.5 rounded-lg bg-arena-red/15 text-arena-red hover:bg-arena-red/25 transition-colors disabled:opacity-50"
                        >
                          <Square size={12} />
                        </button>
                      </Tooltip>
                    )}
                    {aiOnline && registered && (
                      <Tooltip text="Disconnect AI agent (rotates their token — they'll need to login again)" side="bottom">
                        <button
                          onClick={() => handleDisconnect(registered.agent_id, agent.name)}
                          disabled={actionLoading === `disconnect-${agent.name}`}
                          className="p-1.5 rounded-lg bg-arena-orange/15 text-arena-orange hover:bg-arena-orange/25 transition-colors disabled:opacity-50"
                        >
                          <WifiOff size={12} />
                        </button>
                      </Tooltip>
                    )}
                  </div>

                  {expanded ? <ChevronUp size={14} className="text-arena-text-dim" /> : <ChevronDown size={14} className="text-arena-text-dim" />}
                </div>
              </div>

              {/* Expanded details */}
              <AnimatePresence>
                {expanded && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.2 }}
                    className="overflow-hidden"
                  >
                    <div className="px-4 pb-4 pt-1 border-t border-arena-border space-y-3">
                      {/* Goal */}
                      {personality?.goal && (
                        <div>
                          <div className="text-[9px] text-arena-text-dim mb-0.5">GOAL</div>
                          <div className="text-[11px] text-arena-purple">{personality.goal}</div>
                        </div>
                      )}

                      {/* Bio */}
                      {personality?.bio && (
                        <div>
                          <div className="text-[9px] text-arena-text-dim mb-0.5">BIO</div>
                          <div className="text-[11px] text-arena-text-secondary leading-relaxed">{personality.bio}</div>
                        </div>
                      )}

                      {/* Watchlist */}
                      {personality?.watchlist && personality.watchlist.length > 0 && (
                        <div>
                          <div className="text-[9px] text-arena-text-dim mb-1">WATCHLIST</div>
                          <div className="flex flex-wrap gap-1.5">
                            {personality.watchlist.map(sym => (
                              <span key={sym} className="text-[10px] font-mono px-2 py-0.5 bg-arena-bg rounded text-arena-text-secondary">
                                {sym}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Quirks */}
                      {personality?.quirks && personality.quirks.length > 0 && (
                        <div>
                          <div className="text-[9px] text-arena-text-dim mb-1">QUIRKS</div>
                          <div className="space-y-0.5">
                            {personality.quirks.map((q, i) => (
                              <div key={i} className="text-[10px] text-arena-text-secondary italic">— {q}</div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Agent Prompt */}
                      <div>
                        <div className="text-[9px] text-arena-text-dim mb-1">AGENT PROMPT</div>
                        <div className="bg-arena-bg rounded-lg p-3">
                          <p className="text-[11px] text-arena-green font-mono leading-relaxed">{prompt}</p>
                        </div>
                        <div className="flex items-center gap-2 mt-2">
                          <Tooltip text="Copies the prompt to your clipboard — paste it into a new AI chat session" side="bottom">
                            <button
                              onClick={(e) => { e.stopPropagation(); handleCopy(copyKey, prompt); }}
                              className="px-3 py-1.5 bg-arena-purple/15 text-arena-purple rounded-lg hover:bg-arena-purple/25 transition-colors flex items-center gap-1.5"
                            >
                              {copied === copyKey ? <Check size={12} /> : <Copy size={12} />}
                              <span className="text-[10px] font-semibold">{copied === copyKey ? 'Copied!' : 'Copy Agent Prompt'}</span>
                            </button>
                          </Tooltip>
                          <span className="text-[9px] text-arena-text-dim">
                            Paste into a new AI chat to launch {agent.name}
                          </span>
                        </div>
                      </div>

                      {/* Python Bot Controls */}
                      {hasPersonality && (
                        <div className="pt-2 border-t border-arena-border/50">
                          <div className="text-[9px] text-arena-text-dim mb-2">PYTHON BOT</div>
                          <div className="flex items-center gap-2">
                            {botRunning ? (
                              <>
                                <div className="flex-1 flex items-center gap-2 text-[10px] text-arena-green">
                                  <Circle size={8} className="fill-arena-green text-arena-green animate-pulse" />
                                  <span>Running (PID: {botStatus.pid})</span>
                                </div>
                                <Tooltip text="Stops the bot process — the agent will stop trading" side="left">
                                  <button
                                    onClick={(e) => { e.stopPropagation(); handleStopBot(agentKey); }}
                                    disabled={actionLoading === `stop-${agentKey}`}
                                    className="px-3 py-1.5 bg-arena-red/15 text-arena-red rounded-lg hover:bg-arena-red/25 transition-colors flex items-center gap-1.5 disabled:opacity-50"
                                  >
                                    <Square size={12} />
                                    <span className="text-[10px] font-semibold">{actionLoading === `stop-${agentKey}` ? 'Stopping...' : 'Stop Bot'}</span>
                                  </button>
                                </Tooltip>
                              </>
                            ) : (
                              <>
                                <div className="flex-1 text-[10px] text-arena-text-dim">
                                  Not running — launch to start algorithmic trading
                                </div>
                                <Tooltip text="Starts a Python bot that trades using hardcoded strategy rules (no AI reasoning)" side="left">
                                  <button
                                    onClick={(e) => { e.stopPropagation(); handleStartBot(agentKey); }}
                                    disabled={actionLoading === `start-${agentKey}`}
                                    className="px-3 py-1.5 bg-arena-green/15 text-arena-green rounded-lg hover:bg-arena-green/25 transition-colors flex items-center gap-1.5 disabled:opacity-50"
                                  >
                                    <Play size={12} />
                                    <span className="text-[10px] font-semibold">{actionLoading === `start-${agentKey}` ? 'Starting...' : 'Launch Bot'}</span>
                                  </button>
                                </Tooltip>
                              </>
                            )}
                          </div>
                          {aiOnline && botRunning && (
                            <div className="text-[9px] text-arena-orange mt-2">
                              ⚠ AI agent is also online — if the AI agent logs in again, the bot will be auto-stopped.
                            </div>
                          )}
                        </div>
                      )}

                      {/* AI Agent Disconnect */}
                      {aiOnline && registered && (
                        <div className="pt-2 border-t border-arena-border/50">
                          <div className="text-[9px] text-arena-text-dim mb-2">AI AGENT SESSION</div>
                          <div className="flex items-center gap-2">
                            <div className="flex-1 flex items-center gap-2 text-[10px] text-arena-blue">
                              <Wifi size={10} />
                              <span>Online (Agent ID: {registered.agent_id})</span>
                            </div>
                            <Tooltip text="Rotates the agent's API token — their next API call will fail with 401 and they'll need to login again" side="left">
                              <button
                                onClick={(e) => { e.stopPropagation(); handleDisconnect(registered.agent_id, agent.name); }}
                                disabled={actionLoading === `disconnect-${agent.name}`}
                                className="px-3 py-1.5 bg-arena-orange/15 text-arena-orange rounded-lg hover:bg-arena-orange/25 transition-colors flex items-center gap-1.5 disabled:opacity-50"
                              >
                                <WifiOff size={12} />
                                <span className="text-[10px] font-semibold">{actionLoading === `disconnect-${agent.name}` ? 'Disconnecting...' : 'Disconnect'}</span>
                              </button>
                            </Tooltip>
                          </div>
                        </div>
                      )}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          );
        })}
      </div>

      {loading && (
        <div className="text-center text-arena-text-dim text-xs mt-4">Loading personality data...</div>
      )}
    </div>
  );
}

function Avatar({ name }: { name: string }) {
  const seed = name.toLowerCase().replace(/[^a-z0-9]/g, '');
  const hue = seed.charCodeAt(0) * 37 % 360;
  const initials = name.split(/(?=[A-Z])/).map(w => w[0]).join('').slice(0, 2).toUpperCase();

  return (
    <div
      className="w-8 h-8 rounded-lg flex items-center justify-center text-[10px] font-bold text-white shrink-0"
      style={{
        background: `linear-gradient(135deg, hsl(${hue}, 60%, 30%), hsl(${(hue + 40) % 360}, 60%, 20%))`,
        border: '1px solid rgba(255,255,255,.08)',
      }}
    >
      {initials}
    </div>
  );
}
