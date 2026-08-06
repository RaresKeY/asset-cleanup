# Current Python component boundary

`pyproject.toml` declares version ranges; `uv.lock` resolves the installation.
The following versions were resolved for this core revision. They are used as
libraries, not vendored source. See root notices for licenses and attribution.

| Component | Resolved | Responsibility / ownership boundary |
|---|---:|---|
| NumPy | 2.5.1 | Array math and deterministic numeric data handling. |
| SciPy | 1.18.0 | Spatial/numerical helpers used transitively by geometry/collision work. |
| trimesh | 4.12.2 | Scene/mesh parsing, scene export, repair helpers, bounds and mesh operations. Asset Cleanup owns source classification, limits, and policy around it. |
| fast-simplification | 0.1.13 | In-process QEM candidate reduction. Asset Cleanup owns target/error gating and records observed results. |
| CoACD | 1.0.11 optional | Convex decomposition used only by an explicit `coacd` collision mode. |
| Pydantic | 2.13.4 | Strict typed recipe model and JSON Schema. |
| PyYAML | 6.0.3 | Safe recipe YAML loading/serialization. |
| Typer | 0.27.1 | CLI declaration and argument conversion. |
| Rich | 14.3.4 | Human-readable CLI tables/output. |
| NetworkX | 3.6.1 | Available core dependency; no present public graph contract relies on it. |
| Pillow | 12.3.0 | Image dependency reserved for material/texture work; no current texture transformation. |

The active `web` extra uses aiosqlite 0.22.1 for the locked service dependency,
FastAPI 0.141.1 for routes/lifespan, python-multipart 0.0.32 for streamed
uploads, and Uvicorn 0.52.1 for serving. Development tools include pytest 8.4.2,
pytest-cov 7.1.0, Ruff 0.16.1, mypy 1.20.2, httpx 0.28.1, and types-PyYAML
6.0.12.20260724. The service owns storage, policy, path confinement, child
process limits, and response semantics around these libraries.

The locked frontend package set is React/React DOM 19.1.1, Three.js 0.179.1,
Vite 7.3.6, Vitest 3.2.7, TypeScript 5.9.2, and `@vitejs/plugin-react` 4.7.0. The Dockerfile
uses Node 22 Bookworm Slim only in the build stage, Python 3.12 Slim Bookworm at
runtime, and uv 0.11.33 as the lockfile installer/build helper.

## Gaps

- Reproducibility must be tested against the exact lockfile on Linux, macOS,
  and Windows; optional CoACD wheels need a support matrix.
- `networkx` and `pillow` need either an exercised runtime contract or removal
  from core dependencies before the first stable release.
