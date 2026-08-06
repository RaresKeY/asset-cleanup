# Research basis

Research review date: 2026-08-06. Decisions favor upstream repositories,
official manuals/specifications, and original papers. Exact adopted versions
belong in `vendored/`, lockfiles, and candidate manifests once implemented.

## Decisions

- Preserve glTF as a scene. Avoid flattening nodes, instances, materials,
  animation, skins, morphs, extras, or unknown extensions merely to simplify one
  mesh.
- Use deterministic, connectivity-aware region evidence. Bounding-box
  proportions alone do not identify a primitive.
- Ship broad detection and confidence reports before broad reconstruction.
  Planar boundary extraction and retriangulation is the first production visual
  replacement. Curved replacements are opt-in transactions that roll back on
  topology, boundary, scene, UV, material, or error failure.
- Use QEM/meshoptimizer for irregular residuals with protected primitive
  interfaces and absolute error semantics. It complements shape detection; it
  does not replace it.
- Require `UV_NEW` and a high-to-low or retained-field rebake after substantial
  topology changes. Lock final triangulation before tangent baking.
- Reuse the analysis graph for collision, where fitted primitives and compounds
  can provide immediate production value. CoACD is the maintained convex
  decomposition fallback; V-HACD is compatibility-only.
- Keep CGAL Shape Detection as a research reference because its relevant package
  is GPL/commercial. PCL provides the preferred permissive native reference for
  sample-consensus primitive fitting.
- Treat the 2026 convex primitive decomposition work as a future algorithmic
  direction; no public implementation was identified during this review.

## Initial normalized thresholds

Distances are expressed relative to bounding-box diagonal `D` and can also be
supplied in declared scene units.

- Conservative residual simplification error: `0.001 * D`.
- Balanced residual simplification error: `0.0025 * D`.
- Aggressive residual simplification error: `0.01 * D`.
- Plane normal threshold: `7.5 degrees`.
- Curved primitive normal threshold: `12 degrees`.
- RANSAC confidence: `0.999`, capped at `50,000` trials.
- Minimum support: at least 128 deterministic samples and 0.5% of component
  area.
- Minimum predicted triangle saving before visual replacement: 20%.
- Cylinder evidence: at least five axial bins and 45 degrees of angular support,
  plus stable radius and axis.

These are design starting points, not current implementation or universal
presets. Benchmark results must tune them.

## Primary sources

### Geometry and fitting

- [TRELLIS.2](https://github.com/microsoft/TRELLIS.2) and its
  [postprocess implementation](https://github.com/microsoft/TRELLIS.2/blob/main/o-voxel/o_voxel/postprocess.py)
- [trimesh](https://github.com/mikedh/trimesh)
- [Efficient RANSAC for point-cloud shape detection](https://cg.cs.uni-bonn.de/publication/schnabel-2007-efficient)
- [PCL sample-consensus models](https://pointclouds.org/documentation/group__sample__consensus.html)
- [CGAL Shape Detection](https://doc.cgal.org/latest/Shape_detection/index.html)
- [GlobFit](https://graphics.stanford.edu/~niloy/research/globFit/globFit_sigg11.html)
- [meshoptimizer](https://github.com/zeux/meshoptimizer)
- [Garland–Heckbert QEM paper](https://www.cs.cmu.edu/~garland/Papers/quadrics.pdf)
- [Mapbox earcut.hpp](https://github.com/mapbox/earcut.hpp)
- [xatlas](https://github.com/jpcy/xatlas)
- [MikkTSpace](https://github.com/mmikk/MikkTSpace)

### Collision, interchange, and validation

- [CoACD](https://github.com/SarahWeiii/CoACD) and its
  [research project](https://colin97.github.io/CoACD/)
- [V-HACD archive/deprecation notice](https://github.com/kmammou/v-hacd)
- [Convex Primitive Decomposition for Collision Detection](https://arxiv.org/abs/2602.07369)
- [Godot 3D collision guidance](https://docs.godotengine.org/en/latest/tutorials/physics/collision_shapes_3d.html)
- [glTF 2.0 specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html)
- [Khronos glTF Validator](https://github.com/KhronosGroup/glTF-Validator)

### Application and deployment

- [FastAPI file uploads](https://fastapi.tiangolo.com/tutorial/request-files/)
- [Pydantic JSON Schema](https://docs.pydantic.dev/latest/concepts/json_schema/)
- [Three.js GLTFLoader](https://threejs.org/docs/pages/GLTFLoader.html)
- [Docker rootless mode](https://docs.docker.com/engine/security/rootless/)
- [Podman run reference](https://docs.podman.io/en/latest/markdown/podman-run.1.html)

## Gaps

- Some current upstream CLI details remain version-sensitive and must be checked
  again when versions are pinned.
- Cross-architecture determinism and native floating-point equivalence need a
  measured tolerance contract.
- Primitive reconstruction and collision fitting need a public benchmark report
  before defaults are promoted from design to specs.

