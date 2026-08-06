"""Deterministic, inspection-only geometry analysis.

The fits in this module are evidence proposals, not topology classifications.
Callers should retain the reported residuals and rejection reasons when making
policy decisions.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any, cast

import numpy as np
import trimesh

_EPS = np.finfo(np.float64).eps


def _plain(value: Any) -> Any:
    """Convert dataclass content and numpy scalars to JSON-safe values."""
    if isinstance(value, np.ndarray):
        return [_plain(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


@dataclass(frozen=True)
class PrimitiveCandidate:
    """Evidence for one primitive family, suitable for report serialization."""

    primitive: str
    accepted: bool
    confidence: float
    support_area: float
    support_fraction: float
    residual: float | None
    source_face_count: int
    parameters: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    rejection_reasons: tuple[str, ...] = ()

    @property
    def kind(self) -> str:
        """Compatibility-friendly name for the primitive family."""
        return self.primitive

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _plain(asdict(self)))


@dataclass(frozen=True)
class GeometryAnalysis:
    """Serializable geometry inventory and shape evidence."""

    vertices: int
    faces: int
    bounds: list[list[float]] | None
    diagonal: float
    area: float | None
    volume: float | None
    watertight: bool
    winding_consistent: bool
    connected_components: int
    degenerate_faces: int
    boundary_edges: int
    nonmanifold_edges: int
    exported_vertices: int
    position_welded_vertices: int
    pca_axes: list[list[float]]
    pca_extents: list[float]
    planar_regions: list[dict[str, Any]]
    primitive_candidates: list[PrimitiveCandidate]
    seed: int
    warnings: list[str] = field(default_factory=list)

    @property
    def candidates(self) -> list[PrimitiveCandidate]:
        return self.primitive_candidates

    @property
    def vertex_count(self) -> int:
        return self.vertices

    @property
    def face_count(self) -> int:
        return self.faces

    @property
    def surface_area(self) -> float | None:
        return self.area

    @property
    def component_count(self) -> int:
        return self.connected_components

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _plain(asdict(self)))


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > _EPS else np.zeros(3, dtype=np.float64)


def _canonical_axis(axis: np.ndarray) -> np.ndarray:
    axis = _unit(np.asarray(axis, dtype=np.float64))
    if not np.any(axis):
        return axis
    index = int(np.argmax(np.abs(axis)))
    if axis[index] < 0.0:
        axis = -axis
    axis[np.abs(axis) < 1e-15] = 0.0
    return axis


def _stable_pca(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if len(points) < 2:
        return np.eye(3, dtype=np.float64), np.zeros(3, dtype=np.float64)
    centered = points - points.mean(axis=0)
    covariance = centered.T @ centered / max(len(points), 1)
    values, vectors = np.linalg.eigh(covariance)
    order = np.argsort(-values, kind="stable")
    values, vectors = values[order], vectors[:, order]

    # Eigenvectors within a repeated-eigenvalue subspace are not unique. Use
    # projected world axes to choose a deterministic basis in that subspace.
    tolerance = max(float(values[0]) * 1e-10, 1e-15)
    result: list[np.ndarray] = []
    start = 0
    while start < 3:
        end = start + 1
        while end < 3 and abs(float(values[end] - values[start])) <= tolerance:
            end += 1
        subspace = vectors[:, start:end]
        for world in np.eye(3):
            candidate = subspace @ (subspace.T @ world)
            for chosen in result:
                candidate -= chosen * float(np.dot(chosen, candidate))
            norm = float(np.linalg.norm(candidate))
            if norm > 1e-9:
                result.append(_canonical_axis(candidate / norm))
            if len(result) == end:
                break
        start = end
    axes = np.asarray(result[:3], dtype=np.float64)
    if float(np.linalg.det(axes)) < 0.0:
        axes[-1] *= -1.0
    projections = centered @ axes.T
    extents = np.ptp(projections, axis=0) if len(points) else np.zeros(3)
    return axes, extents


def _face_geometry(
    vertices: np.ndarray, faces: np.ndarray, scale: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    triangles = vertices[faces] if len(faces) else np.empty((0, 3, 3))
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    double_area = np.linalg.norm(cross, axis=1)
    areas = double_area * 0.5
    normals = np.zeros_like(cross)
    valid = double_area > max(scale * scale * 1e-12, 1e-24)
    normals[valid] = cross[valid] / double_area[valid, None]
    centroids = triangles.mean(axis=1) if len(triangles) else np.empty((0, 3))
    return centroids, normals, areas, valid


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = np.arange(size, dtype=np.int64)

    def find(self, item: int) -> int:
        root = item
        while self.parent[root] != root:
            root = int(self.parent[root])
        while self.parent[item] != item:
            following = int(self.parent[item])
            self.parent[item] = root
            item = following
        return root

    def union(self, left: int, right: int) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def _edge_inventory(faces: np.ndarray) -> tuple[dict[tuple[int, int], list[int]], int, int]:
    edge_faces: dict[tuple[int, int], list[int]] = {}
    for face_index, face in enumerate(faces):
        for left, right in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edge = (int(min(left, right)), int(max(left, right)))
            edge_faces.setdefault(edge, []).append(face_index)
    boundary = sum(len(owners) == 1 for owners in edge_faces.values())
    nonmanifold = sum(len(owners) > 2 for owners in edge_faces.values())
    return edge_faces, boundary, nonmanifold


def _component_count(face_count: int, edge_faces: dict[tuple[int, int], list[int]]) -> int:
    if face_count == 0:
        return 0
    groups = _UnionFind(face_count)
    for owners in edge_faces.values():
        anchor = owners[0]
        for other in owners[1:]:
            groups.union(anchor, other)
    return len({groups.find(index) for index in range(face_count)})


def _planar_regions(
    faces: np.ndarray,
    centroids: np.ndarray,
    normals: np.ndarray,
    areas: np.ndarray,
    valid: np.ndarray,
    edge_faces: dict[tuple[int, int], list[int]],
    scale: float,
    angle_deg: float,
    total_area: float,
) -> list[dict[str, Any]]:
    adjacency: list[set[int]] = [set() for _ in range(len(faces))]
    for owners in edge_faces.values():
        if len(owners) == 2:
            left, right = owners
            adjacency[left].add(right)
            adjacency[right].add(left)

    cosine = math.cos(math.radians(angle_deg))
    distance_limit = max(scale * 0.0025, 1e-12)
    unvisited = set(int(index) for index in np.flatnonzero(valid))
    seeds = sorted(unvisited, key=lambda index: (-float(areas[index]), index))
    regions: list[dict[str, Any]] = []
    for seed in seeds:
        if seed not in unvisited:
            continue
        unvisited.remove(seed)
        members = [seed]
        queue = [seed]
        reference = normals[seed].copy()
        origin = centroids[seed].copy()
        while queue:
            current = queue.pop(0)
            for neighbor in sorted(adjacency[current]):
                if neighbor not in unvisited or not valid[neighbor]:
                    continue
                aligned = float(np.dot(normals[neighbor], reference)) >= cosine
                distance = abs(float(np.dot(centroids[neighbor] - origin, reference)))
                if aligned and distance <= distance_limit:
                    unvisited.remove(neighbor)
                    members.append(neighbor)
                    queue.append(neighbor)
                    weights = areas[members]
                    reference = _unit(np.sum(normals[members] * weights[:, None], axis=0))
                    origin = np.average(centroids[members], axis=0, weights=weights)

        member_array = np.asarray(sorted(members), dtype=np.int64)
        weights = areas[member_array]
        region_area = float(weights.sum())
        fitted_normal = _unit(np.sum(normals[member_array] * weights[:, None], axis=0))
        normal = _canonical_axis(fitted_normal)
        origin = np.average(centroids[member_array], axis=0, weights=weights)
        distances = np.abs((centroids[member_array] - origin) @ fitted_normal)
        angles = np.degrees(np.arccos(np.clip(normals[member_array] @ fitted_normal, -1.0, 1.0)))
        member_set = set(int(item) for item in member_array)
        boundary_edges = 0
        for owners in edge_faces.values():
            inside = sum(owner in member_set for owner in owners)
            if inside and inside != len(owners):
                boundary_edges += 1
            elif inside == 1 and len(owners) == 1:
                boundary_edges += 1
        regions.append(
            {
                "face_count": len(member_array),
                "first_face_index": int(member_array[0]),
                "face_index_sample": member_array[:64].tolist(),
                "face_index_sha256": hashlib.sha256(
                    member_array.astype("<i8", copy=False).tobytes()
                ).hexdigest(),
                "area": region_area,
                "support_fraction": region_area / total_area if total_area > 0 else 0.0,
                "normal": normal.tolist(),
                "centroid": origin.tolist(),
                "rms_residual": float(np.sqrt(np.average(distances**2, weights=weights))) / scale,
                "max_residual": float(np.max(distances)) / scale,
                "rms_normal_angle_deg": float(np.sqrt(np.average(angles**2, weights=weights))),
                "boundary_edges": boundary_edges,
            }
        )
    regions.sort(
        key=lambda region: (
            -float(region["area"]),
            int(region["first_face_index"]),
        )
    )
    return regions


def _surface_samples(
    vertices: np.ndarray,
    faces: np.ndarray,
    areas: np.ndarray,
    normals: np.ndarray,
    count: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    valid = np.flatnonzero(areas > 0.0)
    if len(valid) == 0 or count <= 0:
        return np.empty((0, 3)), np.empty((0, 3)), np.empty(0)
    count = int(count)
    probabilities = areas[valid] / areas[valid].sum()
    rng = np.random.default_rng(seed)
    selected = rng.choice(valid, size=count, replace=True, p=probabilities)
    random = rng.random((count, 2))
    root = np.sqrt(random[:, 0])
    barycentric = np.column_stack((1.0 - root, root * (1.0 - random[:, 1]), root * random[:, 1]))
    points = np.einsum("ij,ijk->ik", barycentric, vertices[faces[selected]])
    return points, normals[selected], selected


def _candidate(
    primitive: str,
    confidence: float,
    support_area: float,
    total_area: float,
    residual: float | None,
    source_face_count: int,
    parameters: dict[str, Any],
    metrics: dict[str, Any],
    reasons: Iterable[str],
) -> PrimitiveCandidate:
    reasons = tuple(dict.fromkeys(str(reason) for reason in reasons))
    return PrimitiveCandidate(
        primitive=primitive,
        accepted=not reasons,
        confidence=float(np.clip(confidence, 0.0, 1.0)),
        support_area=float(max(support_area, 0.0)),
        support_fraction=float(support_area / total_area) if total_area > 0 else 0.0,
        residual=None if residual is None else float(max(residual, 0.0)),
        source_face_count=int(source_face_count),
        parameters=_plain(parameters),
        metrics=_plain(metrics),
        rejection_reasons=reasons,
    )


def _plane_candidate(
    regions: list[dict[str, Any]], total_area: float, min_support: float
) -> PrimitiveCandidate:
    if not regions:
        return _candidate("plane", 0, 0, total_area, None, 0, {}, {}, ("no valid faces",))
    region = regions[0]
    support = float(region["support_fraction"])
    residual = float(region["rms_residual"])
    angle = float(region["rms_normal_angle_deg"])
    reasons: list[str] = []
    if support < min_support:
        reasons.append("support below minimum")
    if residual > 0.005:
        reasons.append("point-to-plane residual too high")
    if angle > 7.5:
        reasons.append("normal spread too high")
    confidence = support * math.exp(-residual / 0.0025) * math.exp(-angle / 7.5)
    return _candidate(
        "plane",
        confidence,
        float(region["area"]),
        total_area,
        residual,
        int(region["face_count"]),
        {"normal": region["normal"], "origin": region["centroid"]},
        {"normal_rms_deg": angle, "boundary_edges": region["boundary_edges"]},
        reasons,
    )


def _box_candidate(
    points: np.ndarray,
    regions: list[dict[str, Any]],
    total_area: float,
    mesh_volume: float | None,
    scale: float,
    min_support: float,
    source_face_count: int,
) -> PrimitiveCandidate:
    reasons: list[str] = []
    usable = [region for region in regions if float(region["support_fraction"]) >= min_support]
    normals = [_canonical_axis(np.asarray(region["normal"])) for region in usable]
    axes: list[np.ndarray] = []
    for normal in normals:
        if all(abs(float(np.dot(normal, axis))) < math.cos(math.radians(80.0)) for axis in axes):
            axes.append(normal)
        if len(axes) == 3:
            break
    orthogonality = 0.0
    if len(axes) == 3:
        orthogonality = 1.0 - max(
            abs(float(np.dot(axes[i], axes[j]))) for i in range(3) for j in range(i)
        )
        axes = [_canonical_axis(axis) for axis in axes]
        if float(np.linalg.det(np.asarray(axes))) < 0:
            axes[-1] = -axes[-1]
    else:
        reasons.append("fewer than three supported plane families")
        axes = [np.eye(3)[index] for index in range(3)]
    if len(points):
        coordinates = points @ np.asarray(axes).T
        lower, upper = coordinates.min(axis=0), coordinates.max(axis=0)
        extents = upper - lower
        distances = np.minimum(coordinates - lower, upper - coordinates)
        nearest = np.min(np.abs(distances), axis=1)
        residual = float(np.sqrt(np.mean(nearest**2)) / scale)
        near = nearest <= max(scale * 0.01, 1e-12)
        surface_coverage = float(np.mean(near))
        center = ((lower + upper) * 0.5) @ np.asarray(axes)
        box_volume = float(np.prod(extents))
    else:
        extents = np.zeros(3)
        center = np.zeros(3)
        residual, surface_coverage, box_volume = 1.0, 0.0, 0.0
    occupied = (
        None if mesh_volume is None or box_volume <= 0 else min(mesh_volume / box_volume, 1.0)
    )
    support_area = total_area * surface_coverage
    if surface_coverage < max(min_support, 0.5):
        reasons.append("oriented box surface coverage too low")
    if residual > 0.02:
        reasons.append("oriented box surface residual too high")
    if orthogonality < 0.95:
        reasons.append("plane families are not sufficiently orthogonal")
    confidence = surface_coverage * max(orthogonality, 0.0) * math.exp(-residual / 0.01)
    return _candidate(
        "box",
        confidence,
        support_area,
        total_area,
        residual,
        source_face_count,
        {"center": center.tolist(), "axes": np.asarray(axes).tolist(), "extents": extents.tolist()},
        {
            "orthogonality": orthogonality,
            "surface_coverage": surface_coverage,
            "occupied_volume_fraction": occupied,
        },
        reasons,
    )


def _sphere_candidate(
    points: np.ndarray,
    total_area: float,
    scale: float,
    min_support: float,
    source_face_count: int,
) -> PrimitiveCandidate:
    reasons: list[str] = []
    if len(points) < 4:
        return _candidate("sphere", 0, 0, total_area, None, 0, {}, {}, ("insufficient samples",))
    matrix = np.column_stack((2.0 * points, np.ones(len(points))))
    rhs = np.einsum("ij,ij->i", points, points)
    solution, _, rank, _ = np.linalg.lstsq(matrix, rhs, rcond=None)
    center = solution[:3]
    radii = np.linalg.norm(points - center, axis=1)
    radius = float(np.median(radii))
    residuals = np.abs(radii - radius)
    residual = float(np.sqrt(np.mean(residuals**2)) / max(radius, _EPS))
    support = float(np.mean(residuals <= max(radius * 0.025, scale * 1e-5)))
    directions = (points - center) / np.maximum(radii[:, None], _EPS)
    octants = set(tuple((direction >= 0).astype(int)) for direction in directions)
    angular_coverage = len(octants) / 8.0
    covariance = directions.T @ directions / len(directions)
    isotropy = float(np.clip(1.0 - 3.0 * np.std(np.linalg.eigvalsh(covariance)), 0.0, 1.0))
    if rank < 4:
        reasons.append("sphere fit is rank deficient")
    if support < max(min_support, 0.75):
        reasons.append("radial support too low")
    if residual > 0.035:
        reasons.append("radial residual too high")
    if angular_coverage < 0.75 or isotropy < 0.6:
        reasons.append("angular surface coverage too low")
    confidence = support * angular_coverage * isotropy * math.exp(-residual / 0.025)
    return _candidate(
        "sphere",
        confidence,
        total_area * support,
        total_area,
        residual,
        source_face_count,
        {"center": center.tolist(), "radius": radius},
        {
            "angular_coverage": angular_coverage,
            "direction_isotropy": isotropy,
            "radial_support": support,
        },
        reasons,
    )


def _perpendicular_basis(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    world = np.eye(3)[int(np.argmin(np.abs(axis)))]
    first = _canonical_axis(np.cross(axis, world))
    second = _unit(np.cross(axis, first))
    return first, second


def _circle_fit(coordinates: np.ndarray) -> tuple[np.ndarray, float]:
    matrix = np.column_stack((2.0 * coordinates, np.ones(len(coordinates))))
    rhs = np.einsum("ij,ij->i", coordinates, coordinates)
    solution, _, _, _ = np.linalg.lstsq(matrix, rhs, rcond=None)
    center = solution[:2]
    radius = float(np.median(np.linalg.norm(coordinates - center, axis=1)))
    return center, radius


def _cylinder_fit(
    points: np.ndarray,
    sample_normals: np.ndarray,
    axis: np.ndarray,
    total_area: float,
    scale: float,
    curved_angle_deg: float,
) -> dict[str, Any]:
    axis = _canonical_axis(axis)
    first, second = _perpendicular_basis(axis)
    dot_normal = np.abs(sample_normals @ axis)
    side = dot_normal <= math.sin(math.radians(curved_angle_deg))
    if int(side.sum()) < 12:
        return {"score": -1.0, "axis": axis, "reason": "insufficient curved-side samples"}
    transverse = np.column_stack((points @ first, points @ second))
    center2, radius = _circle_fit(transverse[side])
    radial_vectors = transverse - center2
    radial = np.linalg.norm(radial_vectors, axis=1)
    side_residuals = np.abs(radial[side] - radius)
    residual = float(np.sqrt(np.mean(side_residuals**2)) / max(radius, _EPS))
    angles = np.mod(np.arctan2(radial_vectors[side, 1], radial_vectors[side, 0]), 2 * np.pi)
    angular_bins = len(np.unique(np.floor(angles / (2 * np.pi) * 24).astype(int)))
    angular_coverage = angular_bins / 24.0
    axial = points @ axis
    axial_min, axial_max = np.quantile(axial, [0.005, 0.995])
    axial_span = float(axial_max - axial_min)
    axial_coverage = axial_span / max(float(np.ptp(axial)), _EPS)
    edges = np.linspace(axial_min, axial_max, 7)
    slice_radii: list[float] = []
    for low, high in zip(edges[:-1], edges[1:], strict=True):
        selected = side & (axial >= low) & (axial <= high)
        if int(selected.sum()) >= 3:
            slice_radii.append(float(np.median(radial[selected])))
    slice_stability = (
        float(np.std(slice_radii) / max(float(np.mean(slice_radii)), _EPS))
        if len(slice_radii) >= 3
        else 1.0
    )
    unit_radial = radial_vectors[side] / np.maximum(radial[side, None], _EPS)
    normal2 = np.column_stack((sample_normals[side] @ first, sample_normals[side] @ second))
    radial_normal_alignment = float(np.mean(np.abs(np.sum(unit_radial * normal2, axis=1))))
    cap = dot_normal >= math.cos(math.radians(7.5))
    cap_support = float(np.mean(cap))
    side_support = float(np.mean(side))
    combined_support = min(1.0, side_support + cap_support)
    center3 = center2[0] * first + center2[1] * second + float(np.mean(axial)) * axis
    score = (
        combined_support
        * angular_coverage
        * radial_normal_alignment
        * math.exp(-residual / 0.035)
        * math.exp(-slice_stability / 0.08)
    )
    return {
        "score": score,
        "axis": axis,
        "center": center3,
        "radius": radius,
        "height": axial_span,
        "residual": residual,
        "angular_coverage": angular_coverage,
        "axial_coverage": axial_coverage,
        "slice_radii": slice_radii,
        "slice_radius_cv": slice_stability,
        "radial_normal_alignment": radial_normal_alignment,
        "side_support": side_support,
        "cap_support": cap_support,
        "support": combined_support,
    }


def _cylinder_candidate(
    points: np.ndarray,
    normals: np.ndarray,
    axes: np.ndarray,
    total_area: float,
    scale: float,
    min_support: float,
    curved_angle_deg: float,
    source_face_count: int,
) -> PrimitiveCandidate:
    if len(points) < 12:
        return _candidate("cylinder", 0, 0, total_area, None, 0, {}, {}, ("insufficient samples",))
    axis_candidates = list(axes) + [np.eye(3)[index] for index in range(3)]
    unique: list[np.ndarray] = []
    for axis in axis_candidates:
        axis = _canonical_axis(axis)
        if all(abs(float(np.dot(axis, existing))) < 1.0 - 1e-8 for existing in unique):
            unique.append(axis)
    fits = [
        _cylinder_fit(points, normals, axis, total_area, scale, curved_angle_deg) for axis in unique
    ]
    fit = sorted(fits, key=lambda item: (-float(item["score"]), tuple(item["axis"].tolist())))[0]
    reasons: list[str] = []
    if "reason" in fit:
        reasons.append(str(fit["reason"]))
        return _candidate(
            "cylinder", 0, 0, total_area, None, 0, {"axis": fit["axis"].tolist()}, {}, reasons
        )
    if float(fit["support"]) < max(min_support, 0.45):
        reasons.append("side and cap support too low")
    if float(fit["residual"]) > 0.04:
        reasons.append("radial residual too high")
    if float(fit["angular_coverage"]) < 0.7:
        reasons.append("angular coverage too low")
    if float(fit["axial_coverage"]) < 0.8:
        reasons.append("axial coverage too low")
    if float(fit["slice_radius_cv"]) > 0.08:
        reasons.append("per-slice radius is unstable")
    if float(fit["radial_normal_alignment"]) < 0.85:
        reasons.append("curved normals do not point radially")
    return _candidate(
        "cylinder",
        float(fit["score"]),
        total_area * float(fit["support"]),
        total_area,
        float(fit["residual"]),
        source_face_count,
        {
            "axis": fit["axis"].tolist(),
            "center": fit["center"].tolist(),
            "radius": fit["radius"],
            "height": fit["height"],
        },
        {
            key: value
            for key, value in fit.items()
            if key not in {"axis", "center", "score", "radius", "height", "residual"}
        },
        reasons,
    )


def _capsule_fit(points: np.ndarray, axis: np.ndarray, scale: float) -> dict[str, Any]:
    axis = _canonical_axis(axis)
    center = points.mean(axis=0)
    relative = points - center
    axial = relative @ axis
    radial = np.linalg.norm(relative - axial[:, None] * axis, axis=1)
    half_span = float(np.quantile(np.abs(axial), 0.995))
    axial_span = float(np.ptp(axial))
    axial_coverage = float(
        (np.quantile(axial, 0.995) - np.quantile(axial, 0.005)) / max(axial_span, _EPS)
    )
    first, second = _perpendicular_basis(axis)
    transverse = np.column_stack((relative @ first, relative @ second))
    usable_angles = radial > max(scale * 1e-6, _EPS)
    angles = np.mod(
        np.arctan2(transverse[usable_angles, 1], transverse[usable_angles, 0]),
        2 * np.pi,
    )
    angular_coverage = (
        len(np.unique(np.floor(angles / (2 * np.pi) * 24).astype(int))) / 24.0
        if len(angles)
        else 0.0
    )
    best: dict[str, Any] | None = None
    for half_segment in np.linspace(0.0, half_span * 0.9, 31):
        distance_to_axis_segment = np.sqrt(
            radial**2 + np.maximum(np.abs(axial) - half_segment, 0.0) ** 2
        )
        radius = float(np.median(distance_to_axis_segment))
        errors = np.abs(distance_to_axis_segment - radius)
        residual = float(np.sqrt(np.mean(errors**2)) / max(radius, _EPS))
        support = float(np.mean(errors <= max(radius * 0.035, scale * 1e-5)))
        score = support * math.exp(-residual / 0.035)
        candidate = {
            "score": score,
            "half_segment": float(half_segment),
            "radius": radius,
            "residual": residual,
            "support": support,
        }
        if best is None or (candidate["score"], -candidate["half_segment"]) > (
            best["score"],
            -best["half_segment"],
        ):
            best = candidate
    assert best is not None
    best.update(
        {
            "axis": axis,
            "center": center,
            "angular_coverage": angular_coverage,
            "axial_coverage": axial_coverage,
        }
    )
    return best


def _capsule_candidate(
    points: np.ndarray,
    axes: np.ndarray,
    total_area: float,
    scale: float,
    min_support: float,
    source_face_count: int,
) -> PrimitiveCandidate:
    if len(points) < 12:
        return _candidate("capsule", 0, 0, total_area, None, 0, {}, {}, ("insufficient samples",))
    fits = [_capsule_fit(points, axis, scale) for axis in axes]
    fit = sorted(fits, key=lambda item: (-float(item["score"]), tuple(item["axis"].tolist())))[0]
    reasons: list[str] = []
    if float(fit["support"]) < max(min_support, 0.75):
        reasons.append("capsule surface support too low")
    if float(fit["residual"]) > 0.05:
        reasons.append("capsule residual too high")
    if float(fit["half_segment"]) < scale * 0.025:
        reasons.append("axial segment is too short; sphere is a better fit")
    if float(fit["angular_coverage"]) < 0.7:
        reasons.append("angular coverage too low")
    if float(fit["axial_coverage"]) < 0.8:
        reasons.append("axial coverage too low")
    return _candidate(
        "capsule",
        float(fit["score"]),
        total_area * float(fit["support"]),
        total_area,
        float(fit["residual"]),
        source_face_count,
        {
            "axis": fit["axis"].tolist(),
            "center": fit["center"].tolist(),
            "radius": fit["radius"],
            "segment_length": 2.0 * float(fit["half_segment"]),
        },
        {
            "surface_support": fit["support"],
            "angular_coverage": fit["angular_coverage"],
            "axial_coverage": fit["axial_coverage"],
        },
        reasons,
    )


def analyze_mesh(
    mesh: trimesh.Trimesh,
    *,
    seed: int = 0,
    plane_angle_deg: float = 7.5,
    curved_normal_angle_deg: float = 12.0,
    max_samples: int = 20000,
    min_support_area: float = 0.005,
) -> GeometryAnalysis:
    """Inspect a single mesh and return deterministic primitive evidence.

    Scenes are intentionally rejected: scene flattening loses node ownership,
    transforms, materials, and source inventory.
    """
    if not isinstance(mesh, trimesh.Trimesh):
        raise TypeError(
            "analyze_mesh requires one trimesh.Trimesh; scenes must be inventoried explicitly"
        )
    if plane_angle_deg <= 0 or curved_normal_angle_deg <= 0:
        raise ValueError("normal-angle thresholds must be positive")
    if max_samples < 1:
        raise ValueError("max_samples must be at least 1")
    if min_support_area < 0:
        raise ValueError("min_support_area must be non-negative")

    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if vertices.size == 0:
        vertices = vertices.reshape((0, 3))
    if faces.size == 0:
        faces = faces.reshape((0, 3))
    if vertices.ndim != 2 or vertices.shape[1:] != (3,):
        raise ValueError("mesh vertices must have shape (n, 3)")
    if faces.ndim != 2 or faces.shape[1:] != (3,):
        raise ValueError("mesh faces must be triangles with shape (n, 3)")
    if not np.all(np.isfinite(vertices)):
        raise ValueError("mesh contains non-finite vertex positions")
    if len(faces) and (faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError("mesh faces reference vertices outside the vertex array")

    warnings: list[str] = []
    if len(vertices):
        lower, upper = vertices.min(axis=0), vertices.max(axis=0)
        bounds: list[list[float]] | None = [lower.tolist(), upper.tolist()]
        diagonal = float(np.linalg.norm(upper - lower))
    else:
        bounds, diagonal = None, 0.0
    scale = max(diagonal, 1e-12)
    centroids, face_normals, face_areas, valid_faces = _face_geometry(vertices, faces, scale)
    total_area = float(face_areas[valid_faces].sum())
    area = total_area if math.isfinite(total_area) else None
    degenerate_faces = int((~valid_faces).sum())
    if degenerate_faces:
        warnings.append(f"{degenerate_faces} degenerate face(s) excluded from fitting")
    edge_faces, boundary_edges, nonmanifold_edges = _edge_inventory(faces)
    components = _component_count(len(faces), edge_faces)
    watertight = bool(mesh.is_watertight) if len(faces) else False
    winding = bool(mesh.is_winding_consistent) if len(faces) else False
    valid_volume = watertight and winding and bool(mesh.is_volume)
    raw_volume = float(mesh.volume) if valid_volume else math.nan
    volume = raw_volume if valid_volume and math.isfinite(raw_volume) else None

    if len(vertices):
        weld_tolerance = max(scale * 1e-9, 1e-12)
        quantized = np.rint((vertices - vertices.min(axis=0)) / weld_tolerance).astype(np.int64)
        welded = int(len(np.unique(quantized, axis=0)))
        pca_axes, pca_extents = _stable_pca(vertices)
    else:
        welded = 0
        pca_axes, pca_extents = np.eye(3), np.zeros(3)

    support_threshold = (
        float(min_support_area)
        if min_support_area <= 1.0
        else float(min_support_area / max(total_area, _EPS))
    )
    regions = _planar_regions(
        faces,
        centroids,
        face_normals,
        face_areas,
        valid_faces,
        edge_faces,
        scale,
        plane_angle_deg,
        total_area,
    )
    sample_count = min(max_samples, max(512, len(faces) * 8)) if len(faces) else 0
    points, sample_normals, sampled_faces = _surface_samples(
        vertices, faces, face_areas, face_normals, sample_count, int(seed)
    )
    sampled_face_count = int(len(np.unique(sampled_faces)))

    candidates: list[PrimitiveCandidate] = [
        _plane_candidate(regions, total_area, support_threshold),
        _box_candidate(
            points,
            regions,
            total_area,
            volume,
            scale,
            support_threshold,
            sampled_face_count,
        ),
        _sphere_candidate(
            points,
            total_area,
            scale,
            support_threshold,
            sampled_face_count,
        ),
        _cylinder_candidate(
            points,
            sample_normals,
            pca_axes,
            total_area,
            scale,
            support_threshold,
            curved_normal_angle_deg,
            sampled_face_count,
        ),
        _capsule_candidate(
            points,
            pca_axes,
            total_area,
            scale,
            support_threshold,
            sampled_face_count,
        ),
    ]
    strongest = max(
        (candidate.confidence for candidate in candidates if candidate.accepted),
        default=0.0,
    )
    irregular_confidence = float(np.clip(1.0 - strongest, 0.0, 1.0))
    candidates.append(
        _candidate(
            "irregular",
            irregular_confidence,
            total_area,
            total_area,
            None,
            int(valid_faces.sum()),
            {},
            {"strongest_accepted_primitive_confidence": strongest},
            (),
        )
    )
    order = {
        name: index
        for index, name in enumerate(("plane", "box", "sphere", "cylinder", "capsule", "irregular"))
    }
    candidates.sort(key=lambda candidate: order[candidate.primitive])

    reported_regions = [
        region for region in regions if float(region["support_fraction"]) >= support_threshold
    ]
    suppressed_regions = len(regions) - len(reported_regions)
    if suppressed_regions:
        warnings.append(
            f"{suppressed_regions} planar region(s) below the configured support threshold omitted"
        )

    return GeometryAnalysis(
        vertices=len(vertices),
        faces=len(faces),
        bounds=bounds,
        diagonal=diagonal,
        area=area,
        volume=volume,
        watertight=watertight,
        winding_consistent=winding,
        connected_components=components,
        degenerate_faces=degenerate_faces,
        boundary_edges=boundary_edges,
        nonmanifold_edges=nonmanifold_edges,
        exported_vertices=len(vertices),
        position_welded_vertices=welded,
        pca_axes=pca_axes.tolist(),
        pca_extents=pca_extents.tolist(),
        planar_regions=reported_regions,
        primitive_candidates=candidates,
        seed=int(seed),
        warnings=warnings,
    )


def simplify_mesh(
    mesh: trimesh.Trimesh, *, target_faces: int, aggression: int = 7
) -> trimesh.Trimesh:
    """Return a simplified copy through the optional fast-simplification adapter."""
    if not isinstance(mesh, trimesh.Trimesh):
        raise TypeError("simplify_mesh requires one trimesh.Trimesh")
    if isinstance(target_faces, bool) or int(target_faces) != target_faces or target_faces < 4:
        raise ValueError("target_faces must be an integer of at least 4")
    if aggression < 0:
        raise ValueError("aggression must be non-negative")
    target_faces = int(target_faces)
    if target_faces >= len(mesh.faces):
        return mesh.copy()
    try:
        import fast_simplification  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "mesh simplification adapter unavailable: install the optional "
            "'fast-simplification' dependency"
        ) from exc
    reduction = 1.0 - target_faces / max(len(mesh.faces), 1)
    try:
        new_vertices, new_faces = fast_simplification.simplify(
            np.asarray(mesh.vertices, dtype=np.float64).copy(),
            np.asarray(mesh.faces, dtype=np.int32).copy(),
            target_reduction=reduction,
            agg=int(aggression),
        )
    except Exception as exc:  # adapter failures need an actionable boundary error
        raise RuntimeError(f"mesh simplification adapter failed: {exc}") from exc
    result = trimesh.Trimesh(vertices=new_vertices, faces=new_faces, process=False)
    if len(result.faces) > target_faces:
        raise RuntimeError(
            f"mesh simplification adapter stopped at {len(result.faces)} faces "
            f"(target {target_faces})"
        )
    return result
