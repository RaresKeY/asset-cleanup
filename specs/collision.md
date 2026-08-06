# Collision contract

The collision stage writes `asset-cleanup-collision-sidecar/v1`, plus a preview
GLB. The JSON sidecar is neutral/editable data: body type, compatibility,
settings, source metrics, aggregate metrics/warnings, coordinate/unit metadata,
generator facts, and stable generated shapes.

Each shape has an ID, type, 4×4 transform, dimensions, optional mesh vertices
and faces, fit metrics, lock/generated flags, source-region labels, and generator
settings. Current shape types are box, sphere, cylinder, capsule, convex, and
trimesh. IDs derive deterministically from type, ordinal, and body type.

## Modes and safety

| Mode | Core result |
|---|---|
| `none` | No collision output. |
| `box`, `sphere`, `cylinder`, `capsule` | One PCA/bounds-oriented primitive. |
| `convex-hull` | Simplified convex mesh shape. |
| `coacd` | Convex decomposition when optional `coacd` is installed. |
| `trimesh` | Triangle-mesh collision, static-body only. |
| `compound` / `auto` | Scores supported primitive/convex candidates and selects a bounded compound. |

The recipe rejects trimesh for dynamic, kinematic, or character bodies. The
core's `unspecified` body is represented as unconfirmed compatibility; callers
should declare static/dynamic/kinematic/character/area intent before engine
handoff.

Scores are deterministic sampled surface approximation metrics with fit-policy
preference (`cover`, `balanced`, `inside`). The collision acceptance gate requires
confirmed body compatibility, sampled bidirectional surface p95 within limit,
one-shape watertight volume error within limit where measurable, and minimum
shape-volume fractions. A failed/unmeasurable required check leaves `candidate`.
It is not a physics simulation, contact-gap proof, or collision certification.

## Gaps

- Shape editor persistence, lock-aware regeneration, source-region segmentation,
  primitive split/merge, game-semantic openings, and engine exporters are not
  implemented.
- CoACD parameters, output format, and numerical behavior vary by installed
  optional version; the exact installed capability is captured in each manifest.
- No runtime physics tests currently verify the sidecar in Godot or another
  engine.
