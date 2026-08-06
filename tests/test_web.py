from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import trimesh
from fastapi.testclient import TestClient

from asset_cleanup.models import Recipe
from asset_cleanup.web import WebConfig, create_app


def _glb() -> bytes:
    payload = trimesh.creation.box().export(file_type="glb")
    assert isinstance(payload, bytes)
    return payload


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


def test_upload_is_content_addressed_and_content_sniffed(tmp_path: Path) -> None:
    app = create_app(data_root=tmp_path / "data")
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/assets/import",
            files={"file": ("misleading.txt", _glb(), "text/plain")},
        )
        second = client.post(
            "/api/v1/assets/import", files={"file": ("box.glb", _glb(), "application/octet-stream")}
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["format"] == "glb"
    assert "storage_path" not in first.json()


def test_upload_limit_and_unsupported_payload_are_rejected(tmp_path: Path) -> None:
    config = WebConfig(data_root=tmp_path / "data", max_upload_bytes=100)
    app = create_app(config)
    with TestClient(app) as client:
        oversized = client.post(
            "/api/v1/assets/import", files={"file": ("box.glb", _glb(), "model/gltf-binary")}
        )
        unsupported = client.post(
            "/api/v1/assets/import", files={"file": ("notes.txt", b"hello", "text/plain")}
        )

    assert oversized.status_code == 413
    assert unsupported.status_code == 422


def test_multipart_body_is_bounded_before_parser_even_when_length_lies(tmp_path: Path) -> None:
    config = WebConfig(data_root=tmp_path / "data", max_upload_bytes=64)
    app = create_app(config)
    boundary = "asset-cleanup-boundary"
    prefix = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="large.glb"\r\n'
        "Content-Type: model/gltf-binary\r\n\r\n"
    ).encode()
    payload = prefix + (b"x" * (1024 * 1024 + 128)) + f"\r\n--{boundary}--\r\n".encode()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assets/import",
            content=iter((payload[:32], payload[32:])),
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": "10",
            },
        )

    assert response.status_code == 413


def test_recipe_schema_and_strict_validation(tmp_path: Path) -> None:
    app = create_app(data_root=tmp_path / "data")
    with TestClient(app) as client:
        schema = client.get("/api/v1/recipes/schema")
        valid = client.post("/api/v1/recipes/validate", json=_recipe().canonical_dict())
        invalid_data = _recipe().canonical_dict()
        invalid_data["unknown"] = True
        invalid = client.post("/api/v1/recipes/validate", json=invalid_data)

    assert schema.status_code == 200
    assert schema.json()["title"] == "Recipe"
    assert valid.status_code == 200
    assert valid.json()["recipe_hash"] == _recipe().canonical_hash()
    assert invalid.status_code == 422


def test_job_api_worker_sse_resume_and_artifact_download(tmp_path: Path) -> None:
    app = create_app(data_root=tmp_path / "data")
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/v1/assets/import", files={"file": ("box.glb", _glb(), "model/gltf-binary")}
        ).json()
        created_response = client.post(
            "/api/v1/jobs",
            json={"asset_id": uploaded["id"], "recipe": _recipe().canonical_dict()},
        )
        assert created_response.status_code == 201
        job_id = created_response.json()["id"]
        assert app.state.worker.run_until_idle() == 1

        job = client.get(f"/api/v1/jobs/{job_id}")
        events = client.get(f"/api/v1/jobs/{job_id}/events?follow=false")
        resumed = client.get(
            f"/api/v1/jobs/{job_id}/events?follow=false", headers={"Last-Event-ID": "1"}
        )
        artifact = client.get(f"/api/v1/jobs/{job_id}/artifacts/manifest.json")

    assert job.status_code == 200
    assert job.json()["status"] == "succeeded"
    assert any(item["path"] == "manifest.json" for item in job.json()["artifacts"])
    assert "id: 1\n" in events.text
    assert "id: 1\n" not in resumed.text
    assert "id: 2\n" in resumed.text
    assert artifact.status_code == 200
    assert artifact.json()["status"] == "accepted"


def test_cancel_and_retry_endpoints_enforce_lifecycle(tmp_path: Path) -> None:
    app = create_app(data_root=tmp_path / "data")
    with TestClient(app) as client:
        asset = client.post(
            "/api/v1/assets/import", files={"file": ("box.glb", _glb(), "model/gltf-binary")}
        ).json()
        created = client.post(
            "/api/v1/jobs", json={"asset_id": asset["id"], "recipe": _recipe().canonical_dict()}
        ).json()
        premature = client.post(f"/api/v1/jobs/{created['id']}/retry")
        cancelled = client.post(f"/api/v1/jobs/{created['id']}/cancel")
        retried = client.post(f"/api/v1/jobs/{created['id']}/retry")

    assert premature.status_code == 409
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert retried.status_code == 201
    assert retried.json()["retry_of"] == created["id"]


def test_artifact_route_does_not_serve_unregistered_files(tmp_path: Path) -> None:
    root = tmp_path / "data"
    app = create_app(data_root=root)
    with TestClient(app) as client:
        response = client.get(f"/api/v1/jobs/{'0' * 32}/artifacts/../../state.sqlite3")

    assert response.status_code == 404


def test_browser_workspace_contract_runs_and_packages_candidate(tmp_path: Path) -> None:
    app = create_app(data_root=tmp_path / "data")
    browser_recipe = {
        "preset": "balanced",
        "geometry": {
            "enabled": True,
            "strategy": "error",
            "target_faces": 5_000,
            "max_error": 0.0025,
            "preserve_boundaries": True,
            "reconstruct_planar": False,
            "reconstruct_primitives": False,
        },
        "collision": {
            "enabled": True,
            "mode": "box",
            "fit": "balanced",
            "body": "static",
        },
        "validation": {
            "compare_geometry": True,
            "compare_scene_inventory": True,
            "compare_appearance": False,
        },
    }
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        cross_origin = client.post(
            "/api/v1/workspaces",
            json={"name": "blocked"},
            headers={"Origin": "https://attacker.example"},
        )
        workspace = client.post("/api/v1/workspaces", json={"name": "Demo"}).json()
        asset = client.post(
            f"/api/v1/workspaces/{workspace['id']}/sources",
            files={"file": ("box.glb", _glb(), "model/gltf-binary")},
        ).json()
        inspection = client.get(
            f"/api/v1/workspaces/{workspace['id']}/sources/{asset['id']}/inspection"
        )
        created = client.post(
            f"/api/v1/workspaces/{workspace['id']}/sources/{asset['id']}/jobs",
            json={"recipe": browser_recipe},
        )
        job_id = created.json()["id"]
        assert app.state.worker.run_until_idle() == 1
        completed = client.get(f"/api/v1/jobs/{job_id}")
        events = client.get(f"/api/v1/jobs/{job_id}/events?follow=false")
        preview = client.get(asset["preview_url"])
        package = client.get(f"/api/v1/jobs/{job_id}/package.zip")

    assert health.status_code == 200
    assert "default-src 'self'" in health.headers["content-security-policy"]
    assert cross_origin.status_code == 403
    assert inspection.status_code == 200
    assert inspection.json()["topology"]["faces"] == 12
    assert inspection.json()["bounds"]["diagonal"] > 0
    assert created.status_code == 201
    assert completed.json()["state"] == "accepted"
    assert completed.json()["candidate_preview_url"]
    assert all("name" in artifact for artifact in completed.json()["artifacts"])
    assert "event:" not in events.text
    payload = json.loads(
        next(line[6:] for line in events.text.splitlines() if line.startswith("data: "))
    )
    assert payload["type"] == "job.queued"
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "model/gltf-binary"
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert "manifest.json" in archive.namelist()


def test_workspace_job_threads_and_json_events_survive_restart(tmp_path: Path) -> None:
    root = tmp_path / "data"
    app = create_app(data_root=root, embedded_worker=False)
    with TestClient(app) as client:
        primary = client.post("/api/v1/workspaces", json={"name": "Primary"}).json()
        other = client.post("/api/v1/workspaces", json={"name": "Other"}).json()
        source = client.post(
            f"/api/v1/workspaces/{primary['id']}/sources",
            files={"file": ("box.glb", _glb(), "model/gltf-binary")},
        ).json()
        same_source = client.post(
            f"/api/v1/workspaces/{other['id']}/sources",
            files={"file": ("box-copy.glb", _glb(), "model/gltf-binary")},
        ).json()
        created = client.post(
            f"/api/v1/workspaces/{primary['id']}/sources/{source['id']}/jobs",
            json={"recipe": {"preset": "balanced"}},
        ).json()
        primary_jobs = client.get(f"/api/v1/workspaces/{primary['id']}/jobs?limit=200")
        source_jobs = client.get(
            f"/api/v1/workspaces/{primary['id']}/jobs?source_id={source['id']}"
        )
        other_jobs = client.get(f"/api/v1/workspaces/{other['id']}/jobs")
        events = client.get(f"/api/v1/jobs/{created['id']}/events.json")
        resumed = client.get(f"/api/v1/jobs/{created['id']}/events.json?after=1")

    assert same_source["id"] == source["id"]
    assert primary_jobs.status_code == 200
    assert primary_jobs.json()["items"][0]["workspace_id"] == primary["id"]
    assert primary_jobs.json()["items"][0]["event_sequence"] == 1
    assert primary_jobs.json()["items"][0]["editor_recipe"]["preset"] == "balanced"
    assert primary_jobs.json()["items"][0]["editor_recipe"]["geometry"]["max_error"] == 0.0025
    assert source_jobs.json()["items"][0]["id"] == created["id"]
    assert other_jobs.json()["items"] == []
    assert events.status_code == 200
    assert events.json()["items"] == [
        {
            "sequence": 1,
            "created_utc": events.json()["items"][0]["created_utc"],
            "level": "info",
            "type": "job.queued",
            "stage": "queue",
            "status": "queued",
            "stage_status": "queued",
            "progress": 0.0,
            "message": "Job queued",
            "data": {
                "stage": "queue",
                "status": "queued",
                "stage_status": "queued",
                "progress": 0.0,
            },
        }
    ]
    assert resumed.json()["items"] == []

    restarted = create_app(data_root=root, embedded_worker=False)
    with TestClient(restarted) as client:
        persisted = client.get(f"/api/v1/workspaces/{primary['id']}/jobs")

    assert persisted.json()["items"][0]["id"] == created["id"]


def test_legacy_job_editor_recipe_is_derived_only_when_lossless(tmp_path: Path) -> None:
    root = tmp_path / "data"
    app = create_app(data_root=root, embedded_worker=False)
    with TestClient(app) as client:
        workspace = client.post("/api/v1/workspaces", json={"name": "Legacy"}).json()
        source = client.post(
            f"/api/v1/workspaces/{workspace['id']}/sources",
            files={"file": ("box.glb", _glb(), "model/gltf-binary")},
        ).json()
        template = client.post(
            f"/api/v1/workspaces/{workspace['id']}/sources/{source['id']}/jobs",
            json={"recipe": {"preset": "balanced"}},
        ).json()
        stored_template = app.state.store.get_job(template["id"])
        assert stored_template is not None
        legacy = app.state.store.create_job(
            source["id"],
            Recipe.from_json(str(stored_template["recipe_json"])),
            workspace_id=workspace["id"],
        )
        unsupported_data = Recipe.from_json(str(stored_template["recipe_json"])).model_dump(
            mode="python"
        )
        unsupported_data["settings"]["geometry"].update(
            {
                "simplify_mode": "target",
                "target_faces": 1,
                "target_ratio": None,
                "max_error_fraction": None,
            }
        )
        canonical_only = app.state.store.create_job(
            source["id"],
            Recipe.model_validate(unsupported_data),
            workspace_id=workspace["id"],
        )

        recovered = client.get(f"/api/v1/jobs/{legacy['id']}")
        unsupported = client.get(f"/api/v1/jobs/{canonical_only['id']}")
        client.post(f"/api/v1/jobs/{legacy['id']}/cancel")
        retried = client.post(f"/api/v1/jobs/{legacy['id']}/retry")

    assert legacy["editor_recipe_json"] is None
    assert recovered.status_code == 200
    assert recovered.json()["editor_recipe"]["preset"] == "balanced"
    assert recovered.json()["editor_recipe"]["geometry"]["max_error"] == 0.0025
    assert "editor_recipe" not in unsupported.json()
    assert retried.status_code == 201
    assert retried.json()["editor_recipe"] == recovered.json()["editor_recipe"]
    stored_retry = app.state.store.get_job(retried.json()["id"])
    assert stored_retry is not None and stored_retry["editor_recipe_json"] is not None


def test_job_summary_uses_latest_event_after_history_exceeds_window(tmp_path: Path) -> None:
    app = create_app(data_root=tmp_path / "data", embedded_worker=False)
    with TestClient(app) as client:
        source = client.post(
            "/api/v1/assets/import",
            files={"file": ("box.glb", _glb(), "model/gltf-binary")},
        ).json()
        created = client.post(
            "/api/v1/jobs",
            json={"asset_id": source["id"], "recipe": _recipe().canonical_dict()},
        ).json()
        with app.state.store.connect() as database:
            database.execute("BEGIN IMMEDIATE")
            for index in range(505):
                final = index == 504
                app.state.store._append_event(
                    database,
                    created["id"],
                    "stage.started",
                    f"event-{index}",
                    {
                        "stage": "validation" if final else "geometry",
                        "status": "running",
                        "stage_status": "started",
                        "progress": 0.85 if final else 0.50,
                    },
                )
            database.execute("COMMIT")

        summary = client.get(f"/api/v1/jobs/{created['id']}")
        tail = app.state.store.recent_events(created["id"], limit=3)
        first_page = client.get(f"/api/v1/jobs/{created['id']}/events.json?limit=3")
        recent_page = client.get(f"/api/v1/jobs/{created['id']}/events.json?tail=true&limit=3")
        invalid_page = client.get(f"/api/v1/jobs/{created['id']}/events.json?tail=true&after=1")
        cancelled = client.post(f"/api/v1/jobs/{created['id']}/cancel")
        terminal_stream = client.get(f"/api/v1/jobs/{created['id']}/events")

    assert summary.status_code == 200
    assert summary.json()["event_sequence"] == 506
    assert summary.json()["message"] == "event-504"
    assert summary.json()["stage"] == "validation"
    assert summary.json()["progress"] == 0.85
    assert [event["sequence"] for event in tail] == [504, 505, 506]
    assert [event["sequence"] for event in first_page.json()["items"]] == [1, 2, 3]
    assert [event["sequence"] for event in recent_page.json()["items"]] == [504, 505, 506]
    assert invalid_page.status_code == 400
    assert cancelled.json()["status"] == "cancelled"
    assert terminal_stream.text.count("data: ") == 507
    assert "id: 507\n" in terminal_stream.text


def test_job_view_omits_tampered_registered_evidence(tmp_path: Path) -> None:
    config = WebConfig(data_root=tmp_path / "data")
    app = create_app(config)
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/v1/assets/import",
            files={"file": ("box.glb", _glb(), "model/gltf-binary")},
        ).json()
        created = client.post(
            "/api/v1/jobs",
            json={"asset_id": uploaded["id"], "recipe": _recipe().canonical_dict()},
        ).json()
        assert app.state.worker.run_until_idle() == 1
        stored = app.state.store.get_job(created["id"])
        assert stored is not None
        metrics = config.data_root / str(stored["output_path"]) / "metrics.json"
        metrics.write_text('{"forged":true}', encoding="utf-8")

        response = client.get(f"/api/v1/jobs/{created['id']}")

    assert response.status_code == 200
    assert response.json()["metrics"] is None
    assert response.json()["validation"] is not None
