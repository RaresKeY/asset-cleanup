from __future__ import annotations

import json
from pathlib import Path

import pytest
import trimesh

import asset_cleanup.processing as processing
from asset_cleanup.errors import ProcessingError, RecipeError
from asset_cleanup.models import Recipe
from asset_cleanup.processing import plan_run, run_pipeline


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "source.glb"
    path.write_bytes(trimesh.creation.icosphere(subdivisions=2).export(file_type="glb"))
    return path


def _recipe(*, simplify: bool = False) -> Recipe:
    data = Recipe.from_preset("collision").model_dump(mode="python")
    data["settings"]["collision"].update({"body_type": "static", "mode": "sphere"})
    data["settings"]["validation"].update(
        {
            "gltf_validator": False,
            "compare_appearance": False,
            "compare_scene_inventory": False,
        }
    )
    if simplify:
        data["stages"]["geometry"] = True
        data["settings"]["geometry"].update(
            {"simplify_mode": "target", "target_faces": 100, "allow_attribute_loss": True}
        )
    return Recipe.model_validate(data)


def test_full_candidate_package_preserves_source(tmp_path: Path) -> None:
    source = _source(tmp_path)
    before = source.read_bytes()
    output = tmp_path / "candidate"

    result = run_pipeline(source, _recipe(), output)

    assert result.status == "accepted"
    assert source.read_bytes() == before
    assert (output / "00_source" / source.name).read_bytes() == before
    assert (output / "10_inspect" / "inspection.json").is_file()
    assert (output / "50_collision" / "asset.collision.json").is_file()
    assert (output / "60_runtime" / "source.glb").is_file()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "accepted"
    assert manifest["recipe_hash"] == _recipe().canonical_hash()
    assert all("sha256" in item for item in manifest["artifacts"])
    assert any(item["path"] == "events.jsonl" for item in manifest["artifacts"])


def test_target_simplification_writes_measured_report(tmp_path: Path) -> None:
    source = _source(tmp_path)
    output = tmp_path / "simplified"

    run_pipeline(source, _recipe(simplify=True), output)

    report = json.loads(
        (output / "20_geometry" / "geometry_report.json").read_text(encoding="utf-8")
    )
    simplification = report[0]["simplification"]
    assert simplification["input_faces"] == 320
    assert simplification["output_faces"] <= 100
    assert simplification["distance"]["method"].startswith("deterministic-area-samples")


def test_nonempty_output_is_never_overwritten(tmp_path: Path) -> None:
    source = _source(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "owner-data.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(ProcessingError, match="not empty"):
        run_pipeline(source, _recipe(), output)

    assert marker.read_text(encoding="utf-8") == "keep"


def test_sensitive_attributes_require_explicit_loss_or_rebake(tmp_path: Path) -> None:
    mesh = trimesh.creation.icosphere(subdivisions=2)
    mesh.visual = trimesh.visual.TextureVisuals(uv=mesh.vertices[:, :2])
    source = tmp_path / "textured.glb"
    source.write_bytes(mesh.export(file_type="glb"))
    data = _recipe(simplify=True).model_dump(mode="python")
    data["settings"]["geometry"]["allow_attribute_loss"] = False
    recipe = Recipe.model_validate(data)

    with pytest.raises(RecipeError, match="cannot preserve"):
        run_pipeline(source, recipe, tmp_path / "candidate")


def test_plan_reports_trellis_provider_blocker(tmp_path: Path) -> None:
    source = tmp_path / "post.bin"
    source.write_bytes(b"provider-specific")

    plan = plan_run(source, _recipe())

    assert not plan["runnable"]
    assert any("replay provider" in blocker for blocker in plan["blockers"])


def test_pipeline_preserves_external_gltf_members(tmp_path: Path) -> None:
    exported = trimesh.Scene(trimesh.creation.box()).export(file_type="gltf")
    assert isinstance(exported, dict)
    for name, payload in exported.items():
        (tmp_path / name).write_bytes(payload)
    source = tmp_path / "model.gltf"
    output = tmp_path / "candidate"

    run_pipeline(source, _recipe(), output)

    input_manifest = json.loads((output / "input_manifest.json").read_text(encoding="utf-8"))
    member_paths = {item["path"] for item in input_manifest["members"]}
    assert member_paths == {"model.gltf", "gltf_buffer_0.bin", "gltf_buffer_1.bin"}
    for name in member_paths:
        assert (output / "00_source" / name).read_bytes() == (tmp_path / name).read_bytes()


def test_collision_fit_must_pass_recipe_limits_before_acceptance(tmp_path: Path) -> None:
    source = tmp_path / "long-box.glb"
    source.write_bytes(trimesh.creation.box(extents=[100.0, 1.0, 1.0]).export(file_type="glb"))
    data = _recipe().model_dump(mode="python")
    data["settings"]["collision"].update(
        {
            "mode": "sphere",
            "surface_error_fraction": 0.000001,
            "volume_error_fraction": 0.000001,
        }
    )
    recipe = Recipe.model_validate(data)

    result = run_pipeline(source, recipe, tmp_path / "candidate")

    assert result.status == "candidate"
    validation = json.loads(
        (tmp_path / "candidate" / "70_proof" / "validation.json").read_text(encoding="utf-8")
    )
    assert not validation["gates"]["collision"]["passed"]
    assert not validation["gates"]["collision"]["surface"]["passed"]
    sidecar = json.loads(
        (tmp_path / "candidate" / "50_collision" / "asset.collision.json").read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["settings"]["acceptance_limits"]["surface_error_fraction"] == 0.000001


def test_external_validator_warnings_obey_fail_on_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _recipe().model_dump(mode="python")
    data["settings"]["validation"].update({"gltf_validator": True, "fail_on_warning": True})
    recipe = Recipe.model_validate(data)
    capabilities = processing.capability_map()
    capabilities["gltf-validator"] = {
        "name": "gltf-validator",
        "available": True,
        "version": "2.0.0-dev.3.10",
        "provider": "executable",
        "executable": "/test/gltf_validator",
        "note": None,
    }
    monkeypatch.setattr(processing, "capability_map", lambda: capabilities)

    def validator_result(
        path: Path,
        executable: str,
        *,
        discovered_version: str | None = None,
    ) -> dict[str, object]:
        assert path.name == "source.glb"
        assert executable == "/test/gltf_validator"
        assert discovered_version == "2.0.0-dev.3.10"
        return {
            "ran": True,
            "passed": True,
            "warning_count": 1,
        }

    monkeypatch.setattr(processing, "run_gltf_validator", validator_result)

    output = tmp_path / "candidate-with-warning"
    result = processing.run_pipeline(_source(tmp_path), recipe, output)

    assert result.status == "candidate"
    validation = json.loads((output / "70_proof" / "validation.json").read_text(encoding="utf-8"))
    assert validation["gates"]["external_gltf_validator"]["passed"] is True
    assert validation["warnings_gate"] == {
        "requested": True,
        "passed": False,
        "warning_count": 1,
        "internal_warning_count": 0,
        "external_gltf_warning_count": 1,
    }
