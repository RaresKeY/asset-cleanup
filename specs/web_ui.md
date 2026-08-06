# Web UI

The compiled React/TypeScript application is served locally by `asset-cleanup
serve` or the OCI image. It uses the same-origin API and has no runtime CDN
dependency. It provides workspace/source management, click or drag/drop import,
inspection summary, shape evidence, a browser recipe editor, persistent candidate
threads, job events/progress, cancel/retry, validation/metrics/artifact panels,
and a Three.js GLTF viewer. When no workspace exists, a modal requires a named
workspace before the first source is imported.

The fixed-height desktop shell keeps the document itself stationary while the
left workspace rail and right settings/evidence rail scroll independently. Each
build immediately creates and selects a candidate thread in the left rail. Its
right-aligned progress bar and status dot remain live across selection and reload;
accepted/candidate completion is green and failure/cancellation is red. Selecting
a thread restores its source, source/candidate/collision previews, artifacts,
evidence, and bounded event history. Its compact recipe is restored as the
starting point for a new candidate only when that representation round-trips to
the immutable canonical recipe exactly. Otherwise the UI labels the mismatch
and resets the separately titled new-candidate editor to defaults.

The activity console identifies the exact job, source, state, current stage,
progress, and status message. It renders the latest bounded normalized event
history as selectable plain text with a one-click copy action. The viewer supports
source/candidate split or overlay, wireframe, and optional collision overlay. It
frames translated and arbitrarily scaled GLBs from loaded bounds, refits before
the user moves the camera, and reports loading, decode, and WebGL-context errors.
SSE updates the selected active job while scoped polling refreshes all active
threads. Browser presets are expanded server-side into the canonical Recipe. The
UI marks planar/primitive reconstruction destructive; planning blocks those
unavailable operations instead of pretending that the toggles execute them.

## Gaps

- No deletion, bundle intake, full report browser, completed accessibility audit,
  or user-configurable service limits exists.
- The viewer is not deterministic render proof, texture/PBR audit, UV/collision
  editor, or engine simulation.
