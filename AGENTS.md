# Contributor instructions

Read the relevant files in `specs/` before changing code and the corresponding
files in `design/` before changing product direction.

## Project memory

- `specs/` is current implementation truth. Update it in the same change as the
  behavior it describes. `specs/_readme.md` is the canonical map.
- `design/` is current and future desired design. Do not silently turn an
  unimplemented design into an implementation claim.
- `vendored/` documents non-native dependencies, adapters, source ownership,
  pinned interfaces, and replacement plans. Do not place third-party license
  text there.
- Third-party attribution belongs in root `THIRD_PARTY_NOTICES.md`.
- Every focused design/spec/vendored document keeps a `Gaps` section current.

## Engineering

- Prototype orchestration and policy in Python. Move measured hot paths to
  native code behind stable Python interfaces.
- Preserve source assets. All processing writes a new candidate directory.
- Keep the CLI as the canonical interface; the web API calls the same library
  and configuration models.
- A preset is shorthand, not provenance. Persist its fully expanded settings.
- Prefer deterministic algorithms and record tool versions, seeds, commands,
  input hashes, metrics, warnings, and stop reasons.
- Treat topology edits, UV edits, texture baking, and collision generation as
  separate stages with explicit compatibility gates.
- Do not add a project license unless the owner explicitly requests one.

## Changes and verification

- Keep commits coherent and PRs reviewable.
- Add or update tests for changed behavior.
- Run the documented checks in `specs/verification.md`.
- Never commit generated workspaces, uploaded models, dependency caches, or
  secrets.

