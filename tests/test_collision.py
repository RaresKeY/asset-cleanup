from __future__ import annotations

import json

import numpy as np
import pytest
import trimesh

from asset_cleanup.collision import collision_scene, generate_collision


def test_auto_fits_cube_as_oriented_box() -> None:
    source = trimesh.creation.box(extents=[2.0, 1.0, 0.5])
    source.apply_transform(
        trimesh.transformations.rotation_matrix(np.deg2rad(31.0), [0.0, 0.0, 1.0])
    )
    source.apply_translation([3.0, -2.0, 0.75])

    result = generate_collision(source, body_type="dynamic", seed=4)

    assert [shape.type for shape in result.shapes] == ["box"]
    assert result.shapes[0].metrics["sampled_surface_coverage_estimate"] > 0.99
    assert result.body_compatibility == "confirmed"
    assert result.shapes[0].id == "COL_dynamic_BOX_000"


def test_auto_fits_sphere() -> None:
    source = trimesh.creation.icosphere(subdivisions=2, radius=1.25)
    source.apply_translation([0.4, -0.2, 1.0])

    result = generate_collision(source, body_type="dynamic", seed=7)

    assert result.shapes[0].type == "sphere"
    assert result.shapes[0].dimensions["radius"] == pytest.approx(1.25, rel=0.02)
    assert result.shapes[0].metrics["selection_score"] < 0.03


def test_auto_fits_rotated_cylinder() -> None:
    source = trimesh.creation.cylinder(radius=0.4, height=3.0, sections=48)
    source.apply_transform(
        trimesh.transformations.rotation_matrix(np.deg2rad(67.0), [1.0, 0.2, 0.0])
    )

    result = generate_collision(source, body_type="kinematic", seed=9)

    shape = result.shapes[0]
    assert shape.type == "cylinder"
    assert shape.dimensions["radius"] == pytest.approx(0.4, rel=0.03)
    assert shape.dimensions["height"] == pytest.approx(3.0, rel=0.03)


def test_concave_auto_uses_only_convex_safe_shapes() -> None:
    horizontal = trimesh.creation.box(extents=[3.0, 1.0, 1.0])
    vertical = trimesh.creation.box(extents=[1.0, 3.0, 1.0])
    horizontal.apply_translation([1.0, 0.0, 0.0])
    vertical.apply_translation([0.0, 1.0, 0.0])
    source = trimesh.util.concatenate([horizontal, vertical])

    result = generate_collision(
        source,
        body_type="dynamic",
        fit_policy="conservative",
        max_vertices_per_hull=12,
        seed=11,
    )

    assert result.shapes
    assert all(shape.type in {"convex", "coacd_hull"} for shape in result.shapes)
    assert all(shape.type != "trimesh" for shape in result.shapes)
    assert all(
        int(shape.dimensions["vertex_count"]) <= 12
        for shape in result.shapes
        if "vertex_count" in shape.dimensions
    )


@pytest.mark.parametrize("body_type", ["dynamic", "kinematic", "character", "movable"])
def test_trimesh_is_forbidden_for_moving_body_policies(body_type: str) -> None:
    with pytest.raises(ValueError, match="trimesh collision.*forbidden"):
        generate_collision(trimesh.creation.box(), mode="trimesh", body_type=body_type)


def test_static_trimesh_preserves_indexed_geometry() -> None:
    source = trimesh.creation.annulus(r_min=0.6, r_max=1.0, height=0.25)

    result = generate_collision(source, mode="static_trimesh", body_type="static")

    shape = result.shapes[0]
    assert shape.type == "trimesh"
    assert len(shape.vertices or []) == len(source.vertices)
    assert len(shape.faces or []) == len(source.faces)
    assert result.metrics["stop_reason"] == "explicit_static_trimesh"


def test_unspecified_body_is_serializable_and_unconfirmed() -> None:
    result = generate_collision(trimesh.creation.box(), mode="box")

    payload = result.to_dict()
    json.dumps(payload, allow_nan=False)
    assert payload["body"]["compatibility"] == "unconfirmed"
    assert payload["source"]["hash"]["value"] is None
    assert payload["source"]["hash"]["status"].startswith("placeholder")
    assert payload["shapes"][0]["locked"] is False
    assert payload["shapes"][0]["generated"] is True
    assert payload["shapes"][0]["source_regions"] == ["source_mesh:all"]
    assert any("unconfirmed" in warning for warning in payload["warnings"])


def test_recipe_enum_values_and_legacy_aliases_route() -> None:
    source = trimesh.creation.box()

    convex = generate_collision(source, mode="convex-hull", body_type="area", fit_policy="cover")
    compound = generate_collision(source, mode="compound", body_type="trigger", fit_policy="inside")
    legacy = generate_collision(source, mode="convex", fit_policy="conservative")

    assert convex.shapes[0].type == "convex"
    assert convex.settings["mode"] == "convex-hull"
    assert convex.settings["body_type"] == "area"
    assert convex.settings["fit_policy"] == "cover"
    assert compound.shapes
    assert compound.settings["mode"] == "compound"
    assert compound.settings["body_type"] == "area"
    assert compound.settings["fit_policy"] == "inside"
    assert legacy.settings["mode"] == "convex-hull"
    assert legacy.settings["fit_policy"] == "cover"


def test_seeded_generation_and_scene_names_are_deterministic() -> None:
    source = trimesh.creation.capsule(radius=0.35, height=1.8, count=[12, 12])

    first = generate_collision(source, mode="auto", body_type="character", seed=123)
    second = generate_collision(source, mode="auto", body_type="character", seed=123)

    assert first.to_dict() == second.to_dict()
    scene = collision_scene(first)
    assert sorted(scene.geometry) == sorted(shape.id for shape in first.shapes)
    for name, helper in scene.geometry.items():
        assert helper.metadata["collision_shape_id"] == name


def test_scene_graph_transforms_are_preserved() -> None:
    scene = trimesh.Scene()
    transform = np.eye(4)
    transform[:3, 3] = [12.0, -3.0, 2.5]
    scene.add_geometry(trimesh.creation.box(), transform=transform)

    shape = generate_collision(scene, mode="box", body_type="static").shapes[0]

    assert np.asarray(shape.transform)[:3, 3] == pytest.approx(transform[:3, 3])


def test_explicit_capsule_uses_finite_transform_and_dimensions() -> None:
    source = trimesh.creation.capsule(radius=0.3, height=1.4, count=[12, 12])

    shape = generate_collision(source, mode="capsule", body_type="character").shapes[0]

    assert shape.type == "capsule"
    assert np.isfinite(np.asarray(shape.transform)).all()
    assert shape.dimensions["radius"] > 0
    assert shape.dimensions["total_height"] >= 2.0 * shape.dimensions["radius"]
