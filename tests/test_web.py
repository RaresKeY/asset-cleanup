from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
import trimesh
from fastapi.testclient import TestClient

from asset_cleanup.models import Recipe
from asset_cleanup.web import WebConfig, create_app
from asset_cleanup.web.api import (
    BoundedRequestBodyMiddleware,
    BrowserRecipe,
    _browser_recipe_from_canonical,
    _editor_recipe_for_job,
    _recipe_from_web,
)
from asset_cleanup.web.config import RecipeCeilings


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


def _set_recipe_value(recipe: Recipe, path: str, value: int | float) -> Recipe:
    data = recipe.model_dump(mode="python")
    target: dict[str, Any] = data
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    return Recipe.model_validate(data)


def _http_scope(
    headers: list[tuple[bytes, bytes]] | None = None,
    *,
    method: str = "GET",
    path: str = "/",
) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }


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


def test_non_multipart_body_is_bounded_by_declared_and_received_size(tmp_path: Path) -> None:
    config = WebConfig(data_root=tmp_path / "data", max_request_body_bytes=64)
    app = create_app(config)
    with TestClient(app) as client:
        declared = client.post(
            "/api/v1/workspaces",
            content=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "65"},
        )
        streamed = client.post(
            "/api/v1/workspaces",
            content=iter((b"{" + b'"name":"', b"x" * 80, b'"}')),
            headers={"Content-Type": "application/json", "Content-Length": "10"},
        )
        small = client.post("/api/v1/workspaces", json={"name": "bounded"})

    expected = {"detail": "request exceeds configured limit"}
    assert declared.status_code == 413
    assert declared.json() == expected
    assert streamed.status_code == 413
    assert streamed.json() == expected
    assert small.status_code == 201


@pytest.mark.parametrize("content_length", [b"+1", b" 1", b"1 ", b"1_0", b"-1"])
def test_request_body_middleware_rejects_non_decimal_content_length(
    tmp_path: Path, content_length: bytes
) -> None:
    called = False
    sent: list[dict[str, Any]] = []

    async def downstream(*_arguments: Any) -> None:
        nonlocal called
        called = True

    messages = iter([{"type": "http.request", "body": b"", "more_body": False}])

    async def receive() -> dict[str, Any]:
        return next(messages)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = BoundedRequestBodyMiddleware(
        downstream,
        max_request_body_bytes=64,
        max_multipart_body_bytes=128,
        spool_directory=tmp_path,
    )
    asyncio.run(middleware(_http_scope([(b"content-length", content_length)]), receive, send))

    assert not called
    assert sent[0]["status"] == 400
    assert json.loads(sent[1]["body"]) == {"detail": "invalid Content-Length"}


def test_request_body_middleware_rejects_duplicate_content_length(tmp_path: Path) -> None:
    called = False
    sent: list[dict[str, Any]] = []

    async def downstream(*_arguments: Any) -> None:
        nonlocal called
        called = True

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = BoundedRequestBodyMiddleware(
        downstream,
        max_request_body_bytes=64,
        max_multipart_body_bytes=128,
        spool_directory=tmp_path,
    )
    scope = _http_scope([(b"content-length", b"0"), (b"content-length", b"0")])
    asyncio.run(middleware(scope, receive, send))

    assert not called
    assert sent[0]["status"] == 400
    assert json.loads(sent[1]["body"]) == {"detail": "invalid Content-Length"}


def test_request_body_middleware_rejects_duplicate_content_type_before_tier_selection(
    tmp_path: Path,
) -> None:
    called = False
    sent: list[dict[str, Any]] = []

    async def downstream(*_arguments: Any) -> None:
        nonlocal called
        called = True

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"x" * 65, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = BoundedRequestBodyMiddleware(
        downstream,
        max_request_body_bytes=64,
        max_multipart_body_bytes=128,
        spool_directory=tmp_path,
    )
    scope = _http_scope(
        [
            (b"content-type", b"application/json"),
            (b"content-type", b"multipart/form-data"),
        ]
    )
    asyncio.run(middleware(scope, receive, send))

    assert not called
    assert sent[0]["status"] == 400
    assert json.loads(sent[1]["body"]) == {"detail": "invalid Content-Type"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/assets/import",
        "/api/v1/assets",
        f"/api/v1/workspaces/{'a' * 32}/sources",
    ],
)
def test_request_body_middleware_reserves_multipart_allowance_for_upload_routes(
    tmp_path: Path, path: str
) -> None:
    received: list[dict[str, Any]] = []

    async def downstream(_scope: dict[str, Any], receive: Any, _send: Any) -> None:
        received.append(await receive())

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"x" * 65, "more_body": False}

    async def send(_message: dict[str, Any]) -> None:
        return None

    middleware = BoundedRequestBodyMiddleware(
        downstream,
        max_request_body_bytes=64,
        max_multipart_body_bytes=128,
        spool_directory=tmp_path,
    )
    scope = _http_scope(
        [(b"content-type", b"multipart/form-data; boundary=asset-cleanup")],
        method="POST",
        path=path,
    )
    asyncio.run(middleware(scope, receive, send))

    assert received == [{"type": "http.request", "body": b"x" * 65, "more_body": False}]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/workspaces"),
        ("GET", "/api/v1/assets/import"),
        ("POST", "/api/v1/assets/import/extra"),
        ("POST", f"/api/v1/workspaces/{'a' * 31}/sources"),
        ("POST", f"/api/v1/workspaces/{'A' * 32}/sources"),
    ],
)
def test_request_body_middleware_uses_general_limit_outside_upload_routes(
    tmp_path: Path, method: str, path: str
) -> None:
    called = False
    sent: list[dict[str, Any]] = []

    async def downstream(*_arguments: Any) -> None:
        nonlocal called
        called = True

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"x" * 65, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = BoundedRequestBodyMiddleware(
        downstream,
        max_request_body_bytes=64,
        max_multipart_body_bytes=128,
        spool_directory=tmp_path,
    )
    scope = _http_scope(
        [(b"content-type", b"multipart/form-data; boundary=asset-cleanup")],
        method=method,
        path=path,
    )
    asyncio.run(middleware(scope, receive, send))

    assert not called
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"]) == {"detail": "request exceeds configured limit"}


def test_request_body_middleware_accepts_leading_zero_content_length(tmp_path: Path) -> None:
    received: list[dict[str, Any]] = []

    async def downstream(_scope: dict[str, Any], receive: Any, _send: Any) -> None:
        received.append(await receive())

    messages = iter([{"type": "http.request", "body": b"", "more_body": False}])

    async def receive() -> dict[str, Any]:
        return next(messages)

    async def send(_message: dict[str, Any]) -> None:
        return None

    middleware = BoundedRequestBodyMiddleware(
        downstream,
        max_request_body_bytes=64,
        max_multipart_body_bytes=128,
        spool_directory=tmp_path,
    )
    asyncio.run(middleware(_http_scope([(b"content-length", b"000")]), receive, send))

    assert received == [{"type": "http.request", "body": b"", "more_body": False}]


def test_request_body_middleware_handles_very_long_decimal_content_length(
    tmp_path: Path,
) -> None:
    async def exercise(content_length: bytes) -> tuple[bool, list[dict[str, Any]]]:
        called = False
        sent: list[dict[str, Any]] = []

        async def downstream(_scope: dict[str, Any], receive: Any, _send: Any) -> None:
            nonlocal called
            called = True
            await receive()

        messages = iter([{"type": "http.request", "body": b"", "more_body": False}])

        async def receive() -> dict[str, Any]:
            return next(messages)

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        middleware = BoundedRequestBodyMiddleware(
            downstream,
            max_request_body_bytes=64,
            max_multipart_body_bytes=128,
            spool_directory=tmp_path,
        )
        await middleware(_http_scope([(b"content-length", content_length)]), receive, send)
        return called, sent

    oversized_called, oversized_sent = asyncio.run(exercise(b"9" * 5_000))
    zero_called, zero_sent = asyncio.run(exercise(b"0" * 5_000))

    assert not oversized_called
    assert oversized_sent[0]["status"] == 413
    assert zero_called
    assert zero_sent == []


def test_request_body_middleware_bounds_headerless_get_and_preserves_disconnect(
    tmp_path: Path,
) -> None:
    called = False
    sent: list[dict[str, Any]] = []

    async def downstream(*_arguments: Any) -> None:
        nonlocal called
        called = True

    oversized_messages = iter([{"type": "http.request", "body": b"x" * 65, "more_body": False}])

    async def oversized_receive() -> dict[str, Any]:
        return next(oversized_messages)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = BoundedRequestBodyMiddleware(
        downstream,
        max_request_body_bytes=64,
        max_multipart_body_bytes=128,
        spool_directory=tmp_path,
    )
    asyncio.run(middleware(_http_scope(), oversized_receive, send))

    assert not called
    assert sent[0]["status"] == 413

    received: list[dict[str, Any]] = []

    async def streaming_downstream(_scope: dict[str, Any], receive: Any, _send: Any) -> None:
        received.extend([await receive(), await receive()])

    stream_messages = iter(
        [
            {"type": "http.request", "body": b"", "more_body": False},
            {"type": "http.disconnect"},
        ]
    )

    async def stream_receive() -> dict[str, Any]:
        return next(stream_messages)

    streaming_middleware = BoundedRequestBodyMiddleware(
        streaming_downstream,
        max_request_body_bytes=64,
        max_multipart_body_bytes=128,
        spool_directory=tmp_path,
    )
    asyncio.run(
        streaming_middleware(_http_scope([(b"content-length", b"0")]), stream_receive, send)
    )

    assert [message["type"] for message in received] == ["http.request", "http.disconnect"]


def test_multipart_media_type_and_security_checks_precede_body_spooling(tmp_path: Path) -> None:
    config = WebConfig(data_root=tmp_path / "data", max_request_body_bytes=64)
    app = create_app(config)
    body = b"x" * 65
    with TestClient(app) as client:
        spoofed = client.post(
            "/api/v1/workspaces",
            content=body,
            headers={"Content-Type": "multipart/form-dataevil", "Content-Length": "10"},
        )
        cross_origin = client.post(
            "/api/v1/workspaces",
            content=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": "10",
                "Origin": "https://attacker.example",
            },
        )
        untrusted_host = client.post(
            "/api/v1/workspaces",
            content=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": "10",
                "Host": "attacker.example",
            },
        )

    assert spoofed.status_code == 413
    assert cross_origin.status_code == 403
    assert untrusted_host.status_code == 400


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


def test_recipe_policy_accepts_exact_and_stricter_limits_without_mutation(tmp_path: Path) -> None:
    config = WebConfig(data_root=tmp_path / "data", embedded_worker=False)
    app = create_app(config)
    at_ceiling = _recipe()
    stricter = at_ceiling
    for path, value in {
        "settings.limits.max_input_bytes": 1_000_000,
        "settings.limits.max_scene_nodes": 1_000,
        "settings.limits.max_meshes": 100,
        "settings.limits.max_vertices": 10_000,
        "settings.limits.max_triangles": 10_000,
        "settings.limits.max_texture_pixels": 1_000_000,
        "settings.limits.max_runtime_seconds": 60,
        "settings.limits.max_memory_bytes": 134_217_728,
        "settings.inspection.deterministic_samples": 1_000,
        "settings.collision.max_shapes": 8,
        "settings.collision.max_hulls": 4,
        "settings.collision.max_vertices_per_hull": 16,
        "settings.shape_detection.min_support_samples": 256,
        "settings.shape_detection.min_support_area_fraction": 0.01,
        "settings.shape_detection.cylinder_min_axial_bins": 6,
    }.items():
        stricter = _set_recipe_value(stricter, path, value)

    with TestClient(app) as client:
        asset = client.post(
            "/api/v1/assets/import",
            files={"file": ("box.glb", _glb(), "model/gltf-binary")},
        ).json()
        ceiling_response = client.post(
            "/api/v1/jobs",
            json={"asset_id": asset["id"], "recipe": at_ceiling.canonical_dict()},
        )
        stricter_response = client.post(
            "/api/v1/jobs",
            json={"asset_id": asset["id"], "recipe": stricter.canonical_dict()},
        )
        validated = client.post("/api/v1/recipes/validate", json=stricter.canonical_dict())

    assert ceiling_response.status_code == 201
    assert stricter_response.status_code == 201
    assert validated.status_code == 200
    assert validated.json()["recipe_hash"] == stricter.canonical_hash()
    assert validated.json()["recipe"] == stricter.canonical_dict()
    stored_ceiling = app.state.store.get_job(ceiling_response.json()["id"])
    stored_stricter = app.state.store.get_job(stricter_response.json()["id"])
    assert stored_ceiling is not None
    assert stored_ceiling["recipe_json"] == at_ceiling.canonical_json()
    assert stored_ceiling["recipe_hash"] == at_ceiling.canonical_hash()
    assert stored_stricter is not None
    assert stored_stricter["recipe_json"] == stricter.canonical_json()
    assert stored_stricter["recipe_hash"] == stricter.canonical_hash()


_RECIPE_MAXIMUM_CASES = tuple(RecipeCeilings().maximums.items())
_RECIPE_MINIMUM_CASES = tuple(RecipeCeilings().minimums.items())


def test_recipe_policy_rejects_non_finite_configuration() -> None:
    with pytest.raises(ValueError, match="recipe policy limits must be positive"):
        RecipeCeilings(min_support_area_fraction=float("nan"))


def test_create_app_worker_override_preserves_custom_service_policy(tmp_path: Path) -> None:
    ceilings = RecipeCeilings(max_runtime_seconds=900)
    config = WebConfig(
        data_root=tmp_path / "data",
        embedded_worker=False,
        max_upload_bytes=2_000,
        max_request_body_bytes=777,
        multipart_overhead_bytes=333,
        recipe_ceilings=ceilings,
    )

    app = create_app(config, embedded_worker=True)

    effective = app.state.config
    assert effective.embedded_worker is True
    assert effective.max_upload_bytes == 2_000
    assert effective.max_request_body_bytes == 777
    assert effective.multipart_overhead_bytes == 333
    assert effective.recipe_ceilings is ceilings


def test_recipe_policy_reports_multiple_violations_in_path_order(tmp_path: Path) -> None:
    ceilings = RecipeCeilings()
    recipe = _set_recipe_value(
        _set_recipe_value(
            _recipe(),
            "settings.limits.max_runtime_seconds",
            ceilings.max_runtime_seconds + 1,
        ),
        "settings.limits.max_input_bytes",
        ceilings.max_input_bytes + 1,
    )
    app = create_app(data_root=tmp_path / "data", embedded_worker=False)

    with TestClient(app) as client:
        response = client.post("/api/v1/recipes/validate", json=recipe.canonical_dict())

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "recipe_exceeds_server_policy",
        "violations": [
            {
                "path": "settings.limits.max_input_bytes",
                "requested": ceilings.max_input_bytes + 1,
                "maximum": ceilings.max_input_bytes,
            },
            {
                "path": "settings.limits.max_runtime_seconds",
                "requested": ceilings.max_runtime_seconds + 1,
                "maximum": ceilings.max_runtime_seconds,
            },
        ],
    }


def test_compact_browser_recipe_is_checked_against_service_policy(tmp_path: Path) -> None:
    ceilings = RecipeCeilings(max_runtime_seconds=3_599)
    app = create_app(
        WebConfig(
            data_root=tmp_path / "data",
            embedded_worker=False,
            recipe_ceilings=ceilings,
        )
    )
    with TestClient(app) as client:
        workspace = client.post("/api/v1/workspaces", json={"name": "Browser"}).json()
        asset = client.post(
            f"/api/v1/workspaces/{workspace['id']}/sources",
            files={"file": ("box.glb", _glb(), "model/gltf-binary")},
        ).json()
        response = client.post(
            f"/api/v1/workspaces/{workspace['id']}/sources/{asset['id']}/jobs",
            json={"recipe": {"preset": "balanced"}},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "recipe_exceeds_server_policy",
            "violations": [
                {
                    "path": "settings.limits.max_runtime_seconds",
                    "requested": 3_600,
                    "maximum": 3_599,
                }
            ],
        }
    }


@pytest.mark.parametrize(("path", "maximum"), _RECIPE_MAXIMUM_CASES)
def test_recipe_policy_rejects_each_resource_maximum(
    tmp_path: Path, path: str, maximum: int
) -> None:
    app = create_app(data_root=tmp_path / "data", embedded_worker=False)
    recipe = _set_recipe_value(_recipe(), path, maximum + 1)

    with TestClient(app) as client:
        response = client.post("/api/v1/recipes/validate", json=recipe.canonical_dict())

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "recipe_exceeds_server_policy",
            "violations": [{"path": path, "requested": maximum + 1, "maximum": maximum}],
        }
    }


@pytest.mark.parametrize(("path", "minimum"), _RECIPE_MINIMUM_CASES)
def test_recipe_policy_rejects_each_policy_minimum(
    tmp_path: Path, path: str, minimum: int | float
) -> None:
    app = create_app(data_root=tmp_path / "data", embedded_worker=False)
    requested = minimum - (0.001 if isinstance(minimum, float) else 1)
    recipe = _set_recipe_value(_recipe(), path, requested)

    with TestClient(app) as client:
        response = client.post("/api/v1/recipes/validate", json=recipe.canonical_dict())

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "recipe_exceeds_server_policy",
            "violations": [{"path": path, "requested": requested, "minimum": minimum}],
        }
    }


def test_recipe_policy_is_identical_for_global_workspace_retry_and_capabilities(
    tmp_path: Path,
) -> None:
    config = WebConfig(data_root=tmp_path / "data", embedded_worker=False)
    app = create_app(config)
    path = "settings.limits.max_runtime_seconds"
    invalid = _set_recipe_value(_recipe(), path, config.recipe_ceilings.max_runtime_seconds + 1)
    with TestClient(app) as client:
        workspace = client.post("/api/v1/workspaces", json={"name": "Policy"}).json()
        asset = client.post(
            f"/api/v1/workspaces/{workspace['id']}/sources",
            files={"file": ("box.glb", _glb(), "model/gltf-binary")},
        ).json()
        global_response = client.post(
            "/api/v1/jobs",
            json={"asset_id": asset["id"], "recipe": invalid.canonical_dict()},
        )
        workspace_response = client.post(
            f"/api/v1/workspaces/{workspace['id']}/sources/{asset['id']}/jobs",
            json={"recipe": invalid.canonical_dict()},
        )
        stored = app.state.store.create_job(asset["id"], invalid, workspace_id=workspace["id"])
        app.state.store.request_cancel(stored["id"])
        retry_response = client.post(f"/api/v1/jobs/{stored['id']}/retry")
        capabilities = client.get("/api/v1/capabilities")

    expected = {
        "detail": {
            "code": "recipe_exceeds_server_policy",
            "violations": [
                {
                    "path": path,
                    "requested": config.recipe_ceilings.max_runtime_seconds + 1,
                    "maximum": config.recipe_ceilings.max_runtime_seconds,
                }
            ],
        }
    }
    assert global_response.status_code == 422
    assert global_response.json() == expected
    assert workspace_response.status_code == 422
    assert workspace_response.json() == expected
    assert retry_response.status_code == 422
    assert retry_response.json() == expected
    assert capabilities.status_code == 200
    policy = capabilities.json()["service_policy"]
    assert policy["recipe"] == config.recipe_ceilings.to_dict()
    assert policy["request_body"] == {
        "max_upload_bytes": config.max_upload_bytes,
        "max_general_bytes": config.max_request_body_bytes,
        "max_upload_multipart_bytes": config.max_multipart_body_bytes,
        "multipart_upload_paths": [
            "/api/v1/assets/import",
            "/api/v1/assets",
            "/api/v1/workspaces/{workspace_id}/sources",
        ],
    }


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
            "gltf_validator": False,
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

def test_browser_validator_default_and_explicit_disable_round_trip_exactly() -> None:
    enabled = BrowserRecipe.model_validate({"preset": "balanced"})
    disabled = BrowserRecipe.model_validate(
        {"preset": "balanced", "validation": {"gltf_validator": False}}
    )

    assert enabled.validation.gltf_validator is True
    assert disabled.validation.gltf_validator is False

    for editor in (enabled, disabled):
        canonical = _recipe_from_web(editor)
        recovered = _browser_recipe_from_canonical(canonical)

        assert recovered is not None
        assert recovered.model_dump(mode="json") == editor.model_dump(mode="json")
        assert _recipe_from_web(recovered).canonical_json() == canonical.canonical_json()


def test_legacy_editor_json_without_validator_field_recovers_canonical_false() -> None:
    editor = BrowserRecipe.model_validate(
        {"preset": "balanced", "validation": {"gltf_validator": False}}
    )
    canonical = _recipe_from_web(editor)
    canonical_json = canonical.canonical_json()
    legacy_editor = editor.model_dump(mode="json")
    del legacy_editor["validation"]["gltf_validator"]

    recovered = _editor_recipe_for_job(
        {
            "recipe_json": canonical_json,
            "editor_recipe_json": json.dumps(legacy_editor),
        }
    )

    assert recovered is not None
    assert recovered.validation.gltf_validator is False
    assert _recipe_from_web(recovered).canonical_json() == canonical_json
    assert canonical.canonical_hash() == Recipe.from_json(canonical_json).canonical_hash()

