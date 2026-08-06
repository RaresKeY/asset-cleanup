"""Configuration for the local web service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WebConfig:
    """Filesystem and resource policy for one service instance."""

    data_root: Path
    max_upload_bytes: int = 268_435_456
    upload_chunk_bytes: int = 1024 * 1024
    embedded_worker: bool = False
    worker_poll_seconds: float = 0.25
    event_poll_seconds: float = 0.25
    inspection_timeout_seconds: int = 120
    inspection_memory_bytes: int = 2_147_483_648
    max_inspection_report_bytes: int = 16_777_216
    max_evidence_json_bytes: int = 4_194_304
    max_texture_pixels: int = 268_435_456
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")

    def __post_init__(self) -> None:
        if self.max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")
        if not 64 * 1024 <= self.upload_chunk_bytes <= 16 * 1024 * 1024:
            raise ValueError("upload_chunk_bytes must be between 64 KiB and 16 MiB")
        if self.worker_poll_seconds <= 0 or self.event_poll_seconds <= 0:
            raise ValueError("poll intervals must be positive")
        if (
            self.inspection_timeout_seconds <= 0
            or self.inspection_memory_bytes <= 0
            or self.max_inspection_report_bytes <= 0
            or self.max_evidence_json_bytes <= 0
        ):
            raise ValueError("inspection resource limits must be positive")
        if self.max_texture_pixels <= 0:
            raise ValueError("max_texture_pixels must be positive")
        if not self.allowed_hosts or any(not host.strip() for host in self.allowed_hosts):
            raise ValueError("allowed_hosts must contain visible host names")

    @property
    def database_path(self) -> Path:
        return self.data_root / "state.sqlite3"

    @property
    def assets_root(self) -> Path:
        return self.data_root / "assets"

    @property
    def jobs_root(self) -> Path:
        return self.data_root / "jobs"

    @property
    def temporary_root(self) -> Path:
        return self.data_root / "tmp"

    def initialize(self) -> None:
        """Create private service-owned directories."""

        self.data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path in (self.assets_root, self.jobs_root, self.temporary_root):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
