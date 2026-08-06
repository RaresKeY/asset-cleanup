# Specification map

`specs/` is committed project memory for current implementation truth. Desired
or future behavior belongs in `design/` until code and tests implement it. A
behavior change updates its focused spec in the same change.

- [`repository_contract.md`](repository_contract.md) — ownership, documentation,
  licensing, and generated-data boundaries.
- [`project_state.md`](project_state.md) — implemented slice and known limits.
- [`input_formats.md`](input_formats.md) — content-based intake and loader rules.
- [`recipe_schema.md`](recipe_schema.md) — versioned typed recipe contract.
- [`inspection.md`](inspection.md) — inspection report and evidence semantics.
- [`pipeline_stages.md`](pipeline_stages.md) — current run stages and promotion status.
- [`collision.md`](collision.md) — collision sidecar and body-safety contract.
- [`cli.md`](cli.md) — stable command surface and exit behavior.
- [`artifact_manifest.md`](artifact_manifest.md) — package layout, manifests, and hashes.
- [`web_api.md`](web_api.md) — local HTTP/SSE, previews, downloads, and browser recipes.
- [`jobs_storage.md`](jobs_storage.md) — SQLite state, worker claims, cancellation, recovery, and hashes.
- [`web_ui.md`](web_ui.md) — implemented React/Three.js local workspace behavior.
- [`deployment_security.md`](deployment_security.md) — container/deployment and security controls.
- [`verification.md`](verification.md) — local and CI verification requirements.

## Gaps

- TRELLIS-provider, engine-export, multi-worker, and authentication specs will
  be added only with their corresponding implementations.
- A generated JSON Schema snapshot is available through the CLI, but is not yet
  committed as a compatibility fixture.
