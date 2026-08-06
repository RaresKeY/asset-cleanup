# syntax=docker/dockerfile:1.7

# Build the browser application separately. Node is deliberately absent from the
# production image.
FROM node:22-bookworm-slim AS frontend-build

WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts
COPY web/ ./
RUN npm run build

# Fetch the official native validator as exact bytes in a disposable stage.
# Khronos currently publishes this pin only for Linux amd64; unsupported image
# architectures fail explicitly instead of silently omitting a requested gate.
FROM python:3.12-slim-bookworm AS gltf-validator-build

RUN test "$(dpkg --print-architecture)" = "amd64"
ADD --checksum=sha256:168eba887964125abe17ae97899b38d0b3cfd73c266c78424c194929ddcbc522 \
    https://github.com/KhronosGroup/glTF-Validator/releases/download/2.0.0-dev.3.10/gltf_validator-2.0.0-dev.3.10-linux64.tar.xz \
    /tmp/gltf-validator.tar.xz
RUN python - <<'PY'
from pathlib import Path
import tarfile

destination = Path("/opt/gltf-validator")
destination.mkdir(parents=True, mode=0o755)
with tarfile.open("/tmp/gltf-validator.tar.xz", mode="r:xz") as archive:
    for name in ("gltf_validator", "LICENSE", "NOTICES"):
        member = archive.getmember(name)
        if not member.isfile():
            raise RuntimeError(f"expected regular archive member: {name}")
        source = archive.extractfile(member)
        if source is None:
            raise RuntimeError(f"could not read archive member: {name}")
        (destination / name).write_bytes(source.read())
PY
RUN chmod 0755 /opt/gltf-validator/gltf_validator && \
    chmod 0644 /opt/gltf-validator/LICENSE /opt/gltf-validator/NOTICES && \
    test -s /opt/gltf-validator/gltf_validator

# Resolve Python dependencies from the committed uv lock and build a wheel. The
# wheel avoids an editable installation that would point back to the build stage.
FROM python:3.12-slim-bookworm AS python-build

COPY --from=ghcr.io/astral-sh/uv:0.11.33 /uv /uvx /bin/
WORKDIR /build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_PROGRESS=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra collision --extra web --no-install-project
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --out-dir /tmp/wheels && \
    uv pip install --python /opt/venv/bin/python --no-deps /tmp/wheels/*.whl

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
# Build the virtual environment at its final location. Python console-script
# shebangs are absolute, so relocating a venv from /build would make
# `asset-cleanup` fail at container startup with an unavailable interpreter.
COPY --from=python-build --chown=${APP_UID}:${APP_GID} /opt/venv /opt/venv
COPY --from=frontend-build --chown=${APP_UID}:${APP_GID} /build/web/dist /app/web
COPY --from=gltf-validator-build /opt/gltf-validator/gltf_validator /usr/local/bin/gltf_validator
COPY --from=gltf-validator-build /opt/gltf-validator/LICENSE /usr/share/licenses/gltf-validator/LICENSE
COPY --from=gltf-validator-build /opt/gltf-validator/NOTICES /usr/share/doc/gltf-validator/NOTICES
COPY THIRD_PARTY_NOTICES.md /usr/share/doc/asset-cleanup/THIRD_PARTY_NOTICES.md

USER ${APP_UID}:${APP_GID}
VOLUME ["/data"]
EXPOSE 8080

# Use only the Python standard library so the health probe remains available if
# application dependencies are partially degraded.
HEALTHCHECK --interval=20s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=2).status == 200 else 1)"]

CMD ["asset-cleanup", "serve", "--data-root", "/data", "--host", "0.0.0.0", "--web-root", "/app/web", "--allowed-host", "127.0.0.1", "--allowed-host", "localhost"]
