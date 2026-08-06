from __future__ import annotations

from pathlib import Path

import pytest
import trimesh
from typer.testing import CliRunner

from asset_cleanup.cli import app
from asset_cleanup.models import Recipe
from asset_cleanup.processing import run_pipeline

runner = CliRunner()


def _glb(tmp_path: Path) -> Path:
    path = tmp_path / "sphere.glb"
    path.write_bytes(trimesh.creation.icosphere(subdivisions=1).export(file_type="glb"))
    return path


def test_root_help_lists_canonical_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "inspect" in result.stdout
    assert "recipe" in result.stdout
    assert "capabilities" in result.stdout


def test_recipe_init_and_validate(tmp_path: Path) -> None:
    recipe = tmp_path / "balanced.yaml"

    created = runner.invoke(app, ["recipe", "init", str(recipe), "--preset", "balanced"])
    validated = runner.invoke(app, ["recipe", "validate", str(recipe)])

    assert created.exit_code == 0
    assert recipe.is_file()
    assert validated.exit_code == 0
    assert '"valid": true' in validated.stdout


def test_inspect_table_reports_geometry(tmp_path: Path) -> None:
    source = _glb(tmp_path)

    result = runner.invoke(app, ["inspect", str(source), "--samples", "512"])

    assert result.exit_code == 0
    assert "Inspection:" in result.stdout
    assert "geometry_0" in result.stdout


def test_plan_accepts_typed_dotted_override(tmp_path: Path) -> None:
    source = _glb(tmp_path)

    result = runner.invoke(
        app,
        [
            "plan",
            str(source),
            "--preset",
            "collision",
            "--body-type",
            "static",
            "--set",
            "settings.validation.gltf_validator=false",
        ],
    )

    assert result.exit_code == 0
    assert '"runnable": true' in result.stdout


def test_faces_and_ratio_are_mutually_exclusive(tmp_path: Path) -> None:
    source = _glb(tmp_path)

    result = runner.invoke(
        app,
        ["plan", str(source), "--faces", "10", "--ratio", "0.5"],
    )

    assert result.exit_code == 2
    assert "declare --faces or --ratio" in result.stderr


def test_package_rejects_tampered_registered_artifact(tmp_path: Path) -> None:
    source = _glb(tmp_path)
    data = Recipe.from_preset("collision").model_dump(mode="python")
    data["settings"]["collision"].update({"body_type": "static", "mode": "sphere"})
    data["settings"]["validation"].update(
        {
            "gltf_validator": False,
            "compare_appearance": False,
            "compare_scene_inventory": False,
        }
    )
    output = tmp_path / "run"
    run_pipeline(source, Recipe.model_validate(data), output)
    runtime = output / "60_runtime" / "sphere.glb"
    runtime.write_bytes(runtime.read_bytes() + b"tamper")

    result = runner.invoke(
        app,
        ["package", str(output), "--output", str(tmp_path / "package.zip")],
    )

    assert result.exit_code == 2
    assert "integrity check failed" in result.stderr


def test_package_cannot_overwrite_run_members(tmp_path: Path) -> None:
    source = _glb(tmp_path)
    data = Recipe.from_preset("collision").model_dump(mode="python")
    data["settings"]["collision"].update(
        {"body_type": "static", "mode": "sphere", "fit_policy": "cover"}
    )
    data["settings"]["validation"].update(
        {
            "gltf_validator": False,
            "compare_appearance": False,
            "compare_scene_inventory": False,
        }
    )
    output = tmp_path / "run"
    run_pipeline(source, Recipe.model_validate(data), output)
    before = (output / "manifest.json").read_bytes()

    result = runner.invoke(
        app,
        ["package", str(output), "--output", str(output / "manifest.json")],
    )

    assert result.exit_code == 2
    assert "outside the immutable run directory" in result.stderr
    assert (output / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("command", ["inspect", "plan", "validate"])
def test_report_commands_cannot_overwrite_source(tmp_path: Path, command: str) -> None:
    source = _glb(tmp_path)
    before = source.read_bytes()

    result = runner.invoke(app, [command, str(source), "--output", str(source)])

    assert result.exit_code == 2
    assert "must not replace the source" in result.stderr
    assert source.read_bytes() == before
