# External adapters

## Implemented dependency boundaries

| Component | Current boundary |
|---|---|
| trimesh | Imported only after source probe/reference checks. Core calls scene loading with `process=False`; it owns limits, source policy, output layout, and validation reporting. |
| fast-simplification | `geometry.simplify_mesh` is a narrow in-process QEM adapter. Core owns requested target selection, attribute-loss guard, and quality gate/report. |
| CoACD | Optional import inside collision generation. Explicit `coacd` recipes are blocked during planning if unavailable. |
| Pydantic / PyYAML | Recipe parsing only. Recipe data cannot name a program or evaluate code. |

Capability discovery reports packages plus fixed executable names; it never
executes a recipe-provided command. `gltf-validator` now has a fixed-argument
adapter during validation when discovered. `gltfpack`, `gltf-transform`,
`blender`, and `godot` remain detected-only.

## Planned boundaries

| Component | Desired role | Required boundary |
|---|---|---|
| Khronos glTF Validator | Structural conformance proof | Fixed identity/version/argv, captured report and exit status. |
| meshoptimizer / glTF Transform / gltfpack | Runtime compression/packing | Pinned fixed-argument adapter after authoring approval; never silently changes geometry. |
| TRELLIS replay provider | Decode retained post-stage capture/rebake PBR field | Producer-versioned, isolated contract; no generic pickle or `torch.load`. |
| Blender / xatlas / MikkTSpace | UV, bake, tangent, editable handoff | Pinned worker adapter; no arbitrary scripts. |
| Godot | Engine import/physics proof | Target-versioned isolated project and recorded tests. |
| PCL sample consensus | Native primitive fitting | Narrow C/C++ extension after profiling and benchmark acceptance. |
| earcut.hpp | Validated planar boundary triangulation | Native helper only after robust boundary/holes contract. |
| Open3D / point-cloud-utils | Heavy distance metrics | Optional backend behind the existing neutral quality-report schema. |
| CGAL Shape Detection | Research reference | Not a default distributable dependency because relevant packages are GPL/commercial. |

## Gaps

- No adapter ABI, timeout/resource envelope, version pin, golden-output fixture,
  or platform matrix has yet been committed for any planned executable.
- The current capability list is an informational probe, not a proof that an
  executable's arguments or license are suitable for a release.
