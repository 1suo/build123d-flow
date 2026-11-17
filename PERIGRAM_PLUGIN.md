# Perigram plugin integration

This repository is the build123d provider implementation for Perigram. The
standalone Vue Flow editor is retained only as a development harness; it is
not a second production graph editor or execution coordinator.

## Integration boundary

- Python AST translation remains a kernel-free frontend.
- Discovered build123d constructors, methods, operators, and values are
  published as provider-qualified node definitions.
- Semantic identity is independent of transport shape. Discovered types use
  open-world nominal identities; `Any` is a valid semantic descriptor and
  is not a geometry exclusion. The adapter consumes discovered `type_id`
  and `type_provider` metadata rather than inferring CAD types from a
  hardcoded name family.
- Runtime value reconstruction resolves discovered classes and exported
  aliases through the type/symbol registries. Vector/rotation-specific
  coercion tables, vector helper-node families, and implicit collection-shape
  inference have been removed; collection shape must be explicit metadata.
- Canonical documents use Perigram's exact `perigram.graph` format,
  versioned operations, JSON configuration, and node-port edges.
- Native Python/build123d objects remain runtime-owned. Graph JSON contains
  neither Python objects nor serialized B-Rep values.
- The target execution model is local-first browser execution: a declared
  JavaScript plugin runtime hosts Pyodide and OCP.wasm. The JavaScript worker
  boundary carries JSON metadata and opaque/serialized output references,
  never live Python or kernel objects.
- Perigram owns graph persistence, graph evaluation coordination, data-tree
  matching, plugin lifecycle, and preview workspace. The provider owns AST
  translation, type/API discovery, and CAD runtime implementation.

## Runtime adapter and contract tests

- `perigram-listing.json` declares the `build123d-pyodide` JavaScript runtime
  and its root-relative `/perigram-runtime.js` entry.
- `public/perigram-runtime.js` implements Perigram's
  `createRuntime()` contract. It starts the nested Pyodide worker, publishes
  dynamically discovered provider definitions, dispatches each node's inputs
  and configuration, and terminates/restarts the Python worker on cancellation.
- `public/python/perigram_graph.py` is kernel-free. It maps discovered
  operations and translated graphs to Perigram's provider-neutral node
  contract and canonical graph document.
- `public/python/perigram_runtime.py` owns execution-scoped Python values,
  opaque cross-worker handles, node-output retention, and a persistent
  generator frame for nested build123d context managers. Native Python/CAD
  objects never cross the worker boundary.
- The Perigram host's plugin-runtime bridge now strips its host-only
  `readSet` recorder before structured-cloning graph invocations into the
  plugin worker. That host change and its regression test are on Perigram's
  `staging` branch (commit `10ac4f58`).
- CI validates the listing and node definitions against the actual pinned
  Perigram SDK/core, runs kernel-free Python tests, tests the JavaScript
  runtime entry, and runs a real Pyodide/OCP nested BuildPart/BuildSketch
  integration test.

The internal scope binding/result operations are marked preview-safe
(`read`) because they only manage runtime-local builder lifecycle; they do
not request host capabilities or perform external effects. This is necessary
because Perigram's current procedural preview evaluator accepts only pure/read
operations.

## Remaining production gates

1. Run the actual browser-host integration against the Perigram staging
   deployment, including plugin install, provider registration, graph preview,
   and cancellation. The CI runtime tests currently use the real Pyodide/OCP
   engine but a worker-contract harness for the Perigram host.
2. Deliver tessellated CAD preview artifacts through a declared Perigram
   preview surface/renderer; native opaque handles are in place, but preview
   serialization and renderer lifecycle are not yet implemented.
3. Add graph-run/session cleanup hooks so completed or superseded previews
   release all opaque handles immediately rather than relying on the bounded
   runtime-session LRU.
4. Exercise browser authoring plus install, upgrade, uninstall, concurrent
   previews, memory limits, diagnostics, and deterministic cache invalidation.

The old endpoint-mode Python executor under Perigram's
`workers/build123d` is explicitly transitional and is not the target
architecture. Do not add a parallel endpoint service, Python task worker,
graph model, data-tree implementation, editor, or scheduler to work around
the local-first plugin runtime.
