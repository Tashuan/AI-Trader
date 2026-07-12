# ============================================================
# AI-Trader Dockerfile
# Multi-stage build: frontend-legacy -> backend -> final image
# ============================================================

# --- Stage 1: Build legacy frontend ---
FROM node:20-slim AS frontend-builder

WORKDIR /app/service/frontend-legacy
COPY service/frontend-legacy/package.json service/frontend-legacy/package-lock.json* ./
RUN npm ci || npm install
COPY service/frontend-legacy/ ./
RUN npm run build

# --- Stage 2: Python backend ---
FROM python:3.12-slim AS backend

# Install system deps for psycopg and curl (for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY service/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy server code
COPY service/server/ ./service/server/

# Copy built frontend from stage 1
COPY --from=frontend-builder /app/service/frontend-legacy/dist/ ./service/frontend-legacy/dist/

# Copy skills, agents, etc.
COPY skills/ ./skills/
COPY agents/ ./agents/
COPY research/ ./research/

# Copy root files
COPY .env.example .env.example
COPY tsconfig.json ./

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/background-tasks/status || exit 1

CMD ["python", "service/server/main.py"]
