# Web UI

The compiled React/TypeScript application is served locally by `asset-cleanup
serve` or the OCI image. It uses the same-origin API and has no runtime CDN
dependency. It provides workspace/source management, import, inspection summary,
shape evidence, a browser recipe editor, job events/progress, cancel/retry,
validation/metrics/artifact panels, and a Three.js GLTF viewer.

The viewer supports source/candidate split or overlay, wireframe, and optional
collision overlay. SSE updates job activity while polling refreshes active state.
Browser presets are expanded server-side into the canonical Recipe. The UI marks
planar/primitive reconstruction destructive; planning blocks those unavailable
operations instead of pretending that the toggles execute them.

## Gaps

- No saved candidate-history rail, deletion, bundle intake, full report browser,
  keyboard/accessibility audit, or user-configurable service limits exists.
- The viewer is not deterministic render proof, texture/PBR audit, UV/collision
  editor, or engine simulation.
