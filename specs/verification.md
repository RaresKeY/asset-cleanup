# Verification

## Current checks

The bootstrap revision is documentation-only. Review must confirm:

- every link to a repository document resolves;
- `specs/` makes no runtime implementation claim;
- each focused file in `design/`, `specs/`, and `vendored/` contains a `Gaps`
  section;
- no project license file or third-party license text exists under `vendored/`;
- generated-data paths are ignored.

Suggested local checks:

```bash
git diff --check
find design specs vendored -type f -name '*.md' -print
rg '^## Gaps$' design specs vendored
git status --short
```

## Gaps

- Formatting, type, unit, integration, container, and end-to-end checks will be
  added with executable code.
- Link checking is currently a review action rather than automated CI.

