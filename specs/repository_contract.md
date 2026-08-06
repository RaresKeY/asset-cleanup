# Repository contract

## Owned paths

| Path | Current ownership |
|---|---|
| `design/` | Desired product and future design; not implementation proof |
| `specs/` | Current implemented behavior, contracts, boundaries, and checks |
| `vendored/` | Specs-like records for third-party dependencies and adapters |
| `THIRD_PARTY_NOTICES.md` | Third-party attribution and license identifiers/links |
| `src/` | Python application and orchestration code when introduced |
| `native/` | Profile-justified native hot paths when introduced |
| `web/` | Built web-client source when introduced |
| `tests/` | Automated tests and bounded fixtures |

The repository never treats `design/` as proof that a feature exists. A feature
becomes current truth only when code, tests, and the matching `specs/` update
land together.

## Project licensing

No root project license file is present and none may be added without explicit
owner direction. Third-party dependency terms apply to those components only
and remain catalogued separately.

## Generated and private data

Uploaded models, retained captures, generated candidates, job databases,
dependency caches, and local environment files are ignored and must not be
committed. Small synthetic test fixtures require documented provenance.

## Gaps

- Fixture size/provenance limits need to be fixed with the first parser tests.
- Release artifact signing and SBOM retention are not yet specified.
