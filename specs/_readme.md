# Specification map

`specs/` is committed project memory for current implementation truth. Desired
or future behavior belongs in `design/` until it exists. Every behavior change
updates its focused spec in the same PR.

- [`repository_contract.md`](repository_contract.md) — current source ownership,
  directory roles, and documentation rules.
- [`project_state.md`](project_state.md) — what this bootstrap revision actually
  implements and does not implement.
- [`verification.md`](verification.md) — checks required at the current stage.

## Gaps

- Runtime, source, recipe, manifest, CLI, API, web, and adapter specs will be
  added with their implementations.
