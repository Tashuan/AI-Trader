# Local Dev Server — Start / Restart Guide

## Prerequisites

- Python virtualenv at `.venv/` (has `psycopg`, `fastapi`, etc.)
- `.env` file at project root with `DATABASE_URL` set (PostgreSQL/Supabase)
- Node.js + npm for the Arena frontend (Vite dev server)

## 1. Backend API Server (port 8000)

### Check if running

```bash
lsof -iTCP:8000 -sTCP:LISTEN
```

### Start

```bash
cd /Users/tashuanspence/Development/ai-trader
.venv/bin/python service/server/main.py
```

Runs in foreground. Use `&` or a terminal tab for background.

### Restart

```bash
# Find the PID
lsof -iTCP:8000 -sTCP:LISTEN

# Kill it
kill <PID>

# Wait a moment, then start again
sleep 2
.venv/bin/python service/server/main.py
```

### Verify

```bash
curl -s http://localhost:8000/api/arena/state
# Should return: {"states":{}} or agent state data
```

## 2. Arena Frontend (Vite dev server, port 5173 or 5174)

### Check if running

```bash
lsof -iTCP:5173 -sTCP:LISTEN
lsof -iTCP:5174 -sTCP:LISTEN
```

### Start

```bash
cd /Users/tashuanspence/Development/ai-trader/service/arena
npm run dev
```

### Restart

The Vite dev server has HMR (Hot Module Replacement). TypeScript/CSS changes
auto-reload in the browser — no restart needed for frontend-only changes.

Only restart if you change `vite.config.ts`, `package.json`, or installed
new npm packages:

```bash
# Find and kill the Vite process
lsof -iTCP:5173 -sTCP:LISTEN
kill <PID>

# Restart
cd /Users/tashuanspence/Development/ai-trader/service/arena
npm run dev
```

### Verify

Open `http://localhost:5173` (or whichever port Vite prints) in the browser.

## 3. Start/Stop Agent Bots

Bots are managed in-process by the backend via `bot_manager.py`. Use the API:

```bash
# Start a bot
curl -X POST http://localhost:8000/api/arena/bot/newshound/start

# Stop a bot
curl -X POST http://localhost:8000/api/arena/bot/newshound/stop

# Check all bot statuses
curl -s http://localhost:8000/api/arena/bots | python3 -m json.tool
```

Available agent keys: `newshound`, `chartmaster`, `fademaster`, `blitztrader`

## 4. Full Restart Sequence (backend + frontend)

```bash
cd /Users/tashuanspence/Development/ai-trader

# Kill backend
kill $(lsof -tiTCP:8000 -sTCP:LISTEN) 2>/dev/null
sleep 2

# Start backend
.venv/bin/python service/server/main.py &

# Verify backend
sleep 3
curl -s http://localhost:8000/api/arena/state

# Frontend (if not already running)
cd service/arena && npm run dev
```

## 5. TypeScript Type Check

```bash
cd /Users/tashuanspence/Development/ai-trader/service/arena
npx tsc --noEmit
```

## 6. Python Syntax Check

```bash
cd /Users/tashuanspence/Development/ai-trader
.venv/bin/python -c "import py_compile; py_compile.compile('service/server/main.py', doraise=True)"
```

## Notes

- The backend uses PostgreSQL via `DATABASE_URL` in `.env`. The system Python
  does not have `psycopg` — always use `.venv/bin/python`.
- The `.env` file is at project root, not in `service/server/`.
- The Arena frontend proxies `/api` and `/ws` to `localhost:8000` via Vite config.
- Bot threads are in-process (not subprocesses). Restarting the backend kills
  all running bots — re-start them via the API after restart.
