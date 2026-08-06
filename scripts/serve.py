"""Container entry point for the API and built frontend.

This operational wrapper keeps filesystem configuration outside application
imports. It also mounts the static frontend after API routes, so `/api` and
health endpoints retain precedence over the client application.
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles

from asset_cleanup.web import WebConfig, create_app


def _enabled(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    data_root = Path(os.environ.get("ASSET_CLEANUP_DATA_ROOT", "/data"))
    web_root = Path(os.environ.get("ASSET_CLEANUP_WEB_ROOT", "/app/web"))
    allowed_hosts = tuple(
        host.strip()
        for host in os.environ.get("ASSET_CLEANUP_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
        if host.strip()
    )
    app = create_app(
        WebConfig(
            data_root=data_root,
            embedded_worker=_enabled(os.environ.get("ASSET_CLEANUP_EMBEDDED_WORKER"), default=True),
            allowed_hosts=allowed_hosts,
        )
    )
    if not web_root.is_dir():
        raise RuntimeError(f"built frontend is unavailable: {web_root}")
    app.mount("/", StaticFiles(directory=web_root, html=True), name="web")
    uvicorn.run(
        app,
        host=os.environ.get("ASSET_CLEANUP_HOST", "127.0.0.1"),
        port=int(os.environ.get("ASSET_CLEANUP_PORT", "8080")),
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
