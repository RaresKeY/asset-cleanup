from __future__ import annotations

import json

import numpy as np
import pytest
import trimesh

from asset_cleanup.geometry import analyze_mesh, simplify_mesh


def _candidate(analysis, name):
    return next(
        candidate for candidate in analysis.primitive_candidates if candidate.primitive == name
    )


def test_cube_inventory_and_box_evidence() -> None:
    mesh = trimesh.creation.box(extents=(2.0, 3.0, 4.0))
    analysis = analyze_mesh(mesh, seed=11)

    assert analysis.vertices == 8
    assert analysis.faces == 12
    assert analysis.watertight
    assert analysis.winding_consistent
    assert analysis.connected_components == 1
    assert analysis.boundary_edges == 0
    assert analysis.nonmanifold_edges == 0
    assert analysis.volume == pytest.approx(24.0)
    assert len(analysis.planar_regions) == 6
    assert _candidate(analysis, "box").accepted
    assert _candidate(analysis, "box").metrics["surface_coverage"] > 0.95


def test_sphere_uses_radial_residual_and_coverage() -> None:
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=2.0)
    candidate = _candidate(analyze_mesh(mesh, seed=5), "sphere")

    assert candidate.accepted
    assert candidate.residual is not None and candidate.residual < 0.02
    assert candidate.metrics["angular_coverage"] >= 0.875
    assert candidate.parameters["radius"] == pytest.approx(2.0, rel=0.02)


def test_cylinder_reports_slice_and_angular_evidence() -> None:
    mesh = trimesh.creation.cylinder(radius=1.25, height=4.0, sections=64)
    candidate = _candidate(analyze_mesh(mesh, seed=23), "cylinder")

    assert candidate.accepted, candidate.rejection_reasons
    assert candidate.metrics["angular_coverage"] >= 0.8
    assert candidate.metrics["slice_radius_cv"] < 0.03
    assert candidate.metrics["radial_normal_alignment"] > 0.9
    assert candidate.parameters["radius"] == pytest.approx(1.25, rel=0.03)


def test_capsule_reports_surface_and_axis_coverage() -> None:
    mesh = trimesh.creation.capsule(radius=1.0, height=3.0)
    candidate = _candidate(analyze_mesh(mesh, seed=31), "capsule")

    assert candidate.accepted, candidate.rejection_reasons
    assert candidate.metrics["surface_support"] > 0.75
    assert candidate.metrics["angular_coverage"] > 0.8
    assert candidate.metrics["axial_coverage"] > 0.9


def test_connected_planar_growth_keeps_disconnected_regions_separate() -> None:
    first = trimesh.creation.box(extents=(2.0, 2.0, 0.01))
    second = first.copy()
    second.apply_translation((4.0, 0.0, 0.0))
    mesh = trimesh.util.concatenate((first, second))
    analysis = analyze_mesh(mesh, seed=2)

    upward = [
        region
        for region in analysis.planar_regions
        if abs(region["normal"][2]) > 0.99 and region["area"] > 3.9
    ]
    assert len(upward) == 4  # top and bottom on each disconnected slab
    assert analysis.connected_components == 2


def test_open_plane_reports_boundaries_and_no_volume() -> None:
    vertices = np.array([[-1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [1.0, 1.0, 0.0], [-1.0, 1.0, 0.0]])
    mesh = trimesh.Trimesh(vertices=vertices, faces=[[0, 1, 2], [0, 2, 3]], process=False)
    analysis = analyze_mesh(mesh)

    assert not analysis.watertight
    assert analysis.volume is None
    assert analysis.boundary_edges == 4
    assert len(analysis.planar_regions) == 1
    assert _candidate(analysis, "plane").accepted


def test_seeded_noisy_plane_is_deterministic() -> None:
    rng = np.random.default_rng(99)
    x, y = np.meshgrid(np.linspace(-1, 1, 12), np.linspace(-1, 1, 12))
    vertices = np.column_stack((x.ravel(), y.ravel(), rng.normal(0.0, 0.0002, x.size)))
    faces = []
    width = x.shape[1]
    for row in range(x.shape[0] - 1):
        for column in range(width - 1):
            corner = row * width + column
            faces.extend(
                (
                    (corner, corner + 1, corner + width + 1),
                    (corner, corner + width + 1, corner + width),
                )
            )
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)

    first = analyze_mesh(mesh, seed=123, max_samples=1000).to_dict()
    second = analyze_mesh(mesh, seed=123, max_samples=1000).to_dict()
    assert first == second
    assert _candidate(analyze_mesh(mesh, seed=123), "plane").accepted
    json.dumps(first, allow_nan=False)


def test_duplicate_positions_and_degenerate_faces_are_reported() -> None:
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 0], [2, 2, 2]], dtype=float)
    faces = np.array([[0, 1, 2], [3, 3, 1]])
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    analysis = analyze_mesh(mesh)

    assert analysis.exported_vertices == 5
    assert analysis.position_welded_vertices == 4
    assert analysis.degenerate_faces == 1
    # Connectivity is topological; coincident but unwelded positions do not
    # silently join source components.
    assert analysis.connected_components == 2


def test_nonmanifold_edge_is_reported() -> None:
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1]], dtype=float)
    faces = np.array([[0, 1, 2], [1, 0, 3], [0, 1, 4]])
    analysis = analyze_mesh(trimesh.Trimesh(vertices=vertices, faces=faces, process=False))
    assert analysis.nonmanifold_edges == 1


def test_empty_mesh_has_serializable_evidence() -> None:
    analysis = analyze_mesh(trimesh.Trimesh(vertices=[], faces=[], process=False))
    assert analysis.vertices == 0
    assert analysis.faces == 0
    assert analysis.bounds is None
    assert analysis.area == 0.0
    assert _candidate(analysis, "irregular").accepted
    json.dumps(analysis.to_dict(), allow_nan=False)


def test_simplify_noop_is_copy_on_write() -> None:
    mesh = trimesh.creation.icosphere(subdivisions=1)
    result = simplify_mesh(mesh, target_faces=len(mesh.faces))
    assert result is not mesh
    result.vertices[0] += 10.0
    assert not np.array_equal(result.vertices, mesh.vertices)


def test_scene_is_not_flattened() -> None:
    with pytest.raises(TypeError, match="scenes must be inventoried"):
        analyze_mesh(trimesh.Scene(trimesh.creation.box()))  # type: ignore[arg-type]
