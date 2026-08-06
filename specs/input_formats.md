# Input formats and intake

## Contract

Intake classifies bytes, not a filename extension. `probe_source` returns a
`SourceProbe` with source kind, detected format/media type, support status,
reason, and bounded header facts. `load_scene` accepts only a supported probe,
checks bundle references, then loads through `trimesh.load_scene(process=False)`.

| Content | Core status | Important rule |
|---|---|---|
| GLB 2.0 | Supported | Header version and declared length must match the file. Embedded JSON is bounded before reference checking. |
| glTF 2.0 JSON | Supported | JSON is bounded; buffer/image URIs must be local, relative, present, and within size limits. `data:` URIs are bounded. |
| Polygon PLY | Supported | Must declare a face element. Point-only/Gaussian PLY is classified as `point-cloud` and rejected from triangle stages. |
| OBJ | Supported | At least one face must occur in the bounded prefix. `mtllib` is a confined relative file. |
| STL | Supported | Binary size must match declared triangles, or ASCII must carry mesh markers. |
| TRELLIS/post `.bin` | Rejected by core | Classified as `trellis-post`; a pinned producer-specific replay adapter is required. |
| ZIP/archive | Rejected by core | Bundle intake belongs to a future bounded web-intake layer. |

## Loader bounds

Recipe limits default to 1 GiB input, 50M vertices, 50M triangles, 10k meshes,
100k nodes, 256M texture pixels, 1 hour runtime, and 8 GiB memory. The core
loader applies input bytes, vertices, faces, geometries, nodes, and bounded
texture-dimension preflight. Header/JSON/data-URI limits are fixed `LoadLimits`
defaults. A non-finite position, non-triangle geometry, unsafe texture header,
or over-limit scene is rejected.

GLB/glTF URI handling rejects schemes other than bounded `data:`, network or
absolute paths, `..` traversal, missing paths, and over-limit files. It does
not make network requests.

## Source preservation and web intake

CLI intake copies the primary file and every confined local GLB/glTF/OBJ member
into `00_source/`, preserving relative paths and member hashes. Web intake is a
single file, so external glTF buffers/images and OBJ `mtllib` members cannot be
supplied beside the temporary upload and are rejected.

## Scene semantics

Inspection and export retain a `trimesh.Scene`'s local geometry definitions and
graph transforms. Collision and whole-asset measurements deliberately build a
derived world-space concatenation of instantiated mesh nodes. This distinction
must remain explicit: the latter is not a scene-preserving visual export path.

## Gaps

- CLI parsing has POSIX runtime/memory child isolation; equivalent non-POSIX
  parser isolation remains a gap.
- Skins, animation, morph targets, unknown glTF extensions, point clouds, and
  TRELLIS replay need dedicated compatibility tests before broader support.
