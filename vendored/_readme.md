# External component map

`vendored/` is specs-like project memory for dependencies, upstream tools,
adaptor boundaries, source ownership, and replacement plans. It does not copy
third-party source or license text. Resolved attribution belongs exclusively in
root [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).

- [`dependency_policy.md`](dependency_policy.md) — adoption and capability rules.
- [`python_stack.md`](python_stack.md) — current resolved Python components and ownership.
- `web/package-lock.json` — locked frontend dependency graph; it is not copied
  under `vendored/` because it is executable project configuration.
- [`planned_adapters.md`](planned_adapters.md) — implemented and planned external tool boundaries.
- [`gltf_validator.md`](gltf_validator.md) — pinned native Khronos validator
  provenance, adapter boundary, redistribution, and update procedure.

## Gaps

- Lockfile integrity and platform wheel hashes are in `uv.lock`; this directory
  does not yet describe a release SBOM or container image inventory.
- Frontend transitive ownership is lockfile-backed but does not yet have a
  generated component-by-component SBOM under `vendored/`.
- The bundled validator currently has only a verified Linux amd64 artifact;
  arm64 and signed/reproducible artifact provenance remain open.
