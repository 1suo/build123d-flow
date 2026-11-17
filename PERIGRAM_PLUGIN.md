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

## Current adapter and contract tests

`public/python/perigram_graph.py` is a kernel-free adapter. It maps
discovered operations to the provider-neutral node contract and translates
the current frontend graph to Perigram's canonical graph document. It fails
closed on missing operation identity, duplicate node/edge ids, missing edge
endpoints, and non-JSON values.

The adapter tests run without importing build123d or OCP. CI additionally
checks generated node definitions with the actual
`@perigram/plugin-sdk/nodes` validator and graph documents with the actual
`packages/core` graph validator from the pinned Perigram revision `b5e2065f839854c1695aec742faa94dcf3a39824`. This is
stronger than a local copy of those schemas, but it does not yet prove
provider dispatch or runtime execution.

## Remaining production gates

1. Ship a plugin package with a declared JavaScript runtime entry and the
   pinned Pyodide/OCP.wasm assets/dependencies required by that runtime.
2. Register discovered provider definitions through the SDK's node-provider
   contract and prove exact-version dispatch in the Perigram host.
3. Run translated graphs through Perigram's procedural evaluator, preserving
   its list/tree matching and execution-scope semantics.
4. Keep native geometry inside the browser runtime; expose scoped outputs and
   supported preview representations through a declared renderer.
5. Exercise browser authoring plus install, upgrade, uninstall, cancellation,
   memory limits, diagnostics, and deterministic cache invalidation.

The old endpoint-mode Python executor under Perigram's
`workers/build123d` is explicitly transitional and is not the target
architecture. Do not add a parallel endpoint service, Python task worker,
graph model, data-tree implementation, editor, or scheduler to work around
the local-first plugin runtime.
