# Design document map

`design/` records the desired product, including current direction and future
work. It may describe behavior not yet implemented. Current code truth belongs
in `specs/`.

- [`product.md`](product.md) — users, goals, non-goals, and product principles.
- [`pipeline.md`](pipeline.md) — desired staged asset derivation and promotion
  model.
- [`shape_aware_geometry.md`](shape_aware_geometry.md) — primitive-aware visual
  cleanup, reconstruction, and safe fallbacks.
- [`collision.md`](collision.md) — collision strategy, editing model, and engine
  handoff.
- [`web_ui.md`](web_ui.md) — intentional web workflow, job model, preview, and
  container operation.
- [`security.md`](security.md) — hostile-input, process, storage, and deployment
  boundaries.
- [`research_basis.md`](research_basis.md) — primary-source research decisions
  and adoption boundaries.
- [`roadmap.md`](roadmap.md) — release slices and native-optimization triggers.

## Gaps

- User testing has not yet established final preset names or default quality
  budgets.
- A representative cross-category benchmark corpus still needs to be selected.