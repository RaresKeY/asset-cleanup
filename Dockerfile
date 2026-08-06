# syntax=docker/dockerfile:1.7

# Build the browser application separately. Node is deliberately absent from the
# production image.
FROM node:22-bookworm-slim AS frontend-build

WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts
COPY web/ ./
RUN npm run build

# Resolve Python dependencies from the committed uv lock and build a wheel. The
# wheel avoids an editable installation that would point back to the build stage.
FROM python:3.12-slim-bookworm AS python-build

COPY --from=ghcr.io/astral-sh/uv:0.11.33 /uv /uvx /bin/
WORKDIR /build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_PROGRESS=1

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra collision --extra web --no-install-project
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --out-dir /tmp/wheels && \
    uv pip install --python .venv/bin/python --no-deps /tmp/wheels/*.whl

FROM python:3.12-slim-bookworm AS runtime

ARG APP_UID=10001
ARG APP_GID=10001
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

RUN groupadd --gid "${APP_GID}" assetcleanup && \
    useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin assetcleanup && \
    install --directory --owner="${APP_UID}" --group="${APP_GID}" --mode=0700 /data

WORKDIR /app
COPY --from=python-build --chown=${APP_UID}:${APP_GID} /build/.venv /opt/venv
COPY --from=frontend-build --chown=${APP_UID}:${APP_GID} /build/web/dist /app/web

USER ${APP_UID}:${APP_GID}
VOLUME ["/data"]
EXPOSE 8080

# Use only the Python standard library so the health probe remains available if
# application dependencies are partially degraded.
HEALTHCHECK --interval=20s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=2).status == 200 else 1)"]

CMD ["asset-cleanup", "serve", "--data-root", "/data", "--host", "0.0.0.0", "--web-root", "/app/web", "--allowed-host", "127.0.0.1", "--allowed-host", "localhost"]
