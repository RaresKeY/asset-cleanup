"""FastAPI application factory for the local-first asset service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlsplit
from uuid import uuid4

import numpy as np
from fastapi import (
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from asset_cleanup import __version__
from asset_cleanup.capabilities import capability_map
from asset_cleanup.models import Recipe
from asset_cleanup.source import (
    LoadLimits,
    probe_source,
    validate_external_references,
)
from asset_cleanup.util import confined_path, sha256_file
from asset_cleanup.web.config import WebConfig
from asset_cleanup.web.jobs import WorkerService, public_job
from asset_cleanup.web.store import TERMINAL_STATUSES, Store

API_PREFIX = "/api/v1"
_ASSET_ID = re.compile(r"^sha256-[0-9a-f]{64}$")
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_WORKSPACE_ID = re.compile(r"^[0-9a-f]{32}$")
_FORMAT_SUFFIX = {"glb": ".glb", "gltf": ".gltf", "obj": ".obj", "ply": ".ply", "stl": ".stl"}


class BoundedMultipartMiddleware:
    """Spool and bound complete multipart bodies before FastAPI parses them."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int, spool_directory: Path) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.spool_directory = spool_directory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_type = headers.get(b"content-type", b"").lower()
        if not content_type.startswith(b"multipart/form-data"):
            await self.app(scope, receive, send)
            return
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                await JSONResponse(status_code=400, content={"detail": "invalid Content-Length"})(
                    scope, receive, send
                )
                return
            if declared < 0 or declared > self.max_body_bytes:
                await JSONResponse(
                    status_code=413, content={"detail": "request exceeds configured limit"}
                )(scope, receive, send)
                return

        size = 0
        with tempfile.SpooledTemporaryFile(
            max_size=1024 * 1024,
            mode="w+b",
            dir=str(self.spool_directory),
        ) as buffered:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    await JSONResponse(
                        status_code=400, content={"detail": "request body disconnected"}
                    )(scope, receive, send)
                    return
                if message["type"] != "http.request":
                    continue
                body = message.get("body", b"")
                size += len(body)
                if size > self.max_body_bytes:
                    await JSONResponse(
                        status_code=413, content={"detail": "request exceeds configured limit"}
                    )(scope, receive, send)
                    return
                buffered.write(body)
                if not message.get("more_body", False):
                    break
            buffered.seek(0)

            async def replay() -> Message:
                chunk = buffered.read(1024 * 1024)
                return {
                    "type": "http.request",
                    "body": chunk,
                    "more_body": bool(chunk) and buffered.tell() < size,
                }

            await self.app(scope, replay, send)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateJobRequest(StrictRequest):
    asset_id: str = Field(pattern=r"^sha256-[0-9a-f]{64}$")
    recipe: Recipe


class CreateWorkspaceRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=128)


class BrowserGeometryRecipe(StrictRequest):
    enabled: bool = True
    strategy: Literal["target", "error"] = "error"
    target_faces: int = Field(default=5_000, ge=4)
    max_error: float = Field(default=0.0025, ge=0.0, le=1.0)
    preserve_boundaries: bool = True
    reconstruct_planar: bool = False
    reconstruct_primitives: bool = False


class BrowserCollisionRecipe(StrictRequest):
    enabled: bool = True
    mode: Literal["auto", "box", "sphere", "cylinder", "capsule", "convex-hull", "compound"] = (
        "auto"
    )
    fit: Literal["cover", "balanced", "inside"] = "balanced"
    body: Literal["static", "dynamic", "area"] = "static"


class BrowserValidationRecipe(StrictRequest):
    compare_geometry: bool = True
    compare_scene_inventory: bool = True
    compare_appearance: bool = False


class BrowserRecipe(StrictRequest):
    preset: Literal["close", "balanced", "distant", "collision", "custom"] = "balanced"
    geometry: BrowserGeometryRecipe = Field(default_factory=BrowserGeometryRecipe)
    collision: BrowserCollisionRecipe = Field(default_factory=BrowserCollisionRecipe)
    validation: BrowserValidationRecipe = Field(default_factory=BrowserValidationRecipe)


class WorkspaceJobRequest(StrictRequest):
    recipe: BrowserRecipe | Recipe


def _clean_filename(value: str | None) -> str:
    """Return a display-only basename without control characters."""

    value = value or "upload"
    value = PureWindowsPath(value).name
    value = PurePosixPath(value).name
    value = "".join(character for character in value if character.isprintable())
    return value[:255] or "upload"


def _asset_response(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": asset["id"],
        "name": asset["original_name"],
        "kind": asset["format"],
        "hash": asset["sha256"],
        "bytes": asset["size_bytes"],
        "media_type": asset["media_type"],
        "format": asset["format"],
        "created_utc": asset["created_utc"],
        "preview_url": f"{API_PREFIX}/assets/{asset['id']}/preview",
    }


def _require_job_id(job_id: str) -> None:
    if _JOB_ID.fullmatch(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")


def _require_asset_id(asset_id: str) -> None:
    if _ASSET_ID.fullmatch(asset_id) is None:
        raise HTTPException(status_code=404, detail="asset not found")


def _require_workspace_id(workspace_id: str) -> None:
    if _WORKSPACE_ID.fullmatch(workspace_id) is None:
        raise HTTPException(status_code=404, detail="workspace not found")


def _inspection_response(report: Any) -> dict[str, Any]:
    data = report if isinstance(report, dict) else report.to_dict()
    analyses = [item["analysis"] for item in data["geometries"]]
    candidates: list[dict[str, Any]] = []
    for analysis in analyses:
        for item in analysis.get("primitive_candidates", []):
            if not item.get("accepted", False):
                continue
            candidates.append(
                {
                    "kind": item.get("primitive", "irregular"),
                    "confidence": item.get("confidence", 0.0),
                    "support_fraction": item.get("support_fraction"),
                    "summary": item.get("rejection_reason") or item.get("evidence_status"),
                }
            )
    geometry_bounds = [item.get("bounds") for item in analyses if item.get("bounds")]
    bounds: dict[str, Any] = {}
    if geometry_bounds:
        minimum = np.min(np.asarray([item[0] for item in geometry_bounds], dtype=float), axis=0)
        maximum = np.max(np.asarray([item[1] for item in geometry_bounds], dtype=float), axis=0)
        extents = maximum - minimum
        bounds = {
            "minimum": minimum.tolist(),
            "maximum": maximum.tolist(),
            "extents": extents.tolist(),
            "diagonal": float(np.linalg.norm(extents)),
        }
    return {
        "schema": data["schema"],
        "topology": {
            "vertices": sum(int(item.get("vertices", 0)) for item in analyses),
            "faces": sum(int(item.get("faces", 0)) for item in analyses),
            "components": sum(int(item.get("connected_components", 0)) for item in analyses),
            "watertight": bool(analyses) and all(bool(item.get("watertight")) for item in analyses),
        },
        "bounds": bounds,
        "scene": {
            "nodes": data["scene"].get("nodes", 0),
            "geometries": data["scene"].get("geometry_definitions", 0),
        },
        "primitive_evidence": candidates[:100],
        "warnings": data["warnings"],
        "raw": data,
    }


def _recipe_from_web(value: BrowserRecipe | Recipe | dict[str, Any]) -> Recipe:
    if isinstance(value, Recipe):
        return value
    if not isinstance(value, BrowserRecipe):
        value = BrowserRecipe.model_validate(value)
    value = value.model_dump(mode="python")
    preset = str(value.get("preset", "balanced"))
    if preset == "custom":
        preset = "balanced"
    recipe = Recipe.from_preset(preset)
    data = recipe.model_dump(mode="python")
    geometry_input = value.get("geometry", {})
    collision_input = value.get("collision", {})
    validation_input = value.get("validation", {})
    geometry = data["settings"]["geometry"]
    geometry_enabled = bool(geometry_input.get("enabled", True))
    data["stages"]["geometry"] = geometry_enabled
    if not geometry_enabled:
        geometry.update(
            {
                "simplify_mode": "preserve",
                "target_faces": None,
                "target_ratio": None,
                "max_error_fraction": None,
            }
        )
    elif geometry_input.get("strategy", "error") == "target":
        geometry.update(
            {
                "simplify_mode": "target",
                "target_faces": max(4, int(geometry_input.get("target_faces", 5_000))),
                "target_ratio": None,
                "max_error_fraction": None,
            }
        )
    else:
        geometry.update(
            {
                "simplify_mode": "error",
                "target_faces": None,
                "target_ratio": None,
                "max_error_fraction": float(geometry_input.get("max_error", 0.0025)),
            }
        )
    geometry["preserve_boundaries"] = bool(geometry_input.get("preserve_boundaries", True))
    if geometry_input.get("reconstruct_planar", False):
        raise ValueError("planar reconstruction requires an adapter not installed in this release")
    if geometry_input.get("reconstruct_primitives", False):
        raise ValueError("curved primitive reconstruction is experimental and unavailable")

    collision_enabled = bool(collision_input.get("enabled", True))
    data["stages"]["collision"] = collision_enabled
    collision = data["settings"]["collision"]
    if collision_enabled:
        collision.update(
            {
                "mode": collision_input.get("mode", "auto"),
                "fit_policy": collision_input.get("fit", "balanced"),
                "body_type": collision_input.get("body", "static"),
            }
        )
    else:
        collision.update({"mode": "none", "body_type": "none"})
    validation = data["settings"]["validation"]
    validation.update(
        {
            "compare_geometry": bool(validation_input.get("compare_geometry", True)),
            "gltf_validator": False,
            "compare_scene_inventory": bool(validation_input.get("compare_scene_inventory", True)),
            "compare_appearance": bool(validation_input.get("compare_appearance", False)),
        }
    )
    return Recipe.model_validate(data)


async def _save_upload(file: UploadFile, config: WebConfig, store: Store) -> dict[str, Any]:
    temporary = config.temporary_root / f"upload-{uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("xb") as stream:
            while chunk := await file.read(config.upload_chunk_bytes):
                size += len(chunk)
                if size > config.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="upload exceeds configured limit")
                digest.update(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if size == 0:
            raise HTTPException(status_code=422, detail="empty uploads are not supported")

        probe = probe_source(temporary, LoadLimits(max_file_bytes=config.max_upload_bytes))
        if not probe.supported:
            raise HTTPException(status_code=422, detail=probe.reason or "unsupported asset")
        try:
            validate_external_references(
                temporary,
                probe,
                LoadLimits(max_file_bytes=config.max_upload_bytes),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"source dependencies are not self-contained: {exc}",
            ) from exc

        sha256 = digest.hexdigest()
        suffix = _FORMAT_SUFFIX.get(probe.format, ".bin")
        destination = config.assets_root / sha256[:2] / sha256 / f"source{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            temporary.replace(destination)
        except FileExistsError:
            temporary.unlink(missing_ok=True)
        relative = destination.relative_to(config.data_root).as_posix()
        return store.put_asset(
            sha256=sha256,
            original_name=_clean_filename(file.filename),
            media_type=probe.media_type,
            format=probe.format,
            size_bytes=size,
            storage_path=relative,
        )
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()


def _asset_path(asset: dict[str, Any], config: WebConfig) -> Path:
    return confined_path(config.data_root, str(asset["storage_path"]))


def _terminate_child(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=3)


def _run_asset_child(operation: str, source: Path, output: Path, config: WebConfig) -> None:
    """Run parser/export work outside the API process with hard ceilings."""

    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = config.temporary_root / f"{operation}-{uuid4().hex}.stderr.log"
    try:
        with log_path.open("xb") as error_log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "asset_cleanup.sandbox_child",
                    operation,
                    str(source),
                    str(output),
                    "--max-file-bytes",
                    str(config.max_upload_bytes),
                    "--max-texture-pixels",
                    str(config.max_texture_pixels),
                    "--max-runtime-seconds",
                    str(config.inspection_timeout_seconds),
                    "--max-memory-bytes",
                    str(config.inspection_memory_bytes),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=error_log,
                close_fds=True,
                start_new_session=os.name == "posix",
            )
            try:
                process.wait(timeout=config.inspection_timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                _terminate_child(process)
                raise RuntimeError(f"{operation} exceeded its wall-time limit") from exc
        if process.returncode != 0:
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-4_000:]
            raise RuntimeError(
                f"{operation} child exited with code {process.returncode}: {detail}".rstrip()
            )
        if not output.is_file():
            raise RuntimeError(f"{operation} child did not produce its output")
    finally:
        log_path.unlink(missing_ok=True)


def _ensure_inspection(asset: dict[str, Any], config: WebConfig) -> dict[str, Any]:
    source = _asset_path(asset, config)
    if source.stat().st_size != int(asset["size_bytes"]) or sha256_file(source) != asset["sha256"]:
        raise HTTPException(status_code=409, detail="source integrity check failed")
    result_path = (
        config.data_root / "inspections" / str(asset["sha256"][:2]) / f"{asset['sha256']}.json"
    )
    if not result_path.is_file():
        _run_asset_child("inspect", source, result_path, config)
    if result_path.stat().st_size > config.max_inspection_report_bytes:
        result_path.unlink(missing_ok=True)
        raise RuntimeError("inspection report exceeds the web response limit")
    try:
        value = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result_path.unlink(missing_ok=True)
        raise RuntimeError("inspection child produced invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("inspection child produced a non-object report")
    return value


def _ensure_preview(asset: dict[str, Any], config: WebConfig) -> Path:
    source = _asset_path(asset, config)
    if source.stat().st_size != int(asset["size_bytes"]) or sha256_file(source) != asset["sha256"]:
        raise HTTPException(status_code=409, detail="source integrity check failed")
    preview = config.data_root / "previews" / str(asset["sha256"][:2]) / f"{asset['sha256']}.glb"
    if preview.is_file():
        return preview
    _run_asset_child("preview", source, preview, config)
    return preview


def _read_registered_json(
    job: dict[str, Any],
    artifact_records: dict[str, dict[str, Any]],
    relative: str,
    config: WebConfig,
) -> dict[str, Any] | None:
    record = artifact_records.get(relative)
    if record is None or int(record["size_bytes"]) > config.max_evidence_json_bytes:
        return None
    try:
        root = confined_path(config.data_root, str(job["output_path"]))
        path = confined_path(root, relative)
        if (
            not path.is_file()
            or path.stat().st_size != int(record["size_bytes"])
            or sha256_file(path) != str(record["sha256"])
        ):
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _web_job(job: dict[str, Any], store: Store, config: WebConfig) -> dict[str, Any]:
    artifact_rows = store.list_artifacts(str(job["id"]))
    artifact_records = {str(item["path"]): item for item in artifact_rows}
    artifact_paths = set(artifact_records)
    artifacts = [
        {
            "name": item["path"],
            "path": item["path"],
            "kind": item["kind"],
            "size": item["size_bytes"],
            "size_bytes": item["size_bytes"],
            "sha256": item["sha256"],
            "url": f"{API_PREFIX}/jobs/{job['id']}/artifacts/{quote(str(item['path']), safe='/')}",
        }
        for item in artifact_rows
    ]
    if str(job["status"]) == "succeeded" and "manifest.json" in artifact_paths:
        artifacts.append(
            {
                "name": "candidate-package.zip",
                "kind": "package",
                "size": None,
                "url": f"{API_PREFIX}/jobs/{job['id']}/package.zip",
            }
        )
    runtime_glbs = sorted(
        item for item in artifact_paths if item.startswith("60_runtime/") and item.endswith(".glb")
    )
    candidate = next(
        (
            path
            for path in (*runtime_glbs, "20_geometry/visual_candidate.glb")
            if path in artifact_paths
        ),
        None,
    )
    collision = (
        "50_collision/asset.collision.glb"
        if "50_collision/asset.collision.glb" in artifact_paths
        else None
    )
    status_value = str(job["status"])
    state = (
        str(job.get("result_status") or "candidate")
        if status_value == "succeeded"
        else status_value
    )
    recent_events = store.events(str(job["id"]), after=0, limit=500)
    latest = recent_events[-1] if recent_events else None
    stage = (latest or {}).get("data", {}).get("stage")
    stage_progress = {
        "intake": 0.10,
        "inspect": 0.25,
        "geometry": 0.50,
        "collision": 0.70,
        "validation": 0.85,
        "package": 0.95,
    }
    progress = (
        1.0
        if status_value in TERMINAL_STATUSES
        else stage_progress.get(str(stage), 0.08 if status_value == "running" else 0.02)
    )
    result = public_job(job)
    result.update(
        {
            "state": state,
            "progress": progress,
            "message": (
                "Cancellation requested; stopping the isolated worker"
                if status_value == "running" and job.get("cancel_requested")
                else latest["message"]
                if latest
                else None
            ),
            "stage": stage,
            "source_id": job["asset_id"],
            "source_preview_url": f"{API_PREFIX}/assets/{job['asset_id']}/preview",
            "candidate_preview_url": (
                f"{API_PREFIX}/jobs/{job['id']}/artifacts/{quote(candidate, safe='/')}"
                if candidate
                else None
            ),
            "collision_preview_url": (
                f"{API_PREFIX}/jobs/{job['id']}/artifacts/{quote(collision, safe='/')}"
                if collision
                else None
            ),
            "artifacts": artifacts,
            "events": [],
            "metrics": _read_registered_json(job, artifact_records, "metrics.json", config),
            "validation": _read_registered_json(
                job, artifact_records, "70_proof/validation.json", config
            ),
        }
    )
    return result


def _build_job_package(
    job: dict[str, Any],
    artifacts: list[dict[str, Any]],
    config: WebConfig,
) -> Path:
    root = confined_path(config.data_root, str(job["output_path"]))
    package = config.data_root / "packages" / f"{job['id']}.zip"
    temporary = package.parent / f".{job['id']}.{uuid4().hex}.zip"
    package.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for artifact in artifacts:
                relative = str(artifact["path"])
                normalized = PurePosixPath(relative)
                if (
                    "\\" in relative
                    or normalized.is_absolute()
                    or ".." in normalized.parts
                    or normalized.as_posix() != relative
                ):
                    raise HTTPException(status_code=409, detail="invalid registered artifact path")
                path = confined_path(root, relative)
                if (
                    not path.is_file()
                    or path.stat().st_size != int(artifact["size_bytes"])
                    or sha256_file(path) != artifact["sha256"]
                ):
                    raise HTTPException(status_code=409, detail="artifact integrity check failed")
                archive.write(path, arcname=relative)
        temporary.replace(package)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return package


def create_app(
    config: WebConfig | None = None,
    *,
    data_root: Path | None = None,
    embedded_worker: bool | None = None,
) -> FastAPI:
    """Build an app with no global state and an optional embedded worker."""

    if config is not None and data_root is not None:
        raise ValueError("pass config or data_root, not both")
    if config is None:
        config = WebConfig(data_root=data_root or Path(".asset-cleanup"))
    if embedded_worker is not None:
        config = WebConfig(
            data_root=config.data_root,
            max_upload_bytes=config.max_upload_bytes,
            upload_chunk_bytes=config.upload_chunk_bytes,
            embedded_worker=embedded_worker,
            worker_poll_seconds=config.worker_poll_seconds,
            event_poll_seconds=config.event_poll_seconds,
            inspection_timeout_seconds=config.inspection_timeout_seconds,
            inspection_memory_bytes=config.inspection_memory_bytes,
            max_inspection_report_bytes=config.max_inspection_report_bytes,
            max_evidence_json_bytes=config.max_evidence_json_bytes,
            max_texture_pixels=config.max_texture_pixels,
            allowed_hosts=config.allowed_hosts,
        )
    config.initialize()
    store = Store(config.database_path)
    store.initialize()
    worker = WorkerService(config, store)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.ready = True
        if config.embedded_worker:
            worker.start()
        try:
            yield
        finally:
            application.state.ready = False
            worker.stop()

    app = FastAPI(
        title="asset-cleanup",
        version=__version__,
        docs_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.store = store
    app.state.worker = worker
    app.state.ready = False
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(config.allowed_hosts))
    app.add_middleware(
        BoundedMultipartMiddleware,
        max_body_bytes=config.max_upload_bytes + 1024 * 1024,
        spool_directory=config.temporary_root,
    )

    @app.middleware("http")
    async def browser_boundary(request: Request, call_next: Any) -> Any:
        """Enforce same-origin browser writes and baseline local-service headers."""

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            host = request.headers.get("host", "").lower()
            origin = request.headers.get("origin")
            fetch_site = request.headers.get("sec-fetch-site", "").lower()
            if fetch_site == "cross-site" or (
                origin is not None and urlsplit(origin).netloc.lower() != host
            ):
                return JSONResponse(
                    status_code=403, content={"detail": "cross-origin write rejected"}
                )
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    @app.get("/health/live")
    def health_live() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.get("/health/ready")
    def health_ready(request: Request) -> dict[str, Any]:
        if not bool(request.app.state.ready):
            raise HTTPException(status_code=503, detail="service startup is not complete")
        try:
            with store.connect() as database:
                database.execute("SELECT 1").fetchone()
        except Exception as error:
            raise HTTPException(status_code=503, detail="state database unavailable") from error
        if config.embedded_worker and not worker.running:
            raise HTTPException(status_code=503, detail="embedded worker is unavailable")
        return {
            "status": "ready",
            "embedded_worker": config.embedded_worker,
            "worker_running": worker.running,
        }

    @app.get(f"{API_PREFIX}/health")
    def api_health(request: Request) -> dict[str, Any]:
        return health_ready(request)

    @app.get(f"{API_PREFIX}/capabilities")
    def capabilities() -> dict[str, Any]:
        return {"version": __version__, "capabilities": capability_map()}

    @app.get(f"{API_PREFIX}/workspaces")
    def list_workspaces() -> list[dict[str, Any]]:
        return store.list_workspaces()

    @app.post(f"{API_PREFIX}/workspaces", status_code=status.HTTP_201_CREATED)
    def create_workspace(body: CreateWorkspaceRequest) -> dict[str, Any]:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="workspace name must be visible")
        return store.create_workspace(name)

    @app.get(f"{API_PREFIX}/workspaces/{{workspace_id}}/sources")
    def list_workspace_sources(workspace_id: str) -> list[dict[str, Any]]:
        _require_workspace_id(workspace_id)
        if store.get_workspace(workspace_id) is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        return [_asset_response(asset) for asset in store.list_workspace_assets(workspace_id)]

    @app.post(
        f"{API_PREFIX}/workspaces/{{workspace_id}}/sources",
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_workspace_source(
        workspace_id: str,
        file: Annotated[UploadFile, File(description="Self-contained mesh source")],
        content_length: Annotated[int | None, Header(ge=0)] = None,
    ) -> dict[str, Any]:
        _require_workspace_id(workspace_id)
        if store.get_workspace(workspace_id) is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        if content_length is not None and content_length > config.max_upload_bytes + 1024 * 1024:
            raise HTTPException(status_code=413, detail="request exceeds configured limit")
        asset = await _save_upload(file, config, store)
        if not store.attach_asset(workspace_id, str(asset["id"])):
            raise HTTPException(status_code=404, detail="workspace not found")
        return _asset_response(asset)

    @app.get(f"{API_PREFIX}/workspaces/{{workspace_id}}/sources/{{asset_id}}/inspection")
    def inspect_workspace_source(workspace_id: str, asset_id: str) -> dict[str, Any]:
        _require_workspace_id(workspace_id)
        _require_asset_id(asset_id)
        if not store.workspace_has_asset(workspace_id, asset_id):
            raise HTTPException(status_code=404, detail="source not found in workspace")
        asset = store.get_asset(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="asset not found")
        try:
            return _inspection_response(_ensure_inspection(asset, config))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"inspection failed: {exc}") from exc

    @app.post(
        f"{API_PREFIX}/workspaces/{{workspace_id}}/sources/{{asset_id}}/jobs",
        status_code=status.HTTP_201_CREATED,
    )
    def create_workspace_job(
        workspace_id: str, asset_id: str, body: WorkspaceJobRequest
    ) -> dict[str, Any]:
        _require_workspace_id(workspace_id)
        _require_asset_id(asset_id)
        if not store.workspace_has_asset(workspace_id, asset_id):
            raise HTTPException(status_code=404, detail="source not found in workspace")
        try:
            recipe = _recipe_from_web(body.recipe)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job = store.create_job(asset_id, recipe)
        worker.notify()
        result = _web_job(job, store, config)
        result["workspace_id"] = workspace_id
        return result

    @app.post(f"{API_PREFIX}/assets/import", status_code=status.HTTP_201_CREATED)
    @app.post(f"{API_PREFIX}/assets", status_code=status.HTTP_201_CREATED, include_in_schema=False)
    async def import_asset(
        file: Annotated[UploadFile, File(description="GLB, glTF, OBJ, PLY, or STL mesh")],
        content_length: Annotated[int | None, Header(ge=0)] = None,
    ) -> dict[str, Any]:
        if content_length is not None and content_length > config.max_upload_bytes + 1024 * 1024:
            raise HTTPException(status_code=413, detail="request exceeds configured limit")
        return _asset_response(await _save_upload(file, config, store))

    @app.get(f"{API_PREFIX}/assets/{{asset_id}}")
    def get_asset(asset_id: str) -> dict[str, Any]:
        _require_asset_id(asset_id)
        asset = store.get_asset(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="asset not found")
        return _asset_response(asset)

    @app.get(f"{API_PREFIX}/assets/{{asset_id}}/preview")
    def preview_asset(asset_id: str) -> FileResponse:
        _require_asset_id(asset_id)
        asset = store.get_asset(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="asset not found")
        try:
            preview = _ensure_preview(asset, config)
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"preview generation failed: {exc}"
            ) from exc
        return FileResponse(
            preview,
            media_type="model/gltf-binary",
            headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"},
        )

    @app.get(f"{API_PREFIX}/recipes/schema")
    def recipe_schema() -> dict[str, Any]:
        return Recipe.model_json_schema()

    @app.post(f"{API_PREFIX}/recipes/validate")
    def validate_recipe(recipe: Recipe) -> dict[str, Any]:
        return {
            "valid": True,
            "recipe_hash": recipe.canonical_hash(),
            "recipe": recipe.canonical_dict(),
        }

    @app.post(f"{API_PREFIX}/jobs", status_code=status.HTTP_201_CREATED)
    def create_job(body: CreateJobRequest) -> dict[str, Any]:
        if store.get_asset(body.asset_id) is None:
            raise HTTPException(status_code=404, detail="asset not found")
        job = store.create_job(body.asset_id, body.recipe)
        worker.notify()
        return _web_job(job, store, config)

    @app.get(f"{API_PREFIX}/jobs")
    def list_jobs(
        job_status: Annotated[
            Literal["queued", "running", "succeeded", "failed", "cancelled"] | None,
            Query(alias="status"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        jobs = store.list_jobs(status=job_status, limit=limit, offset=offset)
        return {
            "items": [_web_job(job, store, config) for job in jobs],
            "limit": limit,
            "offset": offset,
        }

    @app.get(f"{API_PREFIX}/jobs/{{job_id}}")
    def get_job(job_id: str) -> dict[str, Any]:
        _require_job_id(job_id)
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _web_job(job, store, config)

    @app.post(f"{API_PREFIX}/jobs/{{job_id}}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        _require_job_id(job_id)
        job = store.request_cancel(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        worker.notify()
        return _web_job(job, store, config)

    @app.post(f"{API_PREFIX}/jobs/{{job_id}}/retry", status_code=status.HTTP_201_CREATED)
    def retry_job(job_id: str) -> dict[str, Any]:
        _require_job_id(job_id)
        previous = store.get_job(job_id)
        if previous is None:
            raise HTTPException(status_code=404, detail="job not found")
        if previous["status"] not in {"failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="only failed or cancelled jobs can retry")
        recipe = Recipe.from_json(str(previous["recipe_json"]))
        job = store.create_job(str(previous["asset_id"]), recipe, retry_of=job_id)
        worker.notify()
        return _web_job(job, store, config)

    @app.get(f"{API_PREFIX}/jobs/{{job_id}}/events")
    async def job_events(
        job_id: str,
        request: Request,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
        after: Annotated[int | None, Query(ge=0)] = None,
        follow: bool = True,
    ) -> StreamingResponse:
        _require_job_id(job_id)
        if store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")
        cursor = after or 0
        if last_event_id is not None:
            try:
                cursor = max(cursor, int(last_event_id))
            except ValueError as error:
                raise HTTPException(status_code=400, detail="invalid Last-Event-ID") from error
            if cursor < 0:
                raise HTTPException(status_code=400, detail="invalid Last-Event-ID")

        async def stream() -> AsyncIterator[str]:
            nonlocal cursor
            idle_cycles = 0
            while True:
                events = store.events(job_id, after=cursor)
                for event in events:
                    cursor = int(event["sequence"])
                    data = event.get("data", {})
                    kind = str(event["kind"])
                    payload = json.dumps(
                        {
                            "sequence": cursor,
                            "created_utc": event["created_utc"],
                            "level": data.get(
                                "level",
                                "error"
                                if kind.endswith("failed")
                                else "warning"
                                if "cancel" in kind
                                else "info",
                            ),
                            "type": kind,
                            "stage": data.get("stage"),
                            "message": event["message"],
                            "data": data,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    yield f"id: {cursor}\ndata: {payload}\n\n"
                job = store.get_job(job_id)
                terminal = job is None or job["status"] in TERMINAL_STATUSES
                if not follow or terminal or await request.is_disconnected():
                    break
                if events:
                    idle_cycles = 0
                else:
                    idle_cycles += 1
                    if idle_cycles >= max(1, int(15 / config.event_poll_seconds)):
                        yield ": keepalive\n\n"
                        idle_cycles = 0
                await asyncio.sleep(config.event_poll_seconds)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get(f"{API_PREFIX}/jobs/{{job_id}}/artifacts/{{artifact_path:path}}")
    def download_artifact(job_id: str, artifact_path: str) -> FileResponse:
        _require_job_id(job_id)
        if not artifact_path or Path(artifact_path).is_absolute():
            raise HTTPException(status_code=404, detail="artifact not found")
        artifact = store.get_artifact(job_id, artifact_path)
        job = store.get_job(job_id)
        if artifact is None or job is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        try:
            job_root = confined_path(config.data_root, str(job["output_path"]))
            path = confined_path(job_root, artifact_path)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="artifact not found") from error
        if not path.is_file():
            raise HTTPException(status_code=404, detail="artifact not found")
        if (
            path.stat().st_size != int(artifact["size_bytes"])
            or sha256_file(path) != artifact["sha256"]
        ):
            raise HTTPException(status_code=409, detail="artifact integrity check failed")
        return FileResponse(path, filename=Path(artifact_path).name)

    @app.get(f"{API_PREFIX}/jobs/{{job_id}}/package.zip")
    def download_package(job_id: str) -> FileResponse:
        _require_job_id(job_id)
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job["status"] != "succeeded":
            raise HTTPException(status_code=409, detail="only completed jobs can be packaged")
        artifacts = store.list_artifacts(job_id)
        package = _build_job_package(job, artifacts, config)
        return FileResponse(
            package,
            media_type="application/zip",
            filename=f"asset-cleanup-{job_id[:8]}.zip",
        )

    return app


__all__ = ["API_PREFIX", "create_app"]
