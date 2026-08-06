# Current project state

Version `0.1.0` requires Python 3.12+ and implements a bounded processing
library, Typer CLI, local FastAPI service, SQLite job worker, React/Three UI,
and OCI container build. CLI and web workers use the same typed Recipe contract.

## Implemented

- Strict Pydantic v2 recipe models, YAML/JSON I/O, canonical sorted JSON, and a
  SHA-256 recipe identity.
- Built-in `close`, `balanced`, `distant`, and `collision` presets that expand
  into concrete settings before execution.
- Content-based GLB/glTF/OBJ/PLY/STL probing; bounded GLB/glTF/OBJ reference
  checking; `trimesh.load_scene(process=False)` scene loading; point-only PLY,
  archives, unrecognized data, and generic TRELLIS `.bin` rejection.
- Deterministic geometry inspection: scene inventory, material/attribute flags,
  topology, bounds/PCA, planar regions, and primitive proposals.
- Explicit copy-on-write repair and optional in-process QEM simplification with
  an attribute-loss safety gate.
- Collision candidate generation and neutral sidecar/GLB export for primitive,
  convex, CoACD (when installed), and static trimesh modes.
- Structural validation, geometry distance, scene/appearance inventories,
  optional fixed-argument glTF Validator, collision acceptance, metrics, and
  staged manifests/artifacts/events/hashes.
- Local workspaces, deduplicated sources, workspace-owned persistent candidate
  threads, transactional job/event/artifact state, child processing,
  cancellation/recovery, SSE plus normalized JSON event history, verified
  downloads/ZIPs, and GLB previews.
- Fixed-height React/Three.js workspace UI with drag/drop intake, independently
  scrolling rails, copyable job consoles, bounds-based preview framing, hardened
  Compose/container topology, and CI.

## Deliberately not implemented

- TRELLIS replay or `post.bin` decoding; `.bin` is never deserialized by the
  core.
- Shape-guided mesh reconstruction, planar boundary retriangulation, cone
  fitting, UV unwrap, texture/PBR rebake, tangent generation, or texture-aware
  topology preservation beyond the explicit attribute-loss guard.
- Rendered/silhouette appearance proof, engine round trip/physics proof, runtime
  quantization/compression, and Blender/gltfpack/glTF Transform/Godot execution.
- Authentication, authorization, multi-user quotas, remote storage, and
  multi-worker coordination.

## Compatibility status

`asset-cleanup/v1alpha1` recipe, plan, manifest, validation, collision-sidecar,
and artifact-index schema labels identify the current alpha contracts. They are
test-covered within this repository but are not yet promised as a stable
cross-version public compatibility guarantee. Consumers should store the full
resolved recipe and manifest rather than infer behavior from a preset name.

## Gaps

- No migration policy exists yet for a breaking alpha schema change.
- `accepted` is evidence-backed for enabled current gates, not a substitute for
  unimplemented rendered, engine, or gameplay proof.
