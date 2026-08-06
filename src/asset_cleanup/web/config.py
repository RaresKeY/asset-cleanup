"""Configuration for the local web service."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from asset_cleanup.models import Recipe


class RecipePolicyError(ValueError):
    """A canonical recipe requests resources outside service-owned policy."""

    code = "recipe_exceeds_server_policy"

    def __init__(self, violations: tuple[dict[str, Any], ...]) -> None:
        self.violations = violations
        super().__init__(
            json.dumps(
                {"code": self.code, "violations": list(violations)},
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )

    @property
    def detail(self) -> dict[str, Any]:
        """Return the stable API error payload for this policy failure."""

        return {"code": self.code, "violations": list(self.violations)}


@dataclass(frozen=True, slots=True)
class RecipeCeilings:
    """Immutable service-owned resource and evidence/complexity bounds."""

    max_input_bytes: int = 1_073_741_824
    max_scene_nodes: int = 100_000
    max_meshes: int = 10_000
    max_vertices: int = 50_000_000
    max_triangles: int = 50_000_000
    max_texture_pixels: int = 268_435_456
    max_runtime_seconds: int = 3_600
    max_memory_bytes: int = 8_589_934_592
    deterministic_samples: int = 100_000
    max_collision_shapes: int = 32
    max_collision_hulls: int = 16
    max_vertices_per_hull: int = 64
    min_support_samples: int = 128
    min_support_area_fraction: float = 0.005
    min_cylinder_axial_bins: int = 5

    def __post_init__(self) -> None:
        values = self.maximums | self.minimums
        if any(not math.isfinite(float(value)) or value <= 0 for value in values.values()):
            raise ValueError("recipe policy limits must be positive")
        if self.min_support_area_fraction > 1.0:
            raise ValueError("min_support_area_fraction cannot exceed 1.0")

    @property
    def maximums(self) -> dict[str, int | float]:
        """Return stable canonical recipe paths and their upper bounds."""

        return {
            "settings.collision.max_hulls": self.max_collision_hulls,
            "settings.collision.max_shapes": self.max_collision_shapes,
            "settings.collision.max_vertices_per_hull": self.max_vertices_per_hull,
            "settings.inspection.deterministic_samples": self.deterministic_samples,
            "settings.limits.max_input_bytes": self.max_input_bytes,
            "settings.limits.max_memory_bytes": self.max_memory_bytes,
            "settings.limits.max_meshes": self.max_meshes,
            "settings.limits.max_runtime_seconds": self.max_runtime_seconds,
            "settings.limits.max_scene_nodes": self.max_scene_nodes,
            "settings.limits.max_texture_pixels": self.max_texture_pixels,
            "settings.limits.max_triangles": self.max_triangles,
            "settings.limits.max_vertices": self.max_vertices,
        }

    @property
    def minimums(self) -> dict[str, int | float]:
        """Return evidence/complexity settings enforced as policy lower bounds."""

        return {
            "settings.shape_detection.cylinder_min_axial_bins": (self.min_cylinder_axial_bins),
            "settings.shape_detection.min_support_area_fraction": (self.min_support_area_fraction),
            "settings.shape_detection.min_support_samples": self.min_support_samples,
        }

    def violations(self, recipe: Recipe) -> tuple[dict[str, Any], ...]:
        """Return deterministic policy violations without changing the recipe."""

        limits = recipe.settings.limits
        shape = recipe.settings.shape_detection
        inspection = recipe.settings.inspection
        collision = recipe.settings.collision
        requested: dict[str, int | float] = {
            "settings.collision.max_hulls": collision.max_hulls,
            "settings.collision.max_shapes": collision.max_shapes,
            "settings.collision.max_vertices_per_hull": collision.max_vertices_per_hull,
            "settings.inspection.deterministic_samples": inspection.deterministic_samples,
            "settings.limits.max_input_bytes": limits.max_input_bytes,
            "settings.limits.max_memory_bytes": limits.max_memory_bytes,
            "settings.limits.max_meshes": limits.max_meshes,
            "settings.limits.max_runtime_seconds": limits.max_runtime_seconds,
            "settings.limits.max_scene_nodes": limits.max_scene_nodes,
            "settings.limits.max_texture_pixels": limits.max_texture_pixels,
            "settings.limits.max_triangles": limits.max_triangles,
            "settings.limits.max_vertices": limits.max_vertices,
            "settings.shape_detection.cylinder_min_axial_bins": (shape.cylinder_min_axial_bins),
            "settings.shape_detection.min_support_area_fraction": (shape.min_support_area_fraction),
            "settings.shape_detection.min_support_samples": shape.min_support_samples,
        }
        violations: list[dict[str, Any]] = []
        for path, maximum in self.maximums.items():
            value = requested[path]
            if value > maximum:
                violations.append({"path": path, "requested": value, "maximum": maximum})
        for path, minimum in self.minimums.items():
            value = requested[path]
            if value < minimum:
                violations.append({"path": path, "requested": value, "minimum": minimum})
        return tuple(sorted(violations, key=lambda item: str(item["path"])))

    def enforce(self, recipe: Recipe) -> None:
        """Reject recipes outside policy while retaining their canonical identity."""

        violations = self.violations(recipe)
        if violations:
            raise RecipePolicyError(violations)

    def to_dict(self) -> dict[str, dict[str, int | float]]:
        """Expose effective policy without leaking implementation field names."""

        return {"maximums": self.maximums, "minimums": self.minimums}


@dataclass(frozen=True, slots=True)
class WebConfig:
    """Filesystem and resource policy for one service instance."""

    data_root: Path
    max_upload_bytes: int = 268_435_456
    max_request_body_bytes: int = 1024 * 1024
    multipart_overhead_bytes: int = 1024 * 1024
    upload_chunk_bytes: int = 1024 * 1024
    embedded_worker: bool = False
    worker_poll_seconds: float = 0.25
    event_poll_seconds: float = 0.25
    inspection_timeout_seconds: int = 120
    inspection_memory_bytes: int = 2_147_483_648
    max_inspection_report_bytes: int = 16_777_216
    max_evidence_json_bytes: int = 4_194_304
    max_texture_pixels: int = 268_435_456
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")
    recipe_ceilings: RecipeCeilings = field(default_factory=RecipeCeilings)

    def __post_init__(self) -> None:
        if self.max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")
        if self.max_request_body_bytes <= 0 or self.multipart_overhead_bytes <= 0:
            raise ValueError("request body limits must be positive")
        if not 64 * 1024 <= self.upload_chunk_bytes <= 16 * 1024 * 1024:
            raise ValueError("upload_chunk_bytes must be between 64 KiB and 16 MiB")
        if self.worker_poll_seconds <= 0 or self.event_poll_seconds <= 0:
            raise ValueError("poll intervals must be positive")
        if (
            self.inspection_timeout_seconds <= 0
            or self.inspection_memory_bytes <= 0
            or self.max_inspection_report_bytes <= 0
            or self.max_evidence_json_bytes <= 0
        ):
            raise ValueError("inspection resource limits must be positive")
        if self.max_texture_pixels <= 0:
            raise ValueError("max_texture_pixels must be positive")
        if not self.allowed_hosts or any(not host.strip() for host in self.allowed_hosts):
            raise ValueError("allowed_hosts must contain visible host names")

    @property
    def database_path(self) -> Path:
        return self.data_root / "state.sqlite3"

    @property
    def assets_root(self) -> Path:
        return self.data_root / "assets"

    @property
    def jobs_root(self) -> Path:
        return self.data_root / "jobs"

    @property
    def temporary_root(self) -> Path:
        return self.data_root / "tmp"

    @property
    def max_multipart_body_bytes(self) -> int:
        """Return the upload limit plus a bounded multipart envelope allowance."""

        return self.max_upload_bytes + self.multipart_overhead_bytes

    def initialize(self) -> None:
        """Create private service-owned directories."""

        self.data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path in (self.assets_root, self.jobs_root, self.temporary_root):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
