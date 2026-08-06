# Verification

Run from the repository root. Some current sandbox images require an explicit
fixed Python hash seed; retaining it also makes test hashing behavior explicit.

```bash
PYTHONHASHSEED=0 uv sync --locked --all-extras
PYTHONHASHSEED=0 uv run ruff format --check .
PYTHONHASHSEED=0 uv run ruff check .
PYTHONHASHSEED=0 uv run mypy src/asset_cleanup
PYTHONHASHSEED=0 uv run pytest
PYTHONHASHSEED=0 uv build
PYTHONHASHSEED=0 uv run asset-cleanup doctor
(cd web && npm ci && npm run typecheck && npm run lint && npm run test && npm run build && npm audit --audit-level=high)
docker build --tag asset-cleanup:local .
docker run --rm --read-only asset-cleanup:local sh -ceu '
  test -x /usr/local/bin/gltf_validator
  test -s /usr/share/licenses/gltf-validator/LICENSE
  test -s /usr/share/doc/gltf-validator/NOTICES
  test -s /usr/share/doc/asset-cleanup/THIRD_PARTY_NOTICES.md
'
git diff --check
rg --files design specs vendored | sort
rg --files-without-match '^## Gaps$' design specs vendored
```

The Python suite currently collects 143 tests and the frontend suite collects
20; these counts are informative rather than a compatibility contract. It
covers core processing plus SQLite claims/recovery/cancellation/orphan
termination, strict request-body limits, recipe-policy creation/retry/worker
enforcement, FastAPI upload/SSE/evidence integrity, CLI source-alias protection,
workspace job lineage/event history, browser state, and bounds-based camera
framing.
CI runs locked Python and frontend checks, builds distributions, builds the OCI
image, and starts a hardened read-only container for health/UI smoke checks. It
asserts validator version 2.0.0-dev.3.10 from a timestamp/path-free synthetic
glTF report and verifies that upstream LICENSE, NOTICES, and the application
notice catalogue are readable as the runtime UID.
An end-to-end smoke test should use a synthetic GLB and verify source copy,
resolved recipe, visual candidate, collision sidecar, manifests, and expected
`candidate`/`accepted` status under explicitly chosen proof gates.

Review must also ensure that no project `LICENSE*` file is introduced without
owner direction and that `vendored/` contains no third-party license text.

## Gaps

- Adapter integration, visual/physics proof, fuzzing, performance,
  cross-platform, and reproducibility matrices remain future verification work.
- Strict type checking currently needs to be kept green as optional native
  dependency stubs evolve; there is no published baseline report yet.
- Linux arm64 validator packaging and signed/reproducible upstream artifact
  verification are not in the current matrix.
