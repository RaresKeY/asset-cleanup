from __future__ import annotations

from pathlib import Path
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
