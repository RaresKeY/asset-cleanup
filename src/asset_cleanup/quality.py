"""Deterministic, JSON-safe geometry-comparison evidence.

The routines in this module never mutate the meshes they inspect.  Surface and
boundary distances are sampled approximations rather than exact Hausdorff
distances; every report names the method and records the sample count.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from scipy.spatial import cKDTree  # type: ignore[import-untyped]

MetricValue = float | int | str | None
ComparisonStatus = Literal["measured", "no_samples"]
BoundaryStatus = Literal[
    "measured",
    "both_closed",
    "source_closed",
    "candidate_closed",
    "no_samples",
]


@dataclass(frozen=True, slots=True)
class _MetricSummary:
    """Immutable statistical summary with a fresh dictionary on every read."""

    status: ComparisonStatus
    sample_count: int
    mean: float | None
    rms: float | None
    p95: float | None
    p99: float | None
    maximum: float | None
    normalized_maximum: float | None = None

    def to_distance_dict(self) -> dict[str, MetricValue]:
        return {
            "status": self.status,
            "sample_count": self.sample_count,
            "mean": self.mean,
            "rms": self.rms,
            "p95": self.p95,
            "p99": self.p99,
            "max": self.maximum,
            "normalized_max": self.normalized_maximum,
        }

    def to_angle_dict(self) -> dict[str, MetricValue]:
        return {
            "status": self.status,
            "sample_count": self.sample_count,
            "mean_degrees": self.mean,
            "rms_degrees": self.rms,
            "p95_degrees": self.p95,
            "p99_degrees": self.p99,
            "max_degrees": self.maximum,
        }


@dataclass(frozen=True, slots=True)
class DistanceSummary:
    """Approximate bidirectional surface-distance and normal-angle evidence."""

    method: str
    status: ComparisonStatus
    samples_per_direction: int
    normalization_diagonal: float | None
    _source_to_candidate: _MetricSummary
    _candidate_to_source: _MetricSummary
    symmetric_chamfer: float | None
    approximate_hausdorff: float | None
    _source_to_candidate_normal_degrees: _MetricSummary
    _candidate_to_source_normal_degrees: _MetricSummary
    symmetric_normal_degrees: float | None
    maximum_normal_degrees: float | None

    @property
    def source_to_candidate(self) -> dict[str, MetricValue]:
        """Return a detached copy of the forward distance statistics."""

        return self._source_to_candidate.to_distance_dict()

    @property
    def candidate_to_source(self) -> dict[str, MetricValue]:
        """Return a detached copy of the reverse distance statistics."""

        return self._candidate_to_source.to_distance_dict()

    @property
    def source_to_candidate_normal_degrees(self) -> dict[str, MetricValue]:
        """Return a detached copy of forward, orientation-sensitive angles."""

        return self._source_to_candidate_normal_degrees.to_angle_dict()

    @property
    def candidate_to_source_normal_degrees(self) -> dict[str, MetricValue]:
        """Return a detached copy of reverse, orientation-sensitive angles."""

        return self._candidate_to_source_normal_degrees.to_angle_dict()

    def to_dict(self) -> dict[str, Any]:
        """Serialize into a newly allocated, JSON-safe dictionary."""

        return {
            "method": self.method,
            "status": self.status,
            "samples_per_direction": self.samples_per_direction,
            "normalization_diagonal": self.normalization_diagonal,
            "source_to_candidate": self.source_to_candidate,
            "candidate_to_source": self.candidate_to_source,
            "symmetric_chamfer": self.symmetric_chamfer,
            "approximate_hausdorff": self.approximate_hausdorff,
            "source_to_candidate_normal_degrees": (self.source_to_candidate_normal_degrees),
            "candidate_to_source_normal_degrees": (self.candidate_to_source_normal_degrees),
            "symmetric_normal_degrees": self.symmetric_normal_degrees,
            "maximum_normal_degrees": self.maximum_normal_degrees,
        }


@dataclass(frozen=True, slots=True)
class BoundarySummary:
    """Approximate bidirectional evidence for preservation of open boundaries."""

    method: str
    status: BoundaryStatus
    samples_per_direction: int
    normalization_diagonal: float | None
    source_edge_count: int
    candidate_edge_count: int
    source_component_count: int
    candidate_component_count: int
    source_total_length: float
    candidate_total_length: float
    relative_length_change: float | None
    _source_to_candidate: _MetricSummary
    _candidate_to_source: _MetricSummary
    symmetric_chamfer: float | None
    approximate_hausdorff: float | None
    normalized_approximate_hausdorff: float | None

    @property
    def source_to_candidate(self) -> dict[str, MetricValue]:
        """Return a detached copy of the forward boundary statistics."""

        return self._source_to_candidate.to_distance_dict()

    @property
    def candidate_to_source(self) -> dict[str, MetricValue]:
        """Return a detached copy of the reverse boundary statistics."""

        return self._candidate_to_source.to_distance_dict()

    def to_dict(self) -> dict[str, Any]:
        """Serialize into a newly allocated, JSON-safe dictionary."""

        return {
            "method": self.method,
            "status": self.status,
            "samples_per_direction": self.samples_per_direction,
            "normalization_diagonal": self.normalization_diagonal,
            "source_edge_count": self.source_edge_count,
            "candidate_edge_count": self.candidate_edge_count,
            "source_component_count": self.source_component_count,
            "candidate_component_count": self.candidate_component_count,
            "source_total_length": self.source_total_length,
            "candidate_total_length": self.candidate_total_length,
            "relative_length_change": self.relative_length_change,
            "source_to_candidate": self.source_to_candidate,
            "candidate_to_source": self.candidate_to_source,
            "symmetric_chamfer": self.symmetric_chamfer,
            "approximate_hausdorff": self.approximate_hausdorff,
            "normalized_approximate_hausdorff": (self.normalized_approximate_hausdorff),
        }


@dataclass(frozen=True, slots=True)
class BoundaryGate:
    """Policy result derived from immutable boundary-comparison evidence."""

    passed: bool
    max_distance_fraction: float
    max_length_change_fraction: float
    require_component_count: bool
    reasons: tuple[str, ...]
    comparison: BoundarySummary

    def to_dict(self) -> dict[str, Any]:
        """Serialize the gate and a detached copy of its evidence."""

        return {
            "ran": True,
            "passed": self.passed,
            "max_distance_fraction": self.max_distance_fraction,
            "max_length_change_fraction": self.max_length_change_fraction,
            "require_component_count": self.require_component_count,
            "reasons": list(self.reasons),
            "comparison": self.comparison.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class _SurfaceSamples:
    points: np.ndarray
    normals: np.ndarray


@dataclass(frozen=True, slots=True)
class _BoundaryInventory:
    segments: np.ndarray
    edge_count: int
    component_count: int
    total_length: float


def _derived_seed(seed: int, label: str) -> int:
    digest = hashlib.sha256(f"{seed}:{label}".encode()).digest()
    return int.from_bytes(digest[:8], "little", signed=False)


def _finite(value: float) -> float | None:
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _normalization_diagonal(mesh: Any) -> float | None:
    vertices = np.array(mesh.vertices, dtype=np.float64, copy=True)
    finite = vertices[np.all(np.isfinite(vertices), axis=1)]
    if len(finite) == 0:
        return None
    diagonal = float(np.linalg.norm(np.ptp(finite, axis=0)))
    return _finite(diagonal)


def _surface_samples(mesh: Any, *, count: int, seed: int, label: str) -> _SurfaceSamples:
    if count <= 0:
        raise ValueError("sample count must be positive")

    faces = np.array(mesh.faces, dtype=np.int64, copy=True)
    vertices = np.array(mesh.vertices, dtype=np.float64, copy=True)
    if len(faces) == 0:
        empty = np.empty((0, 3), dtype=np.float64)
        return _SurfaceSamples(points=empty.copy(), normals=empty.copy())

    triangles = vertices[faces]
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    double_areas = np.linalg.norm(cross, axis=1)
    finite_triangles = np.all(np.isfinite(triangles), axis=(1, 2))
    valid = finite_triangles & np.isfinite(double_areas) & (double_areas > np.finfo(np.float64).eps)
    if not np.any(valid):
        empty = np.empty((0, 3), dtype=np.float64)
        return _SurfaceSamples(points=empty.copy(), normals=empty.copy())

    indices = np.flatnonzero(valid)
    weights = double_areas[valid] / double_areas[valid].sum()
    rng = np.random.default_rng(_derived_seed(seed, label))
    selected = rng.choice(indices, size=count, replace=True, p=weights)
    chosen = triangles[selected]
    uv = rng.random((len(selected), 2))
    reflected = uv.sum(axis=1) > 1.0
    uv[reflected] = 1.0 - uv[reflected]
    points = (
        chosen[:, 0]
        + uv[:, :1] * (chosen[:, 1] - chosen[:, 0])
        + uv[:, 1:] * (chosen[:, 2] - chosen[:, 0])
    )
    normals = cross[selected] / double_areas[selected, None]
    return _SurfaceSamples(
        points=np.array(points, dtype=np.float64, copy=True),
        normals=np.array(normals, dtype=np.float64, copy=True),
    )


def sample_surface(mesh: Any, *, count: int, seed: int, label: str = "mesh") -> np.ndarray:
    """Area-weight sample triangle interiors with a stable RNG seed.

    The returned array owns its storage, so callers cannot mutate mesh-backed
    data through it.
    """

    return _surface_samples(mesh, count=count, seed=seed, label=label).points.copy()


def _summary(values: np.ndarray, normalization: float | None = None) -> _MetricSummary:
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if len(finite) == 0:
        return _MetricSummary("no_samples", 0, None, None, None, None, None, None)

    maximum = _finite(float(np.max(finite)))
    normalized = (
        _finite(maximum / normalization)
        if maximum is not None and normalization is not None and normalization > 0.0
        else None
    )
    return _MetricSummary(
        status="measured",
        sample_count=len(finite),
        mean=_finite(float(np.mean(finite))),
        rms=_finite(float(np.sqrt(np.mean(np.square(finite))))),
        p95=_finite(float(np.quantile(finite, 0.95))),
        p99=_finite(float(np.quantile(finite, 0.99))),
        maximum=maximum,
        normalized_maximum=normalized,
    )


def _pair_mean(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return _finite((left + right) * 0.5)


def _pair_max(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return _finite(max(left, right))


def _normal_angles(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    dots = np.einsum("ij,ij->i", left, right)
    return np.asarray(np.degrees(np.arccos(np.clip(dots, -1.0, 1.0))), dtype=np.float64)


def compare_surfaces(
    source: Any, candidate: Any, *, samples: int = 4_096, seed: int = 0
) -> DistanceSummary:
    """Compare surfaces with bidirectional sampled distance and normal evidence.

    Normal angles are orientation-sensitive: a reversed face reports 180
    degrees, not zero.  Nearest sampled points supply the corresponding normals
    in each direction.  This remains an approximation to point-to-triangle
    distance and continuous normal deviation.
    """

    if samples <= 0:
        raise ValueError("samples must be positive")

    source_vertices = np.array(source.vertices, dtype=np.float64, copy=True)
    candidate_vertices = np.array(candidate.vertices, dtype=np.float64, copy=True)
    source_faces = np.array(source.faces, dtype=np.int64, copy=True)
    candidate_faces = np.array(candidate.faces, dtype=np.int64, copy=True)
    exactly_equal = np.array_equal(source_vertices, candidate_vertices) and np.array_equal(
        source_faces, candidate_faces
    )

    source_samples = _surface_samples(source, count=samples, seed=seed, label="source")
    candidate_samples = _surface_samples(candidate, count=samples, seed=seed, label="candidate")
    if exactly_equal and len(source_samples.points) > 0:
        source_distance = np.zeros(len(source_samples.points), dtype=np.float64)
        candidate_distance = np.zeros(len(candidate_samples.points), dtype=np.float64)
        source_angles = np.zeros(len(source_samples.points), dtype=np.float64)
        candidate_angles = np.zeros(len(candidate_samples.points), dtype=np.float64)
    elif len(source_samples.points) == 0 or len(candidate_samples.points) == 0:
        source_distance = np.empty(0, dtype=np.float64)
        candidate_distance = np.empty(0, dtype=np.float64)
        source_angles = np.empty(0, dtype=np.float64)
        candidate_angles = np.empty(0, dtype=np.float64)
    else:
        candidate_tree = cKDTree(candidate_samples.points)
        source_tree = cKDTree(source_samples.points)
        source_distance, source_indices = candidate_tree.query(source_samples.points, workers=1)
        candidate_distance, candidate_indices = source_tree.query(
            candidate_samples.points, workers=1
        )
        source_angles = _normal_angles(
            source_samples.normals, candidate_samples.normals[np.asarray(source_indices)]
        )
        candidate_angles = _normal_angles(
            candidate_samples.normals, source_samples.normals[np.asarray(candidate_indices)]
        )

    diagonal = _normalization_diagonal(source)
    forward = _summary(np.asarray(source_distance), diagonal)
    reverse = _summary(np.asarray(candidate_distance), diagonal)
    forward_normal = _summary(source_angles)
    reverse_normal = _summary(candidate_angles)
    status: ComparisonStatus = (
        "measured"
        if forward.status == "measured" and reverse.status == "measured"
        else "no_samples"
    )
    return DistanceSummary(
        method="deterministic-area-samples/nearest-sample-distance-normal-v2",
        status=status,
        samples_per_direction=samples,
        normalization_diagonal=diagonal,
        _source_to_candidate=forward,
        _candidate_to_source=reverse,
        symmetric_chamfer=_pair_mean(forward.mean, reverse.mean),
        approximate_hausdorff=_pair_max(forward.maximum, reverse.maximum),
        _source_to_candidate_normal_degrees=forward_normal,
        _candidate_to_source_normal_degrees=reverse_normal,
        symmetric_normal_degrees=_pair_mean(forward_normal.mean, reverse_normal.mean),
        maximum_normal_degrees=_pair_max(forward_normal.maximum, reverse_normal.maximum),
    )


def _boundary_inventory(mesh: Any) -> _BoundaryInventory:
    faces = np.array(mesh.faces, dtype=np.int64, copy=True)
    vertices = np.array(mesh.vertices, dtype=np.float64, copy=True)
    owners: dict[tuple[int, int], int] = {}
    for face in faces:
        for raw_left, raw_right in (
            (face[0], face[1]),
            (face[1], face[2]),
            (face[2], face[0]),
        ):
            left, right = sorted((int(raw_left), int(raw_right)))
            owners[(left, right)] = owners.get((left, right), 0) + 1

    boundary_edges = sorted(edge for edge, count in owners.items() if count == 1)
    if not boundary_edges:
        return _BoundaryInventory(np.empty((0, 2, 3), dtype=np.float64), 0, 0, 0.0)

    edge_array = np.asarray(boundary_edges, dtype=np.int64)
    segments = vertices[edge_array]
    lengths = np.linalg.norm(segments[:, 1] - segments[:, 0], axis=1)
    valid = np.all(np.isfinite(segments), axis=(1, 2)) & np.isfinite(lengths) & (lengths > 0.0)
    segments = np.array(segments[valid], dtype=np.float64, copy=True)
    valid_edges = edge_array[valid]
    lengths = lengths[valid]

    adjacency: dict[int, set[int]] = {}
    for left, right in valid_edges:
        adjacency.setdefault(int(left), set()).add(int(right))
        adjacency.setdefault(int(right), set()).add(int(left))
    remaining = set(adjacency)
    components = 0
    while remaining:
        components += 1
        stack = [min(remaining)]
        remaining.remove(stack[0])
        while stack:
            current = stack.pop()
            for neighbor in sorted(adjacency[current]):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)

    total_length = _finite(float(np.sum(lengths))) or 0.0
    return _BoundaryInventory(
        segments=segments,
        edge_count=len(segments),
        component_count=components,
        total_length=total_length,
    )


def _sample_boundary(
    inventory: _BoundaryInventory, *, count: int, seed: int, label: str
) -> np.ndarray:
    if count <= 0:
        raise ValueError("sample count must be positive")
    if inventory.edge_count == 0 or inventory.total_length <= 0.0:
        return np.empty((0, 3), dtype=np.float64)
    segments = inventory.segments
    lengths = np.linalg.norm(segments[:, 1] - segments[:, 0], axis=1)
    rng = np.random.default_rng(_derived_seed(seed, label))
    selected = rng.choice(
        len(segments), size=count, replace=True, p=lengths / inventory.total_length
    )
    amount = rng.random((count, 1))
    points = segments[selected, 0] + amount * (segments[selected, 1] - segments[selected, 0])
    return np.array(points, dtype=np.float64, copy=True)


def _same_boundaries(left: _BoundaryInventory, right: _BoundaryInventory) -> bool:
    if left.edge_count != right.edge_count:
        return False

    def signature(segments: np.ndarray) -> list[tuple[tuple[float, ...], tuple[float, ...]]]:
        result: list[tuple[tuple[float, ...], tuple[float, ...]]] = []
        for segment in segments:
            first = tuple(float(value) for value in segment[0])
            second = tuple(float(value) for value in segment[1])
            result.append((first, second) if first <= second else (second, first))
        return sorted(result)

    return signature(left.segments) == signature(right.segments)


def compare_boundaries(
    source: Any, candidate: Any, *, samples: int = 2_048, seed: int = 0
) -> BoundarySummary:
    """Compare open-boundary geometry without requiring identical tessellation."""

    if samples <= 0:
        raise ValueError("samples must be positive")
    source_inventory = _boundary_inventory(source)
    candidate_inventory = _boundary_inventory(candidate)
    diagonal = _normalization_diagonal(source)

    if source_inventory.edge_count == 0 and candidate_inventory.edge_count == 0:
        status: BoundaryStatus = "both_closed"
    elif source_inventory.edge_count == 0:
        status = "source_closed"
    elif candidate_inventory.edge_count == 0:
        status = "candidate_closed"
    else:
        status = "measured"

    source_points = _sample_boundary(
        source_inventory, count=samples, seed=seed, label="source-boundary"
    )
    candidate_points = _sample_boundary(
        candidate_inventory, count=samples, seed=seed, label="candidate-boundary"
    )
    if status == "measured" and _same_boundaries(source_inventory, candidate_inventory):
        source_distance = np.zeros(len(source_points), dtype=np.float64)
        candidate_distance = np.zeros(len(candidate_points), dtype=np.float64)
    elif status == "measured" and len(source_points) and len(candidate_points):
        candidate_tree = cKDTree(candidate_points)
        source_tree = cKDTree(source_points)
        source_distance = candidate_tree.query(source_points, workers=1)[0]
        candidate_distance = source_tree.query(candidate_points, workers=1)[0]
    else:
        source_distance = np.empty(0, dtype=np.float64)
        candidate_distance = np.empty(0, dtype=np.float64)
        if status == "measured":
            status = "no_samples"

    forward = _summary(np.asarray(source_distance), diagonal)
    reverse = _summary(np.asarray(candidate_distance), diagonal)
    hausdorff = _pair_max(forward.maximum, reverse.maximum)
    normalized_hausdorff = (
        _finite(hausdorff / diagonal)
        if hausdorff is not None and diagonal is not None and diagonal > 0.0
        else None
    )
    relative_length_change = (
        _finite(
            abs(candidate_inventory.total_length - source_inventory.total_length)
            / source_inventory.total_length
        )
        if source_inventory.total_length > 0.0
        else (0.0 if candidate_inventory.total_length == 0.0 else None)
    )
    return BoundarySummary(
        method="deterministic-length-samples/nearest-sample-boundary-v1",
        status=status,
        samples_per_direction=samples,
        normalization_diagonal=diagonal,
        source_edge_count=source_inventory.edge_count,
        candidate_edge_count=candidate_inventory.edge_count,
        source_component_count=source_inventory.component_count,
        candidate_component_count=candidate_inventory.component_count,
        source_total_length=source_inventory.total_length,
        candidate_total_length=candidate_inventory.total_length,
        relative_length_change=relative_length_change,
        _source_to_candidate=forward,
        _candidate_to_source=reverse,
        symmetric_chamfer=_pair_mean(forward.mean, reverse.mean),
        approximate_hausdorff=hausdorff,
        normalized_approximate_hausdorff=normalized_hausdorff,
    )


def gate_boundary_preservation(
    comparison: BoundarySummary,
    *,
    max_distance_fraction: float,
    max_length_change_fraction: float = 0.05,
    require_component_count: bool = True,
) -> BoundaryGate:
    """Apply explicit policy thresholds to boundary-comparison evidence.

    A pair of closed meshes passes because there is no source opening to
    preserve.  Created or removed boundaries, unavailable measurements, excess
    displacement, excess length change, or a component-count change fail.
    """

    if not math.isfinite(max_distance_fraction) or max_distance_fraction < 0.0:
        raise ValueError("max_distance_fraction must be finite and non-negative")
    if not math.isfinite(max_length_change_fraction) or max_length_change_fraction < 0.0:
        raise ValueError("max_length_change_fraction must be finite and non-negative")

    reasons: list[str] = []
    if comparison.status == "both_closed":
        pass
    elif comparison.status != "measured":
        reasons.append(f"boundary comparison status is {comparison.status}")
    else:
        distance = comparison.normalized_approximate_hausdorff
        if distance is None:
            reasons.append("normalized boundary distance is unavailable")
        elif distance > max_distance_fraction:
            reasons.append(
                f"normalized boundary distance {distance:.9g} exceeds {max_distance_fraction:.9g}"
            )
        length_change = comparison.relative_length_change
        if length_change is None:
            reasons.append("relative boundary-length change is unavailable")
        elif length_change > max_length_change_fraction:
            reasons.append(
                f"relative boundary-length change {length_change:.9g} exceeds "
                f"{max_length_change_fraction:.9g}"
            )
        if (
            require_component_count
            and comparison.source_component_count != comparison.candidate_component_count
        ):
            reasons.append(
                "boundary component count changed from "
                f"{comparison.source_component_count} to "
                f"{comparison.candidate_component_count}"
            )

    return BoundaryGate(
        passed=not reasons,
        max_distance_fraction=float(max_distance_fraction),
        max_length_change_fraction=float(max_length_change_fraction),
        require_component_count=require_component_count,
        reasons=tuple(reasons),
        comparison=comparison,
    )
