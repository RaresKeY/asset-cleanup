# Product design

## Outcome

Asset Cleanup turns an opaque sequence of mesh tools into an inspected,
reproducible asset build. A user can bring a retained TRELLIS capture, a
generated output, or an ordinary mesh; understand what it contains; build
multiple candidates; compare their quality and cost; author suitable collision;
and export a complete asset package without overwriting the source.

## Users

- A game developer converting generated meshes into runtime props.
- A technical artist who wants automatic first passes with editable handoff.
- A build engineer applying the same policy to a batch of assets.
- A maintainer comparing algorithms with evidence rather than face count alone.

## Product principles

1. **Immutable source.** Every operation writes beneath a new candidate.
2. **Inspect first.** Input kind, scene inventory, topology, materials, scale,
   and primitive evidence determine the available routes.
3. **Stages, not magic.** Repair, visual geometry, UV/bake, texture, collision,
   runtime packing, and validation remain independently visible.
4. **Quality is bounded error.** Triangle count is a budget and acceptance gate,
   not the definition of an optimum.
5. **Shape awareness is evidence.** Planes, cylinders, spheres, cones, and boxes
   are used only when residual and boundary tests support them.
6. **Collision is authored separately.** The cheapest interaction-correct
   representation wins.
7. **CLI truth, UI clarity.** The CLI and web UI expand to the same typed recipe;
   manifests store numbers, not only preset names.
8. **Honest capabilities.** Missing adapters or unsafe routes fail with an
   actionable reason rather than silently falling back.

## Source classes

| Source | Preferred path | Boundary |
|---|---|---|
| TRELLIS post-stage capture plus replay contract | Replay/clean/reduce before UV and PBR bake | The capture is not a universal mesh format; it requires its producer adapter |
| Polygon PLY plus appearance reference | Treat PLY as HIGH, build LOW, unwrap and rebake | PLY faces may be mixed polygons and materials are not guaranteed |
| GLB or glTF | Preserve scene/material inventory, make conservative derived candidates | Existing seams and baked textures restrict direct topology edits |
| OBJ/STL/ordinary PLY | Import geometry, normalize explicitly, then inspect and process | Units, transforms, materials, and orientation may be incomplete |
| Point or Gaussian PLY | Classify and reject from triangle-only stages or route to a future reconstruction adapter | It is not a triangle mesh merely because the file extension is PLY |

## Non-goals

- Claiming one triangle count or preset is optimal for every asset.
- Replacing deliberate hero-asset retopology or art direction.
- Guaranteeing texture identity after arbitrary topology changes.
- Running untrusted user scripts or arbitrary shell fragments.
- Treating final GLB as the only editable source.

## Gaps

- Final accessibility and keyboard-navigation review requires the implemented UI.
- Project-specific runtime export presets beyond generic glTF and Godot are not
  yet prioritized.
- Multi-user authentication and remote worker orchestration are post-standalone
  concerns.

