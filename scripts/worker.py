"""Networkless container entry point for the standalone job worker."""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

from asset_cleanup.web import WebConfig, WorkerService
from asset_cleanup.web.store import Store


def main() -> None:
    config = WebConfig(data_root=Path(os.environ.get("ASSET_CLEANUP_DATA_ROOT", "/data")))
    config.initialize()
    store = Store(config.database_path)
    store.initialize()
    service = WorkerService(config, store)
    stopping = False

    def stop(*_unused: object) -> None:
        nonlocal stopping
        stopping = True
        service.stop()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    service.start()
    while not stopping:
        time.sleep(0.5)


if __name__ == "__main__":
    main()
