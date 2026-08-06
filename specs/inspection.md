# Inspection and geometry evidence

`inspect_source` is non-mutating. It returns
`asset-cleanup/inspection-v1alpha1` with the source probe, scene graph inventory,
one local-geometry inventory per mesh, and warnings. It exposes visual kind,
material name, UV/color flags, and the mesh analysis below.

## Topology and scale facts

Each geometry analysis records vertices/faces, bounds, diagonal, area/volume,
watertight/winding flags, connected components, degenerate faces, boundary and
non-manifold edges, exported and position-welded vertex counts, stable PCA axes
and extents, sampling seed, warnings, planar regions, and primitive candidates.
The report describes local geometry; graph transforms are reported separately.

## Shape evidence

Planar regions are deterministic face-adjacency regions whose normals satisfy
the configured plane angle. Tiny regions under the support-area threshold are
suppressed from the serialized region list. The analyser emits accepted/rejected
plane, box, sphere, cylinder, and capsule proposals; a proposal includes support
area/fraction, residual where meaningful, source-face count, parameters,
metrics, confidence, and explicit rejection reasons.

Planar membership is compact: face count, a SHA-256 over membership, and a
64-index sample. Shape detection honors `enabled`, plane/cylinder/sphere/box
type switches, deterministic sample budget, support count/area, and cylinder
axial-bin/angular-coverage filters. It is not RANSAC; cone and a recipe-level
triangle-saving field are not present.

Primitive evidence is advisory. It is consumed by inspection and reporting, not
yet by visual reconstruction or the collision fitter's current PCA-based
primitive approximations. A box/cylinder/capsule is therefore never inferred
solely from its bounding-box proportions.

## Determinism

Surface samples and PCA canonicalization use the recipe seed. The report is
intended for repeatable comparative evidence on a fixed dependency environment,
not exact cross-platform floating-point identity.

## Gaps

- Cone fitting, torus/repetition fitting, and primitive reconstruction are not
  implemented.
- Material partitions, UV seams, hard normals, transform-conditioned analysis,
  and repeated-part detection are not yet first-class region boundaries.
- Plane boundaries/holes and high-confidence primitive fitting must be proven on
  benchmark meshes before they drive topology reconstruction.
