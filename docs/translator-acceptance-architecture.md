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

## Implemented acceptance entry points

- `scripts/generate_type_catalog.py` discovers the complete API from the pinned native
  `build123d==0.13.0` / `cadquery-ocp-novtk==8.0.1.1.0` environment. The artifact records
  the build123d, OCP, Python, and catalog-schema versions.
- `tests/upstream-examples.json` is the single pinned 67-example corpus manifest.
- `public/python/python_translator.py` contains the production syntax-to-graph
  translator. It imports without `flow_runtime`, build123d, or OCP and receives the
  versioned `TypeCatalog` explicitly; the runtime supplies only the current graph
  lowering services and discovered node definitions.
- `scripts/test_upstream_translator.py` loads that catalog in a `python -S` process,
  verifies that neither build123d nor OCP is imported, translates every example, and
  validates node definitions, handles, graph connectivity, and topological ordering.
  It saves the exact source and graph for the next gate.
- `scripts/test_upstream_kernel.py` registers the corresponding native callables,
  checks that kernel-backed translation agrees with Gate 1, then executes Gate 1's
  saved graphs and validates tessellation, shape validity, and bounds.
- `tests/test_graph_workflows_native.py` runs the graph-workflow regression suite
  directly in Python against the native kernel.
- Playwright is restricted to `tests/e2e/ui-smoke.spec.ts`; it no longer imports the
  upstream corpus through the browser or settings popup.

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

### Discovery and type registry contract

Discovery is performed against the real installed build123d distribution, not against
a manually curated list of known types. The discovery process must enumerate public
classes across the build123d package modules, including classes that are not eligible
to become palette nodes. It must also discover their declared bases/MRO, constructors,
overloads, methods, operators, properties, fields, enums, annotations, and relevant
value-type serialization contracts.

The generated, versioned catalog has two distinct registries:

- **Type registry:** every discovered public build123d type, with a stable fully
  qualified identity, module, bases/MRO, enum/value classification, and the metadata
  needed to resolve annotations, receiver compatibility, and native operator dispatch.
  Types remain present even when their constructor or members are rejected as graph
  nodes.
- **Node registry:** only callable constructors, methods, properties, operators, and
  app-owned primitives that pass explicit graph-node eligibility checks. Each node
  references type-registry identities rather than relying on guessed class names.

A discovery rejection is scoped to a candidate node/member and records its reason; it
must never delete the corresponding type from the type registry. Unknown or unresolved
type references are reported as catalog-generation errors or explicit unresolved
metadata, not silently mapped to a guessed type.

The discovery command runs in the kernel-enabled environment and serializes plain
JSON data. The interpreter imports only the generated JSON catalog and its own
kernel-free implementation. It must not import the discovery module as a side effect.
The catalog records the build123d distribution version and discovery schema version.
CI regenerates the catalog from the pinned dependency set and verifies that the
committed artifact is current and complete. A kernel upgrade therefore updates the
catalog through discovery rather than through hand edits.

Acceptance tests must prove both directions: every discovered public type is retained
in the type registry, and every emitted node's type references resolve within that
registry. Tests also exercise inheritance and operator dispatch using the serialized
MRO, without importing build123d in the interpreter process.
