# Asset Cleanup

`asset-cleanup` is a standalone, reproducible post-generation pipeline and
local web workspace for triangle-mesh assets. It accepts ordinary GLB, glTF,
OBJ, polygon PLY, and STL;
inspects them before changing them; creates a separate candidate package; and
authors collision independently from visual geometry.

The current release preserves the original input, produces bounded topology and
primitive evidence, applies only declared repair/simplification operations, and
records hashes, settings, capabilities, events, outputs, and validation status.
It includes a FastAPI/SQLite service, isolated processing child, SSE progress,
React/Three.js comparison UI, and hardened container build. TRELLIS replay,
production shape reconstruction, UV/bake, runtime compression, and rendered
visual proof remain future work.

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked --all-extras
uv run asset-cleanup doctor
uv run asset-cleanup inspect ./input.glb --json
uv run asset-cleanup plan ./input.glb --preset balanced
uv run asset-cleanup run ./input.glb --output ./runs/chair-balanced --preset balanced \
  --body-type static
```

An enabled proof gate that cannot pass leaves a completed result as `candidate`:
for example a missing Khronos glTF Validator executable or requested rendered
appearance proof. The CLI copies a local source bundle only when every glTF/OBJ
reference is confined and present; the package retains those members and hashes.

```bash
(cd web && npm ci && npm run build)
uv run asset-cleanup serve --data-root ./.asset-cleanup --web-root ./web/dist
```

Use `asset-cleanup --help` and `asset-cleanup recipe --help` for every option.
The current command and recipe contracts are documented in
[`specs/cli.md`](specs/cli.md) and [`specs/recipe_schema.md`](specs/recipe_schema.md).

The browser accepts one self-contained source file at a time by picker or
drag/drop: GLB, data-URI glTF, or geometry-only OBJ/PLY/STL. A glTF/OBJ with
sibling dependencies cannot be supplied in one upload and is rejected; use the
CLI for such bundles. The fixed-height UI persists workspaces, sources, and
candidate threads locally; restores selectable job history; shows a compact
copyable activity console; frames uploaded and generated GLBs from their real
bounds; and verifies artifact hashes before download or ZIP packaging. `docker
compose up --build` publishes locally at `127.0.0.1:8080`;
`compose.secure.yaml` adds a networkless split worker.

## What is supported now

| Area | Current behavior |
|---|---|
| Intake | Content-sniffs supported triangle mesh formats, applies byte/count limits, rejects point-only PLY, archives, unknown formats, and generic `.bin`/TRELLIS deserialization. |
| Scene handling | Loads a `trimesh.Scene` with `process=False`, validates glTF/OBJ relative references, and retains local geometry definitions and transforms for inspection/export. |
| Inspection | Reports scene inventory, materials/attributes, topology, PCA, planar regions, and plane/box/sphere/cylinder/capsule evidence using deterministic sampling. |
| Visual candidate | Copy-on-write degenerate removal, unreferenced-vertex removal, optional normal repair/merge/hole fill, and optional QEM simplification through `fast-simplification`. |
| Collision | Generates editable neutral box, sphere, cylinder, capsule, convex, CoACD, static-trimesh, or auto candidates, with body-type safety checks. |
| Validation | Runs structural checks, approximate geometry distance, scene/appearance inventory, optional fixed-argument glTF Validator, warning, and collision gates. Rendered/silhouette proof is unavailable. |
| Package | Writes immutable source/input/recipe/event/inspection/geometry/collision/proof/runtime artifacts, manifests, metrics, and SHA-256 index. |
| Service/UI | SQLite-backed workspace-owned candidate threads, isolated child processing, normalized SSE/JSON logs, retry/cancel/recovery, drag/drop, framed previews, verified downloads, and ZIPs. |
| CLI | Inspect, plan, run, validate, capabilities, doctor, serve, worker, compare, package, and recipe subcommands. |

## Project memory

- [`design/_readme.md`](design/_readme.md) — current direction and desired future design.
- [`specs/_readme.md`](specs/_readme.md) — implementation truth, public contracts, and verification.
- [`vendored/_readme.md`](vendored/_readme.md) — external dependency/adaptor boundaries and resolved stack.
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) — third-party attribution only.

No project license has been granted. Third-party terms listed in the notice file
apply only to their respective components; they do not license this repository's
own code, documentation, or assets.

## Gaps

- The API is local and unauthenticated; remote/multi-user deployment needs a
  separately designed authorization and worker boundary.
- TRELLIS replay, planar/curved reconstruction, UV/bake, runtime optimization,
  engine proof, and rendered/silhouette appearance proof are not implemented.
- Use the current specs as the implementation boundary; design documents may
  describe intentionally unimplemented work.
