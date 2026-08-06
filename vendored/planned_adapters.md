# Planned external adapters

## Candidate boundaries

| Component | Desired role | Boundary |
|---|---|---|
| trimesh | General mesh/scene ingestion, inspection, repair helpers, export | Python library; preserve source scene inventory explicitly |
| meshoptimizer / glTF Transform / gltfpack | Error-aware simplification and runtime delivery optimization | Library or fixed-argument CLI; version-sensitive flags |
| fast-simplification | In-process QEM candidate generation | Optional Python/native package; capability-gated |
| CoACD | Offline convex decomposition and box approximation | Optional Python/native adapter with capped settings and seed |
| Blender | Planar cleanup, UV, baking, authoring, and proof rendering | Optional headless adapter; no arbitrary user scripts |
| xatlas | New UV unwrap/packing | Optional native adapter; topology locked before tangent bake |
| Khronos glTF Validator | Structural glTF validation | Fixed-argument CLI; validator does not prove visual quality |
| TRELLIS replay provider | Decode retained post-stage capture and rebake PBR field | Explicit provider contract; `.bin` is not parsed generically |
| Godot | Import and physics validation | Optional engine adapter pinned to target version |
| PCL sample consensus | Native plane/cylinder/sphere/cone candidate generation | BSD-licensed research-to-native path behind a narrow adapter |
| earcut.hpp | Triangulate validated planar boundaries and holes | Optional future native helper; reject invalid polygons before use |
| MikkTSpace | Tangent generation after final topology/UV lock | Reference-compatible native interface |
| Open3D / point-cloud-utils | Surface sampling and distance metrics | Optional heavy metric backends; keep minimal images smaller |
| CGAL Shape Detection | Research reference only | Relevant package is GPL/commercial; exclude from default distribution |

The bootstrap revision implements none of these adapters. Their presence here is
design inventory, not a capability claim.

## Gaps

- Final component selection, versions, hashes, and license review remain open.
- Windows/macOS availability and GPU variants require a platform matrix.
- TRELLIS capture schema/version negotiation requires coordination with the
  producing repository.
