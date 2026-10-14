# API-only runtime; the desktop UI runs on the user's PC.
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000 \
    DATABASE_URL=sqlite:////app/data/aimonitor.db

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
# The connected-AI API shares these standard-library/httpx desktop helpers.
COPY desktop/config.py desktop/ai_detector.py desktop/context_manager.py desktop/quota.py ./desktop/
COPY desktop/claude_quota_syncer.py desktop/accounts.py ./desktop/

# Run as a non-root user; /app/data is the only path that needs to be writable
# and is where the SQLite volume mounts.
RUN useradd --create-home --uid 10001 aimonitor \
    && mkdir -p /app/data \
    && chown -R aimonitor:aimonitor /app/data
USER aimonitor

VOLUME ["/app/data"]
EXPOSE 8000

# Readiness, not liveness: this one fails when the database is unreachable.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/system/ready', timeout=4).status==200 else 1)"

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
