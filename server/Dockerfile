FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    NBX_JOURNAL_DB=/var/lib/nobi/journal.db \
    NBX_AUTH_DB=/var/lib/nobi/auth.db \
    NBX_LIVE_DB=/var/lib/nobi/live_execution.db \
    NBX_PAPER_STATE=/var/lib/nobi/paper.json \
    NBX_CANDLE_CACHE=/var/lib/nobi/candles \
    NBX_BT_DIR=/var/lib/nobi/backtests \
    NBX_EXPORT_DIR=/var/lib/nobi/exports \
    NBX_COIN_CACHE=/var/lib/nobi/coin-cache \
    NBX_MANAGED_ENV=/var/lib/nobi/managed.env \
    NBX_OPERATIONS_AUDIT=/var/lib/nobi/operations-audit.jsonl \
    NBX_SETUP_TOKEN_FILE=/var/lib/nobi/setup.token

WORKDIR /app/server

# Dependencies first so the layer cache survives source edits.
COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# The frontend lives at the repository root: main.py serves index.html and
# /assets from Path(__file__).parent.parent, so the whole repo must be present.
COPY . /app

# Runtime state belongs on the mounted data volume, never in the image layer.
# The ENV block above is load-bearing: the code defaults to server/*.db, which
# is root-owned after COPY and unwritable for USER nobi. Without it a plain
# `docker run` (no compose env) crashes at import with "unable to open
# database file".
RUN addgroup --system nobi && adduser --system --ingroup nobi nobi \
    && mkdir -p /var/lib/nobi/candles /var/lib/nobi/backtests /var/lib/nobi/exports /var/lib/nobi/coin-cache \
    && chown -R nobi:nobi /var/lib/nobi
USER nobi

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
