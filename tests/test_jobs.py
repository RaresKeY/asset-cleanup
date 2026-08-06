from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest
import trimesh

from asset_cleanup.models import Recipe
from asset_cleanup.util import sha256_file
from asset_cleanup.web.config import WebConfig
from asset_cleanup.web.jobs import WorkerService
from asset_cleanup.web.store import Store


def _recipe() -> Recipe:
    data = Recipe.from_preset("collision").model_dump(mode="python")
    data["settings"]["collision"].update({"body_type": "static", "mode": "box"})
    data["settings"]["validation"].update(
        {
            "gltf_validator": False,
            "compare_appearance": False,
            "compare_scene_inventory": False,
        }
    )
    return Recipe.model_validate(data)


def _store_with_asset(tmp_path: Path) -> tuple[WebConfig, Store, dict[str, object]]:
    config = WebConfig(data_root=tmp_path / "data")
    config.initialize()
    store = Store(config.database_path)
    store.initialize()
    source = config.assets_root / "source.glb"
    source.write_bytes(trimesh.creation.box().export(file_type="glb"))
    digest = sha256_file(source)
    asset = store.put_asset(
        sha256=digest,
        original_name="box.glb",
        media_type="model/gltf-binary",
        format="glb",
        size_bytes=source.stat().st_size,
        storage_path=source.relative_to(config.data_root).as_posix(),
    )
    return config, store, asset


def test_worker_persists_lifecycle_events_and_artifacts(tmp_path: Path) -> None:
    config, store, asset = _store_with_asset(tmp_path)
    job = store.create_job(str(asset["id"]), _recipe())

    assert WorkerService(config, store).run_until_idle() == 1

    completed = store.get_job(str(job["id"]))
    assert completed is not None
    assert completed["status"] == "succeeded"
    assert completed["result_status"] == "accepted"
    events = store.events(str(job["id"]))
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    kinds = [event["kind"] for event in events]
    assert kinds[:2] == ["job.queued", "job.started"]
    assert "stage.started" in kinds
    assert "stage.succeeded" in kinds
    assert kinds[-1] == "job.succeeded"
    stage_events = [event for event in events if event["kind"].startswith("stage.")]
    assert all(event["data"]["status"] == "running" for event in stage_events)
    assert all(event["data"]["stage"] for event in stage_events)
    assert all(0.0 <= event["data"]["progress"] <= 1.0 for event in stage_events)
    assert events[-1]["data"]["status"] == "succeeded"
    assert events[-1]["data"]["stage"] == "package"
    assert any(item["path"] == "manifest.json" for item in store.list_artifacts(str(job["id"])))


def test_queued_cancellation_is_terminal_and_retryable(tmp_path: Path) -> None:
    config, store, asset = _store_with_asset(tmp_path)
    job = store.create_job(str(asset["id"]), _recipe())

    cancelled = store.request_cancel(str(job["id"]))
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert WorkerService(config, store).run_until_idle() == 0

    retried = store.create_job(str(asset["id"]), _recipe(), retry_of=str(job["id"]))
    assert retried["status"] == "queued"
    assert retried["retry_of"] == job["id"]


def test_running_cancellation_preserves_current_stage_progress(tmp_path: Path) -> None:
    _, store, asset = _store_with_asset(tmp_path)
    created = store.create_job(str(asset["id"]), _recipe())
    claimed = store.claim_next()
    assert claimed is not None
    store.append_event(
        str(created["id"]),
        "stage.started",
        "Validating candidate",
        {
            "stage": "validation",
            "status": "running",
            "stage_status": "started",
            "progress": 0.85,
        },
    )

    cancelled = store.request_cancel(str(created["id"]))

    assert cancelled is not None
    event = store.events(str(created["id"]))[-1]
    assert event["kind"] == "job.cancel_requested"
    assert event["data"]["stage"] == "validation"
    assert event["data"]["progress"] == 0.85
    assert event["data"]["stage_status"] == "stopping"


def test_content_asset_identifier_is_deterministic(tmp_path: Path) -> None:
    _, store, asset = _store_with_asset(tmp_path)
    duplicate = store.put_asset(
        sha256=str(asset["sha256"]),
        original_name="renamed.glb",
        media_type="model/gltf-binary",
        format="glb",
        size_bytes=int(asset["size_bytes"]),
        storage_path=str(asset["storage_path"]),
    )

    assert duplicate["id"] == asset["id"]
    assert duplicate["original_name"] == "box.glb"


def test_worker_rejects_tampered_content_addressed_source(tmp_path: Path) -> None:
    config, store, asset = _store_with_asset(tmp_path)
    job = store.create_job(str(asset["id"]), _recipe())
    source = config.data_root / str(asset["storage_path"])
    source.write_bytes(source.read_bytes() + b"tamper")

    assert WorkerService(config, store).run_until_idle() == 1

    failed = store.get_job(str(job["id"]))
    assert failed is not None
    assert failed["status"] == "failed"
    assert "integrity check failed" in str(failed["error_message"])
    assert store.list_artifacts(str(job["id"])) == []


def test_worker_terminates_child_when_event_ingestion_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, store, asset = _store_with_asset(tmp_path)
    worker = WorkerService(config, store).worker
    process = Mock()
    process.poll.return_value = None
    terminate = Mock()
    monkeypatch.setattr("asset_cleanup.web.jobs.subprocess.Popen", Mock(return_value=process))
    monkeypatch.setattr(worker, "_terminate", terminate)
    monkeypatch.setattr(
        worker,
        "_ingest_events",
        Mock(side_effect=RuntimeError("event database unavailable")),
    )

    with pytest.raises(RuntimeError, match="event database unavailable"):
        worker._run_with_events(
            "0" * 32,
            config.data_root / str(asset["storage_path"]),
            _recipe(),
            config.jobs_root / ("0" * 32),
        )

    terminate.assert_called_once_with(process)


def test_store_migrates_jobs_to_persistent_workspace_ownership(tmp_path: Path) -> None:
    config = WebConfig(data_root=tmp_path / "data")
    config.initialize()
    with sqlite3.connect(config.database_path) as database:
        database.executescript(
            """CREATE TABLE workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_utc TEXT NOT NULL
            );
            CREATE TABLE workspace_assets (
                workspace_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                created_utc TEXT NOT NULL,
                PRIMARY KEY(workspace_id, asset_id)
            );
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                recipe_json TEXT NOT NULL,
                recipe_hash TEXT NOT NULL,
                output_path TEXT NOT NULL UNIQUE,
                retry_of TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                error_type TEXT,
                error_message TEXT,
                result_status TEXT,
                created_utc TEXT NOT NULL,
                started_utc TEXT,
                completed_utc TEXT,
                updated_utc TEXT NOT NULL
            );"""
        )
        database.execute(
            "INSERT INTO workspaces (id, name, created_utc) VALUES (?, ?, ?)",
            ("workspace-1", "Migrated", "2026-01-01T00:00:00Z"),
        )
        database.execute(
            """INSERT INTO workspace_assets
            (workspace_id, asset_id, created_utc) VALUES (?, ?, ?)""",
            ("workspace-1", "asset-1", "2026-01-01T00:00:00Z"),
        )
        recipe = _recipe()
        database.execute(
            """INSERT INTO jobs (
                id, status, asset_id, recipe_json, recipe_hash, output_path,
                created_utc, updated_utc
            ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?)""",
            (
                "job-1",
                "asset-1",
                recipe.canonical_json(),
                recipe.canonical_hash(),
                "jobs/job-1",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )

    barrier = Barrier(2)

    def initialize() -> None:
        barrier.wait()
        Store(config.database_path).initialize()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(initialize) for _ in range(2)]
        for future in futures:
            future.result()

    store = Store(config.database_path)
    store.initialize()

    with store.connect() as database:
        columns = {
            str(row["name"]) for row in database.execute("PRAGMA table_info(jobs)").fetchall()
        }
        indexes = {
            str(row["name"]) for row in database.execute("PRAGMA index_list(jobs)").fetchall()
        }
        migrated = database.execute(
            "SELECT workspace_id, editor_recipe_json FROM jobs WHERE id = 'job-1'"
        ).fetchone()
        schema_version = int(database.execute("PRAGMA user_version").fetchone()[0])

    assert {"workspace_id", "editor_recipe_json"}.issubset(columns)
    assert "jobs_workspace_created" in indexes
    assert migrated is not None and migrated["workspace_id"] == "workspace-1"
    assert migrated is not None and migrated["editor_recipe_json"] is None
    assert schema_version == 2
