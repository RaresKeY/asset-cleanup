"""Strict, serializable configuration models for asset-cleanup recipes.

The models in this module are the public policy boundary shared by the CLI,
web API, manifests, and processing library.  They deliberately contain no
mesh-processing behavior.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceKind(StrEnum):
    """How the input should be classified before inspection."""

    AUTO = "auto"
    MESH = "mesh"
    TRELLIS_POST = "trellis-post"


class BodyType(StrEnum):
    """Runtime physics-body intent used to constrain collision generation."""

    UNSPECIFIED = "unspecified"
    NONE = "none"
    STATIC = "static"
    DYNAMIC = "dynamic"
    KINEMATIC = "kinematic"
    CHARACTER = "character"
    AREA = "area"


class SimplifyMode(StrEnum):
    """Stopping semantics for visual-mesh simplification."""

    PRESERVE = "preserve"
    TARGET = "target"
    ERROR = "error"
    HYBRID = "hybrid"


class ComponentPolicy(StrEnum):
    """Whether disconnected components are retained or size-filtered."""

    PRESERVE = "preserve"
    PRUNE_SMALL = "prune-small"


class CollisionMode(StrEnum):
    """Collision representation requested from the collision stage."""

    AUTO = "auto"
    NONE = "none"
    BOX = "box"
    SPHERE = "sphere"
    CAPSULE = "capsule"
    CYLINDER = "cylinder"
    COMPOUND = "compound"
    CONVEX_HULL = "convex-hull"
    COACD = "coacd"
    TRIMESH = "trimesh"


class FitPolicy(StrEnum):
    """Whether fitted collision favors coverage, balance, or containment."""

    COVER = "cover"
    BALANCED = "balanced"
    INSIDE = "inside"


class StrictModel(BaseModel):
    """Base for recipe records that rejects misspelled or future fields."""

    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=False,
        validate_assignment=True,
    )


class LimitsSettings(StrictModel):
    """Resource and input-complexity ceilings applied before expensive work."""

    max_input_bytes: int = Field(default=1_073_741_824, gt=0)
    max_scene_nodes: int = Field(default=100_000, gt=0)
    max_meshes: int = Field(default=10_000, gt=0)
    max_vertices: int = Field(default=50_000_000, gt=0)
    max_triangles: int = Field(default=50_000_000, gt=0)
    max_texture_pixels: int = Field(default=268_435_456, gt=0)
    max_runtime_seconds: int = Field(default=3_600, gt=0)
    max_memory_bytes: int = Field(default=8_589_934_592, gt=0)


class InspectionSettings(StrictModel):
    """Bounded inspection sampling policy."""

    deterministic_samples: int = Field(default=100_000, ge=128)


class ShapeDetectionSettings(StrictModel):
    """Normalized thresholds for deterministic primitive-region proposals."""

    enabled: bool = True
    planes: bool = True
    cylinders: bool = True
    spheres: bool = True
    boxes: bool = True
    plane_normal_degrees: float = Field(default=7.5, gt=0.0, le=90.0)
    curved_normal_degrees: float = Field(default=12.0, gt=0.0, le=90.0)
    min_support_samples: int = Field(default=128, ge=3)
    min_support_area_fraction: float = Field(default=0.005, gt=0.0, le=1.0)
    cylinder_min_axial_bins: int = Field(default=5, ge=2)
    cylinder_min_angular_degrees: float = Field(default=45.0, gt=0.0, le=360.0)


class GeometrySettings(StrictModel):
    """Visual cleanup, component, and simplification policy."""

    simplify_mode: SimplifyMode = SimplifyMode.PRESERVE
    target_faces: int | None = Field(default=None, gt=0)
    target_ratio: float | None = Field(default=None, gt=0.0, le=1.0)
    max_faces: int | None = Field(default=None, gt=0)
    max_error_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    preserve_boundaries: bool = True
    max_boundary_length_change_fraction: float = Field(default=0.05, ge=0.0, le=1.0)
    preserve_material_boundaries: bool = True
    preserve_uv_seams: bool = True
    preserve_normals: bool = True
    component_policy: ComponentPolicy = ComponentPolicy.PRESERVE
    min_component_area_fraction: float = Field(default=0.005, ge=0.0, le=1.0)
    remove_degenerate: bool = True
    remove_unreferenced: bool = True
    fix_normals: bool = True
    merge_vertices: bool = False
    fill_holes: bool = False
    merge_tolerance_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    allow_attribute_loss: bool = False
    uv_policy: Literal["preserve", "new", "rebake"] = "preserve"
    reconstruct_planes: bool = False
    reconstruct_curved_primitives: bool = False

    @model_validator(mode="after")
    def validate_simplification_contract(self) -> GeometrySettings:
        """Require each simplification mode to carry its stopping values."""

        has_target = self.target_faces is not None or self.target_ratio is not None
        has_error = self.max_error_fraction is not None
        if self.target_faces is not None and self.target_ratio is not None:
            raise ValueError("declare target_faces or target_ratio, not both")
        if self.simplify_mode is SimplifyMode.PRESERVE and (has_target or has_error):
            raise ValueError("preserve simplification cannot declare a target or error")
        if self.simplify_mode is SimplifyMode.TARGET and (not has_target or has_error):
            raise ValueError("target simplification requires one target and no max_error_fraction")
        if self.simplify_mode is SimplifyMode.ERROR and (not has_error or has_target):
            raise ValueError("error simplification requires only max_error_fraction")
        if self.simplify_mode is SimplifyMode.HYBRID and not (has_target and has_error):
            raise ValueError("hybrid simplification requires a target and max_error_fraction")
        if (
            self.component_policy is ComponentPolicy.PRUNE_SMALL
            and self.min_component_area_fraction <= 0
        ):
            raise ValueError("prune-small requires a positive min_component_area_fraction")
        if self.merge_vertices and self.merge_tolerance_fraction <= 0:
            raise ValueError("merge_vertices requires a positive merge_tolerance_fraction")
        reconstructs = self.reconstruct_planes or self.reconstruct_curved_primitives
        if reconstructs and self.uv_policy == "preserve":
            raise ValueError("primitive reconstruction requires uv_policy 'new' or 'rebake'")
        return self


class CollisionSettings(StrictModel):
    """Body-aware collision fitting and complexity policy."""

    body_type: BodyType = BodyType.UNSPECIFIED
    mode: CollisionMode = CollisionMode.AUTO
    fit_policy: FitPolicy = FitPolicy.BALANCED
    max_shapes: int = Field(default=32, gt=0)
    max_hulls: int = Field(default=16, gt=0)
    max_vertices_per_hull: int = Field(default=64, ge=4)
    max_concavity: float = Field(default=0.05, ge=0.0, le=1.0)
    surface_error_fraction: float = Field(default=0.01, ge=0.0, le=1.0)
    volume_error_fraction: float = Field(default=0.05, ge=0.0, le=1.0)
    min_shape_volume_fraction: float = Field(default=0.001, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_body_safety(self) -> CollisionSettings:
        """Keep collision safe even when settings are validated independently."""

        moving = {
            BodyType.DYNAMIC,
            BodyType.KINEMATIC,
            BodyType.CHARACTER,
        }
        if self.mode is CollisionMode.TRIMESH and self.body_type in moving:
            raise ValueError("trimesh collision is unsafe for moving bodies")
        if self.body_type is BodyType.NONE and self.mode is not CollisionMode.NONE:
            raise ValueError("body type 'none' requires collision mode 'none'")
        return self


class ValidationSettings(StrictModel):
    """Acceptance gates run against generated candidates and packages."""

    finite_values: bool = True
    structural: bool = True
    manifold_report: bool = True
    compare_geometry: bool = True
    gltf_validator: bool = True
    compare_scene_inventory: bool = True
    compare_appearance: bool = True
    max_hausdorff_error_fraction: float = Field(default=0.01, ge=0.0, le=1.0)
    max_normal_error_degrees: float = Field(default=15.0, ge=0.0, le=180.0)
    min_silhouette_iou: float = Field(default=0.98, ge=0.0, le=1.0)
    fail_on_warning: bool = False


class OutputSettings(StrictModel):
    """Controls neutral artifacts written after candidate validation."""

    format: Literal["glb", "gltf"] = "glb"
    include_manifest: bool = True
    include_reports: bool = True
    include_collision_sidecar: bool = True
    keep_intermediates: bool = True
    quantize: bool = False
    compress: bool = False


class StageSettings(StrictModel):
    """Explicit stage switches used to build the processing dependency graph."""

    inspect: bool = True
    repair: bool = True
    detect_shapes: bool = True
    geometry: bool = True
    collision: bool = True
    validation: bool = True
    package: bool = True


class RecipeSettings(StrictModel):
    """Fully expanded settings persisted with every processing job."""

    source_kind: SourceKind = SourceKind.AUTO
    limits: LimitsSettings = Field(default_factory=LimitsSettings)
    inspection: InspectionSettings = Field(default_factory=InspectionSettings)
    shape_detection: ShapeDetectionSettings = Field(default_factory=ShapeDetectionSettings)
    geometry: GeometrySettings = Field(default_factory=GeometrySettings)
    collision: CollisionSettings = Field(default_factory=CollisionSettings)
    validation: ValidationSettings = Field(default_factory=ValidationSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)


class Recipe(StrictModel):
    """Versioned, deterministic contract for one asset-cleanup run.

    ``expanded_preset`` records the shorthand that produced a recipe, while
    ``settings`` always stores the actual values.  Consequently, execution and
    hashing never depend on mutable preset definitions.
    """

    api_version: Literal["asset-cleanup/v1alpha1"] = "asset-cleanup/v1alpha1"
    kind: Literal["Recipe"] = "Recipe"
    name: str = Field(default="custom", min_length=1, max_length=128)
    description: str = Field(default="", max_length=2_000)
    expanded_preset: Literal["close", "balanced", "distant", "collision"] | None = None
    seed: int = Field(default=0, ge=0, le=4_294_967_295)
    deterministic: bool = True
    stages: StageSettings = Field(default_factory=StageSettings)
    settings: RecipeSettings = Field(default_factory=RecipeSettings)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        """Reject invisible recipe names while preserving user-visible text."""

        if not value.strip():
            raise ValueError("name must contain a visible character")
        return value

    @model_validator(mode="after")
    def validate_cross_stage_safety(self) -> Recipe:
        """Reject collision/body combinations that engines cannot safely use."""

        collision = self.settings.collision
        moving = {
            BodyType.DYNAMIC,
            BodyType.KINEMATIC,
            BodyType.CHARACTER,
        }
        if collision.mode is CollisionMode.TRIMESH and collision.body_type in moving:
            raise ValueError("trimesh collision is unsafe for moving bodies")
        if collision.body_type is BodyType.NONE and collision.mode is not CollisionMode.NONE:
            raise ValueError("body type 'none' requires collision mode 'none'")
        implicit_trimesh = (
            self.stages.collision
            and collision.mode is CollisionMode.TRIMESH
            and collision.body_type is BodyType.UNSPECIFIED
        )
        if implicit_trimesh:
            raise ValueError("trimesh collision requires an explicit static body type")
        if not self.stages.collision and collision.mode is not CollisionMode.NONE:
            raise ValueError("a disabled collision stage requires collision mode 'none'")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible, key-sorted representation of the recipe."""

        raw = self.model_dump(mode="json", exclude_none=False)
        return cast(dict[str, Any], _sort_mapping(raw))

    def canonical_json(self) -> str:
        """Return the unique compact JSON serialization used for hashing."""

        return json.dumps(
            self.canonical_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def canonical_hash(self) -> str:
        """Return the lowercase SHA-256 digest of :meth:`canonical_json`."""

        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize the fully expanded recipe as stable, readable JSON."""

        return (
            json.dumps(
                self.canonical_dict(),
                ensure_ascii=False,
                allow_nan=False,
                indent=indent,
                sort_keys=True,
            )
            + "\n"
        )

    def to_yaml(self) -> str:
        """Serialize the fully expanded recipe as safe, deterministic YAML."""

        return yaml.safe_dump(
            self.canonical_dict(),
            allow_unicode=True,
            sort_keys=True,
            default_flow_style=False,
        )

    @classmethod
    def from_json(cls, text: str | bytes) -> Recipe:
        """Validate a recipe from JSON text or UTF-8 bytes."""

        return cls.model_validate_json(text)

    @classmethod
    def from_yaml(cls, text: str | bytes) -> Recipe:
        """Validate a recipe from safe YAML text or UTF-8 bytes."""

        if isinstance(text, bytes):
            text = text.decode("utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, Mapping):
            raise ValueError("recipe document must contain a mapping")
        return cls.model_validate(data)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> Recipe:
        """Load and validate a ``.json``, ``.yaml``, or ``.yml`` recipe file."""

        recipe_path = Path(path)
        suffix = recipe_path.suffix.lower()
        text = recipe_path.read_text(encoding="utf-8")
        if suffix == ".json":
            return cls.from_json(text)
        if suffix in {".yaml", ".yml"}:
            return cls.from_yaml(text)
        raise ValueError(f"unsupported recipe extension: {suffix or '<none>'}")

    def save(self, path: str | os.PathLike[str]) -> Path:
        """Atomically save JSON or YAML selected by the destination suffix."""

        recipe_path = Path(path)
        suffix = recipe_path.suffix.lower()
        if suffix == ".json":
            text = self.to_json()
        elif suffix in {".yaml", ".yml"}:
            text = self.to_yaml()
        else:
            raise ValueError(f"unsupported recipe extension: {suffix or '<none>'}")

        recipe_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=recipe_path.parent,
            prefix=f".{recipe_path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(text)
            temporary_path = Path(temporary.name)
        try:
            temporary_path.replace(recipe_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        return recipe_path

    @classmethod
    def from_preset(cls, preset_name: str, **overrides: Any) -> Recipe:
        """Expand a named built-in preset, optionally applying top-level fields."""

        from .recipes import expand_preset

        return expand_preset(preset_name, **overrides)


def _sort_mapping(value: Any) -> Any:
    """Recursively sort mappings and reject non-finite numbers."""

    if isinstance(value, dict):
        return {key: _sort_mapping(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sort_mapping(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("canonical recipes cannot contain non-finite numbers")
    return value


__all__ = [
    "BodyType",
    "CollisionMode",
    "CollisionSettings",
    "ComponentPolicy",
    "FitPolicy",
    "GeometrySettings",
    "InspectionSettings",
    "LimitsSettings",
    "OutputSettings",
    "Recipe",
    "RecipeSettings",
    "ShapeDetectionSettings",
    "SimplifyMode",
    "SourceKind",
    "StageSettings",
    "ValidationSettings",
]
