# Asset Cleanup

`asset-cleanup` is a standalone, reproducible post-generation pipeline for
inspecting, repairing, simplifying, collision-authoring, validating, and
packaging 3D assets.

The project is designed for retained TRELLIS/TRELLIS.2 captures, generated PLY
or GLB outputs, shaped derivatives, and ordinary GLB, glTF, OBJ, PLY, and STL
meshes. It treats source geometry, visual candidates, collision, authoring
files, runtime outputs, and proof as separate artifacts.

The central rule is simple: preserve the source, diagnose before changing it,
and make every derived candidate reproducible from an explicit recipe and
manifest.

## Project memory

- [`design/_readme.md`](design/_readme.md) maps the desired product and future
  design.
- [`specs/_readme.md`](specs/_readme.md) maps implemented behavior and current
  repository contracts.
- [`vendored/_readme.md`](vendored/_readme.md) records external dependencies,
  adapters, and source ownership without storing third-party license text.
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) is the only project-owned
  notice catalogue for third-party components.

## Status

The repository is being bootstrapped in reviewable slices. The design and
project-memory contract land first, followed by the processing core and CLI,
then the containerized web application.

No project license has been granted. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for dependency attribution;
those third-party terms do not license this repository's own code or content.
