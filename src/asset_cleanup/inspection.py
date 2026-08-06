"""Scene-preserving source inspection and serializable reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from asset_cleanup.geometry import analyze_mesh
from asset_cleanup.source import LoadLimits, iter_meshes, load_scene, probe_source
from asset_cleanup.util import sha256_file


@dataclass(frozen=True, slots=True)
class GeometryInventory:
    """One local-space mesh definition and its analysis."""

    name: str
    visual_kind: str
    material_name: str | None
    has_uv: bool
    has_vertex_colors: bool
    analysis: dict[str, Any]


@dataclass(frozen=True, slots=True)
class InspectionReport:
    """Stable, JSON-compatible inspection result."""

    schema: str
    source: dict[str, Any]
    scene: dict[str, Any]
    geometries: list[GeometryInventory] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _visual_inventory(mesh: Any) -> tuple[str, str | None, bool, bool]:
    visual = mesh.visual
    kind = str(getattr(visual, "kind", "none") or "none")
    material = getattr(visual, "material", None)
    material_name = getattr(material, "name", None) if material is not None else None
    uv = getattr(visual, "uv", None)
    colors = getattr(visual, "vertex_colors", None)
    has_uv = kind == "texture" and uv is not None and len(uv) == len(mesh.vertices)
    has_colors = kind in {"vertex", "face"} and colors is not None
    return kind, material_name, bool(has_uv), bool(has_colors)


def _scene_inventory(scene: Any) -> dict[str, Any]:
    node_names = sorted(str(item) for item in scene.graph.nodes)
    geometry_nodes = sorted(str(item) for item in scene.graph.nodes_geometry)
    transforms: list[dict[str, Any]] = []
    for node_name in geometry_nodes:
        transform, geometry_name = scene.graph.get(node_name)
        matrix = np.asarray(transform, dtype=float)
        transforms.append(
            {
                "node": node_name,
                "geometry": str(geometry_name),
                "determinant": float(np.linalg.det(matrix[:3, :3])),
                "matrix": matrix.round(12).tolist(),
            }
        )
    return {
        "nodes": len(node_names),
        "node_names": node_names,
        "geometry_definitions": len(scene.geometry),
        "geometry_instances": len(geometry_nodes),
        "instances": transforms,
        "metadata_keys": sorted(str(item) for item in scene.metadata),
    }


def inspect_source(
    path: Path,
    *,
    limits: LoadLimits | None = None,
    seed: int = 0,
    plane_angle_deg: float = 7.5,
    curved_normal_angle_deg: float = 12.0,
    max_samples: int = 20_000,
    min_support_area: float = 0.005,
    detect_shapes: bool = True,
    enabled_primitives: frozenset[str] | None = None,
    min_support_samples: int = 1,
    cylinder_min_axial_bins: int = 2,
    cylinder_min_angular_degrees: float = 0.0,
) -> InspectionReport:
    """Classify, load, and inspect a source without changing it."""

    limits = limits or LoadLimits()
    resolved = path.expanduser().resolve()
    probe = probe_source(resolved, limits)
    source = {
        **probe.to_dict(),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }
    if not probe.supported:
        return InspectionReport(
            schema="asset-cleanup/inspection-v1alpha1",
            source=source,
            scene={"loaded": False},
            warnings=[probe.reason or "source requires an unavailable adapter"],
        )

    scene = load_scene(resolved, limits)
    geometries: list[GeometryInventory] = []
    warnings: list[str] = []
    for name, mesh in iter_meshes(scene):
        visual_kind, material_name, has_uv, has_colors = _visual_inventory(mesh)
        analysis = analyze_mesh(
            mesh,
            seed=seed,
            plane_angle_deg=plane_angle_deg,
            curved_normal_angle_deg=curved_normal_angle_deg,
            max_samples=max_samples,
            min_support_area=min_support_area,
        )
        analysis_dict = analysis.to_dict()
        if detect_shapes:
            allowed = enabled_primitives or frozenset(
                {"plane", "box", "sphere", "cylinder", "capsule", "irregular"}
            )
            sample_count = min(max_samples, max(512, len(mesh.faces) * 8))
            filtered: list[dict[str, Any]] = []
            for candidate in analysis_dict["primitive_candidates"]:
                primitive = str(candidate["primitive"])
                if primitive not in allowed:
                    continue
                metrics = dict(candidate.get("metrics", {}))
                support_samples = int(
                    round(float(candidate.get("support_fraction", 0.0)) * sample_count)
                )
                metrics["estimated_support_samples"] = support_samples
                candidate["metrics"] = metrics
                reasons = list(candidate.get("rejection_reasons", []))
                if primitive != "irregular" and support_samples < min_support_samples:
                    reasons.append("support sample count below configured minimum")
                if primitive == "cylinder":
                    axial_bins = len(metrics.get("slice_radii", []))
                    angular_degrees = float(metrics.get("angular_coverage", 0.0)) * 360.0
                    metrics["supported_axial_bins"] = axial_bins
                    metrics["angular_coverage_degrees"] = angular_degrees
                    if axial_bins < cylinder_min_axial_bins:
                        reasons.append("supported axial bins below configured minimum")
                    if angular_degrees < cylinder_min_angular_degrees:
                        reasons.append("angular coverage below configured minimum")
                candidate["rejection_reasons"] = sorted(set(reasons))
                candidate["accepted"] = not reasons
                filtered.append(candidate)
            analysis_dict["primitive_candidates"] = filtered
        else:
            analysis_dict["primitive_candidates"] = []
            analysis_dict["planar_regions"] = []
            analysis_dict.setdefault("warnings", []).append("shape detection disabled by recipe")
        geometries.append(
            GeometryInventory(
                name=name,
                visual_kind=visual_kind,
                material_name=material_name,
                has_uv=has_uv,
                has_vertex_colors=has_colors,
                analysis=analysis_dict,
            )
        )
        if has_uv:
            warnings.append(
                f"{name}: topology-changing adapters must preserve attributes "
                "or use UV_NEW plus rebake"
            )
        if not analysis_dict.get("watertight", False):
            warnings.append(
                f"{name}: mesh is not watertight; volume and signed-distance claims are limited"
            )

    return InspectionReport(
        schema="asset-cleanup/inspection-v1alpha1",
        source=source,
        scene={"loaded": True, **_scene_inventory(scene)},
        geometries=geometries,
        warnings=sorted(set(warnings)),
    )
