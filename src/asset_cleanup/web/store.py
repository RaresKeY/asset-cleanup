"""Small synchronous SQLite repository with transactional job claims."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from asset_cleanup.models import Recipe

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
JOB_STATUSES = frozenset({"queued", "running", *TERMINAL_STATUSES})


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class Store:
    """SQLite state boundary; each operation owns its connection."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as database:
            database.executescript(
                """
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL UNIQUE,
                    original_name TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    format TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                    storage_path TEXT NOT NULL UNIQUE,
                    created_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspace_assets (
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    created_utc TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, asset_id)
                );
                CREATE INDEX IF NOT EXISTS workspace_assets_created
                    ON workspace_assets(workspace_id, created_utc);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK(status IN
                        ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
                    asset_id TEXT NOT NULL REFERENCES assets(id),
                    recipe_json TEXT NOT NULL,
                    recipe_hash TEXT NOT NULL,
                    output_path TEXT NOT NULL UNIQUE,
                    retry_of TEXT REFERENCES jobs(id),
                    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0, 1)),
                    error_type TEXT,
                    error_message TEXT,
                    result_status TEXT,
                    created_utc TEXT NOT NULL,
                    started_utc TEXT,
                    completed_utc TEXT,
                    updated_utc TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_utc);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL CHECK(sequence > 0),
                    kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_utc TEXT NOT NULL,
                    UNIQUE(job_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS events_job_sequence ON events(job_id, sequence);
                CREATE TABLE IF NOT EXISTS artifacts (
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    path TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                    sha256 TEXT NOT NULL,
                    PRIMARY KEY(job_id, path)
                );
                """
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        database = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys = ON")
        database.execute("PRAGMA busy_timeout = 30000")
        database.execute("PRAGMA journal_mode = WAL")
        try:
            yield database
        finally:
            database.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        if "cancel_requested" in result:
            result["cancel_requested"] = bool(result["cancel_requested"])
        return result

    def put_asset(
        self,
        *,
        sha256: str,
        original_name: str,
        media_type: str,
        format: str,
        size_bytes: int,
        storage_path: str,
    ) -> dict[str, Any]:
        asset_id = f"sha256-{sha256}"
        with self.connect() as database:
            database.execute(
                """INSERT OR IGNORE INTO assets
                (id, sha256, original_name, media_type, format, size_bytes,
                 storage_path, created_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    asset_id,
                    sha256,
                    original_name,
                    media_type,
                    format,
                    size_bytes,
                    storage_path,
                    utc_now(),
                ),
            )
            row = database.execute("SELECT * FROM assets WHERE sha256 = ?", (sha256,)).fetchone()
        result = self._row(row)
        assert result is not None
        return result

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        with self.connect() as database:
            row = database.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return self._row(row)

    def create_workspace(self, name: str) -> dict[str, Any]:
        workspace_id = uuid4().hex
        created = utc_now()
        with self.connect() as database:
            database.execute(
                "INSERT INTO workspaces (id, name, created_utc) VALUES (?, ?, ?)",
                (workspace_id, name, created),
            )
        return {"id": workspace_id, "name": name, "created_utc": created}

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        with self.connect() as database:
            row = database.execute(
                "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
        return self._row(row)

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self.connect() as database:
            rows = database.execute(
                "SELECT * FROM workspaces ORDER BY created_utc DESC, id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def attach_asset(self, workspace_id: str, asset_id: str) -> bool:
        with self.connect() as database:
            if (
                database.execute(
                    "SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)
                ).fetchone()
                is None
            ):
                return False
            database.execute(
                """INSERT OR IGNORE INTO workspace_assets
                (workspace_id, asset_id, created_utc) VALUES (?, ?, ?)""",
                (workspace_id, asset_id, utc_now()),
            )
        return True

    def workspace_has_asset(self, workspace_id: str, asset_id: str) -> bool:
        with self.connect() as database:
            row = database.execute(
                """SELECT 1 FROM workspace_assets
                WHERE workspace_id = ? AND asset_id = ?""",
                (workspace_id, asset_id),
            ).fetchone()
        return row is not None

    def list_workspace_assets(self, workspace_id: str) -> list[dict[str, Any]]:
        with self.connect() as database:
            rows = database.execute(
                """SELECT assets.* FROM assets
                JOIN workspace_assets ON workspace_assets.asset_id = assets.id
                WHERE workspace_assets.workspace_id = ?
                ORDER BY workspace_assets.created_utc DESC, assets.id DESC""",
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_job(
        self,
        asset_id: str,
        recipe: Recipe,
        *,
        retry_of: str | None = None,
    ) -> dict[str, Any]:
        job_id = uuid4().hex
        output_path = f"jobs/{job_id}"
        now = utc_now()
        with self.connect() as database:
            database.execute("BEGIN IMMEDIATE")
            try:
                database.execute(
                    """INSERT INTO jobs
                    (id, status, asset_id, recipe_json, recipe_hash, output_path, retry_of,
                     created_utc, updated_utc)
                    VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        job_id,
                        asset_id,
                        recipe.canonical_json(),
                        recipe.canonical_hash(),
                        output_path,
                        retry_of,
                        now,
                        now,
                    ),
                )
                self._append_event(database, job_id, "job.queued", "Job queued", {})
                database.execute("COMMIT")
            except BaseException:
                database.execute("ROLLBACK")
                raise
        job = self.get_job(job_id)
        assert job is not None
        return job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as database:
            row = database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row(row)

    def list_jobs(
        self, *, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM jobs"
        arguments: list[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            arguments.append(status)
        query += " ORDER BY created_utc DESC, id DESC LIMIT ? OFFSET ?"
        arguments.extend((limit, offset))
        with self.connect() as database:
            rows = database.execute(query, arguments).fetchall()
        return [dict(self._row(row) or {}) for row in rows]

    def claim_next(self) -> dict[str, Any] | None:
        """Atomically transition the oldest queued job to running."""

        with self.connect() as database:
            database.execute("BEGIN IMMEDIATE")
            try:
                row = database.execute(
                    """SELECT * FROM jobs WHERE status = 'queued' AND cancel_requested = 0
                    ORDER BY created_utc, id LIMIT 1"""
                ).fetchone()
                if row is None:
                    database.execute("COMMIT")
                    return None
                job_id = str(row["id"])
                now = utc_now()
                changed = database.execute(
                    """UPDATE jobs SET status = 'running', started_utc = ?, updated_utc = ?
                    WHERE id = ? AND status = 'queued' AND cancel_requested = 0""",
                    (now, now, job_id),
                ).rowcount
                if changed != 1:
                    database.execute("ROLLBACK")
                    return None
                self._append_event(database, job_id, "job.started", "Job started", {})
                database.execute("COMMIT")
            except BaseException:
                database.execute("ROLLBACK")
                raise
        return self.get_job(job_id)

    def recover_interrupted_jobs(self) -> int:
        """Fail jobs left running after the single configured worker stopped."""

        with self.connect() as database:
            database.execute("BEGIN IMMEDIATE")
            try:
                rows = database.execute(
                    "SELECT id FROM jobs WHERE status = 'running' ORDER BY id"
                ).fetchall()
                now = utc_now()
                for row in rows:
                    job_id = str(row["id"])
                    database.execute(
                        """UPDATE jobs SET status = 'failed', error_type = 'WorkerInterrupted',
                        error_message = 'worker stopped before the job reached a terminal state',
                        completed_utc = ?, updated_utc = ? WHERE id = ?""",
                        (now, now, job_id),
                    )
                    self._append_event(
                        database,
                        job_id,
                        "job.failed",
                        "Worker interruption detected during startup recovery",
                        {"error_type": "WorkerInterrupted"},
                    )
                database.execute("COMMIT")
            except BaseException:
                database.execute("ROLLBACK")
                raise
        return len(rows)

    def request_cancel(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as database:
            database.execute("BEGIN IMMEDIATE")
            try:
                row = database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if row is None:
                    database.execute("COMMIT")
                    return None
                status = str(row["status"])
                if status == "queued":
                    now = utc_now()
                    database.execute(
                        """UPDATE jobs SET status = 'cancelled', cancel_requested = 1,
                        completed_utc = ?, updated_utc = ? WHERE id = ?""",
                        (now, now, job_id),
                    )
                    self._append_event(
                        database, job_id, "job.cancelled", "Queued job cancelled", {}
                    )
                elif status == "running" and not bool(row["cancel_requested"]):
                    database.execute(
                        "UPDATE jobs SET cancel_requested = 1, updated_utc = ? WHERE id = ?",
                        (utc_now(), job_id),
                    )
                    self._append_event(
                        database,
                        job_id,
                        "job.cancel_requested",
                        "Cancellation requested",
                        {},
                    )
                database.execute("COMMIT")
            except BaseException:
                database.execute("ROLLBACK")
                raise
        return self.get_job(job_id)

    def finish_job(
        self,
        job_id: str,
        *,
        result_status: str,
        artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self.connect() as database:
            database.execute("BEGIN IMMEDIATE")
            try:
                row = database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if row is None:
                    raise KeyError(job_id)
                cancelled = bool(row["cancel_requested"])
                status = "cancelled" if cancelled else "succeeded"
                now = utc_now()
                database.execute(
                    """UPDATE jobs SET status = ?, result_status = ?, completed_utc = ?,
                    updated_utc = ? WHERE id = ? AND status = 'running'""",
                    (status, result_status, now, now, job_id),
                )
                for artifact in artifacts:
                    database.execute(
                        """INSERT OR REPLACE INTO artifacts
                        (job_id, path, kind, stage, size_bytes, sha256)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            job_id,
                            artifact["path"],
                            artifact["kind"],
                            artifact["stage"],
                            artifact["bytes"],
                            artifact["sha256"],
                        ),
                    )
                kind = "job.cancelled" if cancelled else "job.succeeded"
                message = (
                    "Job cancelled after current processing step" if cancelled else "Job succeeded"
                )
                self._append_event(
                    database, job_id, kind, message, {"result_status": result_status}
                )
                database.execute("COMMIT")
            except BaseException:
                database.execute("ROLLBACK")
                raise
        result = self.get_job(job_id)
        assert result is not None
        return result

    def fail_job(
        self,
        job_id: str,
        error: BaseException,
        *,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self.connect() as database:
            database.execute("BEGIN IMMEDIATE")
            try:
                row = database.execute(
                    "SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(job_id)
                cancelled = bool(row["cancel_requested"])
                status = "cancelled" if cancelled else "failed"
                now = utc_now()
                database.execute(
                    """UPDATE jobs SET status = ?, error_type = ?, error_message = ?,
                    completed_utc = ?, updated_utc = ? WHERE id = ? AND status = 'running'""",
                    (status, type(error).__name__, str(error)[:4000], now, now, job_id),
                )
                for artifact in artifacts or []:
                    database.execute(
                        """INSERT OR REPLACE INTO artifacts
                        (job_id, path, kind, stage, size_bytes, sha256)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            job_id,
                            artifact["path"],
                            artifact["kind"],
                            artifact["stage"],
                            artifact["bytes"],
                            artifact["sha256"],
                        ),
                    )
                self._append_event(
                    database,
                    job_id,
                    "job.cancelled" if cancelled else "job.failed",
                    "Job cancelled" if cancelled else str(error)[:1000],
                    {"error_type": type(error).__name__},
                )
                database.execute("COMMIT")
            except BaseException:
                database.execute("ROLLBACK")
                raise
        result = self.get_job(job_id)
        assert result is not None
        return result

    def append_event(
        self, job_id: str, kind: str, message: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        with self.connect() as database:
            database.execute("BEGIN IMMEDIATE")
            try:
                sequence = self._append_event(database, job_id, kind, message, data or {})
                database.execute("COMMIT")
            except BaseException:
                database.execute("ROLLBACK")
                raise
        events = self.events(job_id, after=sequence - 1, limit=1)
        return events[0]

    @staticmethod
    def _append_event(
        database: sqlite3.Connection,
        job_id: str,
        kind: str,
        message: str,
        data: dict[str, Any],
    ) -> int:
        row = database.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next FROM events WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        sequence = int(row["next"])
        database.execute(
            """INSERT INTO events (job_id, sequence, kind, message, data_json, created_utc)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                sequence,
                kind,
                message,
                json.dumps(data, sort_keys=True, separators=(",", ":")),
                utc_now(),
            ),
        )
        return sequence

    def events(self, job_id: str, *, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        with self.connect() as database:
            rows = database.execute(
                """SELECT sequence, kind, message, data_json, created_utc FROM events
                WHERE job_id = ? AND sequence > ? ORDER BY sequence LIMIT ?""",
                (job_id, after, limit),
            ).fetchall()
        return [
            {
                "sequence": int(row["sequence"]),
                "kind": row["kind"],
                "message": row["message"],
                "data": json.loads(row["data_json"]),
                "created_utc": row["created_utc"],
            }
            for row in rows
        ]

    def list_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as database:
            rows = database.execute(
                "SELECT * FROM artifacts WHERE job_id = ? ORDER BY path", (job_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_artifact(self, job_id: str, path: str) -> dict[str, Any] | None:
        with self.connect() as database:
            row = database.execute(
                "SELECT * FROM artifacts WHERE job_id = ? AND path = ?", (job_id, path)
            ).fetchone()
        return dict(row) if row is not None else None
