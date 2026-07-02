# syntax=docker/dockerfile:1

# ---- Stage 1: build the web UI ----
FROM node:26-alpine AS frontend

WORKDIR /frontend

# Install dependencies from the lockfile for a reproducible build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Build the single-page app (outputs to /frontend/dist)
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.14-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Copy the built web UI from the frontend stage
COPY --from=frontend /frontend/dist ./frontend/dist

# Create config directory
RUN mkdir -p /config

# Set environment defaults
# NOTE: qBittorrent credentials are intentionally NOT baked into the image.
# The application defaults to admin/adminadmin if unset; override at runtime.
ENV QBITTORRENT_URL=http://qbittorrent:8080
ENV API_PORT=8000
ENV LOG_LEVEL=INFO
ENV LOG_FORMAT=text
ENV RENAME_MODE=torrent_and_folder
ENV DRY_RUN=false
ENV RULES_FILE=/config/rename_rules.yaml
ENV STATIC_DIR=/app/frontend/dist

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
