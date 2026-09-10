FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    XDG_CACHE_HOME=/opt/xsentinel-cache

WORKDIR /app

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app

COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

RUN mkdir -p /var/lib/xsentinel/xhs-home /var/lib/xsentinel/xhs-uploads \
        /var/lib/xsentinel/article-uploads /var/log/xsentinel \
    && chown -R app:app /var/lib/xsentinel /var/log/xsentinel

FROM base AS xhs-worker

# Keep this layer before the application source. A normal code-only release
# can then reuse the large browser layer by digest instead of downloading it
# again. Changes to requirements.txt still rebuild the runtime deliberately.
RUN playwright install-deps firefox \
    && python -m camoufox fetch \
    && python -c 'from camoufox.utils import get_env_vars; [get_env_vars({}, os_name) for os_name in ("lin", "mac", "win")]' \
    && chmod -R a+rX /opt/xsentinel-cache

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
RUN chown -R app:app /app

USER app

EXPOSE 8000 8001 8002 8003 8004 8005
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8005/metrics', timeout=3).read()"]

CMD ["python", "-m", "app.xhs_worker"]

# The core target deliberately stays browser-free. It is selected by the
# regular backend services and by the CI job for the backend image.
FROM base AS core
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
RUN chown -R app:app /app

USER app

EXPOSE 8000 8001 8002 8003 8004 8005
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=3).read()"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
