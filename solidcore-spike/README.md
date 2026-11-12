# SolidCore WASM spike

This isolated crate tests the backend boundary without importing SolidCraft's application engine, command registry, document model, timeline, or UI.

The smoke test executes this typed operation chain directly:

1. `solidcore_ops::make_box`
2. `solidcore_ops::boolean::boolean`
3. `Body::to_record`
4. `solidcore_tess::plan` and `TessPlan::evaluate`

The public result is a JSON summary of the resulting B-rep and display mesh. The browser test page is served at `/solidcore-test/` after the normal Vite build.

SolidCore is pinned to commit `bfc2b6c7660a8609b172c1a0ba459ab0b3de3a8b`; update deliberately, not implicitly.

This is a backend feasibility spike, not the final graph/runtime design. In particular, `solidcore-ops` currently owns a process-wide session kernel. The WASM module therefore runs inside its own module/worker boundary. A production graph evaluator should own evaluation lifetime, graph values and caching itself, and should not make SolidCraft engine commands into graph nodes.
