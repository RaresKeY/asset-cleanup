"""Contract tests for recipe validation, expansion, and persistence."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from asset_cleanup.models import (
    BodyType,
    CollisionMode,
    CollisionSettings,
    Recipe,
)
from asset_cleanup.recipes import expand_preset, preset_names


def test_yaml_and_json_roundtrip(tmp_path) -> None:
    """Both supported formats preserve a complete validated recipe."""

    expected = expand_preset("balanced", name="shipping-balanced", seed=42)
    yaml_path = expected.save(tmp_path / "recipe.yaml")
    json_path = expected.save(tmp_path / "recipe.json")

    assert Recipe.load(yaml_path) == expected
    assert Recipe.load(json_path) == expected
    assert Recipe.from_yaml(expected.to_yaml()) == expected
    assert Recipe.from_json(expected.to_json()) == expected
    assert (
        json.loads(json_path.read_text(encoding="utf-8"))["settings"]["geometry"][
            "max_error_fraction"
        ]
        == 0.0025
    )


def test_presets_expand_to_independent_complete_recipes() -> None:
    """Preset shorthand becomes concrete values and cannot leak mutations."""

    assert preset_names() == ("close", "balanced", "distant", "collision")
    expected_errors = {"close": 0.001, "balanced": 0.0025, "distant": 0.01}
    for name, expected_error in expected_errors.items():
        recipe = expand_preset(name)
        assert recipe.expanded_preset == name
        assert recipe.settings.geometry.max_error_fraction == expected_error
        assert recipe.settings.shape_detection.plane_normal_degrees == 7.5
        assert recipe.settings.shape_detection.curved_normal_degrees == 12.0
        assert recipe.settings.shape_detection.min_support_samples == 128
        assert recipe.settings.shape_detection.min_support_area_fraction == 0.005
        assert recipe.settings.shape_detection.cylinder_min_axial_bins == 5
        assert recipe.settings.shape_detection.cylinder_min_angular_degrees == 45.0

    first = expand_preset("close")
    first.description = "local change"
    assert expand_preset("close").description != "local change"

    collision = expand_preset("collision")
    assert collision.stages.geometry is False
    assert collision.stages.collision is True
    assert collision.settings.geometry.simplify_mode.value == "preserve"
    assert collision.settings.collision.mode is CollisionMode.AUTO


def test_canonical_hash_is_stable_across_mapping_and_file_formats(tmp_path) -> None:
    """Equivalent mappings and serializations have one canonical digest."""

    original = expand_preset("close", seed=7)
    reordered = {
        key: original.canonical_dict()[key] for key in reversed(tuple(original.canonical_dict()))
    }
    equivalent = Recipe.model_validate(reordered)
    loaded_yaml = Recipe.from_yaml(original.to_yaml())
    loaded_json = Recipe.from_json(original.to_json())

    assert len(original.canonical_hash()) == 64
    assert original.canonical_hash() == equivalent.canonical_hash()
    assert original.canonical_hash() == loaded_yaml.canonical_hash()
    assert original.canonical_hash() == loaded_json.canonical_hash()

    path = original.save(tmp_path / "stable.yml")
    assert Recipe.load(path).canonical_hash() == original.canonical_hash()


def test_unknown_keys_are_rejected_at_every_level() -> None:
    """Typos cannot silently fall back to defaults."""

    with pytest.raises(ValidationError, match="extra_forbidden"):
        Recipe.model_validate({"unexpected": True})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Recipe.model_validate({"settings": {"geometry": {"targte_ratio": 0.5}}})


@pytest.mark.parametrize(
    "body_type",
    [BodyType.DYNAMIC, BodyType.KINEMATIC, BodyType.CHARACTER],
)
def test_trimesh_collision_is_rejected_for_moving_bodies(body_type: BodyType) -> None:
    """Concave trimeshes are never accepted for moving bodies."""

    with pytest.raises(ValidationError, match="trimesh collision is unsafe"):
        Recipe(
            settings={
                "collision": CollisionSettings(
                    body_type=body_type,
                    mode=CollisionMode.TRIMESH,
                )
            }
        )


def test_static_trimesh_collision_is_explicitly_allowed() -> None:
    """Static bodies retain the documented simplified-trimesh escape hatch."""

    recipe = Recipe(
        settings={
            "collision": CollisionSettings(
                body_type=BodyType.STATIC,
                mode=CollisionMode.TRIMESH,
            )
        }
    )
    assert recipe.settings.collision.mode is CollisionMode.TRIMESH
