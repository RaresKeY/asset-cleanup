from __future__ import annotations

import json

import numpy as np
import pytest
import trimesh

from asset_cleanup.quality import (
    compare_boundaries,
    compare_surfaces,
    gate_boundary_preservation,
    sample_surface,
)


def test_surface_sampling_is_deterministic() -> None:
    mesh = trimesh.creation.icosphere(subdivisions=2)

    first = sample_surface(mesh, count=128, seed=42)
    second = sample_surface(mesh, count=128, seed=42)

    assert first.tolist() == second.tolist()


def test_identical_surface_comparison_is_near_zero() -> None:
    mesh = trimesh.creation.box()

    report = compare_surfaces(mesh, mesh.copy(), samples=1024, seed=0)

    assert report.approximate_hausdorff == pytest.approx(0.0)
    assert report.symmetric_chamfer == pytest.approx(0.0)
    assert report.maximum_normal_degrees == pytest.approx(0.0)
    assert report.symmetric_normal_degrees == pytest.approx(0.0)
    assert report.source_to_candidate_normal_degrees["p95_degrees"] == pytest.approx(0.0)

    # Report mappings are copy-on-read and always strict-JSON serializable.
    detached = report.source_to_candidate
    detached["mean"] = 123.0
    assert report.source_to_candidate["mean"] == pytest.approx(0.0)
    json.dumps(report.to_dict(), allow_nan=False)


def test_surface_comparison_reports_normalized_error() -> None:
    source = trimesh.creation.box()
    candidate = source.copy()
    candidate.apply_translation([0.1, 0.0, 0.0])

    report = compare_surfaces(source, candidate, samples=2048, seed=0)

    assert report.approximate_hausdorff > 0.0
    assert report.source_to_candidate["normalized_max"] > 0.0
    assert report.method.startswith("deterministic-area-samples")


def test_surface_comparison_detects_flipped_normals() -> None:
    source = trimesh.Trimesh(
        vertices=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0]]),
        faces=np.array([[0, 1, 2], [0, 2, 3]]),
        process=False,
    )
    candidate = trimesh.Trimesh(
        vertices=np.array(source.vertices, copy=True),
        faces=np.array(source.faces[:, ::-1], copy=True),
        process=False,
    )

    report = compare_surfaces(source, candidate, samples=512, seed=12)

    assert report.maximum_normal_degrees == pytest.approx(180.0)
    assert report.symmetric_normal_degrees == pytest.approx(180.0)
    assert report.source_to_candidate_normal_degrees["p95_degrees"] == pytest.approx(180.0)


def test_boundary_gate_accepts_preserved_open_boundary() -> None:
    source = trimesh.Trimesh(
        vertices=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0]]),
        faces=np.array([[0, 1, 2], [0, 2, 3]]),
        process=False,
    )

    comparison = compare_boundaries(source, source.copy(), samples=512, seed=4)
    gate = gate_boundary_preservation(
        comparison,
        max_distance_fraction=0.001,
        max_length_change_fraction=0.001,
    )

    assert comparison.status == "measured"
    assert comparison.source_edge_count == 4
    assert comparison.normalized_approximate_hausdorff == pytest.approx(0.0)
    assert gate.passed is True
    assert gate.reasons == ()
    json.dumps(gate.to_dict(), allow_nan=False)


def test_boundary_gate_rejects_moved_open_boundary() -> None:
    source = trimesh.Trimesh(
        vertices=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0]]),
        faces=np.array([[0, 1, 2], [0, 2, 3]]),
        process=False,
    )
    moved = source.copy()
    moved.apply_translation([0.2, 0.0, 0.0])

    comparison = compare_boundaries(source, moved, samples=4_096, seed=4)
    gate = gate_boundary_preservation(
        comparison,
        max_distance_fraction=0.02,
        max_length_change_fraction=0.001,
    )

    assert comparison.normalized_approximate_hausdorff is not None
    assert comparison.normalized_approximate_hausdorff > 0.02
    assert gate.passed is False
    assert any("distance" in reason for reason in gate.reasons)
