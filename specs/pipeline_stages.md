# Processing stages

`run_pipeline(source, recipe, output)` creates a new, initially empty output
directory. A non-empty destination fails before work starts. Inputs are never
modified. The pipeline records structured JSONL events and writes a partial
failed manifest if an exception occurs after intake begins.

| Stage | Current behavior | Main outputs |
|---|---|---|
| Intake | Plans capabilities, copies the primary source plus confined local references, hashes members, and persists the resolved recipe. | `00_source/`, `input_manifest.json`, `resolved_recipe.yaml` |
| Inspect | Runs bounded source inspection when enabled. | `10_inspect/inspection.json` |
| Geometry | Copy-on-write repair and optional simplification per local mesh, then GLB export. A repair-only run still enters this stage. | `20_geometry/visual_candidate.glb`, `geometry_report.json` |
| Collision | Builds a derived world-space collision mesh and writes neutral sidecar/GLB if enabled and not `none`. | `50_collision/asset.collision.{json,glb}` |
| Validation | Runs structural, geometry-distance, scene/appearance inventory, optional glTF-validator, warning, and collision gates. | `70_proof/validation.json`, `metrics.json` |
| Package | Copies visual GLB and sidecar into a generic runtime directory. | `60_runtime/` |
| Finalize | Registers the event log, writes artifact index and final manifest/status. | `artifact_manifest.json`, `manifest.json`, `events.jsonl` |

## Status semantics

`failed` means execution raised or structural validation failed. `candidate`
means the run completed but a requested promotion gate is absent or not met.
`accepted` means structural safety, enabled geometry comparison, every enabled
implemented gate, collision acceptance when generated, and warning policy passed.
Asking for appearance comparison currently yields `candidate`: inventory runs,
but deterministic rendered/silhouette proof is unavailable.

`plan_run` reports source classification, expanded recipe hash, enabled stages,
capabilities, warnings, blockers, and `runnable`. Missing
`fast-simplification` blocks non-preserve geometry; missing CoACD blocks only an
explicit CoACD collision recipe. A missing glTF validator is a warning.

## Gaps

- Stage cache/resume, immutable directory promotion after external staging,
  dependency invalidation, and distributed execution are not implemented.
- Shape detection is configured but not an independently recorded stage toggle;
  it runs as part of inspection.
- The glTF Validator runs only when its executable is available; rendered
  appearance/silhouette proof remains unavailable.
