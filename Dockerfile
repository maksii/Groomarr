FROM python:3.11-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Create config directory
RUN mkdir -p /config

# Set environment defaults
ENV QBITTORRENT_URL=http://qbittorrent:8080
ENV QBITTORRENT_USERNAME=admin
ENV QBITTORRENT_PASSWORD=adminadmin
ENV API_PORT=8000
ENV LOG_LEVEL=INFO
ENV LOG_FORMAT=text
ENV RENAME_MODE=torrent_and_folder
ENV DRY_RUN=false
ENV RULES_FILE=/config/rename_rules.yaml

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
