"""Canonical staged processing service shared by CLI and web workers."""

from __future__ import annotations

import platform
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from asset_cleanup import __version__
from asset_cleanup.capabilities import capability_map
from asset_cleanup.collision import CollisionResult, collision_scene, generate_collision
from asset_cleanup.errors import CapabilityError, ProcessingError, RecipeError
from asset_cleanup.events import EventRecorder
from asset_cleanup.geometry import simplify_mesh
from asset_cleanup.inspection import InspectionReport, inspect_source
from asset_cleanup.models import CollisionMode, ComponentPolicy, Recipe, SimplifyMode, SourceKind
from asset_cleanup.quality import (
    compare_boundaries,
    compare_surfaces,
    gate_boundary_preservation,
)
from asset_cleanup.source import (
    LoadLimits,
    iter_meshes,
    load_scene,
    merged_world_mesh,
    probe_source,
    referenced_files,
)
from asset_cleanup.util import atomic_write_bytes, confined_path, sha256_file, write_json
from asset_cleanup.validation import (
    compare_appearance_inventory,
    compare_scene_inventory,
    run_gltf_validator,
    validate_source,
)


@dataclass(frozen=True, slots=True)
class RunResult:
    """Completed or partial candidate package."""

    output_directory: str
    manifest_path: str
    status: str
    artifacts: list[dict[str, Any]]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _limits(recipe: Recipe) -> LoadLimits:
    configured = recipe.settings.limits
    return LoadLimits(
        max_file_bytes=configured.max_input_bytes,
        max_vertices=configured.max_vertices,
        max_faces=configured.max_triangles,
        max_geometries=configured.max_meshes,
        max_nodes=configured.max_scene_nodes,
        max_referenced_bytes=configured.max_input_bytes,
        max_texture_pixels=configured.max_texture_pixels,
    )


def _prepare_output(output: Path) -> None:
    if output.exists() and any(output.iterdir()):
        raise ProcessingError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    for name in (
        "00_source",
        "10_inspect",
        "20_geometry",
        "30_uv_bake",
        "40_texture",
        "50_collision",
        "60_runtime",
        "70_proof",
    ):
        (output / name).mkdir(exist_ok=True)


def _artifact(path: Path, root: Path, kind: str, stage: str) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "kind": kind,
        "stage": stage,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _has_sensitive_attributes(mesh: Any) -> bool:
    visual = mesh.visual
    kind = str(getattr(visual, "kind", "") or "")
    uv = getattr(visual, "uv", None)
    colors = getattr(visual, "vertex_colors", None)
    face_materials = getattr(visual, "face_materials", None)
    multiple_materials = (
        face_materials is not None and len(np.unique(np.asarray(face_materials))) > 1
    )
    return bool(
        (kind == "texture" and uv is not None and len(uv) == len(mesh.vertices))
        or (kind in {"vertex", "face"} and colors is not None)
        or multiple_materials
    )


def _repair_mesh(mesh: Any, recipe: Recipe) -> tuple[Any, list[dict[str, Any]]]:
    """Apply only explicitly configured, copy-on-write repair operations."""

    import trimesh

    settings = recipe.settings.geometry
    result = mesh.copy()
    operations: list[dict[str, Any]] = []
    if settings.remove_degenerate and len(result.faces):
        before = len(result.faces)
        mask = result.nondegenerate_faces()
        result.update_faces(mask)
        removed = before - len(result.faces)
        operations.append({"operation": "remove_degenerate", "removed_faces": removed})
    if settings.remove_unreferenced:
        before = len(result.vertices)
        result.remove_unreferenced_vertices()
        operations.append(
            {"operation": "remove_unreferenced", "removed_vertices": before - len(result.vertices)}
        )
    if settings.merge_vertices:
        if _has_sensitive_attributes(result):
            if settings.preserve_material_boundaries or settings.preserve_uv_seams:
                raise RecipeError(
                    "merge_vertices cannot preserve requested material/UV boundaries; "
                    "disable those promises and allow attribute loss explicitly"
                )
            if not settings.allow_attribute_loss:
                raise RecipeError(
                    "merge_vertices could cross UV/color seams; set allow_attribute_loss explicitly"
                )
        diagonal = float(np.linalg.norm(result.extents))
        tolerance = max(diagonal * settings.merge_tolerance_fraction, np.finfo(float).eps)
        digits = max(0, int(np.ceil(-np.log10(tolerance))))
        before = len(result.vertices)
        result.merge_vertices(digits_vertex=digits)
        operations.append(
            {
                "operation": "merge_vertices",
                "removed_vertices": before - len(result.vertices),
                "tolerance": tolerance,
                "rounding_digits": digits,
            }
        )
    if settings.fill_holes:
        if _has_sensitive_attributes(result):
            if settings.preserve_material_boundaries or settings.preserve_uv_seams:
                raise RecipeError(
                    "fill_holes cannot preserve requested material/UV boundaries; "
                    "disable those promises and allow attribute loss explicitly"
                )
            if not settings.allow_attribute_loss:
                raise RecipeError("fill_holes creates new corners without UV/color data")
        changed = bool(trimesh.repair.fill_holes(result))
        operations.append({"operation": "fill_holes", "changed": changed})
    if settings.fix_normals and not result.is_winding_consistent:
        trimesh.repair.fix_normals(result, multibody=True)  # type: ignore[no-untyped-call]
        operations.append({"operation": "fix_normals", "changed": True})
    elif settings.fix_normals:
        operations.append({"operation": "fix_normals", "changed": False})
    return result, operations


def _requested_target(input_faces: int, recipe: Recipe) -> int | None:
    geometry = recipe.settings.geometry
    if geometry.target_faces is not None:
        return min(input_faces, max(4, geometry.target_faces))
    if geometry.target_ratio is not None:
        return min(input_faces, max(4, int(round(input_faces * geometry.target_ratio))))
    return None


def _simplify_with_gate(mesh: Any, recipe: Recipe, *, label: str) -> tuple[Any, dict[str, Any]]:
    geometry = recipe.settings.geometry
    mode = geometry.simplify_mode
    input_faces = len(mesh.faces)
    if mode is SimplifyMode.PRESERVE or input_faces <= 4:
        return mesh.copy(), {
            "mode": mode.value,
            "input_faces": input_faces,
            "output_faces": input_faces,
            "stop_reason": "preserve",
        }
    if _has_sensitive_attributes(mesh):
        if geometry.preserve_material_boundaries or geometry.preserve_uv_seams:
            raise RecipeError(
                f"{label}: simplification adapter cannot preserve requested material/UV boundary "
                "preservation; use preserve mode or disable those promises with an explicit "
                "rebake/attribute-loss policy"
            )
        if not geometry.allow_attribute_loss:
            raise RecipeError(
                f"{label}: simplification adapter cannot preserve all UV/color corner attributes; "
                "use preserve mode, provide a rebake adapter, or explicitly allow attribute loss"
            )

    target = _requested_target(input_faces, recipe)
    error_limit = geometry.max_error_fraction
    cache: dict[int, tuple[Any, dict[str, Any]]] = {}

    def candidate(face_target: int) -> tuple[Any, dict[str, Any]]:
        face_target = max(4, min(input_faces, int(face_target)))
        if face_target in cache:
            return cache[face_target]
        simplified = simplify_mesh(mesh, target_faces=face_target)
        metrics = compare_surfaces(
            mesh,
            simplified,
            samples=min(8_192, max(1_024, input_faces * 2)),
            seed=recipe.seed,
        ).to_dict()
        hausdorff = metrics["approximate_hausdorff"]
        diagonal = metrics["normalization_diagonal"]
        normalized = (
            float(hausdorff) / float(diagonal)
            if isinstance(hausdorff, (int, float))
            and isinstance(diagonal, (int, float))
            and diagonal > 0
            else None
        )
        result = {
            "requested_faces": face_target,
            "actual_faces": len(simplified.faces),
            "distance": metrics,
            "normalized_approximate_hausdorff": normalized,
        }
        cache[face_target] = (simplified, result)
        return cache[face_target]

    if mode is SimplifyMode.TARGET:
        if target is None:
            raise RecipeError("target mode requires target_faces or target_ratio")
        selected, details = candidate(target)
        stop_reason = "target_attempted"
    else:
        if error_limit is None:
            raise RecipeError(f"{mode.value} mode requires max_error_fraction")
        low = target if mode is SimplifyMode.HYBRID and target is not None else 4
        high = input_faces
        selected = mesh.copy()
        details = {
            "requested_faces": input_faces,
            "actual_faces": input_faces,
            "normalized_approximate_hausdorff": 0.0,
            "distance": None,
        }
        for _ in range(12):
            if low >= high:
                break
            midpoint = low if high - low <= 1 else (low + high) // 2
            attempted, attempted_details = candidate(midpoint)
            measured_error = attempted_details["normalized_approximate_hausdorff"]
            if isinstance(measured_error, (int, float)) and measured_error <= error_limit:
                selected, details = attempted, attempted_details
                high = midpoint
            else:
                low = midpoint + 1
        if len(selected.faces) == input_faces and low < input_faces:
            attempted, attempted_details = candidate(low)
            measured_error = attempted_details["normalized_approximate_hausdorff"]
            if isinstance(measured_error, (int, float)) and measured_error <= error_limit:
                selected, details = attempted, attempted_details
        stop_reason = "error_limit"

    output_faces = len(selected.faces)
    max_faces_passed = geometry.max_faces is None or output_faces <= geometry.max_faces
    if not max_faces_passed:
        raise ProcessingError(
            f"{label}: error-bounded result has {output_faces} faces, "
            f"above max_faces={geometry.max_faces}"
        )
    return selected, {
        "mode": mode.value,
        "input_faces": input_faces,
        "output_faces": output_faces,
        "target_faces": target,
        "max_error_fraction": error_limit,
        "max_faces": geometry.max_faces,
        "max_faces_passed": max_faces_passed,
        "stop_reason": stop_reason,
        **details,
    }


def _export_glb(scene: Any, path: Path) -> None:
    payload = scene.export(file_type="glb")
    if not isinstance(payload, bytes):
        raise ProcessingError("GLB exporter did not return bytes")
    atomic_write_bytes(path, payload)


def _collision_acceptance(
    result: CollisionResult,
    source_mesh: Any,
    recipe: Recipe,
) -> dict[str, Any]:
    """Evaluate collision evidence without confusing body safety with fit quality."""

    settings = recipe.settings.collision
    single_shape = len(result.shapes) == 1
    shape_metrics = result.shapes[0].metrics if single_shape else {}
    raw_p95 = shape_metrics.get("sampled_bidirectional_p95_distance_estimate")
    p95 = float(raw_p95) if isinstance(raw_p95, (int, float)) else None
    surface_ran = p95 is not None and bool(np.isfinite(p95))
    surface_passed = p95 is not None and surface_ran and p95 <= settings.surface_error_fraction

    measured_source_volume = abs(float(source_mesh.volume))
    source_volume = (
        measured_source_volume
        if bool(source_mesh.is_watertight) and measured_source_volume > 0
        else None
    )
    helper_scene = collision_scene(result)
    shape_volumes = [
        abs(float(mesh.volume)) if bool(mesh.is_watertight) else None
        for _, mesh in iter_meshes(helper_scene)
    ]
    shape_volume = shape_volumes[0] if single_shape and shape_volumes else None
    volume_ran = source_volume is not None and shape_volume is not None
    volume_error = (
        abs(shape_volume - source_volume) / source_volume
        if source_volume is not None and shape_volume is not None
        else None
    )
    volume_passed = volume_error is not None and volume_error <= settings.volume_error_fraction
    if source_volume is not None:
        volume_fractions = [
            float(volume) / source_volume for volume in shape_volumes if volume is not None
        ]
    else:
        volume_fractions = []
    minimum_shape_passed = (
        len(volume_fractions) == len(result.shapes)
        and bool(volume_fractions)
        and min(volume_fractions) >= settings.min_shape_volume_fraction
    )
    body_passed = result.body_compatibility == "confirmed"
    passed = bool(body_passed and surface_passed and volume_passed and minimum_shape_passed)
    return {
        "requested": True,
        "ran": bool(surface_ran or volume_ran),
        "passed": passed,
        "body_compatibility": {
            "passed": body_passed,
            "value": result.body_compatibility,
        },
        "surface": {
            "ran": surface_ran,
            "passed": surface_passed,
            "normalized_bidirectional_p95_estimate": p95,
            "limit": settings.surface_error_fraction,
            "method": shape_metrics.get("sampled_metric_status"),
        },
        "volume": {
            "ran": volume_ran,
            "passed": volume_passed,
            "absolute_volume_error_fraction": volume_error,
            "limit": settings.volume_error_fraction,
            "limitation": (
                None
                if volume_ran
                else "requires one watertight source and one watertight collision shape"
            ),
        },
        "minimum_shape_volume": {
            "passed": minimum_shape_passed,
            "fractions": volume_fractions,
            "limit": settings.min_shape_volume_fraction,
        },
        "shape_count": len(result.shapes),
    }


def plan_run(source: Path, recipe: Recipe) -> dict[str, Any]:
    """Resolve a non-mutating execution plan and capability warnings."""

    probe = probe_source(source, _limits(recipe))
    capabilities = capability_map()
    warnings: list[str] = []
    blockers: list[str] = []
    if not probe.supported:
        blockers.append(probe.reason or "source is unsupported")
    requested_kind = recipe.settings.source_kind
    if requested_kind is SourceKind.MESH and probe.source_kind != "mesh":
        blockers.append(
            f"recipe requires a mesh source but intake classified {probe.source_kind!r}"
        )
    if requested_kind is SourceKind.TRELLIS_POST and probe.source_kind != "trellis-post":
        blockers.append(
            "recipe requires a retained TRELLIS post capture but intake classified "
            f"{probe.source_kind!r}"
        )
    geometry = recipe.settings.geometry
    if (
        geometry.simplify_mode is not SimplifyMode.PRESERVE
        and not capabilities["fast-simplification"]["available"]
    ):
        blockers.append("fast-simplification is required by the geometry stage")
    if (
        recipe.settings.collision.mode is CollisionMode.COACD
        and not capabilities["coacd"]["available"]
    ):
        blockers.append("coacd is required by the collision recipe")
    if (
        recipe.settings.validation.gltf_validator
        and not capabilities["gltf-validator"]["available"]
    ):
        warnings.append("Khronos glTF Validator is unavailable; output remains a candidate")
    if recipe.settings.validation.compare_appearance:
        warnings.append(
            "rendered silhouette/appearance comparison is unavailable; output remains a candidate"
        )
    if not recipe.stages.validation:
        warnings.append("validation is disabled; output remains a candidate")
    if not recipe.settings.validation.compare_geometry:
        warnings.append("geometry comparison is disabled; output remains a candidate")
    if not (recipe.settings.validation.finite_values and recipe.settings.validation.structural):
        warnings.append(
            "required structural safety validation is disabled; output remains a candidate"
        )
    if geometry.reconstruct_planes:
        blockers.append("planar reconstruction is designed but not implemented by this release")
    if geometry.reconstruct_curved_primitives:
        blockers.append(
            "curved primitive reconstruction is experimental and has no installed adapter"
        )
    if geometry.component_policy is ComponentPolicy.PRUNE_SMALL:
        blockers.append("small-component pruning is not implemented by this release")
    if geometry.uv_policy != "preserve":
        blockers.append(f"UV policy {geometry.uv_policy!r} requires an unavailable UV/bake adapter")
    output = recipe.settings.output
    if output.format != "glb":
        blockers.append("the current runtime exporter supports GLB output only")
    if output.quantize or output.compress:
        blockers.append(
            "quantization and compression require an unavailable glTF optimization adapter"
        )
    if not output.include_manifest or not output.include_reports:
        blockers.append(
            "manifests and reports are mandatory in the current immutable package format"
        )
    if not output.include_collision_sidecar and recipe.stages.collision:
        blockers.append("collision sidecar omission is not implemented")
    if not output.keep_intermediates:
        blockers.append("intermediate pruning is not implemented")
    stages = [name for name, enabled in recipe.stages.model_dump().items() if enabled]
    return {
        "schema": "asset-cleanup/plan-v1alpha1",
        "source": probe.to_dict(),
        "recipe_hash": recipe.canonical_hash(),
        "stages": stages,
        "capabilities": capabilities,
        "warnings": warnings,
        "blockers": blockers,
        "runnable": not blockers,
    }


def run_pipeline(source: Path, recipe: Recipe, output: Path) -> RunResult:
    """Execute a deterministic candidate build without mutating the source."""

    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    plan = plan_run(source, recipe)
    if plan["blockers"]:
        raise CapabilityError("; ".join(plan["blockers"]))
    _prepare_output(output)
    events = EventRecorder(output / "events.jsonl")
    artifacts: list[dict[str, Any]] = []
    warnings = list(plan["warnings"])
    started = time.monotonic()
    manifest: dict[str, Any] = {
        "schema": "asset-cleanup/manifest-v1alpha1",
        "app_version": __version__,
        "status": "running",
        "created_utc": _utc_now(),
        "recipe_hash": recipe.canonical_hash(),
        "recipe": recipe.canonical_dict(),
        "source": plan["source"],
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "capabilities": plan["capabilities"],
        },
        "stages": [],
        "artifacts": artifacts,
        "warnings": warnings,
    }
    write_json(output / "manifest.json", manifest)
    recipe.save(output / "resolved_recipe.yaml")
    artifacts.append(_artifact(output / "resolved_recipe.yaml", output, "recipe", "intake"))

    try:
        events.emit("stage.started", "Preserving source", stage="intake")
        source_members = [(source.name, source), *referenced_files(source, limits=_limits(recipe))]
        member_manifest: list[dict[str, Any]] = []
        for relative, original in source_members:
            source_copy_member = confined_path(output / "00_source", relative)
            source_copy_member.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, source_copy_member)
            artifacts.append(_artifact(source_copy_member, output, "source", "intake"))
            member_manifest.append(
                {
                    "path": relative,
                    "bytes": original.stat().st_size,
                    "sha256": sha256_file(original),
                }
            )
        source_copy = output / "00_source" / source.name
        input_manifest = {
            "schema": "asset-cleanup/input-v1alpha1",
            "source_name": source.name,
            "bytes": source.stat().st_size,
            "sha256": sha256_file(source),
            "probe": plan["source"],
            "members": member_manifest,
        }
        write_json(output / "input_manifest.json", input_manifest)
        artifacts.append(_artifact(output / "input_manifest.json", output, "manifest", "intake"))
        manifest["stages"].append({"id": "intake", "status": "succeeded"})
        events.emit("stage.succeeded", "Source preserved", stage="intake")

        detection = recipe.settings.shape_detection
        inspection: InspectionReport | None = None
        if recipe.stages.inspect:
            events.emit("stage.started", "Inspecting source", stage="inspect")
            inspection = inspect_source(
                source,
                limits=_limits(recipe),
                seed=recipe.seed,
                plane_angle_deg=detection.plane_normal_degrees,
                curved_normal_angle_deg=detection.curved_normal_degrees,
                max_samples=recipe.settings.inspection.deterministic_samples,
                min_support_area=detection.min_support_area_fraction,
                detect_shapes=recipe.stages.detect_shapes and detection.enabled,
                enabled_primitives=frozenset(
                    primitive
                    for primitive, enabled in (
                        ("plane", detection.planes),
                        ("box", detection.boxes),
                        ("sphere", detection.spheres),
                        ("cylinder", detection.cylinders),
                        ("capsule", detection.cylinders),
                        ("irregular", True),
                    )
                    if enabled
                ),
                min_support_samples=detection.min_support_samples,
                cylinder_min_axial_bins=detection.cylinder_min_axial_bins,
                cylinder_min_angular_degrees=detection.cylinder_min_angular_degrees,
            )
            write_json(output / "10_inspect" / "inspection.json", inspection.to_dict())
            artifacts.append(
                _artifact(
                    output / "10_inspect" / "inspection.json", output, "inspection", "inspect"
                )
            )
            warnings.extend(inspection.warnings)
            manifest["stages"].append({"id": "inspect", "status": "succeeded"})
            events.emit("stage.succeeded", "Inspection complete", stage="inspect")

        scene = load_scene(source, _limits(recipe))
        geometry_reports: list[dict[str, Any]] = []
        working_scene = scene.copy()
        if recipe.stages.repair or recipe.stages.geometry:
            events.emit("stage.started", "Building visual candidate", stage="geometry")
            for name, mesh in iter_meshes(scene):
                working = mesh.copy()
                operations: list[dict[str, Any]] = []
                if recipe.stages.repair:
                    working, operations = _repair_mesh(working, recipe)
                simplify_report: dict[str, Any] | None = None
                if recipe.stages.geometry:
                    working, simplify_report = _simplify_with_gate(working, recipe, label=name)
                working_scene.geometry[name] = working
                geometry_reports.append(
                    {"geometry": name, "repair": operations, "simplification": simplify_report}
                )
            visual_path = output / "20_geometry" / "visual_candidate.glb"
            _export_glb(working_scene, visual_path)
            artifacts.append(_artifact(visual_path, output, "visual-glb", "geometry"))
            write_json(output / "20_geometry" / "geometry_report.json", geometry_reports)
            artifacts.append(
                _artifact(
                    output / "20_geometry" / "geometry_report.json",
                    output,
                    "geometry-report",
                    "geometry",
                )
            )
            manifest["stages"].append({"id": "geometry", "status": "succeeded"})
            events.emit("stage.succeeded", "Visual candidate built", stage="geometry")
        else:
            visual_path = (
                source_copy
                if plan["source"]["format"] == "glb"
                else output / "20_geometry" / "visual_candidate.glb"
            )
            if visual_path != source_copy:
                _export_glb(scene, visual_path)
                artifacts.append(_artifact(visual_path, output, "visual-glb", "geometry"))

        collision_result = None
        collision_gate: dict[str, Any] | None = None
        if recipe.stages.collision and recipe.settings.collision.mode is not CollisionMode.NONE:
            events.emit("stage.started", "Building collision candidate", stage="collision")
            collision_settings = recipe.settings.collision
            collision_mesh = merged_world_mesh(working_scene)
            collision_result = generate_collision(
                collision_mesh,
                mode=collision_settings.mode.value,
                body_type=collision_settings.body_type.value,
                fit_policy=collision_settings.fit_policy.value,
                max_primitives=collision_settings.max_shapes,
                max_hulls=collision_settings.max_hulls,
                max_vertices_per_hull=collision_settings.max_vertices_per_hull,
                coacd_threshold=collision_settings.max_concavity,
                seed=recipe.seed,
            )
            collision_result.settings["acceptance_limits"] = {
                "surface_error_fraction": collision_settings.surface_error_fraction,
                "volume_error_fraction": collision_settings.volume_error_fraction,
                "min_shape_volume_fraction": collision_settings.min_shape_volume_fraction,
            }
            collision_gate = _collision_acceptance(collision_result, collision_mesh, recipe)
            if not collision_gate["passed"]:
                warnings.append(
                    "collision fit is unproved or exceeds its acceptance limits; "
                    "output remains a candidate"
                )
            collision_json = output / "50_collision" / "asset.collision.json"
            write_json(collision_json, collision_result.to_dict())
            artifacts.append(_artifact(collision_json, output, "collision-sidecar", "collision"))
            collision_glb = output / "50_collision" / "asset.collision.glb"
            _export_glb(collision_scene(collision_result), collision_glb)
            artifacts.append(_artifact(collision_glb, output, "collision-glb", "collision"))
            manifest["stages"].append({"id": "collision", "status": "succeeded"})
            events.emit("stage.succeeded", "Collision candidate built", stage="collision")

        validation_report = None
        promotable = recipe.stages.validation and (
            collision_gate is None or bool(collision_gate["passed"])
        )
        metrics_data: dict[str, Any] = {
            "schema": "asset-cleanup/metrics-v1alpha1",
            "geometry": None,
            "collision": (
                {"generator": collision_result.metrics, "acceptance": collision_gate}
                if collision_result is not None
                else None
            ),
        }
        if recipe.stages.validation:
            events.emit("stage.started", "Validating candidate", stage="validation")
            validation_report = validate_source(visual_path, limits=_limits(recipe))
            validation_data = validation_report.to_dict()
            validation_settings = recipe.settings.validation
            geometry_settings = recipe.settings.geometry

            geometry_gate: dict[str, Any]
            if validation_settings.compare_geometry:
                source_world = merged_world_mesh(scene)
                # Acceptance evidence covers the actual exported artifact, not
                # merely the in-memory mesh that preceded serialization.
                candidate_scene = load_scene(visual_path, _limits(recipe))
                candidate_world = merged_world_mesh(candidate_scene)
                distance = compare_surfaces(
                    source_world,
                    candidate_world,
                    samples=min(8_192, max(1_024, len(source_world.faces) * 2)),
                    seed=recipe.seed,
                ).to_dict()
                diagonal = distance["normalization_diagonal"]
                hausdorff = distance["approximate_hausdorff"]
                normalized_hausdorff = (
                    float(hausdorff) / float(diagonal)
                    if isinstance(hausdorff, (int, float))
                    and isinstance(diagonal, (int, float))
                    and diagonal > 0
                    else None
                )
                distance_passed = (
                    normalized_hausdorff is not None
                    and normalized_hausdorff <= validation_settings.max_hausdorff_error_fraction
                )
                maximum_normal = distance.get("maximum_normal_degrees")
                normal_gate = {
                    "requested": geometry_settings.preserve_normals,
                    "ran": isinstance(maximum_normal, (int, float)),
                    "passed": (
                        not geometry_settings.preserve_normals
                        or (
                            isinstance(maximum_normal, (int, float))
                            and maximum_normal <= validation_settings.max_normal_error_degrees
                        )
                    ),
                    "maximum_degrees": maximum_normal,
                    "limit_degrees": validation_settings.max_normal_error_degrees,
                }
                boundary_comparison = compare_boundaries(
                    source_world,
                    candidate_world,
                    samples=min(4_096, max(512, len(source_world.faces))),
                    seed=recipe.seed,
                )
                boundary_gate = gate_boundary_preservation(
                    boundary_comparison,
                    max_distance_fraction=validation_settings.max_hausdorff_error_fraction,
                    max_length_change_fraction=(
                        geometry_settings.max_boundary_length_change_fraction
                    ),
                ).to_dict()
                boundary_gate["requested"] = geometry_settings.preserve_boundaries
                if not geometry_settings.preserve_boundaries:
                    boundary_gate["passed"] = True
                geometry_gate = {
                    "requested": True,
                    "ran": True,
                    "passed": bool(
                        distance_passed and normal_gate["passed"] and boundary_gate["passed"]
                    ),
                    "limit": validation_settings.max_hausdorff_error_fraction,
                    "normalized_approximate_hausdorff": normalized_hausdorff,
                    "distance": distance,
                    "normal_preservation": normal_gate,
                    "boundary_preservation": boundary_gate,
                }
            else:
                geometry_gate = {
                    "requested": False,
                    "ran": False,
                    "passed": False,
                    "reason": "geometry comparison was explicitly disabled",
                }
                promotable = False
            metrics_data["geometry"] = geometry_gate

            if validation_settings.compare_scene_inventory:
                scene_gate = compare_scene_inventory(
                    source,
                    visual_path,
                    limits=_limits(recipe),
                )
                scene_gate["requested"] = True
            else:
                scene_gate = {"requested": False, "ran": False, "passed": True}

            if validation_settings.compare_appearance:
                appearance_gate = compare_appearance_inventory(
                    source,
                    visual_path,
                    limits=_limits(recipe),
                )
                appearance_gate["requested"] = True
                appearance_gate["inventory_passed"] = appearance_gate["passed"]
                appearance_gate["silhouette"] = {
                    "ran": False,
                    "passed": False,
                    "required_iou": validation_settings.min_silhouette_iou,
                    "reason": "a deterministic render-comparison adapter is not installed",
                }
                appearance_gate["passed"] = False
            else:
                appearance_gate = {"requested": False, "ran": False, "passed": True}

            validator_capability = plan["capabilities"]["gltf-validator"]
            if validation_settings.gltf_validator and validator_capability["available"]:
                executable = validator_capability.get("executable")
                external_gate = run_gltf_validator(visual_path, str(executable))
                external_gate.update({"requested": True, "available": True})
            elif validation_settings.gltf_validator:
                external_gate = {
                    "requested": True,
                    "available": False,
                    "ran": False,
                    "passed": False,
                    "reason": "Khronos glTF Validator executable is unavailable",
                }
            else:
                external_gate = {
                    "requested": False,
                    "available": bool(validator_capability["available"]),
                    "ran": False,
                    "passed": True,
                }

            validation_data["gates"] = {
                "geometry_distance": geometry_gate,
                "scene_inventory": scene_gate,
                "appearance": appearance_gate,
                "external_gltf_validator": external_gate,
            }
            if collision_gate is not None:
                validation_data["gates"]["collision"] = collision_gate
            requested_gates = [
                gate for gate in validation_data["gates"].values() if bool(gate.get("requested"))
            ]
            warnings_passed = not validation_settings.fail_on_warning or not any(
                issue.severity == "warning" for issue in validation_report.issues
            )
            validation_data["warnings_gate"] = {
                "requested": validation_settings.fail_on_warning,
                "passed": warnings_passed,
            }
            all_gates_passed = all(bool(gate.get("passed")) for gate in requested_gates)
            required_safety_enabled = (
                validation_settings.finite_values and validation_settings.structural
            )
            validation_data["promotable"] = (
                validation_report.passed
                and all_gates_passed
                and warnings_passed
                and required_safety_enabled
                and validation_settings.compare_geometry
            )
            promotable = promotable and bool(validation_data["promotable"])
            write_json(output / "70_proof" / "validation.json", validation_data)
            artifacts.append(
                _artifact(
                    output / "70_proof" / "validation.json", output, "validation", "validation"
                )
            )
            manifest["stages"].append(
                {
                    "id": "validation",
                    "status": "succeeded" if validation_report.passed else "failed",
                    "promotable": bool(validation_data["promotable"]),
                }
            )
            events.emit(
                "stage.succeeded" if validation_report.passed else "stage.failed",
                (
                    "Validation complete"
                    if validation_data["promotable"]
                    else "Validation complete; one or more acceptance gates did not pass"
                ),
                stage="validation",
                level=(
                    "info"
                    if validation_data["promotable"]
                    else "warning"
                    if validation_report.passed
                    else "error"
                ),
            )

        write_json(output / "metrics.json", metrics_data)
        artifacts.append(_artifact(output / "metrics.json", output, "metrics", "validation"))

        if recipe.stages.package:
            events.emit("stage.started", "Writing runtime package", stage="package")
            runtime_visual = output / "60_runtime" / f"{source.stem}.glb"
            shutil.copy2(visual_path, runtime_visual)
            artifacts.append(_artifact(runtime_visual, output, "runtime-glb", "package"))
            if collision_result is not None:
                shutil.copy2(
                    output / "50_collision" / "asset.collision.json",
                    output / "60_runtime" / "asset.collision.json",
                )
                artifacts.append(
                    _artifact(
                        output / "60_runtime" / "asset.collision.json",
                        output,
                        "runtime-collision-sidecar",
                        "package",
                    )
                )
            manifest["stages"].append({"id": "package", "status": "succeeded"})
            events.emit("stage.succeeded", "Runtime package written", stage="package")

        manifest["status"] = "accepted" if promotable else "candidate"
        manifest["completed_utc"] = _utc_now()
        manifest["elapsed_seconds"] = round(time.monotonic() - started, 6)
        manifest["artifacts"] = artifacts
        manifest["warnings"] = sorted(set(warnings))
        events.emit("job.completed", f"Run completed as {manifest['status']}")
        artifacts.append(_artifact(output / "events.jsonl", output, "event-log", "package"))
        write_json(
            output / "artifact_manifest.json",
            {"schema": "asset-cleanup/artifacts-v1alpha1", "artifacts": artifacts},
        )
        artifacts.append(
            _artifact(output / "artifact_manifest.json", output, "artifact-index", "package")
        )
        manifest["artifacts"] = artifacts
        write_json(output / "manifest.json", manifest)
        return RunResult(
            output_directory=str(output),
            manifest_path=str(output / "manifest.json"),
            status=manifest["status"],
            artifacts=artifacts,
            warnings=manifest["warnings"],
        )
    except BaseException as exc:
        manifest["status"] = "failed"
        manifest["completed_utc"] = _utc_now()
        manifest["elapsed_seconds"] = round(time.monotonic() - started, 6)
        manifest["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        manifest["artifacts"] = artifacts
        manifest["warnings"] = sorted(set(warnings))
        write_json(output / "manifest.json", manifest)
        events.emit("job.failed", str(exc), level="error", data={"type": type(exc).__name__})
        raise
