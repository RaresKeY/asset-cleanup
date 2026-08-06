# Dependency policy

## Rules

- Prefer maintained upstream projects with a clear license and primary
  documentation.
- Keep orchestration in Python and isolate optional native or CLI tools behind
  typed adapters.
- Pin production/container dependencies and record their resolved versions in
  candidate manifests.
- Detect capabilities before accepting a job; never silently change algorithms
  because a package or executable is missing.
- Pass external-tool arguments as arrays, never through user-evaluated shell
  fragments.
- Record tool version, command arguments, seed, exit status, elapsed time, and
  output hashes.
- Keep third-party source and binaries out of this repository unless a later
  decision explicitly vendors them with provenance and update procedure.
- Keep all third-party attribution outside `vendored/` in root
  `THIRD_PARTY_NOTICES.md`.

## Review gates

- License compatibility and redistribution terms.
- Release/update cadence and active maintenance.
- Determinism and platform support.
- Parser and decompression attack surface.
- Ability to bound CPU, memory, disk, time, subprocesses, and output growth.
- A removal/replacement path that does not break the recipe schema.

## Gaps

- A vulnerability-scanning and lockfile update cadence is not yet selected.
- Native ABI and wheel platform support will be documented after profiling.

