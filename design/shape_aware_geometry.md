# Shape-aware geometry design

## Motivation

Generic QEM often removes planar triangles cheaply, but it does not explicitly
decide that a region is a plane or that a pipe should become a controlled
cylinder. The desired optimizer combines region evidence with specialized
operations and keeps an irregular fallback.

## Detection

Detection operates on connected face regions and records both geometry and
boundaries.

- **Plane:** area-weighted normal consistency plus point-to-plane residual.
- **Cylinder:** candidate axis stability, radial residual, circularity across
  several cross-sections, angular coverage, axial span, and cap/side separation.
- **Sphere:** stable center, radius residual, and surface coverage.
- **Cone:** axis/apex stability and linear radius change along the axis.
- **Box/slab:** compatible plane families, near-orthogonal dominant normals,
  coverage, and occupied versus empty oriented-bounds volume.
- **Irregular:** residual region or any fit whose boundary/confidence gate fails.

Bounding-box proportions alone never prove a cylinder, capsule, or box. All
scores are normalized by a recorded mesh scale and deterministic sampling seed.

## Visual candidate modes

| Mode | Behavior | Texture policy |
|---|---|---|
| Preserve | Inspect only; retain source topology | Existing UV/maps retained |
| Conservative | Safe cleanup and attribute-aware QEM with boundary locks | Existing mapping may be retained only after comparison |
| Hybrid | Planar cleanup and controlled primitive reconstruction where accepted; QEM on irregular residuals | New UV and rebake required by default |
| Rebuild | Remesh/retopology adapter followed by simplification | New UV and rebake required |

Primitive reconstruction must preserve or deliberately rebuild region boundary
loops, respect open boundaries and material partitions, and reject joins that
create non-manifold geometry. A detected primitive is a proposal; it becomes
visual topology only when its Hausdorff/silhouette/normal error and boundary
tests pass.

## Simplification semantics

- `target` pursues a requested triangle count and carries an explicit destructive
  quality warning.
- `error` reduces as far as a finite normalized error permits; `max_faces` is an
  acceptance ceiling.
- `hybrid` pursues a count but refuses collapses above a finite error ceiling.
- Border locks, attribute/seam preservation, component pruning, and permissive
  behavior are explicit settings and never invisible fallbacks.

Reports separate exported vertices from position-welded vertices and include
input/output triangles, requested/observed error, stop reason, components,
boundaries, non-manifold edges, degenerates, elapsed time, and peak memory.

## Native optimization direction

The first implementation uses Python for orchestration, inspection, scoring,
and adapter control. Profiling decides whether adjacency construction,
region-growing, distance queries, fitting, or mesh assembly moves into a C/C++
extension. The Python recipe and manifest contract remains stable.

## Gaps

- Production-safe mixed-region reconstruction with holes and T-junction-free
  joins remains research work until benchmarked across hard-surface and organic
  assets.
- Existing-texture preservation after regional reconstruction cannot be promised;
  the default must remain `UV_NEW` plus rebake.
- Cone/torus and repeated-structure fitting have lower priority than planes,
  cylinders, spheres, and boxes.
- Skinned/deforming topology needs a separate skin-weight and animation policy.

