from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
import trimesh

from asset_cleanup.errors import InputRejectedError
from asset_cleanup.source import (
    LoadLimits,
    load_scene,
    probe_source,
    referenced_files,
    validate_external_references,
)

_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02"
)


def test_probe_uses_glb_content_not_extension(tmp_path: Path) -> None:
    path = tmp_path / "ordinary-output.bin"
    path.write_bytes(trimesh.creation.box().export(file_type="glb"))

    probe = probe_source(path)

    assert probe.supported
    assert probe.format == "glb"
    assert probe.source_kind == "mesh"


def test_point_only_ply_is_not_a_triangle_mesh(tmp_path: Path) -> None:
    path = tmp_path / "points.ply"
    path.write_text(
        "ply\nformat ascii 1.0\nelement vertex 1\n"
        "property float x\nproperty float y\nproperty float z\n"
        "end_header\n0 0 0\n",
        encoding="ascii",
    )

    probe = probe_source(path)

    assert not probe.supported
    assert probe.source_kind == "point-cloud"
    assert "no polygon face" in (probe.reason or "")


def test_unknown_post_bin_is_never_deserialized(tmp_path: Path) -> None:
    path = tmp_path / "post.bin"
    path.write_bytes(b"not-a-safe-universal-format")

    probe = probe_source(path)

    assert not probe.supported
    assert probe.source_kind == "trellis-post"
    with pytest.raises(InputRejectedError, match="replay provider"):
        load_scene(path)


def test_gltf_traversal_uri_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "asset.gltf"
    path.write_text(
        json.dumps(
            {
                "asset": {"version": "2.0"},
                "buffers": [{"uri": "../escape.bin", "byteLength": 4}],
                "scenes": [{"nodes": []}],
                "scene": 0,
            }
        ),
        encoding="utf-8",
    )
    probe = probe_source(path)

    with pytest.raises(InputRejectedError, match="escapes"):
        validate_external_references(path, probe, LoadLimits())


def test_gltf_references_are_discovered_as_confined_members(tmp_path: Path) -> None:
    (tmp_path / "textures").mkdir()
    (tmp_path / "mesh.bin").write_bytes(b"mesh")
    (tmp_path / "textures" / "albedo.png").write_bytes(_PNG_1X1)
    path = tmp_path / "asset.gltf"
    path.write_text(
        json.dumps(
            {
                "asset": {"version": "2.0"},
                "buffers": [{"uri": "mesh.bin", "byteLength": 4}],
                "images": [{"uri": "textures/albedo.png"}],
            }
        ),
        encoding="utf-8",
    )

    members = referenced_files(path)

    assert [relative for relative, _ in members] == ["mesh.bin", "textures/albedo.png"]


def test_external_texture_dimensions_are_bounded_before_parser(tmp_path: Path) -> None:
    (tmp_path / "mesh.bin").write_bytes(b"mesh")
    oversized = bytearray(_PNG_1X1)
    oversized[16:24] = struct.pack(">II", 100, 100)
    (tmp_path / "albedo.png").write_bytes(oversized)
    path = tmp_path / "asset.gltf"
    path.write_text(
        json.dumps(
            {
                "asset": {"version": "2.0"},
                "buffers": [{"uri": "mesh.bin", "byteLength": 4}],
                "images": [{"uri": "albedo.png"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(InputRejectedError, match="max_texture_pixels"):
        referenced_files(path, limits=LoadLimits(max_texture_pixels=9_999))


def test_obj_mtl_texture_traversal_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "asset.obj"
    path.write_text("mtllib material.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
    (tmp_path / "material.mtl").write_text(
        "newmtl material\nmap_Kd ../secret.png\n", encoding="utf-8"
    )

    with pytest.raises(InputRejectedError, match="escapes"):
        referenced_files(path)


def test_obj_dependency_after_header_window_is_still_confined(tmp_path: Path) -> None:
    path = tmp_path / "late.obj"
    path.write_text(
        ("# padding\n" * 140_000)
        + "mtllib ../outside.mtl\n"
        + "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n",
        encoding="utf-8",
    )

    with pytest.raises(InputRejectedError, match="escapes"):
        referenced_files(path)


def test_gltf_declared_geometry_is_rejected_before_mesh_parser(tmp_path: Path) -> None:
    path = tmp_path / "oversized.gltf"
    path.write_text(
        json.dumps(
            {
                "asset": {"version": "2.0"},
                "buffers": [{"uri": "data:application/octet-stream;base64,AA==", "byteLength": 1}],
                "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 1}],
                "accessors": [
                    {
                        "bufferView": 0,
                        "componentType": 5126,
                        "count": 999,
                        "type": "VEC3",
                    }
                ],
                "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(InputRejectedError, match="declared geometry"):
        load_scene(path, LoadLimits(max_vertices=10))


def test_load_scene_preserves_multiple_geometry_definitions(tmp_path: Path) -> None:
    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.box(), geom_name="box", node_name="box-node")
    scene.add_geometry(
        trimesh.creation.icosphere(subdivisions=1),
        geom_name="sphere",
        node_name="sphere-node",
        transform=trimesh.transformations.translation_matrix([3.0, 0.0, 0.0]),
    )
    path = tmp_path / "scene.glb"
    path.write_bytes(scene.export(file_type="glb"))

    loaded = load_scene(path)

    assert len(loaded.geometry) == 2
    assert len(loaded.graph.nodes_geometry) == 2


def test_file_limit_is_enforced_before_parser(tmp_path: Path) -> None:
    path = tmp_path / "box.glb"
    path.write_bytes(trimesh.creation.box().export(file_type="glb"))

    with pytest.raises(InputRejectedError, match="max_file_bytes"):
        probe_source(path, LoadLimits(max_file_bytes=16))
