# Web application design

## Workflow

The web UI is a local-first project workspace, not a thin page around a file
upload.

1. Create or open an asset workspace.
2. Add one or more sources and optional replay/high/appearance references.
3. Inspect source classification, scene tree, material inventory, topology,
   scale, and detected shape regions.
4. Choose a preset, then see and edit its fully expanded recipe.
5. Run a candidate and follow explicit stage progress, logs, warnings, resource
   use, and stop reasons.
6. Compare source and candidates in synchronized 3D views with textured, clay,
   wireframe, normals, UV, and collision overlays.
7. Inspect metrics and validation gates, promote an accepted candidate, and
   download the complete package or selected runtime artifacts.

## Interface layout

- Left rail: workspaces, sources, candidates, and immutable lineage.
- Main viewport: 3D preview and A/B comparison.
- Right inspector: current stage settings, detected regions, metrics, warnings,
  and artifact actions.
- Bottom activity panel: queued/running stages, structured logs, timings, and
  resource use.

Destructive implications are shown beside the setting that causes them. For
example, regional reconstruction marks existing UV/bake evidence stale before
the job is submitted.

## Implemented service architecture

- FastAPI exposes typed workspace, single-file upload, inspection, recipe, job,
  event, artifact, preview, capability, and health endpoints.
- SQLite stores workspace/job/event/artifact metadata; source bytes are
  content-addressed below the mounted data root.
- A local worker executes each pipeline run in an isolated child process with
  timeout, cancellation escalation, POSIX resource limits, and recovery of
  interrupted running jobs.
- The static React/TypeScript application uses local Three.js and SSE; the OCI
  image contains no runtime CDN or Node build tooling.
- The container runs non-root, persists the data root, and has live/readiness
  endpoints; compose supports embedded or networkless split worker modes.

## Safety boundaries

- File extensions do not decide parser or source kind; bounded sniffing and
  parser results do.
- Upload names are display metadata, never trusted paths.
- Archives, external glTF URIs, data URIs, texture dimensions, mesh counts, and
  decompression growth are bounded.
- Recipes are typed data. No user-provided shell command is evaluated.
- External adapters receive fixed argument arrays and isolated candidate paths.
- Preview and download routes are scoped to registered artifacts beneath the
  workspace root.

## Gaps

- The implementation is single-user/local; authentication and authorization are
  deliberately deferred.
- GPU rendering and distributed workers need a separate deployment design.
- Browser-side measurement parity with the canonical server inspection needs
  explicit tests.
- Accessibility, touch layout, and large-scene preview budgets require hands-on
  validation after implementation.
