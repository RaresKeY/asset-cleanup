# Roadmap

## Release slices

### Foundation

- Establish `design/`, `specs/`, `vendored/`, notice, repository, and CI rules.
- Define immutable-source, recipe, manifest, and capability contracts.

### Processing core and CLI

- Load and classify ordinary triangle meshes.
- Inspect scene, geometry, topology, scale, material, and texture state.
- Detect planar and primitive evidence with deterministic metrics.
- Repair safe defects and build conservative QEM candidates.
- Generate box/sphere/capsule/cylinder/convex/CoACD/static-trimesh collision
  candidates under body-aware policy.
- Write complete candidate packages and manifests.
- Provide `inspect`, `plan`, `run`, `validate`, `capabilities`, and config tools.

### Containerized web application — implemented first slice

- Durable local workspaces/jobs, bounded single-file uploads, structured progress,
  artifact registration, cancellation, retry, and interrupted-job recovery exist.
- A local Three.js source/candidate/collision comparison UI and multi-stage
  container/Compose deployment exist.
- Future work is authentication, bundle intake, editing, distributed workers,
  engine proof, and a documented Podman production path.

### Quality expansion

- TRELLIS replay adapter and pre-UV PBR rebake integration.
- Shape-aware visual reconstruction behind explicit experimental policy.
- Deterministic proof rendering and engine-specific physics labs.
- LOD families, texture/PBR diagnostics, and editable Blender handoff.

## Native-code triggers

Move a Python hot path to C/C++ only after a representative benchmark shows it
dominates wall time or memory and the native implementation can keep deterministic
behavior and the manifest contract. Likely candidates are adjacency/segmentation,
distance queries, region fitting, and mesh assembly; orchestration remains Python.

## Gaps

- Release versioning and compatibility policy will be fixed after the first
  end-to-end candidate format stabilizes.
- Benchmark assets, hardware matrix, and quality thresholds need owner approval.
- Packaging beyond the OCI image and Python wheel remains unplanned.
