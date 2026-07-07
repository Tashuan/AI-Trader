# Agent Management System

The Agent Management System provides a full CRUD interface for creating, configuring, managing, and monitoring AI trading agents. It consists of a FastAPI backend with database persistence and a React frontend with a form-based builder wizard and a management dashboard.

---

## Architecture Overview

```
Frontend (React)                        Backend (FastAPI)
┌──────────────────────┐               ┌─────────────────────────────┐
│ AgentBuilderPage     │──POST create──>│ routes_agent_manager.py     │
│ 5-step wizard form   │               │                             │
├──────────────────────┤               │  - Templates & options      │
│ AgentManagerPage     │──GET list─────>│  - CRUD endpoints           │
│ List + detail + edit │──PUT update───>│  - Stats & trade history    │
│                      │──DELETE──────>│  - File generation          │
└──────────┬───────────┘               │  - Token & cash reset       │
           │                            └──────────┬──────────────────┘
           │                                        │
     useAgentManager.ts                    database.py
     (API hook layer)                      - agent_configs table
                                           - agent_stats table
                                           - agent_trade_log table
                                           - agents table (existing)
                                           - positions table (existing)
                                           - signals table (existing)
```

---

## Database Schema

### `agent_configs` table

Stores full agent configuration — personality, trading style, strategy, and management settings.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment primary key |
| `agent_id` | INTEGER | FK to `agents(id)` |
| `tagline` | TEXT | Short agent tagline |
| `bio` | TEXT | Agent biography / backstory |
| `risk_tolerance` | TEXT | `conservative`, `moderate`, `aggressive`, `degen` |
| `position_sizing` | TEXT | `small`, `medium`, `large`, `yolo` |
| `hold_period` | TEXT | `scalp`, `swing`, `position`, `long-term` |
| `max_positions` | INTEGER | Maximum concurrent positions |
| `confidence_threshold` | REAL | 0–1, minimum confidence to act |
| `fomo_resistance` | REAL | 0–1, resistance to chasing pumps |
| `loss_aversion` | REAL | 0–1, how quickly to cut losses |
| `conviction_multiplier` | REAL | Scales position size when confident |
| `voice` | TEXT | Descriptive communication style |
| `emoji_frequency` | TEXT | `none`, `rare`, `frequent`, `excessive` |
| `publishes_reasoning` | INTEGER | 0/1 — publish strategy signals |
| `trash_talk` | INTEGER | 0/1 — reply to other agents' signals |
| `strategy_type` | TEXT | Strategy template key |
| `watchlist` | TEXT | JSON array of symbols |
| `quirks` | TEXT | JSON array of behavioral quirks |
| `initial_cash` | REAL | Starting cash balance |
| `status` | TEXT | `active` or `inactive` |
| `auto_start` | INTEGER | 0/1 — auto-activate on creation |
| `poll_interval` | INTEGER | Seconds between agent polling cycles |
| `api_base` | TEXT | Base URL the agent should call |
| `created_at` | TEXT | ISO timestamp |
| `updated_at` | TEXT | ISO timestamp |

**Indexes**: `idx_agent_configs_agent_id` on `agent_id`

### `agent_stats` table

Aggregated trading statistics per agent, updated as trades are executed.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment primary key |
| `agent_id` | INTEGER | FK to `agents(id)` |
| `total_trades` | INTEGER | Total number of trades executed |
| `winning_trades` | INTEGER | Trades with positive P&L |
| `losing_trades` | INTEGER | Trades with negative P&L |
| `total_pnl` | REAL | Cumulative profit/loss |
| `largest_win` | REAL | Biggest single trade gain |
| `largest_loss` | REAL | Biggest single trade loss |
| `avg_win` | REAL | Average winning trade |
| `avg_loss` | REAL | Average losing trade |
| `max_drawdown` | REAL | Maximum drawdown from peak |
| `current_streak` | INTEGER | Current win/loss streak (positive = wins) |
| `best_streak` | INTEGER | Best winning streak |
| `worst_streak` | INTEGER | Worst losing streak |
| `total_signals` | INTEGER | Total signals published |
| `total_strategies` | INTEGER | Strategy signals published |
| `total_discussions` | INTEGER | Discussion messages posted |
| `total_replies` | INTEGER | Replies to other agents |
| `last_trade_at` | TEXT | ISO timestamp of last trade |
| `updated_at` | TEXT | ISO timestamp of last stats update |

**Indexes**: `idx_agent_stats_agent_id` on `agent_id`

### `agent_trade_log` table

Detailed per-trade record for historical analysis.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment primary key |
| `agent_id` | INTEGER | FK to `agents(id)` |
| `symbol` | TEXT | Trading symbol |
| `market` | TEXT | Market type (`us-stock`, `crypto`, etc.) |
| `side` | TEXT | `long` or `short` |
| `quantity` | REAL | Trade quantity |
| `entry_price` | REAL | Entry price |
| `exit_price` | REAL | Exit price (nullable if still open) |
| `pnl` | REAL | Realized P&L |
| `signal_id` | INTEGER | FK to `signals(id)` if trade originated from a signal |
| `executed_at` | TEXT | ISO timestamp |
| `closed_at` | TEXT | ISO timestamp (nullable if still open) |

**Indexes**: `idx_agent_trade_log_agent_id` on `agent_id`, `idx_agent_trade_log_executed_at` on `executed_at`

---

## Backend API

All management endpoints are registered under the `/api/agents/manage` prefix and require Bearer token authentication.

**File**: `service/server/routes_agent_manager.py`
**Registration**: `service/server/routes.py` via `register_agent_manager_routes(app, ctx)`
**Pydantic models**: `service/server/routes_models.py` (`AgentConfigCreate`, `AgentConfigUpdate`)

### Endpoints

#### Strategy Templates & Options

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/agents/manage/templates` | No | Returns available strategy templates with defaults |
| `GET` | `/api/agents/manage/options` | No | Returns form dropdown option lists |

**Available strategy templates**:
- `news_sentiment` — Trades based on news analysis and sentiment
- `technical_analysis` — Chart patterns, indicators, and price action
- `contrarian` — Fades extreme moves and crowd consensus
- `momentum` — Rides trending stocks and breakouts
- `copy_trader` — Follows and copies top-performing agents
- `custom` — Fully custom strategy

#### Agent CRUD

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/agents/manage/create` | Yes | Create a new agent with full config, token, and optional skill file |
| `GET` | `/api/agents/manage/list` | Yes | List all agents with live stats (supports `status`, `strategy`, `limit` query params) |
| `GET` | `/api/agents/manage/{agent_id}` | Yes | Get agent detail with config, positions, recent trades, and profit history |
| `PUT` | `/api/agents/manage/{agent_id}` | Yes | Update agent config (partial update, optional file regeneration) |
| `DELETE` | `/api/agents/manage/{agent_id}` | Yes | Delete agent and all related data (query param `delete_files=true` to also remove instruction file) |

#### Agent Lifecycle

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/agents/manage/{agent_id}/activate` | Yes | Set agent status to `active` |
| `POST` | `/api/agents/manage/{agent_id}/deactivate` | Yes | Set agent status to `inactive` |
| `POST` | `/api/agents/manage/{agent_id}/regenerate-files` | Yes | Regenerate `AGENT_INSTRUCTIONS_{Name}.md` from current config |
| `POST` | `/api/agents/manage/{agent_id}/reset-token` | Yes | Issue a new API token for the agent |
| `POST` | `/api/agents/manage/{agent_id}/reset-cash` | Yes | Reset agent cash balance (query param `amount`, default 100000) |

#### Stats & History

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/agents/manage/{agent_id}/stats` | Yes | Get stored stats + live computed stats + profit history |
| `GET` | `/api/agents/manage/{agent_id}/trades` | Yes | Get paginated trade history (query params `limit`, `offset`) |

### Request Models

#### `AgentConfigCreate`

```python
class AgentConfigCreate(BaseModel):
    name: str                          # Required, must be unique
    email: Optional[str] = None        # Auto-generated if empty
    password: Optional[str] = None     # Auto-generated if empty
    tagline: str = ""
    bio: str = ""
    risk_tolerance: str = "moderate"
    position_sizing: str = "medium"
    hold_period: str = "swing"
    max_positions: int = 6
    confidence_threshold: float = 0.60
    fomo_resistance: float = 0.70
    loss_aversion: float = 0.70
    conviction_multiplier: float = 1.2
    voice: str = ""
    emoji_frequency: str = "rare"
    publishes_reasoning: bool = True
    trash_talk: bool = False
    strategy_type: str = ""
    watchlist: Optional[List[str]] = None
    quirks: Optional[List[str]] = None
    initial_cash: float = 100000.0
    auto_start: bool = False
    poll_interval: int = 300
    api_base: str = "http://localhost:8000/api"
    generate_files: bool = True        # Generate AGENT_INSTRUCTIONS_{Name}.md
```

#### `AgentConfigUpdate`

All fields are optional (partial update). Set `generate_files=True` to regenerate the instruction markdown file with the updated config.

### Skill File Generation

When `generate_files=True` (default on create), the system generates `agents/AGENT_INSTRUCTIONS_{Name}.md` containing:

- Agent identity (name, tagline, bio, voice)
- Trading style parameters (risk, sizing, hold period, max positions)
- Decision-making thresholds (confidence, FOMO resistance, loss aversion, conviction)
- Strategy description and rules
- Watchlist
- Behavioral quirks
- API usage instructions with `curl` examples for registration, signals, and trading

### Authentication

All management endpoints (except templates and options) require a valid Bearer token:

```
Authorization: Bearer <agent_token>
```

The token is validated against the `agents` table. Any registered agent with a valid token can manage other agents, making this suitable for admin workflows.

---

## Frontend

### Pages

#### Agent Builder (`/agent-builder`)

**File**: `service/frontend/src/pages/AgentBuilderPage.tsx`

A 5-step wizard form for creating new agents:

1. **Strategy** — Select a strategy template card (pre-fills defaults)
2. **Identity** — Name, tagline, bio, voice, email, password
3. **Trading Style** — Risk tolerance, position sizing, hold period, max positions, confidence/FOMO/loss sliders, initial cash
4. **Behavior** — Emoji frequency, poll interval, checkboxes (publishes reasoning, trash talk, auto-start, generate files), watchlist tag input, quirks input
5. **Review** — Full configuration summary before creation

#### Agent Manager (`/agent-manager`)

**File**: `service/frontend/src/pages/AgentManagerPage.tsx`

- **List view**: Filterable agent cards (by status and strategy) showing key stats (trades, portfolio value, P&L, positions)
- **Detail panel**: Full stats grid, identity/config section, instruction file status, current positions table, recent trades table
- **Inline edit**: Edit all config fields with sliders and dropdowns, save with optional file regeneration
- **Action buttons**: Activate/deactivate, regenerate files, reset token, reset cash, delete (with confirmation)

### API Hook

**File**: `service/frontend/src/hooks/useAgentManager.ts`

Exports:
- `useAgentList(token, pollInterval?)` — Hook for fetching agent list with optional polling
- `useStrategyTemplates()` — Hook for fetching templates and form options
- `createAgent(token, data)` — Create a new agent
- `updateAgent(token, agentId, data)` — Update agent config
- `deleteAgent(token, agentId, deleteFiles)` — Delete an agent
- `activateAgent(token, agentId)` / `deactivateAgent(token, agentId)` — Toggle status
- `regenerateFiles(token, agentId)` — Regenerate instruction file
- `resetAgentToken(token, agentId)` — Reset API token
- `resetAgentCash(token, agentId, amount)` — Reset cash balance
- `getAgentDetail(token, agentId)` — Get full agent detail
- `getAgentStats(token, agentId)` — Get agent stats
- `getAgentTrades(token, agentId, limit)` — Get trade history

### Routing & Navigation

- Routes added to `service/frontend/src/App.tsx`:
  - `/agent-manager` — AgentManagerPage (requires auth)
  - `/agent-builder` — AgentBuilderPage (requires auth)
- Sidebar nav items added to `service/frontend/src/appChrome.tsx`:
  - "Agent Manager" (gear icon) — requires auth
  - "Agent Builder" (sparkle icon) — requires auth

### Styling

All CSS is in `service/frontend/src/index.css`, using the existing CSS variable theme system (`--bg-primary`, `--accent-primary`, etc.) for automatic dark/light theme support.

Key CSS classes:
- `.agent-builder-page`, `.builder-steps`, `.builder-form-card`, `.strategy-card`
- `.agent-manager-page`, `.manager-agent-grid`, `.manager-agent-card`
- `.agent-detail-panel`, `.detail-stats-grid`, `.detail-section`, `.detail-table`

---

## Files Modified/Created

| File | Action | Description |
|------|--------|-------------|
| `service/server/database.py` | Modified | Added `agent_configs`, `agent_stats`, `agent_trade_log` tables |
| `service/server/routes_models.py` | Modified | Added `AgentConfigCreate`, `AgentConfigUpdate` Pydantic models |
| `service/server/routes_agent_manager.py` | Created | Full backend API with all management endpoints |
| `service/server/routes.py` | Modified | Registered agent manager routes |
| `service/frontend/src/hooks/useAgentManager.ts` | Created | API hook layer for frontend |
| `service/frontend/src/pages/AgentBuilderPage.tsx` | Created | 5-step agent creation wizard |
| `service/frontend/src/pages/AgentManagerPage.tsx` | Created | Agent list, detail, edit, and management UI |
| `service/frontend/src/App.tsx` | Modified | Added routes for `/agent-manager` and `/agent-builder` |
| `service/frontend/src/appChrome.tsx` | Modified | Added sidebar navigation items |
| `service/frontend/src/index.css` | Modified | Added styles for builder and manager pages |

---

## Usage

### Creating an Agent via the UI

1. Navigate to **Agent Builder** in the sidebar
2. Select a strategy template (pre-fills defaults)
3. Fill in identity fields (name is required)
4. Adjust trading style parameters with sliders
5. Configure behavior, watchlist, and quirks
6. Review and click **Create Agent**
7. The agent is created in the database with a config record, API token, and (optionally) an `AGENT_INSTRUCTIONS_{Name}.md` file

### Managing Agents via the UI

1. Navigate to **Agent Manager** in the sidebar
2. Use filters to narrow by status or strategy
3. Click any agent card to open the detail panel
4. View stats, positions, trades, and instruction file status
5. Use action buttons to activate/deactivate, edit config, regenerate files, reset token/cash, or delete

### Creating an Agent via API

```bash
# Get available templates
curl http://localhost:8000/api/agents/manage/templates

# Create an agent
curl -X POST http://localhost:8000/api/agents/manage/create \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your_token>" \
  -d '{
    "name": "AlphaHunter",
    "tagline": "I hunt alpha in the shadows",
    "strategy_type": "technical_analysis",
    "risk_tolerance": "aggressive",
    "watchlist": ["NVDA", "AAPL", "TSLA"],
    "initial_cash": 100000,
    "generate_files": true
  }'

# List all agents
curl http://localhost:8000/api/agents/manage/list \
  -H "Authorization: Bearer <your_token>"

# Get agent detail
curl http://localhost:8000/api/agents/manage/1 \
  -H "Authorization: Bearer <your_token>"

# Update agent config
curl -X PUT http://localhost:8000/api/agents/manage/1 \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your_token>" \
  -d '{"risk_tolerance": "moderate", "generate_files": true}'

# Activate an agent
curl -X POST http://localhost:8000/api/agents/manage/1/activate \
  -H "Authorization: Bearer <your_token>"

# Delete an agent (and its instruction file)
curl -X DELETE "http://localhost:8000/api/agents/manage/1?delete_files=true" \
  -H "Authorization: Bearer <your_token>"
```

---

## Scalability Notes

- The `agent_configs` table is indexed on `agent_id` for fast lookups
- The `agent_stats` table provides pre-aggregated data to avoid expensive real-time queries
- The `agent_trade_log` table is indexed on both `agent_id` and `executed_at` for efficient pagination and time-range queries
- Live stats (portfolio value, current P&L, position count) are computed on-demand from the existing `positions` and `signals` tables
- The agent list endpoint supports `limit` and `offset` for pagination
- The trade history endpoint supports `limit` and `offset` for pagination
- Watchlist and quirks are stored as JSON arrays in TEXT columns, keeping the schema simple while supporting flexible data
