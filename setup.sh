#!/bin/bash
# ============================================================
# AI-Trader Full Setup Script
# Works on macOS (Intel + Apple Silicon) and Linux.
# Installs: Homebrew, PostgreSQL, Python venv, Node.js, all deps,
#           creates database, builds frontend, starts server.
#
# Usage:
#   chmod +x setup.sh && ./setup.sh
#
# To start fresh on a new machine, just clone the repo and run this.
# ============================================================

set -euo pipefail

# --- Config ---
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_NAME="ai_trader"
DB_USER="ai_trader"
DB_PASS="ai_trader_local"
DB_PORT="5432"
PYTHON="${PYTHON:-python3}"
NODE_VERSION="${NODE_VERSION:-20}"

cd "$PROJECT_DIR"

echo "============================================"
echo "  AI-Trader Setup"
echo "  Project: $PROJECT_DIR"
echo "============================================"

# ============================================================
# 1. Homebrew (macOS only)
# ============================================================
if [[ "$OSTYPE" == "darwin"* ]]; then
  if ! command -v brew &>/dev/null; then
    echo "[1/8] Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for Apple Silicon
    if [[ -f /opt/homebrew/bin/brew ]]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
  else
    echo "[1/8] Homebrew already installed."
  fi
else
  echo "[1/8] Not macOS, skipping Homebrew."
fi

# ============================================================
# 2. PostgreSQL
# ============================================================
echo "[2/8] Setting up PostgreSQL..."

if [[ "$OSTYPE" == "darwin"* ]]; then
  if ! brew list postgresql@16 &>/dev/null 2>&1; then
    brew install postgresql@16
  fi
  # Start PostgreSQL
  brew services start postgresql@16 2>/dev/null || true
  # Wait for it to be ready
  export PATH="/opt/homebrew/opt/postgresql@16/bin:/usr/local/opt/postgresql@16/bin:$PATH"
elif command -v psql &>/dev/null; then
  echo "  PostgreSQL already installed."
  sudo systemctl start postgresql 2>/dev/null || sudo service postgresql start 2>/dev/null || true
else
  echo "  Please install PostgreSQL manually for your Linux distro."
  echo "  e.g. sudo apt install postgresql postgresql-contrib"
  exit 1
fi

# Wait for PostgreSQL to accept connections
echo "  Waiting for PostgreSQL to be ready..."
for i in $(seq 1 30); do
  if pg_isready -q 2>/dev/null; then
    echo "  PostgreSQL is ready."
    break
  fi
  sleep 1
  if [ $i -eq 30 ]; then
    echo "  ERROR: PostgreSQL did not become ready in 30 seconds."
    exit 1
  fi
done

# Create database and user
echo "  Creating database '$DB_NAME' and user '$DB_USER'..."
psql postgres -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DB_USER') THEN CREATE ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASS'; END IF; END \$\$;" 2>/dev/null || \
  psql -U postgres -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DB_USER') THEN CREATE ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASS'; END IF; END \$\$;" 2>/dev/null || \
  echo "  (User may already exist or using peer auth — continuing)"

psql postgres -c "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1 || \
  createdb -U postgres "$DB_NAME" 2>/dev/null || \
  createdb "$DB_NAME" 2>/dev/null || \
  echo "  (Database may already exist — continuing)"

psql postgres -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;" 2>/dev/null || \
  psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;" 2>/dev/null || true

echo "  PostgreSQL ready: db=$DB_NAME user=$DB_USER"

# ============================================================
# 3. Python virtual environment + dependencies
# ============================================================
echo "[3/8] Setting up Python..."

if [[ "$OSTYPE" == "darwin"* ]] && ! command -v python3 &>/dev/null; then
  brew install python@3.12
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  echo "  Created .venv"
fi

source .venv/bin/activate
pip install --upgrade pip wheel
pip install -r service/requirements.txt
echo "  Python dependencies installed."

# ============================================================
# 4. Node.js + frontend dependencies
# ============================================================
echo "[4/8] Setting up Node.js + frontend..."

if ! command -v node &>/dev/null; then
  if [[ "$OSTYPE" == "darwin"* ]]; then
    brew install node@${NODE_VERSION}
    brew link node@${NODE_VERSION} --overwrite 2>/dev/null || true
  else
    echo "  Please install Node.js v${NODE_VERSION}+ manually."
    exit 1
  fi
fi

echo "  Node version: $(node --version)"

cd service/frontend
if [ ! -d node_modules ]; then
  npm install
fi
npm run build
cd "$PROJECT_DIR"
echo "  Frontend built."

# ============================================================
# 5. .env configuration
# ============================================================
echo "[5/8] Configuring .env..."

if [ ! -f .env ]; then
  cp .env.example .env
fi

# Set DATABASE_URL to PostgreSQL
set_env_var() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" .env; then
    sed -i.bak "s|^${key}=.*|${key}=${value}|" .env && rm -f .env.bak
  else
    echo "${key}=${value}" >> .env
  fi
}

set_env_var "DATABASE_URL" "postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:${DB_PORT}/${DB_NAME}"
set_env_var "AI_TRADER_API_BACKGROUND_TASKS" "true"

echo "  .env configured with PostgreSQL."

# ============================================================
# 6. Initialize database schema
# ============================================================
echo "[6/8] Initializing database schema..."
python -c "
import sys
sys.path.insert(0, 'service/server')
from database import init_database, get_database_status
init_database()
status = get_database_status()
print(f'  Database ready: {status.get(\"backend\")}')
"
echo "  Schema initialized."

# ============================================================
# 7. DB backup cron job
# ============================================================
echo "[7/8] Setting up DB backup cron..."
chmod +x service/server/scripts/backup_db.sh

# Adapt backup script for PostgreSQL
cat > service/server/scripts/backup_db.sh << 'BACKUP_EOF'
#!/bin/bash
# PostgreSQL DB backup script — creates timestamped compressed dumps with retention.
set -euo pipefail

DB_NAME="${DB_NAME:-ai_trader}"
DB_USER="${DB_USER:-ai_trader}"
BACKUP_DIR="${BACKUP_DIR:-$(cd "$(dirname "$0")" && pwd)/../data/backups}"
RETENTION_DAYS=7
MAX_BACKUPS=200

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/ai_trader_${TIMESTAMP}.sql.gz"

pg_dump -U "$DB_USER" "$DB_NAME" 2>/dev/null | gzip > "$BACKUP_FILE"

if [ ! -s "$BACKUP_FILE" ]; then
  echo "[backup] Failed to create backup"
  rm -f "$BACKUP_FILE"
  exit 1
fi

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[backup] Created $BACKUP_FILE ($SIZE)"

# Prune old backups
find "$BACKUP_DIR" -name "ai_trader_*.sql.gz" -mtime +$RETENTION_DAYS -delete 2>/dev/null || true

BACKUP_COUNT=$(find "$BACKUP_DIR" -name "ai_trader_*.sql.gz" | wc -l | tr -d ' ')
if [ "$BACKUP_COUNT" -gt "$MAX_BACKUPS" ]; then
  find "$BACKUP_DIR" -name "ai_trader_*.sql.gz" -type f | sort | head -n $((BACKUP_COUNT - MAX_BACKUPS)) | xargs rm -f
fi

echo "[backup] Done. Total backups: $(find "$BACKUP_DIR" -name 'ai_trader_*.sql.gz' | wc -l | tr -d ' ')"
BACKUP_EOF
chmod +x service/server/scripts/backup_db.sh

# Add cron job (every 15 minutes)
CRON_LINE="*/15 * * * * $PROJECT_DIR/service/server/scripts/backup_db.sh >> $PROJECT_DIR/service/server/logs/backup.log 2>&1"
(crontab -l 2>/dev/null | grep -v "backup_db.sh"; echo "$CRON_LINE") | crontab - 2>/dev/null || echo "  (Could not set cron — set up manually if needed)"
echo "  Backup cron configured (every 15 min)."

# Run first backup
service/server/scripts/backup_db.sh 2>&1 || echo "  (First backup skipped — will run on next cron)"

# ============================================================
# 8. Start server
# ============================================================
echo "[8/8] Starting server..."
pkill -f "service/server/main.py" 2>/dev/null || true
sleep 1

.venv/bin/python service/server/main.py > /tmp/ai-trader-server.log 2>&1 &
SERVER_PID=$!
echo "  Server started (PID=$SERVER_PID)"
echo "  Logs: /tmp/ai-trader-server.log"

sleep 3
if curl -s http://localhost:8000/api/background-tasks/status | grep -q "active_count"; then
  echo "  Server is running with background tasks."
else
  echo "  WARNING: Server may not have started cleanly. Check /tmp/ai-trader-server.log"
fi

echo ""
echo "============================================"
echo "  Setup Complete!"
echo "============================================"
echo ""
echo "  Server:   http://localhost:8000"
echo "  Frontend: http://localhost:3000 (dev) or served at :8000 (built)"
echo "  Database: PostgreSQL @ 127.0.0.1:5432/$DB_NAME"
echo "  Backups:  service/server/data/backups/ (every 15 min)"
echo ""
echo "  To start frontend dev server:"
echo "    cd service/frontend && npm run dev"
echo ""
echo "  To stop server:"
echo "    pkill -f 'service/server/main.py'"
echo ""
echo "  To restart server:"
echo "    .venv/bin/python service/server/main.py"
echo ""
