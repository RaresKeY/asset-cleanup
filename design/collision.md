# Collision design

## Decision ladder

Choose the cheapest interaction-correct representation:

1. none for decoration;
2. box, sphere, capsule, or cylinder;
3. one convex hull;
4. a small compound of primitives and convex hulls;
5. simplified concave/trimesh for static geometry;
6. render-mesh trimesh only as an explicit static control candidate.

Dynamic, character, and movable-body policies reject concave trimesh output.
Functional openings, support surfaces, and interaction zones matter more than
visual indentations.

## Automatic strategy

The shape detector proposes oriented boxes, spheres, cylinders, and capsules
with residual, coverage, contact-gap, and complexity scores. Unexplained regions
fall back to a convex hull or capped CoACD decomposition. The candidate selector
balances approximation error, empty volume, shape count, hull count, vertices,
and declared gameplay importance.

All automatic shapes are editable data. Each records type, transform,
dimensions, source face/component IDs, confidence, lock state, and generator
settings. Regeneration replaces only unlocked generated shapes.

## Body-aware defaults

| Body policy | Allowed automatic output |
|---|---|
| none | no collision |
| static | primitives, convex compound, simplified trimesh |
| dynamic | primitives and convex compound |
| character | primarily capsule/box/convex with conservative contacts |
| trigger | simple primitives or convex query volumes |

## Export

The neutral sidecar is authoritative. Engine exporters may create named helper
meshes or native scene fragments. The Godot adapter uses explicit ownership such
as `COL_<body_id>_BOX_*`, `COL_<body_id>_CAPSULE_*`,
`COL_<body_id>_CYLINDER_*`, `COL_<body_id>_CONVEX_*`, and
`COL_<body_id>_TRIMESH_*` and validates that shapes attach directly to the
intended physics body.

## Validation

- Reject zero-volume, non-finite, or inverted shapes.
- Report shape/hull/triangle counts and vertices per hull.
- Compare surface and occupied-volume error at declared scale.
- Run opening-clearance, support-height, drop/roll/stack/slide, and high-speed
  tests where applicable.
- Record generator version, full settings, seed, input/output hashes, elapsed
  time, and stop reason.

## Gaps

- Gameplay-semantic importance still needs user hints for handles, doors,
  traversal openings, and interaction zones.
- Automated physics labs are engine-specific and remain adapter work.
- Primitive merge/split optimization needs benchmark-derived default weights.
- Editable Blender helper generation is planned but not part of the first core
  release.

