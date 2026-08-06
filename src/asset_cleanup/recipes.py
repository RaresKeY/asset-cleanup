"""Built-in, fully expanded recipe presets.

Presets are authoring conveniences.  :func:`expand_preset` always returns an
independent :class:`~asset_cleanup.models.Recipe` containing concrete values,
so later preset changes cannot alter a recorded job or its canonical hash.
"""

from __future__ import annotations

from os import PathLike
from pathlib import Path
from typing import Any, Literal

from .models import (
    CollisionMode,
    CollisionSettings,
    GeometrySettings,
    Recipe,
    RecipeSettings,
    SimplifyMode,
    StageSettings,
    ValidationSettings,
)


def _quality_preset(
    name: Literal["close", "balanced", "distant"],
    error: float,
    silhouette_iou: float,
) -> Recipe:
    """Build one error-bounded visual-quality starting point."""

    return Recipe(
        name=name,
        description=f"{name.capitalize()}-view error-bounded visual cleanup",
        expanded_preset=name,
        seed=0,
        deterministic=True,
        stages=StageSettings(
            inspect=True,
            repair=True,
            detect_shapes=True,
            geometry=True,
            collision=True,
            validation=True,
            package=True,
        ),
        settings=RecipeSettings(
            geometry=GeometrySettings(
                simplify_mode=SimplifyMode.ERROR,
                max_error_fraction=error,
                preserve_boundaries=True,
                preserve_material_boundaries=True,
                preserve_uv_seams=True,
                preserve_normals=True,
                remove_degenerate=True,
                remove_unreferenced=True,
                fix_normals=True,
                merge_vertices=False,
                fill_holes=False,
                merge_tolerance_fraction=0.0,
                allow_attribute_loss=False,
                uv_policy="preserve",
                reconstruct_planes=False,
                reconstruct_curved_primitives=False,
            ),
            collision=CollisionSettings(
                mode=CollisionMode.AUTO,
                max_shapes=32,
                max_hulls=16,
                max_vertices_per_hull=64,
                max_concavity=0.05,
                surface_error_fraction=0.01,
                volume_error_fraction=0.05,
                min_shape_volume_fraction=0.001,
            ),
            validation=ValidationSettings(
                finite_values=True,
                structural=True,
                manifold_report=True,
                gltf_validator=True,
                compare_scene_inventory=True,
                compare_appearance=True,
                max_hausdorff_error_fraction=error,
                max_normal_error_degrees=15.0,
                min_silhouette_iou=silhouette_iou,
                fail_on_warning=False,
            ),
        ),
    )


_PRESETS: dict[str, Recipe] = {
    # These normalized residual limits are the design's documented starting
    # points: 0.001 D, 0.0025 D, and 0.01 D respectively.
    "close": _quality_preset("close", 0.001, 0.995),
    "balanced": _quality_preset("balanced", 0.0025, 0.98),
    "distant": _quality_preset("distant", 0.01, 0.95),
    "collision": Recipe(
        name="collision",
        description="Collision-authoring pass that preserves visual geometry",
        expanded_preset="collision",
        seed=0,
        deterministic=True,
        stages=StageSettings(
            inspect=True,
            repair=True,
            detect_shapes=True,
            geometry=False,
            collision=True,
            validation=True,
            package=True,
        ),
        settings=RecipeSettings(
            geometry=GeometrySettings(
                simplify_mode=SimplifyMode.PRESERVE,
                remove_degenerate=True,
                remove_unreferenced=True,
                fix_normals=True,
                merge_vertices=False,
                fill_holes=False,
                merge_tolerance_fraction=0.0,
                allow_attribute_loss=False,
                uv_policy="preserve",
                reconstruct_planes=False,
                reconstruct_curved_primitives=False,
            ),
            collision=CollisionSettings(
                mode=CollisionMode.AUTO,
                max_shapes=32,
                max_hulls=16,
                max_vertices_per_hull=64,
                max_concavity=0.05,
                surface_error_fraction=0.01,
                volume_error_fraction=0.05,
                min_shape_volume_fraction=0.001,
            ),
            validation=ValidationSettings(
                finite_values=True,
                structural=True,
                manifold_report=True,
                gltf_validator=True,
                compare_scene_inventory=True,
                compare_appearance=False,
                max_hausdorff_error_fraction=0.01,
                max_normal_error_degrees=15.0,
                min_silhouette_iou=0.98,
                fail_on_warning=False,
            ),
        ),
    ),
}


def preset_names() -> tuple[str, ...]:
    """Return built-in preset names in stable user-interface order."""

    return ("close", "balanced", "distant", "collision")


def expand_preset(preset_name: str, **overrides: Any) -> Recipe:
    """Return an independent expanded preset with optional top-level overrides.

    Overrides are validated by :class:`Recipe`; unknown keys are rejected.  To
    make nested policy changes, callers should expand first and then validate a
    modified canonical dictionary, keeping the resulting recipe self-contained.
    """

    normalized_name = preset_name.strip().lower()
    try:
        template = _PRESETS[normalized_name]
    except KeyError as error:
        choices = ", ".join(preset_names())
        raise ValueError(f"unknown preset {preset_name!r}; expected one of: {choices}") from error

    data = template.model_dump(mode="python")
    data.update(overrides)
    # A caller may rename the human-readable recipe, but not obscure which
    # built-in shorthand was expanded.
    data["expanded_preset"] = normalized_name
    return Recipe.model_validate(data)


def get_preset(preset_name: str) -> Recipe:
    """Compatibility-friendly synonym for :func:`expand_preset`."""

    return expand_preset(preset_name)


def load_recipe(path: str | PathLike[str]) -> Recipe:
    """Load a validated YAML or JSON recipe from *path*."""

    return Recipe.load(path)


def save_recipe(recipe: Recipe, path: str | PathLike[str]) -> Path:
    """Atomically save a fully expanded recipe and return its path."""

    return recipe.save(path)


__all__ = [
    "expand_preset",
    "get_preset",
    "load_recipe",
    "preset_names",
    "save_recipe",
]
