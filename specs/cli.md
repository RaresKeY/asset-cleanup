# Command-line interface

The console entry point is `asset-cleanup`. Errors raised from the application
boundary are formatted as concise messages and exit with code 2. `validate`
uses code 4 for a non-passing report; `doctor` uses code 5 when core capability
or temporary-workspace checks fail.

| Command | Contract |
|---|---|
| `inspect INPUT` | Bounded inspection. `--json` prints full JSON; `--output` writes it. `--seed` and `--samples` control deterministic evidence. |
| `plan INPUT` | Resolves recipe/preset plus options and prints a non-mutating plan. |
| `run INPUT --output DIR` | Creates a new candidate package. `--dry-run` prints plan without writing. `--print-recipe` prints the expansion. |
| `validate INPUT` | Performs bounded parser and structural/topology validation; optional `--output` writes JSON. |
| `capabilities [--json]` | Lists Python package and executable availability/version probes. |
| `doctor` | Checks NumPy, SciPy, trimesh, fast-simplification, and temporary write access. |
| `serve` | Runs local FastAPI, optional embedded worker, and an optional built frontend. |
| `worker` | Runs the persistent local worker, or drains queued jobs with `--once`. |
| `compare MANIFEST...` | Displays status, recipe hash prefix, artifact count, and warning count without selecting a winner. |
| `package RUN_DIR --output FILE.zip` | Archives manifest, artifact index, resolved recipe, and only manifest-registered artifacts from a completed candidate/accepted run. Output must be outside immutable `RUN_DIR`. |
| `recipe init/validate/explain/schema` | Writes a preset, validates/hash-identifies a recipe, explains its implications, or emits JSON Schema. |

`inspect`, `plan`, and `validate` resolve aliases before writing and reject an
output that would replace the primary source or a discovered source-bundle
member. Inspection and validation accept explicit input-byte, texture-pixel,
wall-time, and memory ceilings and execute parser work in a child process.

`plan` and `run` share these policy flags: `--recipe`, `--preset`, `--body-type`,
`--collision-mode`, `--simplify-mode`, `--faces`, `--ratio`, `--max-faces`,
`--target-error`, `--allow-attribute-loss`, `--uv-policy`, `--seed`, and repeated
`--set dotted.path=value`. Values for `--set` are parsed as safe YAML scalars;
the resulting complete document is Pydantic-validated, so unknown/mistyped
fields fail rather than silently pass through.
`--faces` and `--ratio` are mutually exclusive; supplying a target without an
explicit simplification mode selects target mode, while supplying both a target
and `--target-error` selects hybrid mode.

## Gaps

- Machine-oriented error objects, stable per-command exit-code taxonomy, batch
  input, and shell completion are not yet specified.
- `serve` is a local deployment primitive, not an authentication or production
  reverse-proxy configuration system.
