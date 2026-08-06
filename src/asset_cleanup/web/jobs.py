"""Persistent job execution shared by embedded and standalone workers."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

from asset_cleanup.models import Recipe
from asset_cleanup.processing import RunResult
from asset_cleanup.util import confined_path, sha256_file
from asset_cleanup.web.config import WebConfig
from asset_cleanup.web.store import Store

STAGE_PROGRESS = {
    "queue": 0.0,
    "intake": 0.10,
    "inspect": 0.25,
    "geometry": 0.50,
    "collision": 0.70,
    "validation": 0.85,
    "package": 0.95,
    "worker": 0.02,
}


def public_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return one stable console/progress event, including legacy stored rows."""

    kind = str(event["kind"])
    data = dict(event.get("data") or {})
    stage = data.get("stage")
    if stage is None:
        stage = {
            "job.queued": "queue",
            "job.started": "intake",
            "job.cancel_requested": "worker",
            "job.succeeded": "package",
            "job.failed": "worker",
            "job.cancelled": "worker",
        }.get(kind)
    status = data.get("status")
    if status is None:
        status = {
            "job.queued": "queued",
            "job.started": "running",
            "job.cancel_requested": "running",
            "job.succeeded": "succeeded",
            "job.failed": "failed",
            "job.cancelled": "cancelled",
        }.get(kind, "running" if kind.startswith("stage.") else None)
    stage_status = data.get("stage_status")
    if stage_status is None:
        stage_status = kind.split(".", 1)[1] if kind.startswith("stage.") else status
    progress = data.get("progress")
    if progress is None:
        progress = (
            1.0
            if status in {"succeeded", "failed", "cancelled"}
            else STAGE_PROGRESS.get(str(stage), 0.02 if status == "running" else 0.0)
        )
    level = data.get(
        "level",
        "error" if kind.endswith("failed") else "warning" if "cancel" in kind else "info",
    )
    data.update(
        {
            "stage": stage,
            "status": status,
            "stage_status": stage_status,
            "progress": progress,
        }
    )
    return {
        "sequence": int(event["sequence"]),
        "created_utc": event["created_utc"],
        "level": level,
        "type": kind,
        "stage": stage,
        "status": status,
        "stage_status": stage_status,
        "progress": progress,
        "message": event["message"],
        "data": data,
    }


class JobWorker:
    """Claim and execute at most one queued job per call."""

    def __init__(self, config: WebConfig, store: Store | None = None) -> None:
        self.config = config
        if store is None:
            config.initialize()
            store = Store(config.database_path)
            store.initialize()
        self.store = store
        self._shutdown = threading.Event()

    def request_stop(self) -> None:
        self._shutdown.set()

    def clear_stop(self) -> None:
        self._shutdown.clear()

    def run_once(self) -> bool:
        job = self.store.claim_next()
        if job is None:
            return False
        output: Path | None = None
        try:
            recipe = Recipe.from_json(str(job["recipe_json"]))
            self.config.recipe_ceilings.enforce(recipe)
            asset = self.store.get_asset(str(job["asset_id"]))
            if asset is None:
                raise RuntimeError("job references a missing asset")
            source = confined_path(self.config.data_root, str(asset["storage_path"]))
            if (
                not source.is_file()
                or source.stat().st_size != int(asset["size_bytes"])
                or sha256_file(source) != str(asset["sha256"])
            ):
                raise RuntimeError("source integrity check failed before processing")
            output = confined_path(self.config.data_root, str(job["output_path"]))
            expected = (self.config.jobs_root / str(job["id"])).resolve()
            if output != expected:
                raise RuntimeError("job output path does not match its identifier")
            result = self._run_with_events(str(job["id"]), source, recipe, output)
            artifacts = list(result.artifacts)
            manifest = Path(result.manifest_path).resolve()
            if manifest.parent == output and not any(
                item["path"] == manifest.name for item in artifacts
            ):
                artifacts.append(
                    {
                        "path": manifest.name,
                        "kind": "run-manifest",
                        "stage": "package",
                        "bytes": manifest.stat().st_size,
                        "sha256": sha256_file(manifest),
                    }
                )
            self.store.finish_job(str(job["id"]), result_status=result.status, artifacts=artifacts)
        except Exception as error:
            partial = self._partial_artifacts(output) if output is not None else []
            self.store.fail_job(str(job["id"]), error, artifacts=partial)
        return True

    @staticmethod
    def _partial_artifacts(output: Path) -> list[dict[str, Any]]:
        """Register only confined files that actually survived a failed child."""

        candidates: dict[str, tuple[str, str]] = {}
        manifest_path = output / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for item in manifest.get("artifacts", []):
                    if isinstance(item, dict) and isinstance(item.get("path"), str):
                        candidates[item["path"]] = (
                            str(item.get("kind", "partial")),
                            str(item.get("stage", "partial")),
                        )
            except (OSError, json.JSONDecodeError):
                pass
            candidates["manifest.json"] = ("run-manifest", "failure")
        if (output / "events.jsonl").is_file():
            candidates["events.jsonl"] = ("event-log", "failure")
        artifacts: list[dict[str, Any]] = []
        for relative, (kind, stage) in sorted(candidates.items()):
            normalized = PurePosixPath(relative)
            if (
                "\\" in relative
                or normalized.is_absolute()
                or ".." in normalized.parts
                or normalized.as_posix() != relative
            ):
                continue
            try:
                path = confined_path(output, relative)
            except ValueError:
                continue
            if path.is_file():
                artifacts.append(
                    {
                        "path": relative,
                        "kind": kind,
                        "stage": stage,
                        "bytes": path.stat().st_size,
                        "sha256": sha256_file(path),
                    }
                )
        return artifacts

    def _run_with_events(
        self, job_id: str, source: Path, recipe: Recipe, output: Path
    ) -> RunResult:
        """Run one isolated child while copying structured progress into SQLite."""

        event_path = output / "events.jsonl"
        recipe_path = self.config.temporary_root / f"{job_id}.recipe.json"
        log_path = self.config.temporary_root / f"{job_id}.stderr.log"
        recipe.save(recipe_path)
        event_offset = 0
        started = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        try:
            with log_path.open("wb") as error_log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "asset_cleanup.worker_child",
                        str(source),
                        str(recipe_path),
                        str(output),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=error_log,
                    close_fds=True,
                    start_new_session=os.name == "posix",
                )
                while process.poll() is None:
                    event_offset = self._ingest_events(job_id, event_path, event_offset)
                    current = self.store.get_job(job_id)
                    cancelled = current is not None and bool(current["cancel_requested"])
                    if cancelled:
                        self._terminate(process)
                        raise RuntimeError("processing cancelled by request")
                    if self._shutdown.is_set():
                        self._terminate(process)
                        raise RuntimeError("worker stopped while processing")
                    if time.monotonic() - started > recipe.settings.limits.max_runtime_seconds:
                        self._terminate(process)
                        raise TimeoutError("processing exceeded max_runtime_seconds")
                    try:
                        process.wait(timeout=min(0.1, self.config.worker_poll_seconds))
                    except subprocess.TimeoutExpired:
                        pass
                event_offset = self._ingest_events(job_id, event_path, event_offset)
            if process.returncode != 0:
                detail = log_path.read_text(encoding="utf-8", errors="replace")[-4_000:]
                raise RuntimeError(
                    f"processing child exited with code {process.returncode}: {detail}".rstrip()
                )
            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            status = str(manifest.get("status", "failed"))
            if status not in {"accepted", "candidate"}:
                raise RuntimeError(f"processing child produced terminal status {status!r}")
            artifacts = manifest.get("artifacts", [])
            if not isinstance(artifacts, list):
                raise RuntimeError("processing manifest contains an invalid artifact list")
            return RunResult(
                output_directory=str(output),
                manifest_path=str(manifest_path),
                status=status,
                artifacts=artifacts,
                warnings=list(manifest.get("warnings", [])),
            )
        except BaseException:
            if process is not None:
                self._terminate(process)
            try:
                self._ingest_events(job_id, event_path, event_offset)
            except Exception:
                pass
            raise
        finally:
            recipe_path.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
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

    def _ingest_events(self, job_id: str, path: Path, offset: int) -> int:
        if not path.is_file():
            return offset
        with path.open("rb") as stream:
            stream.seek(offset)
            while line := stream.readline(1024 * 1024 + 1):
                if len(line) > 1024 * 1024:
                    raise RuntimeError("pipeline event exceeds the web event limit")
                if not line.endswith(b"\n"):
                    break
                event = json.loads(line)
                kind = str(event.get("type", "pipeline.event"))
                if kind not in {"job.completed", "job.failed"}:
                    data = dict(event.get("data") or {})
                    stage = event.get("stage")
                    stage_status = kind.split(".", 1)[1] if kind.startswith("stage.") else None
                    data.update(
                        {
                            "stage": stage,
                            "status": "running",
                            "stage_status": stage_status,
                            "progress": STAGE_PROGRESS.get(str(stage), 0.02),
                            "level": event.get("level", "info"),
                            "pipeline_sequence": event.get("sequence"),
                            "pipeline_created_utc": event.get("created_utc"),
                        }
                    )
                    self.store.append_event(job_id, kind, str(event.get("message", kind)), data)
                offset = stream.tell()
        return offset


class WorkerService:
    """Cooperative in-process worker suitable for app lifespan or CLI use."""

    def __init__(self, config: WebConfig, store: Store | None = None) -> None:
        self.config = config
        if store is None:
            config.initialize()
            store = Store(config.database_path)
            store.initialize()
        self.store = store
        self.worker = JobWorker(config, self.store)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._recovered = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.worker.clear_stop()
        self._recover_once()
        self._thread = threading.Thread(target=self._run, name="asset-cleanup-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self.worker.request_stop()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def notify(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            did_work = self.worker.run_once()
            if not did_work:
                self._wake.wait(self.config.worker_poll_seconds)
                self._wake.clear()

    def run_until_idle(self, *, max_jobs: int | None = None) -> int:
        """Synchronously process queued jobs; useful for a standalone CLI worker."""

        self._recover_once()
        processed = 0
        while max_jobs is None or processed < max_jobs:
            if not self.worker.run_once():
                break
            processed += 1
        return processed

    def _recover_once(self) -> None:
        """Recover stale claims once per single-worker service lifetime."""

        if not self._recovered:
            self.store.recover_interrupted_jobs()
            self._recovered = True


def public_job(
    job: dict[str, Any], *, artifacts: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Remove internal filesystem and recipe storage details from API records."""

    result = {
        key: value
        for key, value in job.items()
        if key not in {"output_path", "recipe_json", "editor_recipe_json"}
    }
    if result.get("workspace_id") is None:
        result.pop("workspace_id", None)
    recipe_json = job.get("recipe_json")
    if isinstance(recipe_json, str):
        result["recipe"] = json.loads(recipe_json)
    editor_recipe_json = job.get("editor_recipe_json")
    if isinstance(editor_recipe_json, str):
        try:
            result["editor_recipe"] = json.loads(editor_recipe_json)
        except json.JSONDecodeError:
            pass
    if artifacts is not None:
        result["artifacts"] = artifacts
    return result
