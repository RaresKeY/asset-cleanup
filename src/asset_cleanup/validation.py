"""Structural source/candidate validation gates."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

from asset_cleanup.source import LoadLimits, iter_meshes, load_scene, probe_source


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One stable validation finding."""

    severity: str
    code: str
    message: str
    geometry: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Structural validation outcome."""

    schema: str
    passed: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_source(path: Path, *, limits: LoadLimits | None = None) -> ValidationReport:
    """Validate a source using bounded parsing and topology checks."""

    limits = limits or LoadLimits()
    probe = probe_source(path, limits)
    issues: list[ValidationIssue] = []
    if not probe.supported:
        issues.append(
            ValidationIssue("error", "source.unsupported", probe.reason or "unsupported source")
        )
        return ValidationReport(
            schema="asset-cleanup/validation-v1alpha1",
            passed=False,
            issues=issues,
            facts={"probe": probe.to_dict()},
        )

    scene = load_scene(path, limits)
    vertices = 0
    faces = 0
    for name, mesh in iter_meshes(scene):
        vertices += len(mesh.vertices)
        faces += len(mesh.faces)
        if not np.isfinite(mesh.vertices).all():
            issues.append(
                ValidationIssue("error", "geometry.non_finite", "non-finite vertex position", name)
            )
        if len(mesh.faces) == 0:
            issues.append(
                ValidationIssue("error", "geometry.empty", "mesh has no triangle faces", name)
            )
        if not bool(mesh.is_winding_consistent):
            issues.append(
                ValidationIssue("warning", "topology.winding", "winding is inconsistent", name)
            )
        if not bool(mesh.is_watertight):
            issues.append(
                ValidationIssue("warning", "topology.open", "mesh is not watertight", name)
            )
        try:
            degenerate = int(len(mesh.faces) - int(np.count_nonzero(mesh.nondegenerate_faces())))
        except BaseException:
            degenerate = 0
        if degenerate:
            issues.append(
                ValidationIssue(
                    "warning",
                    "topology.degenerate",
                    f"mesh contains {degenerate} degenerate faces",
                    name,
                )
            )
    passed = not any(issue.severity == "error" for issue in issues)
    return ValidationReport(
        schema="asset-cleanup/validation-v1alpha1",
        passed=passed,
        issues=issues,
        facts={
            "probe": probe.to_dict(),
            "nodes": len(scene.graph.nodes),
            "geometries": len(scene.geometry),
            "vertices": vertices,
            "faces": faces,
        },
    )


def _scene_facts(path: Path, limits: LoadLimits) -> dict[str, Any]:
    scene = load_scene(path, limits)
    instances: list[dict[str, Any]] = []
    for node_name in sorted(scene.graph.nodes_geometry):
        transform, geometry_name = scene.graph.get(node_name)
        instances.append(
            {
                "node": str(node_name),
                "geometry": str(geometry_name),
                "transform": np.asarray(transform, dtype=float).round(12).tolist(),
            }
        )
    return {
        "nodes": sorted(str(item) for item in scene.graph.nodes),
        "geometry_names": sorted(str(item) for item in scene.geometry),
        "instances": instances,
    }


def compare_scene_inventory(
    source_path: Path,
    candidate_path: Path,
    *,
    limits: LoadLimits | None = None,
) -> dict[str, Any]:
    """Compare stable node, geometry, transform, and instance inventory."""

    limits = limits or LoadLimits()
    source = _scene_facts(source_path, limits)
    candidate = _scene_facts(candidate_path, limits)
    differences = [
        key for key in ("nodes", "geometry_names", "instances") if source[key] != candidate[key]
    ]
    return {
        "method": "scene-inventory-exact-v1",
        "ran": True,
        "passed": not differences,
        "differences": differences,
        "source": source,
        "candidate": candidate,
    }


def _appearance_facts(path: Path, limits: LoadLimits) -> list[dict[str, Any]]:
    scene = load_scene(path, limits)
    facts: list[dict[str, Any]] = []
    for name, mesh in iter_meshes(scene):
        visual = mesh.visual
        kind = str(getattr(visual, "kind", "none") or "none")
        material = getattr(visual, "material", None)
        uv = getattr(visual, "uv", None)
        colors = getattr(visual, "vertex_colors", None)
        facts.append(
            {
                "geometry": name,
                "visual_kind": kind,
                "material_name": getattr(material, "name", None),
                "has_uv": bool(kind == "texture" and uv is not None),
                "has_vertex_colors": bool(kind in {"vertex", "face"} and colors is not None),
            }
        )
    return facts


def compare_appearance_inventory(
    source_path: Path,
    candidate_path: Path,
    *,
    limits: LoadLimits | None = None,
) -> dict[str, Any]:
    """Compare material/attribute inventory without claiming rendered equivalence."""

    limits = limits or LoadLimits()
    source = _appearance_facts(source_path, limits)
    candidate = _appearance_facts(candidate_path, limits)
    return {
        "method": "appearance-inventory-v1",
        "ran": True,
        "passed": source == candidate,
        "source": source,
        "candidate": candidate,
        "limitation": "does not replace silhouette or neutral-PBR render comparison",
    }


GLTF_VALIDATOR_VERSION = "2.0.0-dev.3.10"
GLTF_VALIDATOR_MAX_ISSUES = 1_000
GLTF_VALIDATOR_MAX_REPORT_BYTES = 16 * 1024 * 1024
GLTF_VALIDATOR_MAX_STDERR_BYTES = 64 * 1024
_GLTF_VALIDATOR_TERMINATE_GRACE_SECONDS = 1.0
_GLTF_VALIDATOR_PIPE_JOIN_SECONDS = 0.25


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_validator(process: subprocess.Popen[bytes]) -> None:
    """Terminate the validator and, on POSIX, every process in its group."""

    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.poll()
            return
        except OSError:
            if process.poll() is None:
                process.terminate()

        deadline = time.monotonic() + _GLTF_VALIDATOR_TERMINATE_GRACE_SECONDS
        while _process_group_exists(process.pid) and time.monotonic() < deadline:
            process.poll()
            time.sleep(0.02)
        if _process_group_exists(process.pid):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=_GLTF_VALIDATOR_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        return

    if process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=_GLTF_VALIDATOR_TERMINATE_GRACE_SECONDS)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=_GLTF_VALIDATOR_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _drain_validator_pipe(
    stream: BinaryIO,
    sink: bytearray,
    total: list[int],
    *,
    limit: int,
    keep_tail: bool,
    overflow: threading.Event | None = None,
) -> None:
    """Drain one pipe without retaining more than its configured evidence bound."""

    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            total[0] += len(chunk)
            if keep_tail:
                sink.extend(chunk)
                if len(sink) > limit:
                    del sink[:-limit]
                continue
            remaining = max(0, limit - len(sink))
            sink.extend(chunk[:remaining])
            if len(chunk) > remaining and overflow is not None:
                overflow.set()
    except (OSError, ValueError):
        return
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _issue_count(report: dict[str, Any], name: str) -> int | None:
    issues = report.get("issues")
    value = issues.get(name) if isinstance(issues, dict) else None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _invalid_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def run_gltf_validator(
    path: Path,
    executable: str,
    *,
    timeout_seconds: float = 120,
    discovered_version: str | None = None,
    max_report_bytes: int = GLTF_VALIDATOR_MAX_REPORT_BYTES,
    max_stderr_bytes: int = GLTF_VALIDATOR_MAX_STDERR_BYTES,
) -> dict[str, Any]:
    """Run the pinned native Khronos validator with bounded evidence."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_report_bytes <= 0:
        raise ValueError("max_report_bytes must be positive")
    if max_stderr_bytes <= 0:
        raise ValueError("max_stderr_bytes must be positive")

    path = path.expanduser().resolve()
    started = time.monotonic()
    provider: dict[str, Any] = {
        "name": "Khronos glTF Validator",
        "kind": "native-executable",
        "executable": Path(executable).name,
        "expected_version": GLTF_VALIDATOR_VERSION,
        "discovered_version": discovered_version,
        "version": None,
        "version_match": False,
    }
    limits = {
        "timeout_seconds": timeout_seconds,
        "max_issues": GLTF_VALIDATOR_MAX_ISSUES,
        "max_report_bytes": max_report_bytes,
        "max_stderr_bytes": max_stderr_bytes,
        "termination_grace_seconds": _GLTF_VALIDATOR_TERMINATE_GRACE_SECONDS,
    }
    base_result: dict[str, Any] = {
        "schema": "asset-cleanup/gltf-validator-v1alpha1",
        "ran": False,
        "passed": False,
        "returncode": None,
        "timed_out": False,
        "report_overflow": False,
        "drain_incomplete": False,
        "report": None,
        "report_bytes": 0,
        "stderr": "",
        "stderr_bytes": 0,
        "stderr_truncated": False,
        "issue_counts": None,
        "warning_count": None,
        "provider": provider,
        "limits": limits,
        "argv_contract": [
            "<provider-executable>",
            "--stdout",
            "--validate-resources",
            "--no-write-timestamp",
            "--no-absolute-path",
            "--no-messages",
            "--config",
            "<private-config>",
            "<relative-candidate>",
        ],
    }

    with tempfile.TemporaryDirectory(prefix="asset-cleanup-validator-") as temporary:
        config_path = Path(temporary) / "validator-config.yaml"
        config_path.write_text(
            f"max-issues: {GLTF_VALIDATOR_MAX_ISSUES}\n",
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        environment = {
            "PATH": os.defpath,
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
        }
        if os.name == "nt":
            for name in ("SYSTEMROOT", "WINDIR"):
                if value := os.environ.get(name):
                    environment[name] = value

        try:
            process = subprocess.Popen(
                [
                    executable,
                    "--stdout",
                    "--validate-resources",
                    "--no-write-timestamp",
                    "--no-absolute-path",
                    "--no-messages",
                    "--config",
                    str(config_path),
                    f"./{path.name}",
                ],
                cwd=path.parent,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            base_result.update(
                {
                    "reason": f"validator execution failed: {type(exc).__name__}: {exc}",
                    "elapsed_seconds": round(time.monotonic() - started, 6),
                }
            )
            return base_result

        stdout_stream = process.stdout
        stderr_stream = process.stderr
        assert stdout_stream is not None
        assert stderr_stream is not None
        stdout = bytearray()
        stderr_tail = bytearray()
        stdout_total = [0]
        stderr_total = [0]
        stdout_overflow = threading.Event()
        stdout_thread = threading.Thread(
            target=_drain_validator_pipe,
            args=(stdout_stream, stdout, stdout_total),
            kwargs={
                "limit": max_report_bytes,
                "keep_tail": False,
                "overflow": stdout_overflow,
            },
            name="gltf-validator-stdout",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_drain_validator_pipe,
            args=(stderr_stream, stderr_tail, stderr_total),
            kwargs={"limit": max_stderr_bytes, "keep_tail": True},
            name="gltf-validator-stderr",
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        deadline = started + timeout_seconds
        timed_out = False
        report_overflow = False
        while process.poll() is None:
            if stdout_overflow.is_set():
                report_overflow = True
                _stop_validator(process)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _stop_validator(process)
                break
            try:
                process.wait(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                pass

        stdout_thread.join(timeout=_GLTF_VALIDATOR_PIPE_JOIN_SECONDS)
        stderr_thread.join(timeout=_GLTF_VALIDATOR_PIPE_JOIN_SECONDS)
        drain_incomplete = stdout_thread.is_alive() or stderr_thread.is_alive()
        if drain_incomplete:
            _stop_validator(process)
            stdout_stream.close()
            stderr_stream.close()
            stdout_thread.join(timeout=_GLTF_VALIDATOR_TERMINATE_GRACE_SECONDS)
            stderr_thread.join(timeout=_GLTF_VALIDATOR_TERMINATE_GRACE_SECONDS)
        report_overflow = report_overflow or stdout_overflow.is_set()

    report: dict[str, Any] | None = None
    parse_reason: str | None = None
    if not timed_out and not report_overflow and not drain_incomplete:
        try:
            parsed: Any = json.loads(
                stdout.decode("utf-8"),
                parse_constant=_invalid_json_constant,
            )
            if isinstance(parsed, dict):
                report = parsed
            else:
                parse_reason = "validator report is not a JSON object"
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
            parse_reason = "validator did not emit strict UTF-8 JSON"

    names = ("numErrors", "numWarnings", "numInfos", "numHints")
    counts = (
        {name: _issue_count(report, name) for name in names}
        if report is not None
        else None
    )
    issue_counts = (
        {
            "errors": counts["numErrors"],
            "warnings": counts["numWarnings"],
            "infos": counts["numInfos"],
            "hints": counts["numHints"],
        }
        if counts is not None and all(value is not None for value in counts.values())
        else None
    )
    report_version_value = report.get("validatorVersion") if report is not None else None
    report_version = (
        report_version_value
        if isinstance(report_version_value, str) and report_version_value.strip()
        else None
    )
    version_match = report_version == GLTF_VALIDATOR_VERSION
    provider.update({"version": report_version, "version_match": version_match})

    error_count = issue_counts["errors"] if issue_counts is not None else None
    warning_count = issue_counts["warnings"] if issue_counts is not None else None
    returncode = process.returncode
    passed = bool(
        not timed_out
        and not report_overflow
        and not drain_incomplete
        and parse_reason is None
        and returncode == 0
        and version_match
        and error_count == 0
    )
    if timed_out:
        reason = "validator exceeded its wall-time limit"
    elif report_overflow:
        reason = f"validator report exceeded its {max_report_bytes} byte limit"
    elif drain_incomplete:
        reason = "validator output streams did not close"
    elif parse_reason is not None:
        reason = parse_reason
    elif report_version is None:
        reason = "validator report did not identify its version"
    elif not version_match:
        reason = (
            "validator version mismatch: "
            f"expected {GLTF_VALIDATOR_VERSION}, received {report_version}"
        )
    elif issue_counts is None:
        reason = "validator report has an invalid issue summary"
    elif returncode != 0 or error_count:
        reason = "validator reported glTF errors"
    else:
        reason = None

    base_result.update(
        {
            "ran": True,
            "passed": passed,
            "reason": reason,
            "returncode": returncode,
            "timed_out": timed_out,
            "report_overflow": report_overflow,
            "drain_incomplete": drain_incomplete,
            "report": report,
            "report_bytes": stdout_total[0],
            "stderr": stderr_tail.decode("utf-8", errors="replace"),
            "stderr_bytes": stderr_total[0],
            "stderr_truncated": stderr_total[0] > len(stderr_tail),
            "issue_counts": issue_counts,
            "warning_count": warning_count,
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }
    )
    return base_result
