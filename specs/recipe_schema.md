# Recipe schema

## Identity and serialization

A run uses `Recipe` with `api_version: asset-cleanup/v1alpha1` and `kind:
Recipe`. Models reject unknown fields. JSON and YAML are accepted by extension;
YAML uses safe loading. The canonical identity is SHA-256 of recursively
key-sorted compact JSON, including defaults and `null` values. Every run writes
the fully expanded `resolved_recipe.yaml`, never just a preset name.

`asset-cleanup recipe schema` emits the authoritative JSON Schema for the
installed implementation. `recipe init`, `validate`, and `explain` use the same
model validators as execution.

## Top-level fields

| Field | Meaning |
|---|---|
| `name`, `description` | Human-oriented metadata; name must contain visible text. |
| `expanded_preset` | Provenance of one of `close`, `balanced`, `distant`, or `collision`; execution still uses explicit settings. |
| `seed`, `deterministic` | Deterministic analysis/generation seed and declared intent. |
| `stages` | Boolean switches for inspect, repair, shape detection, geometry, collision, validation, and package. |
| `settings` | Source, limits, inspection, shape, geometry, collision, validation, and output policies. |

## Cross-field safety rules

- `preserve` simplification cannot declare a target/error; `target`, `error`,
  and `hybrid` require their respective target/error inputs.
- `prune-small` requires a positive component threshold; merging requires a
  positive tolerance.
- Plane/curved reconstruction demands `uv_policy: new` or `rebake`; neither
  reconstruction operation is implemented in this core.
- Dynamic, kinematic, and character bodies cannot request trimesh collision.
  Body `none` requires collision mode `none`; disabling collision likewise
  requires collision mode `none`. Unspecified-body trimesh is rejected.

## Simplification and output semantics

Geometry modes are `preserve`, `target`, `error`, and `hybrid`. The current
adapter works from a target face count and measures approximate symmetric
surface error after simplification; it records whether the requested error/budget
gate was met. `target_ratio`, `target_faces`, `max_faces`, and
`max_error_fraction` have the validators above. For textured UVs or vertex/face
colors, simplification/merge/fill refuse the operation unless
`allow_attribute_loss` is explicit.

Output currently writes GLB even though `output.format` also permits `gltf`.
The current runtime exporter writes GLB only. Requesting glTF, quantization,
compression, UV `new`/`rebake`, reconstruction, component pruning, report/
manifest omission, collision-sidecar omission, or intermediate pruning is a
planning blocker rather than a silent no-op.

Shape configuration honors `enabled`, plane/cylinder/sphere/box switches,
deterministic sample budget, support thresholds, and cylinder filters. It has no
cone, RANSAC, or minimum-triangle-saving recipe fields. Validation executes
structural, geometry, scene inventory, appearance inventory, optional external
validator, warning, and collision gates; appearance cannot pass without a renderer.

## Web-service policy

Recipe resource settings are per-run requests, not permission to enlarge the
service's resource budget. A web-service instance has an immutable
`RecipeCeilings` policy. Its current defaults map to canonical recipe paths as
follows; an operator can configure different positive instance-owned bounds:

| Canonical recipe path | Bound | Default |
|---|---:|---:|
| `settings.collision.max_hulls` | maximum | 16 |
| `settings.collision.max_shapes` | maximum | 32 |
| `settings.collision.max_vertices_per_hull` | maximum | 64 |
| `settings.inspection.deterministic_samples` | maximum | 100,000 |
| `settings.limits.max_input_bytes` | maximum | 1,073,741,824 |
| `settings.limits.max_memory_bytes` | maximum | 8,589,934,592 |
| `settings.limits.max_meshes` | maximum | 10,000 |
| `settings.limits.max_runtime_seconds` | maximum | 3,600 |
| `settings.limits.max_scene_nodes` | maximum | 100,000 |
| `settings.limits.max_texture_pixels` | maximum | 268,435,456 |
| `settings.limits.max_triangles` | maximum | 50,000,000 |
| `settings.limits.max_vertices` | maximum | 50,000,000 |
| `settings.shape_detection.cylinder_min_axial_bins` | minimum | 5 |
| `settings.shape_detection.min_support_area_fraction` | minimum | 0.005 |
| `settings.shape_detection.min_support_samples` | minimum | 128 |

These are operator-owned evidence and complexity floors.
`min_support_samples` and `cylinder_min_axial_bins` are post-fit evidence
acceptance thresholds that reject primitive evidence supported by too few
samples or axial sections. `min_support_area_fraction` rejects undersized planar
support and chiefly bounds reported planar-region cardinality. Effective
maximums and minimums are instance configuration and are reported by the
capabilities API rather than embedded into a submitted recipe.

Full canonical recipes and server-expanded browser recipes must satisfy every
bound before a job can be persisted. The service rejects a violation with a
structured `422 recipe_exceeds_server_policy` response; it never silently
clamps a value, because doing so would make the stored canonical recipe and its
hash misrepresent the work that will run.

The CLI remains governed by the explicit limits in its resolved recipe. The
additional `RecipeCeilings` boundary belongs to the long-lived web service and
worker. A recipe may request a value inside each permitted interval but cannot
broaden an operator-owned maximum or weaken an evidence/complexity floor.

Normal and boundary preservation have measured validation gates. The adapter
cannot promise material boundaries or UV seams; requested promises block
destructive merge, hole fill, or simplification rather than silently losing them.

## Presets

`close`, `balanced`, and `distant` select error fractions of 0.001, 0.0025, and
0.01 of the candidate diagonal with silhouette thresholds 0.995, 0.98, and
0.95. `collision` preserves visual geometry and turns off appearance comparison.
Quality presets request the external glTF validation gate. When its executable
is unavailable, a normal run remains `candidate`; when available it is invoked
with fixed arguments and captured in validation evidence.

## Gaps

- Recipe migrations, profile inheritance, external adapter version selection,
  and signed/remote recipe provenance are not implemented.
- `deterministic` records intent, not a promise of bitwise equality across OS,
  Python, NumPy, SciPy, or native dependency versions.
- Service ceilings are process configuration rather than per-workspace or
  authenticated-principal quotas; those policy layers do not exist yet.
- Policy uses explicit per-field bounds rather than an aggregate model for
  interactions between otherwise valid high-cost settings.
