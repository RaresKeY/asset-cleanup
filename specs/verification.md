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
git diff --check
rg --files design specs vendored | sort
rg --files-without-match '^## Gaps$' design specs vendored
```

The suite currently collects 82 tests; this count is informative rather than a
compatibility contract. It covers core processing plus SQLite claims/recovery/
cancellation/orphan termination, FastAPI upload/SSE/evidence integrity, CLI
source-alias protection, and browser state behavior.
CI runs locked Python and frontend checks, builds distributions, builds the OCI
image, and starts a hardened read-only container for health/UI smoke checks.
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
