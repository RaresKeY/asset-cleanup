# Candidate package and manifests

## Layout

Every run reserves the following directories, whether or not an optional stage
currently writes into each one:

```text
00_source/       primary immutable source copy
10_inspect/      inspection report
20_geometry/     visual candidate and repair/simplification report
30_uv_bake/      reserved
40_texture/      reserved
50_collision/    neutral collision sidecar and collision preview GLB
60_runtime/      generic runtime visual/collision copies
70_proof/        structural validation report
events.jsonl     append-only structured job events
resolved_recipe.yaml
input_manifest.json
artifact_manifest.json
manifest.json
```

## Manifest contracts

`manifest.json` (`asset-cleanup/manifest-v1alpha1`) starts with `running` and
finishes as `failed`, `candidate`, or `accepted`. It stores app/environment
versions and capabilities, source probe, canonical recipe/hash, stage statuses,
warnings, elapsed times, failure details where applicable, and artifacts.

Each artifact contains a root-relative POSIX path, kind, producing stage, byte
count, and SHA-256. `artifact_manifest.json`
(`asset-cleanup/artifacts-v1alpha1`) lists the artifacts known before it is
itself indexed; `manifest.json` is then updated with the artifact-index entry.
This small self-reference asymmetry is current behavior and must not be hidden
from consumers.

`input_manifest.json` (`asset-cleanup/input-v1alpha1`) names and hashes the
original primary input and every preserved confined member. `events.jsonl` records time, level, kind, message,
optional stage, and data as jobs proceed. ZIP packaging reads only the completed
manifest's registered relative artifacts through confined paths.
`events.jsonl` is itself a registered artifact in the final package.

## Gaps

- Artifact signatures, SBOMs, content-store lifecycle/deduplication policy, and
  schema migrations are not implemented.
- `include_manifest`, `include_reports`, `include_collision_sidecar`, and
  `keep_intermediates` are mandatory current package invariants. A conflicting
  recipe is blocked rather than silently changing package emission.
