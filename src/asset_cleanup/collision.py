"""Deterministic, engine-neutral collision candidate generation.

The module deliberately accepts and returns plain ``trimesh``/dataclass values.
It has no dependency on the application's model or geometry layers, which keeps
it usable by the CLI, web workers, and small standalone conversion scripts.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np
import trimesh

GENERATOR_NAME = "asset-cleanup.collision"
GENERATOR_VERSION = "1"
_CONCAVE_UNSAFE_BODIES = {"dynamic", "kinematic", "character", "movable"}
_KNOWN_BODIES = {
    "unspecified",
    "static",
    "dynamic",
    "kinematic",
    "character",
    "movable",
    "area",
    "none",
}
_PRIMITIVES = {"box", "sphere", "capsule", "cylinder"}


def _json_value(value: Any) -> Any:
    """Convert numpy and tuple-rich values to strict JSON-compatible values."""

    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


@dataclass(slots=True)
class CollisionShape:
    """One editable collision helper in source-model coordinates."""

    id: str
    type: str
    transform: list[list[float]]
    dimensions: dict[str, Any] = field(default_factory=dict)
    vertices: list[list[float]] | None = None
    faces: list[list[int]] | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    locked: bool = False
    generated: bool = True
    source_regions: list[str] = field(default_factory=lambda: ["source_mesh:all"])
    generator_settings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            _json_value(
                {
                    "id": self.id,
                    "type": self.type,
                    "transform": self.transform,
                    "dimensions": self.dimensions,
                    "vertices": self.vertices,
                    "faces": self.faces,
                    "metrics": self.metrics,
                    "locked": self.locked,
                    "generated": self.generated,
                    "source_regions": self.source_regions,
                    "generator_settings": self.generator_settings,
                },
            ),
        )


@dataclass(slots=True)
class CollisionResult:
    """Authoritative neutral collision sidecar data."""

    shapes: list[CollisionShape]
    body_type: str
    body_compatibility: str
    seed: int
    settings: dict[str, Any]
    source: dict[str, Any]
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    units: dict[str, Any] = field(
        default_factory=lambda: {
            "name": "source_units",
            "meters_per_unit": None,
            "status": "unconfirmed",
        }
    )
    coordinates: dict[str, Any] = field(
        default_factory=lambda: {
            "space": "source_model",
            "handedness": "right",
            "up_axis": None,
            "transform_convention": "row_major_4x4_local_to_source",
            "status": "partially_unconfirmed",
        }
    )
    generator: dict[str, Any] = field(
        default_factory=lambda: {"name": GENERATOR_NAME, "version": GENERATOR_VERSION}
    )
    schema: str = "asset-cleanup-collision-sidecar/v1"

    def to_dict(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            _json_value(
                {
                    "schema": self.schema,
                    "source": self.source,
                    "units": self.units,
                    "coordinates": self.coordinates,
                    "body": {
                        "type": self.body_type,
                        "compatibility": self.body_compatibility,
                    },
                    "generator": self.generator,
                    "seed": self.seed,
                    "settings": self.settings,
                    "metrics": self.metrics,
                    "warnings": self.warnings,
                    "shapes": [shape.to_dict() for shape in self.shapes],
                },
            ),
        )


def _as_mesh(value: trimesh.Trimesh | trimesh.Scene) -> trimesh.Trimesh:
    if isinstance(value, trimesh.Trimesh):
        mesh = value.copy()
    elif isinstance(value, trimesh.Scene):
        if not value.geometry:
            raise ValueError("collision input scene contains no mesh geometry")
        # ``to_geometry`` bakes every scene-graph transform into a new mesh.
        # Reading ``scene.geometry`` directly would silently discard them.
        converted: Any
        if hasattr(value, "to_geometry"):
            converted = value.to_geometry()
        else:  # trimesh < 4.7 compatibility
            converted = value.dump(concatenate=True)
        if not isinstance(converted, trimesh.Trimesh):
            raise ValueError("collision input scene contains unsupported non-mesh geometry")
        mesh = converted
    else:
        raise TypeError("mesh must be a trimesh.Trimesh or trimesh.Scene")

    if len(mesh.vertices) < 4 or len(mesh.faces) < 4:
        raise ValueError("collision input must contain at least four vertices and faces")
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if not np.isfinite(vertices).all():
        raise ValueError("collision input contains non-finite vertices")
    if faces.ndim != 2 or faces.shape[1] != 3:
        raise ValueError("collision input must be triangulated")
    if faces.min(initial=0) < 0 or faces.max(initial=-1) >= len(vertices):
        raise ValueError("collision input contains invalid face indices")
    if float(np.linalg.norm(np.ptp(vertices, axis=0))) <= np.finfo(float).eps:
        raise ValueError("collision input has zero scale")
    return mesh


def _canonical_axis(axis: np.ndarray) -> np.ndarray:
    axis = np.asarray(axis, dtype=np.float64)
    norm = float(np.linalg.norm(axis))
    if norm <= np.finfo(float).eps:
        return np.array([0.0, 0.0, 1.0])
    axis = axis / norm
    pivot = int(np.argmax(np.abs(axis)))
    if axis[pivot] < 0:
        axis = -axis
    return axis


def _frame_from_z(axis: np.ndarray) -> np.ndarray:
    """Return a stable rotation whose local Z points along *axis*."""

    z_axis = _canonical_axis(axis)
    guide = np.array([1.0, 0.0, 0.0])
    if abs(float(np.dot(z_axis, guide))) > 0.9:
        guide = np.array([0.0, 1.0, 0.0])
    x_axis = guide - z_axis * float(np.dot(guide, z_axis))
    x_axis /= np.linalg.norm(x_axis)
    y_axis = np.cross(z_axis, x_axis)
    rotation = np.column_stack((x_axis, y_axis, z_axis))
    transform = np.eye(4)
    transform[:3, :3] = rotation
    return transform


def _principal_axis(vertices: np.ndarray) -> np.ndarray:
    centered = vertices - vertices.mean(axis=0)
    covariance = centered.T @ centered / max(len(vertices), 1)
    values, vectors = np.linalg.eigh(covariance)
    return _canonical_axis(vectors[:, int(np.argmax(values))])


def _stable_shape_id(kind: str, ordinal: int, body_type: str) -> str:
    body = "body" if body_type == "unspecified" else body_type
    return f"COL_{body}_{kind.upper()}_{ordinal:03d}"


def _base_shape(
    kind: str,
    transform: np.ndarray,
    dimensions: Mapping[str, Any],
    *,
    body_type: str,
    ordinal: int = 0,
    vertices: np.ndarray | None = None,
    faces: np.ndarray | None = None,
    settings: Mapping[str, Any] | None = None,
) -> CollisionShape:
    return CollisionShape(
        id=_stable_shape_id(kind, ordinal, body_type),
        type=kind,
        transform=np.asarray(transform, dtype=float).tolist(),
        dimensions=dict(dimensions),
        vertices=None if vertices is None else np.asarray(vertices, dtype=float).tolist(),
        faces=None if faces is None else np.asarray(faces, dtype=np.int64).tolist(),
        generator_settings=dict(settings or {}),
    )


def _fit_box(mesh: trimesh.Trimesh, body_type: str) -> CollisionShape:
    to_local, extents = trimesh.bounds.oriented_bounds(mesh)  # type: ignore[no-untyped-call]
    transform = np.linalg.inv(np.asarray(to_local, dtype=float))
    extents = np.maximum(np.asarray(extents, dtype=float), np.finfo(float).eps)
    return _base_shape(
        "box",
        transform,
        {"extents": extents.tolist()},
        body_type=body_type,
    )


def _fit_sphere(mesh: trimesh.Trimesh, body_type: str) -> CollisionShape:
    vertices = np.asarray(mesh.vertices, dtype=float)
    # Linear least-squares gives a materially better center for partial but
    # sphere-like inputs than a plain vertex centroid. Fall back when singular.
    reference = vertices.mean(axis=0)
    centered = vertices - reference
    matrix = np.column_stack((2.0 * centered, np.ones(len(centered))))
    rhs = np.einsum("ij,ij->i", centered, centered)
    try:
        solution, *_ = np.linalg.lstsq(matrix, rhs, rcond=None)
        center = reference + solution[:3]
    except np.linalg.LinAlgError:
        center = reference
    distances = np.linalg.norm(vertices - center, axis=1)
    radius = max(float(np.max(distances)), np.finfo(float).eps)
    transform = np.eye(4)
    transform[:3, 3] = center
    return _base_shape(
        "sphere",
        transform,
        {"radius": radius},
        body_type=body_type,
    )


def _axial_fit(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray, float, float]:
    vertices = np.asarray(mesh.vertices, dtype=float)
    axis = _principal_axis(vertices)
    origin = vertices.mean(axis=0)
    axial = (vertices - origin) @ axis
    center_t = 0.5 * (float(axial.min()) + float(axial.max()))
    center = origin + center_t * axis
    axial_centered = (vertices - center) @ axis
    radial_vectors = vertices - center - np.outer(axial_centered, axis)
    radius = max(float(np.linalg.norm(radial_vectors, axis=1).max()), np.finfo(float).eps)
    span = max(float(axial_centered.max() - axial_centered.min()), np.finfo(float).eps)
    return axis, center, radius, span


def _fit_cylinder(mesh: trimesh.Trimesh, body_type: str) -> CollisionShape:
    axis, center, radius, height = _axial_fit(mesh)
    transform = _frame_from_z(axis)
    transform[:3, 3] = center
    return _base_shape(
        "cylinder",
        transform,
        {"radius": radius, "height": height, "axis": "local_z"},
        body_type=body_type,
    )


def _fit_capsule(mesh: trimesh.Trimesh, body_type: str) -> CollisionShape:
    axis, center, radius, axial_span = _axial_fit(mesh)
    segment_height = max(0.0, axial_span - 2.0 * radius)
    transform = _frame_from_z(axis)
    transform[:3, 3] = center
    return _base_shape(
        "capsule",
        transform,
        {
            "radius": radius,
            "segment_height": segment_height,
            "total_height": segment_height + 2.0 * radius,
            "axis": "local_z",
        },
        body_type=body_type,
    )


def _farthest_points(points: np.ndarray, count: int) -> np.ndarray:
    """Deterministically retain well-spread points for a capped hull."""

    points = np.asarray(points, dtype=float)
    if len(points) <= count:
        return points
    order = np.lexsort((points[:, 2], points[:, 1], points[:, 0]))
    chosen = [int(order[0])]
    nearest_sq = np.sum((points - points[chosen[0]]) ** 2, axis=1)
    while len(chosen) < count:
        index = int(np.argmax(nearest_sq))
        chosen.append(index)
        distance_sq = np.sum((points - points[index]) ** 2, axis=1)
        nearest_sq = np.minimum(nearest_sq, distance_sq)
        nearest_sq[chosen] = -1.0
    return points[np.asarray(chosen, dtype=np.int64)]


def _fit_convex(
    mesh: trimesh.Trimesh,
    body_type: str,
    max_vertices_per_hull: int,
    *,
    ordinal: int = 0,
    kind: str = "convex",
) -> CollisionShape:
    hull = mesh.convex_hull
    if len(hull.vertices) > max_vertices_per_hull:
        points = _farthest_points(np.asarray(hull.vertices), max_vertices_per_hull)
        capped = trimesh.Trimesh(vertices=points, process=False).convex_hull
        if len(capped.vertices) >= 4 and len(capped.faces) >= 4:
            hull = capped
    return _base_shape(
        kind,
        np.eye(4),
        {
            "vertex_count": int(len(hull.vertices)),
            "triangle_count": int(len(hull.faces)),
        },
        body_type=body_type,
        ordinal=ordinal,
        vertices=np.asarray(hull.vertices),
        faces=np.asarray(hull.faces),
        settings={"max_vertices_per_hull": max_vertices_per_hull},
    )


def _fit_trimesh(mesh: trimesh.Trimesh, body_type: str) -> CollisionShape:
    return _base_shape(
        "trimesh",
        np.eye(4),
        {
            "vertex_count": int(len(mesh.vertices)),
            "triangle_count": int(len(mesh.faces)),
        },
        body_type=body_type,
        vertices=np.asarray(mesh.vertices),
        faces=np.asarray(mesh.faces),
    )


def _shape_mesh(shape: CollisionShape) -> trimesh.Trimesh:
    transform = np.asarray(shape.transform, dtype=float)
    if shape.type == "box":
        helper = trimesh.creation.box(extents=np.asarray(shape.dimensions["extents"], dtype=float))
    elif shape.type == "sphere":
        helper = trimesh.creation.icosphere(
            subdivisions=2, radius=float(shape.dimensions["radius"])
        )
    elif shape.type == "cylinder":
        helper = trimesh.creation.cylinder(
            radius=float(shape.dimensions["radius"]),
            height=float(shape.dimensions["height"]),
            sections=32,
        )
    elif shape.type == "capsule":
        helper = trimesh.creation.capsule(
            radius=float(shape.dimensions["radius"]),
            height=float(shape.dimensions["segment_height"]),
            count=[16, 16],
        )
    elif shape.type in {"convex", "coacd_hull", "trimesh"}:
        if shape.vertices is None or shape.faces is None:
            raise ValueError(f"{shape.type} shape is missing indexed geometry")
        helper = trimesh.Trimesh(vertices=shape.vertices, faces=shape.faces, process=False)
    else:
        raise ValueError(f"unsupported collision shape type: {shape.type}")
    helper.apply_transform(transform)
    return cast(trimesh.Trimesh, helper)


def _sample_surface(mesh: trimesh.Trimesh, count: int, rng: np.random.Generator) -> np.ndarray:
    triangles = np.asarray(mesh.triangles, dtype=float)
    if len(triangles) == 0:
        return np.empty((0, 3), dtype=float)
    areas = np.asarray(mesh.area_faces, dtype=float)
    total = float(areas.sum())
    probability = None if total <= np.finfo(float).eps else areas / total
    face_indices = rng.choice(len(triangles), size=count, replace=True, p=probability)
    picked = triangles[face_indices]
    uv = rng.random((count, 2))
    fold = uv.sum(axis=1) > 1.0
    uv[fold] = 1.0 - uv[fold]
    return np.asarray(
        picked[:, 0]
        + uv[:, :1] * (picked[:, 1] - picked[:, 0])
        + uv[:, 1:] * (picked[:, 2] - picked[:, 0]),
        dtype=float,
    )


def _closest_distances(mesh: trimesh.Trimesh, points: np.ndarray) -> np.ndarray:
    if len(points) == 0:
        return np.empty(0, dtype=float)
    try:
        _, distances, _ = trimesh.proximity.closest_point(  # type: ignore[no-untyped-call]
            mesh, points
        )
    except (ImportError, ModuleNotFoundError, AttributeError):
        _, distances, _ = trimesh.proximity.closest_point_naive(  # type: ignore[no-untyped-call]
            mesh, points
        )
    return np.asarray(distances, dtype=float)


def _shape_score(
    source_mesh: trimesh.Trimesh,
    shape: CollisionShape,
    *,
    seed: int,
    fit_policy: str,
    sample_count: int = 384,
) -> tuple[float, dict[str, Any]]:
    candidate = _shape_mesh(shape)
    diagonal = max(float(np.linalg.norm(source_mesh.extents)), np.finfo(float).eps)
    source_points = _sample_surface(source_mesh, sample_count, np.random.default_rng(seed))
    candidate_points = _sample_surface(candidate, sample_count, np.random.default_rng(seed + 1))
    forward = _closest_distances(candidate, source_points) / diagonal
    reverse = _closest_distances(source_mesh, candidate_points) / diagonal
    distances = np.concatenate((forward, reverse))
    tolerance = 0.015
    mean_distance = float(np.mean(distances))
    p95_distance = float(np.quantile(distances, 0.95))
    coverage = float(np.mean(distances <= tolerance))

    source_volume = abs(float(source_mesh.volume)) if source_mesh.is_watertight else 0.0
    candidate_volume = abs(float(candidate.volume)) if candidate.is_watertight else 0.0
    excess: float | None = None
    if source_volume > np.finfo(float).eps and candidate_volume > 0:
        excess = max(0.0, candidate_volume - source_volume) / source_volume

    policy_weights = {
        # cover strongly penalizes source surface left outside the proposal,
        # while inside strongly penalizes proposal volume beyond the source.
        "cover": (1.0, 0.8, 1.4, 0.04),
        "balanced": (1.0, 0.6, 0.8, 0.15),
        "inside": (1.0, 0.5, 0.3, 0.70),
    }
    mean_w, p95_w, coverage_w, volume_w = policy_weights[fit_policy]
    complexity = {"box": 0.0, "sphere": 0.002, "capsule": 0.004, "cylinder": 0.006}.get(
        shape.type, 0.02
    )
    score = (
        mean_w * mean_distance
        + p95_w * p95_distance
        + coverage_w * (1.0 - coverage)
        + (volume_w * min(excess, 2.0) if excess is not None else 0.0)
        + complexity
    )
    metrics = {
        "sampled_metric_status": "estimated_from_deterministic_surface_samples",
        "sample_count_per_direction": sample_count,
        "normalized_by_source_bounds_diagonal": diagonal,
        "sampled_bidirectional_mean_distance_estimate": mean_distance,
        "sampled_bidirectional_p95_distance_estimate": p95_distance,
        "sampled_surface_coverage_estimate": coverage,
        "sampled_coverage_tolerance_fraction_of_diagonal": tolerance,
        "excess_volume_fraction_estimate": excess,
        "excess_volume_metric_status": (
            "estimated_from_watertight_mesh_volumes" if excess is not None else "not_applicable"
        ),
        "complexity_penalty": complexity,
        "selection_score": float(score),
    }
    return float(score), metrics


def _coacd_module() -> Any | None:
    try:
        return importlib.import_module("coacd")
    except (ImportError, ModuleNotFoundError):
        return None


def _call_with_supported_kwargs(
    function: Any, positional: list[Any], options: dict[str, Any]
) -> Any:
    try:
        parameters: Mapping[str, inspect.Parameter] = inspect.signature(function).parameters
    except (TypeError, ValueError):
        parameters = {}
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values()):
        selected = options
    else:
        selected = {name: value for name, value in options.items() if name in parameters}
    return function(*positional, **selected)


def _fit_coacd(
    mesh: trimesh.Trimesh,
    body_type: str,
    *,
    threshold: float,
    real_metric: bool,
    max_hulls: int,
    max_vertices_per_hull: int,
    seed: int,
) -> list[CollisionShape]:
    module = _coacd_module()
    if module is None:
        raise RuntimeError("CoACD mode requested but the optional 'coacd' package is not installed")
    coacd_mesh = module.Mesh(
        np.ascontiguousarray(mesh.vertices, dtype=np.float64),
        np.ascontiguousarray(mesh.faces, dtype=np.int32),
    )
    options = {
        "threshold": threshold,
        "max_convex_hull": max_hulls,
        "max_ch_vertex": max_vertices_per_hull,
        "mcts_max_depth": 3,
        "mcts_iterations": 150,
        "mcts_nodes": 20,
        "resolution": 2000,
        "seed": seed,
        "pca": False,
        "merge": True,
        "decimate": True,
        "apx_mode": "ch",
        "real_metric": real_metric,
    }
    output = _call_with_supported_kwargs(module.run_coacd, [coacd_mesh], options)
    shapes: list[CollisionShape] = []
    for ordinal, part in enumerate(output[:max_hulls]):
        if isinstance(part, trimesh.Trimesh):
            part_mesh = part
        else:
            vertices, faces = part
            part_mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        shape = _fit_convex(
            part_mesh,
            body_type,
            max_vertices_per_hull,
            ordinal=ordinal,
            kind="coacd_hull",
        )
        shape.generator_settings.update(
            {
                "adapter": "coacd",
                "threshold": threshold,
                "real_metric": real_metric,
                "seed": seed,
            }
        )
        shapes.append(shape)
    if not shapes:
        raise RuntimeError("CoACD returned no collision hulls")
    return shapes


def _source_metrics(mesh: trimesh.Trimesh) -> dict[str, Any]:
    vertices = np.ascontiguousarray(mesh.vertices, dtype="<f8")
    faces = np.ascontiguousarray(mesh.faces, dtype="<i8")
    fingerprint = hashlib.sha256(vertices.tobytes() + faces.tobytes()).hexdigest()
    return {
        "hash": {
            "algorithm": "sha256",
            "value": None,
            "status": "placeholder_for_pipeline_source_file_hash",
            "geometry_fingerprint": fingerprint,
        },
        "metrics": {
            "vertex_count": int(len(mesh.vertices)),
            "triangle_count": int(len(mesh.faces)),
            "component_count": int(len(mesh.split(only_watertight=False))),
            "watertight": bool(mesh.is_watertight),
            "bounds": np.asarray(mesh.bounds, dtype=float).tolist(),
            "bounds_diagonal": float(np.linalg.norm(mesh.extents)),
            "volume": abs(float(mesh.volume)) if mesh.is_watertight else None,
            "volume_metric_status": (
                "exact_for_watertight_input" if mesh.is_watertight else "unavailable"
            ),
        },
    }


def _result(
    mesh: trimesh.Trimesh,
    shapes: list[CollisionShape],
    *,
    body_type: str,
    seed: int,
    settings: dict[str, Any],
    warnings: Iterable[str] = (),
    stop_reason: str,
) -> CollisionResult:
    compatibility = "unconfirmed" if body_type == "unspecified" else "confirmed"
    warnings_list = list(warnings)
    if body_type == "unspecified":
        warnings_list.append(
            "Body type is unspecified; the candidate is generated but runtime compatibility "
            "is unconfirmed."
        )
    triangle_count = sum(int(shape.dimensions.get("triangle_count", 0)) for shape in shapes)
    return CollisionResult(
        shapes=shapes,
        body_type=body_type,
        body_compatibility=compatibility,
        seed=seed,
        settings=settings,
        source=_source_metrics(mesh),
        metrics={
            "shape_count": len(shapes),
            "hull_count": sum(shape.type in {"convex", "coacd_hull"} for shape in shapes),
            "indexed_collision_triangle_count": triangle_count,
            "stop_reason": stop_reason,
            "metric_note": (
                "Keys containing 'sampled' or 'estimate' are estimates, not exact guarantees."
            ),
        },
        warnings=warnings_list,
    )


def generate_collision(
    mesh: trimesh.Trimesh | trimesh.Scene,
    *,
    mode: str = "auto",
    body_type: str = "unspecified",
    fit_policy: str = "balanced",
    primitive_types: Sequence[str] = ("box", "sphere", "capsule", "cylinder"),
    max_primitives: int = 8,
    max_hulls: int = 8,
    max_vertices_per_hull: int = 32,
    coacd_threshold: float = 0.05,
    coacd_real_metric: bool = False,
    seed: int = 0,
) -> CollisionResult:
    """Generate one deterministic, editable collision candidate.

    ``mode`` accepts ``auto``, each primitive name, ``compound``,
    ``convex-hull``, ``coacd``, ``trimesh``, and ``none``. Automatic and
    compound selection fit and measure the requested primitives, accepting only
    a policy-dependent quality level before falling back to CoACD for
    meaningfully concave input when available, or one capped convex hull
    otherwise.
    """

    source = _as_mesh(mesh)
    mode = str(mode).lower().replace("_", "-")
    mode = {"convex": "convex-hull"}.get(mode, mode)
    if mode == "static-trimesh":
        mode = "trimesh"
    body_type = str(body_type).lower()
    body_type = {"trigger": "area"}.get(body_type, body_type)
    if body_type not in _KNOWN_BODIES:
        raise ValueError(f"unknown body_type {body_type!r}")
    fit_policy = str(fit_policy).lower()
    fit_policy = {"conservative": "cover", "permissive": "inside"}.get(fit_policy, fit_policy)
    if fit_policy not in {"cover", "balanced", "inside"}:
        raise ValueError("fit_policy must be cover, balanced, or inside")
    primitive_types = tuple(dict.fromkeys(str(item).lower() for item in primitive_types))
    unknown_primitives = set(primitive_types) - _PRIMITIVES
    if unknown_primitives:
        raise ValueError(f"unknown primitive types: {sorted(unknown_primitives)}")
    if mode not in {
        "auto",
        "none",
        "compound",
        "convex-hull",
        "coacd",
        "trimesh",
        *_PRIMITIVES,
    }:
        raise ValueError(f"unknown collision mode {mode!r}")
    if max_primitives < 0 or max_hulls < 1 or max_vertices_per_hull < 4:
        raise ValueError(
            "limits require max_primitives >= 0, max_hulls >= 1, max_vertices_per_hull >= 4"
        )
    if not (0.0 < coacd_threshold <= 1.0):
        raise ValueError("coacd_threshold must be in (0, 1]")
    if mode == "trimesh" and body_type in _CONCAVE_UNSAFE_BODIES:
        raise ValueError(f"trimesh collision is unsafe and forbidden for {body_type} bodies")
    if body_type == "none" and mode not in {"none", "auto"}:
        raise ValueError("body_type='none' only supports mode='none' or mode='auto'")

    settings = {
        "mode": mode,
        "body_type": body_type,
        "fit_policy": fit_policy,
        "primitive_types": list(primitive_types),
        "max_primitives": max_primitives,
        "max_hulls": max_hulls,
        "max_vertices_per_hull": max_vertices_per_hull,
        "coacd_threshold": coacd_threshold,
        "coacd_real_metric": coacd_real_metric,
        "seed": int(seed),
    }

    if mode == "none" or body_type == "none":
        return _result(
            source,
            [],
            body_type=body_type,
            seed=seed,
            settings=settings,
            stop_reason="collision_disabled",
        )
    if mode in _PRIMITIVES:
        fitter = {
            "box": _fit_box,
            "sphere": _fit_sphere,
            "capsule": _fit_capsule,
            "cylinder": _fit_cylinder,
        }[mode]
        shape = fitter(source, body_type)
        _, shape.metrics = _shape_score(source, shape, seed=seed, fit_policy=fit_policy)
        return _result(
            source,
            [shape],
            body_type=body_type,
            seed=seed,
            settings=settings,
            stop_reason=f"explicit_{mode}",
        )
    if mode == "convex-hull":
        shape = _fit_convex(source, body_type, max_vertices_per_hull)
        _, shape.metrics = _shape_score(source, shape, seed=seed, fit_policy=fit_policy)
        return _result(
            source,
            [shape],
            body_type=body_type,
            seed=seed,
            settings=settings,
            stop_reason="explicit_convex_hull",
        )
    if mode == "trimesh":
        warning = (
            "Trimesh collision is intended only for static geometry."
            if body_type in {"static", "unspecified"}
            else "Trimesh collision body policy should be verified by the target engine."
        )
        shape = _fit_trimesh(source, body_type)
        shape.metrics = {
            "sampled_metric_status": "exact_source_geometry",
            "sampled_bidirectional_mean_distance_estimate": 0.0,
            "sampled_bidirectional_p95_distance_estimate": 0.0,
            "sampled_surface_coverage_estimate": 1.0,
            "excess_volume_fraction_estimate": 0.0,
        }
        return _result(
            source,
            [shape],
            body_type=body_type,
            seed=seed,
            settings=settings,
            warnings=[warning],
            stop_reason="explicit_static_trimesh",
        )
    if mode == "coacd":
        shapes = _fit_coacd(
            source,
            body_type,
            threshold=coacd_threshold,
            real_metric=coacd_real_metric,
            max_hulls=max_hulls,
            max_vertices_per_hull=max_vertices_per_hull,
            seed=seed,
        )
        return _result(
            source,
            shapes,
            body_type=body_type,
            seed=seed,
            settings=settings,
            stop_reason="explicit_coacd",
        )

    candidates: list[tuple[float, CollisionShape]] = []
    fitters = {
        "box": _fit_box,
        "sphere": _fit_sphere,
        "capsule": _fit_capsule,
        "cylinder": _fit_cylinder,
    }
    if max_primitives:
        for offset, kind in enumerate(primitive_types[:max_primitives]):
            shape = fitters[kind](source, body_type)
            score, shape.metrics = _shape_score(
                source,
                shape,
                seed=seed + 17 * offset,
                fit_policy=fit_policy,
            )
            candidates.append((score, shape))
    thresholds = {"cover": 0.20, "balanced": 0.18, "inside": 0.25}
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1].type))
        best_score, best = candidates[0]
        if best_score <= thresholds[fit_policy]:
            return _result(
                source,
                [best],
                body_type=body_type,
                seed=seed,
                settings=settings,
                stop_reason=(
                    "compound_primitive_accepted"
                    if mode == "compound"
                    else "automatic_primitive_accepted"
                ),
            )

    hull = source.convex_hull
    source_volume = abs(float(source.volume)) if source.is_watertight else 0.0
    hull_volume = abs(float(hull.volume)) if hull.is_watertight else 0.0
    concavity = (
        max(0.0, hull_volume - source_volume) / hull_volume
        if hull_volume > np.finfo(float).eps and source_volume > 0
        else 0.0
    )
    if concavity > coacd_threshold and _coacd_module() is not None:
        try:
            shapes = _fit_coacd(
                source,
                body_type,
                threshold=coacd_threshold,
                real_metric=coacd_real_metric,
                max_hulls=max_hulls,
                max_vertices_per_hull=max_vertices_per_hull,
                seed=seed,
            )
            return _result(
                source,
                shapes,
                body_type=body_type,
                seed=seed,
                settings=settings,
                stop_reason=(
                    "compound_coacd_generated"
                    if mode == "compound"
                    else "automatic_concave_coacd_fallback"
                ),
            )
        except Exception as error:  # optional adapter failure must retain safe fallback
            warning = f"CoACD fallback failed ({type(error).__name__}); used one convex hull."
        else:  # pragma: no cover - return above is unconditional
            warning = ""
    else:
        warning = ""
    shape = _fit_convex(source, body_type, max_vertices_per_hull)
    _, shape.metrics = _shape_score(source, shape, seed=seed, fit_policy=fit_policy)
    shape.metrics.update(
        {
            "concavity_volume_fraction_estimate": concavity,
            "concavity_metric_status": (
                "estimated_from_watertight_source_and_convex_hull_volumes"
                if source_volume > 0
                else "unavailable_for_non_watertight_source"
            ),
        }
    )
    return _result(
        source,
        [shape],
        body_type=body_type,
        seed=seed,
        settings=settings,
        warnings=[warning] if warning else [],
        stop_reason=(
            "compound_convex_fallback" if mode == "compound" else "automatic_convex_fallback"
        ),
    )


def collision_scene(result: CollisionResult) -> trimesh.Scene:
    """Create a portable preview/export scene with stable helper names."""

    if not isinstance(result, CollisionResult):
        raise TypeError("result must be a CollisionResult")
    scene = trimesh.Scene()
    for shape in result.shapes:
        helper = _shape_mesh(shape)
        helper.metadata.update(
            {
                "collision_shape_id": shape.id,
                "collision_shape_type": shape.type,
                "generated": shape.generated,
                "locked": shape.locked,
            }
        )
        scene.add_geometry(helper, geom_name=shape.id, node_name=shape.id)
    scene.metadata["collision_sidecar"] = result.to_dict()
    return scene


__all__ = ["CollisionShape", "CollisionResult", "generate_collision", "collision_scene"]
