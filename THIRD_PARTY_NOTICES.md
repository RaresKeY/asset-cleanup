# Third-party notices

This repository has no project license. The terms below apply only to the
identified third-party components and do not grant rights in this repository's
code, documentation, or assets. Full upstream license texts remain with their
distributions; they are intentionally not copied under `vendored/`.

Resolved versions below are from the current `uv.lock`. Package source is
[PyPI](https://pypi.org/) unless an upstream link is shown.

## Runtime and optional components

| Component | Version | License | Authoritative source |
|---|---:|---|---|
| [fast-simplification](https://github.com/pyvista/fast-simplification) | 0.1.13 | MIT | Upstream repository |
| [NetworkX](https://networkx.org/) | 3.6.1 | BSD-3-Clause | Upstream project |
| [NumPy](https://numpy.org/) | 2.5.1 | BSD-3-Clause | Upstream project |
| [Pillow](https://python-pillow.org/) | 12.3.0 | HPND | Upstream project |
| [Pydantic](https://github.com/pydantic/pydantic) | 2.13.4 | MIT | Upstream repository |
| [PyYAML](https://pyyaml.org/) | 6.0.3 | MIT | Upstream project |
| [Rich](https://github.com/Textualize/rich) | 14.3.4 | MIT | Upstream repository |
| [SciPy](https://scipy.org/) | 1.18.0 | BSD-3-Clause | Upstream project |
| [trimesh](https://github.com/mikedh/trimesh) | 4.12.2 | MIT | Upstream repository |
| [Typer](https://github.com/fastapi/typer) | 0.27.1 | MIT | Upstream repository |
| [CoACD](https://github.com/SarahWeiii/CoACD) (optional) | 1.0.11 | MIT | Upstream repository |

## Development and web runtime

| Component | Version | License | Authoritative source |
|---|---:|---|---|
| [httpx](https://github.com/encode/httpx) | 0.28.1 | BSD-3-Clause | Upstream repository |
| [mypy](https://github.com/python/mypy) | 1.20.2 | MIT | Upstream repository |
| [pytest](https://github.com/pytest-dev/pytest) | 8.4.2 | MIT | Upstream repository |
| [pytest-cov](https://github.com/pytest-dev/pytest-cov) | 7.1.0 | MIT | Upstream repository |
| [Ruff](https://github.com/astral-sh/ruff) | 0.16.1 | MIT | Upstream repository |
| [aiosqlite](https://github.com/omnilib/aiosqlite) (web extra) | 0.22.1 | MIT | Upstream repository |
| [FastAPI](https://github.com/fastapi/fastapi) (web extra) | 0.141.1 | MIT | Upstream repository |
| [python-multipart](https://github.com/Kludex/python-multipart) (web extra) | 0.0.32 | Apache-2.0 | Upstream repository |
| [Uvicorn](https://github.com/Kludex/uvicorn) (web extra) | 0.52.1 | BSD-3-Clause | Upstream repository |
| [Starlette](https://github.com/Kludex/starlette) (FastAPI dependency) | 1.4.1 | BSD-3-Clause | Upstream repository |
| [React](https://github.com/facebook/react) | 19.1.1 | MIT | Upstream repository |
| [React DOM](https://github.com/facebook/react) | 19.1.1 | MIT | Upstream repository |
| [Three.js](https://github.com/mrdoob/three.js) | 0.179.1 | MIT | Upstream repository |
| [Vite](https://github.com/vitejs/vite) | 7.3.6 | MIT | Upstream repository |
| [TypeScript](https://github.com/microsoft/TypeScript) | 5.9.2 | Apache-2.0 | Upstream repository |
| [Vite React plugin](https://github.com/vitejs/vite-plugin-react) | 4.7.0 | MIT | Upstream repository |
| [Vitest](https://github.com/vitest-dev/vitest) | 3.2.7 | MIT | Upstream repository |
| [ESLint](https://github.com/eslint/eslint) and `@eslint/js` | 9.35.0 | MIT | Upstream repository |
| [typescript-eslint](https://github.com/typescript-eslint/typescript-eslint) | 8.42.0 | MIT | Upstream repository |
| [eslint-plugin-react-hooks](https://github.com/facebook/react) | 5.2.0 | MIT | Upstream repository |
| [eslint-plugin-react-refresh](https://github.com/ArnaudBarre/eslint-plugin-react-refresh) | 0.4.20 | MIT | Upstream repository |
| [DefinitelyTyped React/Node/Three types](https://github.com/DefinitelyTyped/DefinitelyTyped) | locked in `web/package-lock.json` | MIT | Upstream repository |
| [globals](https://github.com/sindresorhus/globals) | 16.3.0 | MIT | Upstream repository |

## Build and container components

| Component | Version/reference | License | Authoritative source |
|---|---:|---|---|
| [Node.js](https://github.com/nodejs/node) build image | 22 Bookworm Slim | MIT | Upstream project |
| [Python](https://www.python.org/) build/runtime image | 3.12 Slim Bookworm | PSF-2.0 | Upstream project |
| [uv](https://github.com/astral-sh/uv) build helper | 0.11.33 | MIT OR Apache-2.0 | Upstream repository |
| [Khronos glTF Validator](https://github.com/KhronosGroup/glTF-Validator) native CLI | 2.0.0-dev.3.10, Linux amd64 | Apache-2.0; bundled upstream `NOTICES` contains Dart and transitive terms | [Official release](https://github.com/KhronosGroup/glTF-Validator/releases/tag/2.0.0-dev.3.10) |

Transitive Python dependencies are resolved in `uv.lock`; frontend transitives
are resolved in `web/package-lock.json`. Both accompany their own upstream
distributions. A release SBOM/notice-generation process will replace this manual
catalogue before a redistributable container or binary release.

The OCI image retains the validator's complete upstream `LICENSE` at
`/usr/share/licenses/gltf-validator/LICENSE` and `NOTICES` at
`/usr/share/doc/gltf-validator/NOTICES`. The archive itself is not committed
to this repository.

## Gaps

- Copyright notices and complete transitive dependency attribution need automated
  SBOM generation before distributing a bundled application image or wheel.
- Automated notice/SBOM generation, signed-artifact verification,
  upstream-advisory monitoring, and Linux arm64 validator redistribution remain
  unresolved.
