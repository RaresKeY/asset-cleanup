# External component map

`vendored/` is specs-like project memory for dependencies, external tools,
source ownership, pinned interfaces, capability detection, and replacement
plans. It does not contain third-party license text and does not imply that a
component is copied into this repository.

- [`dependency_policy.md`](dependency_policy.md) — adoption, pinning, capability,
  security, and notice rules.
- [`planned_adapters.md`](planned_adapters.md) — desired external-tool boundaries;
  none are implemented in the bootstrap revision.

Third-party attribution and license identifiers/links belong only in root
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).

## Gaps

- Exact dependency versions and integrity hashes await executable packaging.
- Automated notice/SBOM generation is not yet configured.

