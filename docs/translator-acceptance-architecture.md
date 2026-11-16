# Translator acceptance test architecture

## Required gate order

The acceptance pipeline is strictly serial. A later gate must not start unless every
required test in the preceding gate passes.

1. **Interpreter / translation (no CAD kernel, no browser).** Run the production
   Python-to-node translator directly in a normal Python test process. Feed it the
   pinned upstream build123d examples corpus. Assert that every supported example
   produces a complete, valid node graph: node identities/types, parameters, typed
   references, data/control-flow edges, execution scopes, and ordering dependencies.
   Assert that unsupported syntax is reported with source location; it must never be
   silently dropped. This stage must not import or initialize build123d, OCP, Pyodide,
   a browser, or any CAD backend.
2. **Graph execution (real CAD kernel, no browser).** Only after Gate 1 is green,
   execute the graphs produced by the same translator through the production graph
   evaluator and real build123d kernel. Validate execution errors and meaningful
   geometry invariants (validity, solids, volume, bounds, and topology where relevant).
3. **Application integration (browser).** Only after Gate 2 is green, run a small
   Playwright smoke suite for application boot, editor/settings interactions, graph
   display, and browser integration. UI failures must not block or obscure Gate 1
   diagnostics.

## Architectural requirements

- The production translator must be importable independently of the CAD evaluator.
  Move translation, syntax handling, graph construction, and graph validation into a
  kernel-free module. Do not create a second test-only translator.
- The translator receives an explicit, versioned node/API catalog as data. Catalog
  loading and translation must not import build123d or OCP. The catalog must describe
  the real production node contracts; do not use a reduced mock catalog in acceptance
  tests.
- Keep CAD execution and kernel-backed introspection in a separate runtime module.
  The execution stage consumes the exact graph output produced by Gate 1.
- Pin the upstream examples repository commit and enumerate the complete intended
  corpus. Do not silently omit, quarantine, or replace failing examples. Any
  unsupported upstream example must fail acceptance with a source-located diagnostic
  until its syntax is implemented or an explicit product-scope decision is recorded.
- Save per-example structured results (pass/fail, graph validation errors, source
  location, and traceback). A failure must identify the exact example and stage.
- CI must run gates in order and stop immediately on failure. Do not use long timeouts,
  retries, browser startup, or settings-popup interaction as substitutes for fixing
  translator behavior.

## Required commands / CI shape

Provide distinct commands for the three stages, e.g. `test:translator`,
`test:kernel`, and `test:e2e`. The CI job must enforce:

```sh
npm run build
npm run typecheck
python -m pytest tests/translator
# Only if Gate 1 passes:
python -m pytest tests/kernel
# Only if Gate 2 passes:
npm run test:e2e
```

The translator suite must be runnable locally without installing or importing the CAD
kernel. The kernel suite may install the real kernel dependencies. Playwright is
reserved for UI behavior and is not an acceptance harness for Python translation.
