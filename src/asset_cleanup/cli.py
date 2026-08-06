"""Canonical command-line interface for local and container execution."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

import typer
import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from asset_cleanup import __version__
from asset_cleanup.capabilities import detect_capabilities
from asset_cleanup.errors import AssetCleanupError, InputRejectedError, ProcessingError
from asset_cleanup.models import (
    BodyType,
    CollisionMode,
    LimitsSettings,
    Recipe,
    SimplifyMode,
)
from asset_cleanup.processing import RunResult, plan_run
from asset_cleanup.recipes import preset_names
from asset_cleanup.source import LoadLimits, probe_source, referenced_files
from asset_cleanup.util import confined_path, sha256_file, write_json

_DEFAULT_LIMITS = LimitsSettings()


def _assert_output_not_source_member(
    input_path: Path,
    output: Path | None,
    *,
    max_input_bytes: int,
    max_texture_pixels: int,
) -> None:
    """Prevent report options from replacing the source or a bundle member."""

    if output is None:
        return
    resolved_input = input_path.expanduser().resolve()
    protected = {resolved_input}
    limits = LoadLimits(
        max_file_bytes=max_input_bytes,
        max_referenced_bytes=max_input_bytes,
        max_texture_pixels=max_texture_pixels,
    )
    try:
        probe = probe_source(resolved_input, limits)
        protected.update(
            path.resolve() for _, path in referenced_files(resolved_input, probe, limits)
        )
    except InputRejectedError:
        # The operation itself will report invalid intake. The primary path is
        # still protected even when dependency enumeration cannot complete.
        pass
    if output.expanduser().resolve() in protected:
        raise ValueError("report output must not replace the source or a source bundle member")


console = Console()
error_console = Console(stderr=True)
app = typer.Typer(
    name="asset-cleanup",
    help="Inspect and derive reproducible visual, collision, and runtime asset candidates.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
recipe_app = typer.Typer(help="Create, validate, inspect, and export typed recipes.")
app.add_typer(recipe_app, name="recipe")


def _exit_error(error: BaseException) -> None:
    error_console.print(f"[bold red]error:[/bold red] {error}")
    raise typer.Exit(code=2) from error


def _set_dotted(document: dict[str, Any], assignment: str) -> None:
    if "=" not in assignment:
        raise ValueError(f"override must use dotted.path=value: {assignment!r}")
    dotted, raw = assignment.split("=", 1)
    parts = [part for part in dotted.split(".") if part]
    if not parts:
        raise ValueError("override path is empty")
    cursor: dict[str, Any] = document
    for part in parts[:-1]:
        value = cursor.get(part)
        if not isinstance(value, dict):
            raise ValueError(f"override path does not exist: {dotted}")
        cursor = value
    if parts[-1] not in cursor:
        raise ValueError(f"override path does not exist: {dotted}")
    cursor[parts[-1]] = yaml.safe_load(raw)


def _resolve_recipe(
    *,
    recipe_path: Path | None,
    preset: str,
    body_type: BodyType | None,
    collision_mode: CollisionMode | None,
    simplify_mode: SimplifyMode | None,
    faces: int | None,
    ratio: float | None,
    max_faces: int | None,
    target_error: float | None,
    allow_attribute_loss: bool,
    uv_policy: str | None,
    seed: int | None,
    overrides: list[str],
) -> Recipe:
    if faces is not None and ratio is not None:
        raise ValueError("declare --faces or --ratio, not both")
    recipe = Recipe.load(recipe_path) if recipe_path else Recipe.from_preset(preset)
    data = recipe.model_dump(mode="python")
    if body_type is not None:
        data["settings"]["collision"]["body_type"] = body_type
    if collision_mode is not None:
        data["settings"]["collision"]["mode"] = collision_mode
    geometry = data["settings"]["geometry"]
    target_override = faces is not None or ratio is not None
    if simplify_mode is None:
        if target_override and target_error is not None:
            geometry["simplify_mode"] = SimplifyMode.HYBRID
        elif target_override:
            geometry["simplify_mode"] = SimplifyMode.TARGET
            geometry["max_error_fraction"] = None
        elif target_error is not None:
            geometry["simplify_mode"] = SimplifyMode.ERROR
            geometry["target_faces"] = None
            geometry["target_ratio"] = None
    else:
        geometry["simplify_mode"] = simplify_mode
        if simplify_mode is SimplifyMode.PRESERVE:
            geometry["target_faces"] = None
            geometry["target_ratio"] = None
            geometry["max_error_fraction"] = None
        elif simplify_mode is SimplifyMode.TARGET and target_error is None:
            geometry["max_error_fraction"] = None
        elif simplify_mode is SimplifyMode.ERROR and not target_override:
            geometry["target_faces"] = None
            geometry["target_ratio"] = None
    if faces is not None:
        geometry["target_faces"] = faces
        geometry["target_ratio"] = None
    if ratio is not None:
        geometry["target_ratio"] = ratio
        geometry["target_faces"] = None
    if max_faces is not None:
        geometry["max_faces"] = max_faces
    if target_error is not None:
        geometry["max_error_fraction"] = target_error
    if allow_attribute_loss:
        geometry["allow_attribute_loss"] = True
    if uv_policy is not None:
        geometry["uv_policy"] = uv_policy
    if seed is not None:
        data["seed"] = seed
    for assignment in overrides:
        _set_dotted(data, assignment)
    return Recipe.model_validate(data)


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option("--version", help="Print the application version and exit."),
    ] = False,
) -> None:
    """Asset Cleanup preserves sources and writes only new candidate packages."""

    if version:
        console.print(__version__)
        raise typer.Exit()


@app.command("inspect")
def inspect_command(
    input_path: Annotated[Path, typer.Argument(exists=True, readable=True, dir_okay=False)],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write JSON report.")
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Print the complete JSON report.")
    ] = False,
    seed: Annotated[int, typer.Option(min=0, help="Deterministic analysis seed.")] = 0,
    samples: Annotated[
        int, typer.Option(min=128, help="Maximum analysis samples per mesh.")
    ] = 20_000,
    max_input_bytes: Annotated[int, typer.Option(min=1)] = _DEFAULT_LIMITS.max_input_bytes,
    max_texture_pixels: Annotated[int, typer.Option(min=1)] = (_DEFAULT_LIMITS.max_texture_pixels),
    max_runtime_seconds: Annotated[int, typer.Option(min=1)] = (
        _DEFAULT_LIMITS.max_runtime_seconds
    ),
    max_memory_bytes: Annotated[int, typer.Option(min=1)] = _DEFAULT_LIMITS.max_memory_bytes,
) -> None:
    """Classify and inspect a source without changing it."""

    try:
        _assert_output_not_source_member(
            input_path,
            output,
            max_input_bytes=max_input_bytes,
            max_texture_pixels=max_texture_pixels,
        )
        data = _run_sandbox_report(
            "inspect",
            input_path,
            max_input_bytes=max_input_bytes,
            max_texture_pixels=max_texture_pixels,
            max_runtime_seconds=max_runtime_seconds,
            max_memory_bytes=max_memory_bytes,
            samples=samples,
            seed=seed,
        )
        if output:
            write_json(output, data)
        if json_output:
            console.print_json(data=data)
            return
        table = Table(title=f"Inspection: {input_path.name}")
        table.add_column("Geometry")
        table.add_column("Faces", justify="right")
        table.add_column("Vertices", justify="right")
        table.add_column("Components", justify="right")
        table.add_column("Evidence")
        for geometry in data["geometries"]:
            analysis = geometry["analysis"]
            candidates = analysis.get("primitive_candidates", analysis.get("candidates", []))
            evidence = ", ".join(
                str(item.get("primitive", item.get("kind", item.get("type", "unknown"))))
                for item in candidates[:4]
                if item.get("accepted", True)
            )
            table.add_row(
                geometry["name"],
                str(analysis.get("faces", analysis.get("face_count", "?"))),
                str(analysis.get("vertices", analysis.get("vertex_count", "?"))),
                str(analysis.get("connected_components", analysis.get("component_count", "?"))),
                evidence or "irregular / insufficient support",
            )
        console.print(table)
        for warning in data["warnings"]:
            console.print(f"[yellow]warning:[/yellow] {warning}")
        if output:
            console.print(f"Report: {output}")
    except (AssetCleanupError, OSError, ValueError) as error:
        _exit_error(error)


def _common_recipe(
    recipe_path: Path | None,
    preset: str,
    body_type: BodyType | None,
    collision_mode: CollisionMode | None,
    simplify_mode: SimplifyMode | None,
    faces: int | None,
    ratio: float | None,
    max_faces: int | None,
    target_error: float | None,
    allow_attribute_loss: bool,
    uv_policy: str | None,
    seed: int | None,
    set_values: list[str],
) -> Recipe:
    return _resolve_recipe(
        recipe_path=recipe_path,
        preset=preset,
        body_type=body_type,
        collision_mode=collision_mode,
        simplify_mode=simplify_mode,
        faces=faces,
        ratio=ratio,
        max_faces=max_faces,
        target_error=target_error,
        allow_attribute_loss=allow_attribute_loss,
        uv_policy=uv_policy,
        seed=seed,
        overrides=set_values,
    )


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
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


def _run_sandbox_report(
    operation: str,
    input_path: Path,
    *,
    max_input_bytes: int,
    max_texture_pixels: int,
    max_runtime_seconds: int,
    max_memory_bytes: int,
    samples: int = 20_000,
    seed: int = 0,
) -> dict[str, Any]:
    """Run a bounded inspection/validation child and load its finite report."""

    with tempfile.TemporaryDirectory(prefix=f"asset-cleanup-{operation}-") as temporary:
        report_path = Path(temporary) / "report.json"
        log_path = Path(temporary) / "stderr.log"
        with log_path.open("wb") as error_log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "asset_cleanup.sandbox_child",
                    operation,
                    str(input_path.expanduser().resolve()),
                    str(report_path),
                    "--max-file-bytes",
                    str(max_input_bytes),
                    "--max-texture-pixels",
                    str(max_texture_pixels),
                    "--max-runtime-seconds",
                    str(max_runtime_seconds),
                    "--max-memory-bytes",
                    str(max_memory_bytes),
                    "--max-samples",
                    str(samples),
                    "--seed",
                    str(seed),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=error_log,
                close_fds=True,
                start_new_session=os.name == "posix",
            )
            try:
                process.wait(timeout=max_runtime_seconds)
            except subprocess.TimeoutExpired as exc:
                _terminate_process(process)
                raise ProcessingError(f"{operation} exceeded max_runtime_seconds") from exc
        if process.returncode != 0:
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-4_000:]
            raise ProcessingError(
                f"isolated {operation} exited with code {process.returncode}: {detail}".rstrip()
            )
        if not report_path.is_file() or report_path.stat().st_size > 32 * 1024 * 1024:
            raise ProcessingError(f"isolated {operation} produced no bounded report")
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProcessingError(f"isolated {operation} produced invalid JSON") from exc
        if not isinstance(report, dict):
            raise ProcessingError(f"isolated {operation} produced a non-object report")
        return report


def _run_isolated(input_path: Path, recipe: Recipe, output: Path) -> RunResult:
    """Run untrusted parsing in a resource-limited child for the public CLI."""

    with tempfile.TemporaryDirectory(prefix="asset-cleanup-run-") as temporary:
        recipe_path = Path(temporary) / "recipe.json"
        log_path = Path(temporary) / "stderr.log"
        recipe.save(recipe_path)
        with log_path.open("wb") as error_log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "asset_cleanup.worker_child",
                    str(input_path.expanduser().resolve()),
                    str(recipe_path),
                    str(output.expanduser().resolve()),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=error_log,
                close_fds=True,
                start_new_session=os.name == "posix",
            )
            try:
                process.wait(timeout=recipe.settings.limits.max_runtime_seconds)
            except subprocess.TimeoutExpired as exc:
                _terminate_process(process)
                raise ProcessingError("run exceeded max_runtime_seconds") from exc
        if process.returncode != 0:
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-4_000:]
            raise ProcessingError(
                f"isolated run exited with code {process.returncode}: {detail}".rstrip()
            )
    manifest_path = output.expanduser().resolve() / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProcessingError("isolated run did not produce a valid manifest") from exc
    status_value = manifest.get("status")
    artifacts = manifest.get("artifacts")
    warnings = manifest.get("warnings")
    if status_value not in {"accepted", "candidate"} or not isinstance(artifacts, list):
        raise ProcessingError("isolated run produced an invalid terminal manifest")
    return RunResult(
        output_directory=str(output.expanduser().resolve()),
        manifest_path=str(manifest_path),
        status=str(status_value),
        artifacts=artifacts,
        warnings=warnings if isinstance(warnings, list) else [],
    )


@app.command("plan")
def plan_command(
    input_path: Annotated[Path, typer.Argument(exists=True, readable=True, dir_okay=False)],
    recipe_path: Annotated[
        Path | None, typer.Option("--recipe", exists=True, dir_okay=False)
    ] = None,
    preset: Annotated[
        str, typer.Option(help=f"Built-in preset: {', '.join(preset_names())}.")
    ] = "balanced",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    body_type: Annotated[BodyType | None, typer.Option()] = None,
    collision_mode: Annotated[CollisionMode | None, typer.Option()] = None,
    simplify_mode: Annotated[SimplifyMode | None, typer.Option()] = None,
    faces: Annotated[int | None, typer.Option(min=4)] = None,
    ratio: Annotated[float | None, typer.Option(min=0.0, max=1.0)] = None,
    max_faces: Annotated[int | None, typer.Option(min=4)] = None,
    target_error: Annotated[float | None, typer.Option(min=0.0, max=1.0)] = None,
    allow_attribute_loss: Annotated[bool, typer.Option()] = False,
    uv_policy: Annotated[str | None, typer.Option(help="preserve, new, or rebake")] = None,
    seed: Annotated[int | None, typer.Option(min=0)] = None,
    set_values: Annotated[
        list[str] | None, typer.Option("--set", help="Typed dotted.path=value override.")
    ] = None,
) -> None:
    """Resolve stages, source classification, capabilities, and blockers."""

    try:
        recipe = _common_recipe(
            recipe_path,
            preset,
            body_type,
            collision_mode,
            simplify_mode,
            faces,
            ratio,
            max_faces,
            target_error,
            allow_attribute_loss,
            uv_policy,
            seed,
            set_values or [],
        )
        _assert_output_not_source_member(
            input_path,
            output,
            max_input_bytes=recipe.settings.limits.max_input_bytes,
            max_texture_pixels=recipe.settings.limits.max_texture_pixels,
        )
        plan = plan_run(input_path, recipe)
        if output:
            write_json(output, plan)
        console.print_json(data=plan)
        if not plan["runnable"]:
            raise typer.Exit(code=3)
    except (AssetCleanupError, OSError, ValueError, ValidationError) as error:
        _exit_error(error)


@app.command("run")
def run_command(
    input_path: Annotated[Path, typer.Argument(exists=True, readable=True, dir_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o", help="New candidate directory.")],
    recipe_path: Annotated[
        Path | None, typer.Option("--recipe", exists=True, dir_okay=False)
    ] = None,
    preset: Annotated[
        str, typer.Option(help=f"Built-in preset: {', '.join(preset_names())}.")
    ] = "balanced",
    body_type: Annotated[BodyType | None, typer.Option()] = None,
    collision_mode: Annotated[CollisionMode | None, typer.Option()] = None,
    simplify_mode: Annotated[SimplifyMode | None, typer.Option()] = None,
    faces: Annotated[int | None, typer.Option(min=4)] = None,
    ratio: Annotated[float | None, typer.Option(min=0.0, max=1.0)] = None,
    max_faces: Annotated[int | None, typer.Option(min=4)] = None,
    target_error: Annotated[float | None, typer.Option(min=0.0, max=1.0)] = None,
    allow_attribute_loss: Annotated[
        bool, typer.Option(help="Explicitly allow UV/color loss in QEM output.")
    ] = False,
    uv_policy: Annotated[str | None, typer.Option(help="preserve, new, or rebake")] = None,
    seed: Annotated[int | None, typer.Option(min=0)] = None,
    set_values: Annotated[
        list[str] | None, typer.Option("--set", help="Typed dotted.path=value override.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option(help="Print the resolved plan without writing a candidate.")
    ] = False,
    print_recipe: Annotated[
        bool, typer.Option("--print-recipe", help="Print fully expanded recipe.")
    ] = False,
) -> None:
    """Build a new immutable candidate package from a typed recipe."""

    try:
        recipe = _common_recipe(
            recipe_path,
            preset,
            body_type,
            collision_mode,
            simplify_mode,
            faces,
            ratio,
            max_faces,
            target_error,
            allow_attribute_loss,
            uv_policy,
            seed,
            set_values or [],
        )
        if print_recipe:
            console.print(recipe.to_yaml())
        if dry_run:
            console.print_json(data=plan_run(input_path, recipe))
            return
        result = _run_isolated(input_path, recipe, output)
        console.print_json(data=result.to_dict())
    except (AssetCleanupError, OSError, ValueError, ValidationError) as error:
        _exit_error(error)


@app.command("validate")
def validate_command(
    input_path: Annotated[Path, typer.Argument(exists=True, readable=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    max_input_bytes: Annotated[int, typer.Option(min=1)] = _DEFAULT_LIMITS.max_input_bytes,
    max_texture_pixels: Annotated[int, typer.Option(min=1)] = (_DEFAULT_LIMITS.max_texture_pixels),
    max_runtime_seconds: Annotated[int, typer.Option(min=1)] = (
        _DEFAULT_LIMITS.max_runtime_seconds
    ),
    max_memory_bytes: Annotated[int, typer.Option(min=1)] = _DEFAULT_LIMITS.max_memory_bytes,
) -> None:
    """Run bounded parser and structural topology validation."""

    try:
        _assert_output_not_source_member(
            input_path,
            output,
            max_input_bytes=max_input_bytes,
            max_texture_pixels=max_texture_pixels,
        )
        data = _run_sandbox_report(
            "validate",
            input_path,
            max_input_bytes=max_input_bytes,
            max_texture_pixels=max_texture_pixels,
            max_runtime_seconds=max_runtime_seconds,
            max_memory_bytes=max_memory_bytes,
        )
        if output:
            write_json(output, data)
        console.print_json(data=data)
        if not bool(data.get("passed")):
            raise typer.Exit(code=4)
    except (AssetCleanupError, OSError, ValueError) as error:
        _exit_error(error)


@app.command("capabilities")
def capabilities_command(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Show installed Python/native/CLI processing adapters and versions."""

    capabilities = [item.to_dict() for item in detect_capabilities()]
    if json_output:
        console.print_json(data={"capabilities": capabilities})
        return
    table = Table(title="Asset Cleanup capabilities")
    table.add_column("Capability")
    table.add_column("Provider")
    table.add_column("Available")
    table.add_column("Version / note")
    for item in capabilities:
        detail = str(item["version"] or item["note"] or "")
        table.add_row(
            str(item["name"]),
            str(item["provider"]),
            "yes" if item["available"] else "no",
            detail,
        )
    console.print(table)


@app.command("doctor")
def doctor_command() -> None:
    """Check the canonical core and local writable workspace behavior."""

    capabilities = {item.name: item for item in detect_capabilities()}
    required = ("numpy", "scipy", "trimesh", "fast-simplification")
    problems = [name for name in required if not capabilities[name].available]
    with tempfile.TemporaryDirectory(prefix="asset-cleanup-doctor-") as temporary:
        marker = Path(temporary) / "write-test.json"
        write_json(marker, {"ok": True})
        writable = marker.is_file()
    data = {
        "version": __version__,
        "core_available": not problems,
        "missing_core": problems,
        "temporary_workspace_writable": writable,
    }
    console.print_json(data=data)
    if problems or not writable:
        raise typer.Exit(code=5)


@app.command("serve")
def serve_command(
    data_root: Annotated[Path, typer.Option(help="Persistent service data directory.")] = Path(
        ".asset-cleanup"
    ),
    host: Annotated[str, typer.Option(help="HTTP bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65_535)] = 8080,
    embedded_worker: Annotated[bool, typer.Option("--embedded-worker/--no-embedded-worker")] = True,
    web_root: Annotated[
        Path | None, typer.Option(help="Built frontend directory; API-only when omitted.")
    ] = None,
    allowed_host: Annotated[
        list[str] | None,
        typer.Option("--allowed-host", help="Accepted HTTP Host value; repeatable."),
    ] = None,
) -> None:
    """Serve the local API and, when supplied, the compiled browser UI."""

    try:
        import uvicorn
        from fastapi.staticfiles import StaticFiles

        from asset_cleanup.web import WebConfig, create_app

        hosts = tuple(allowed_host or ["127.0.0.1", "localhost"])
        web_app = create_app(
            WebConfig(
                data_root=data_root,
                embedded_worker=embedded_worker,
                allowed_hosts=hosts,
            )
        )
        if web_root is not None:
            resolved_web = web_root.expanduser().resolve()
            if not (resolved_web / "index.html").is_file():
                raise ValueError(f"built frontend is unavailable: {resolved_web}")
            web_app.mount("/", StaticFiles(directory=resolved_web, html=True), name="web")
        uvicorn.run(
            web_app,
            host=host,
            port=port,
            proxy_headers=False,
            server_header=False,
        )
    except (ImportError, OSError, ValueError) as error:
        _exit_error(error)


@app.command("worker")
def worker_command(
    data_root: Annotated[Path, typer.Option(help="Persistent service data directory.")] = Path(
        ".asset-cleanup"
    ),
    once: Annotated[bool, typer.Option(help="Drain queued jobs and exit.")] = False,
) -> None:
    """Run the single persistent processing worker."""

    try:
        from asset_cleanup.web import WebConfig, WorkerService

        service = WorkerService(WebConfig(data_root=data_root))
        if once:
            console.print_json(data={"processed": service.run_until_idle()})
            return
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
    except (ImportError, OSError, ValueError) as error:
        _exit_error(error)


@app.command("compare")
def compare_command(
    manifests: Annotated[list[Path], typer.Argument(exists=True, readable=True, dir_okay=False)],
) -> None:
    """Compare run manifests without choosing a winner from face count alone."""

    table = Table(title="Candidate comparison")
    table.add_column("Manifest")
    table.add_column("Status")
    table.add_column("Recipe")
    table.add_column("Artifacts", justify="right")
    table.add_column("Warnings", justify="right")
    for path in manifests:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            _exit_error(error)
        table.add_row(
            str(path),
            str(data.get("status", "unknown")),
            str(data.get("recipe_hash", ""))[:12],
            str(len(data.get("artifacts", []))),
            str(len(data.get("warnings", []))),
        )
    console.print(table)


@app.command("package")
def package_command(
    run_directory: Annotated[Path, typer.Argument(exists=True, readable=True, file_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o", help="Destination ZIP archive.")],
) -> None:
    """Archive only artifacts registered by a completed run manifest."""

    try:
        resolved_run = run_directory.resolve()
        resolved_output = output.expanduser().resolve()
        if resolved_output == resolved_run or resolved_run in resolved_output.parents:
            raise ValueError("package output must be outside the immutable run directory")
        output = resolved_output
        manifest_path = run_directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") not in {"candidate", "accepted"}:
            raise ValueError("only completed candidate or accepted runs can be packaged")
        members = {"manifest.json", "artifact_manifest.json", "resolved_recipe.yaml"}
        members.update(str(item["path"]) for item in manifest.get("artifacts", []))
        registered = {str(item["path"]): item for item in manifest.get("artifacts", [])}
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".zip", dir=output.parent
        )
        os.close(descriptor)
        Path(temporary_name).unlink(missing_ok=True)
        try:
            with zipfile.ZipFile(
                temporary_name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as archive:
                for relative in sorted(members):
                    normalized = PurePosixPath(relative)
                    if (
                        "\\" in relative
                        or normalized.is_absolute()
                        or ".." in normalized.parts
                        or normalized.as_posix() != relative
                    ):
                        raise ValueError(f"invalid artifact path: {relative!r}")
                    path = confined_path(run_directory, relative)
                    if not path.is_file():
                        raise ValueError(f"registered artifact is missing: {relative}")
                    record = registered.get(relative)
                    if record is not None and (
                        path.stat().st_size != int(record["bytes"])
                        or sha256_file(path) != record["sha256"]
                    ):
                        raise ValueError(f"artifact integrity check failed: {relative}")
                    archive.write(path, arcname=relative)
            Path(temporary_name).replace(output)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise
        console.print_json(
            data={
                "archive": str(output),
                "members": len(members),
                "recipe_hash": manifest.get("recipe_hash"),
            }
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        _exit_error(error)


@recipe_app.command("init")
def recipe_init_command(
    output: Annotated[Path, typer.Argument()],
    preset: Annotated[str, typer.Option(help=f"{', '.join(preset_names())}")] = "balanced",
    name: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Write a fully expanded built-in recipe to YAML or JSON."""

    try:
        recipe = Recipe.from_preset(preset, **({"name": name} if name else {}))
        recipe.save(output)
        console.print(f"Wrote {output} ({recipe.canonical_hash()})")
    except (OSError, ValueError, ValidationError) as error:
        _exit_error(error)


@recipe_app.command("validate")
def recipe_validate_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Validate a recipe and print its canonical identity."""

    try:
        recipe = Recipe.load(path)
        console.print_json(
            data={"valid": True, "name": recipe.name, "canonical_hash": recipe.canonical_hash()}
        )
    except (OSError, ValueError, ValidationError) as error:
        _exit_error(error)


@recipe_app.command("explain")
def recipe_explain_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Print a recipe's complete resolved settings and safety implications."""

    try:
        recipe = Recipe.load(path)
        implications: list[str] = []
        geometry = recipe.settings.geometry
        if geometry.simplify_mode is not SimplifyMode.PRESERVE:
            implications.append("visual topology may change")
        if geometry.allow_attribute_loss:
            implications.append("UV/color attribute loss is explicitly allowed")
        if geometry.reconstruct_curved_primitives:
            implications.append("experimental curved reconstruction requested")
        if recipe.settings.collision.body_type is BodyType.UNSPECIFIED:
            implications.append("collision body compatibility remains unconfirmed")
        console.print_json(
            data={
                "canonical_hash": recipe.canonical_hash(),
                "implications": implications,
                "recipe": recipe.canonical_dict(),
            }
        )
    except (OSError, ValueError, ValidationError) as error:
        _exit_error(error)


@recipe_app.command("schema")
def recipe_schema_command(
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """Print or save the authoritative JSON Schema for recipes."""

    schema = Recipe.model_json_schema()
    if output:
        write_json(output, schema)
    console.print_json(data=schema)


def main() -> None:
    """Console-script entry point."""

    app()


if __name__ == "__main__":  # pragma: no cover
    main()
