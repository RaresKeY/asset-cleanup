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
