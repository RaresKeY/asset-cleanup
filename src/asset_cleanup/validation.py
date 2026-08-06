"""Structural source/candidate validation gates."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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


def run_gltf_validator(
    path: Path,
    executable: str,
    *,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Run Khronos glTF Validator with fixed argv and retain its JSON report."""

    try:
        completed = subprocess.run(
            [
                executable,
                "--stdout",
                "--no-write-timestamp",
                "--no-absolute-path",
                str(path),
            ],
            cwd=path.parent,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ran": False,
            "passed": False,
            "reason": f"validator execution failed: {type(exc).__name__}: {exc}",
        }
    try:
        report: Any = json.loads(completed.stdout)
    except json.JSONDecodeError:
        report = None
    return {
        "ran": True,
        "passed": completed.returncode == 0 and isinstance(report, dict),
        "returncode": completed.returncode,
        "report": report,
        "stderr": completed.stderr[-8_192:],
    }
