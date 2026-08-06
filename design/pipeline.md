# Desired processing pipeline

## Stage graph

1. **Intake** copies or links the source into an immutable, hashed source area.
2. **Classify** identifies container/mesh kind and adapter capabilities.
3. **Inspect** records scene, geometry, topology, material, texture, transform,
   and scale evidence.
4. **Repair** creates an optional candidate with explicit, individually logged
   operations such as finite-value rejection, degenerate removal, safe weld,
   orientation repair, or hole filling.
5. **Segment and fit** finds connected planar, cylindrical, spherical, conical,
   box-like, and irregular regions with confidence and boundary records.
6. **Build visual candidates** applies conservative direct simplification,
   shape-guided reconstruction, remesh/retopology adapters, or preserve-only
   policy according to source constraints.
7. **UV and bake** preserves an accepted mapping or creates `UV_NEW` and rebakes
   from HIGH or a retained TRELLIS PBR field.
8. **Build collision** independently fits primitives, convex compounds, CoACD,
   or a simplified static trimesh under a declared body policy.
9. **Pack runtime output** performs accepted cache/fetch/quantization/compression
   steps without changing the already-approved authoring candidate.
10. **Validate and promote** compares metrics, matched renders, conformance,
    engine round trips, and collision behavior before marking a candidate
    accepted.

Stages form an explicit dependency graph. A topology change invalidates
downstream UV, tangent, bake, and collision evidence. A lighting or atlas change
cannot be hidden inside a geometry A/B comparison.

## Asset package

```text
00_source/       immutable inputs, replay contract, hashes
10_inspect/      source inventory and topology reports
20_geometry/     repaired HIGH and LOW candidates
30_uv_bake/      UV sets, cages, bake settings and maps
40_texture/      editable paint/material sources and flattened maps
50_collision/    primitive/convex/trimesh sources and sidecar
60_runtime/      accepted engine-facing assets
70_proof/        validators, metrics and matched visual evidence
manifest.json    lineage, expanded recipe, results and verdict
```

## Candidate rules

- Candidate IDs are stable, filesystem-safe, and unique inside one asset.
- A candidate points to its parent source or candidate; it never mutates it.
- Every output has a SHA-256 digest and a producing stage record.
- Optional tools are capability-gated before a job starts.
- Partial output is kept for diagnosis but cannot be promoted.
- Resuming skips only a stage whose inputs, expanded settings, tool versions,
  and output hashes still match.

## Promotion rules

A candidate is promotable only when required structural checks pass, requested
error and budget semantics are explicit, no unexplained scene data disappears,
appearance evidence is comparable, and collision matches the declared body
policy. Lowest face count or smallest file does not win automatically.

## Gaps

- The exact invalidation matrix must be encoded and tested with real stage
  implementations.
- Render-proof automation needs a pinned renderer and deterministic scene.
- Remote cache and distributed execution are intentionally deferred.

