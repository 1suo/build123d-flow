# Perigram plugin integration

This repository is the build123d provider implementation for Perigram. The
standalone Vue Flow editor is retained only as a development harness while
the provider is moved onto Perigram's canonical graph and plugin contracts.

## Integration boundary

- Python AST translation remains a kernel-free frontend.
- Discovered build123d constructors, methods, operators, and values are
  published as provider-qualified node definitions.
- Semantic identity is independent of transport shape. A discovered type
  name is an open-world nominal identity; `Any` is a valid semantic
  descriptor, not a geometry exclusion.
- Canonical graph documents use Perigram's graph format, pinned operation
  identities, JSON configuration, and source/target node-port edges.
- Native Python/build123d objects remain runtime-owned. Graph JSON contains
  neither Python objects nor serialized B-Rep values.
- Runtime outputs cross the host boundary as scoped references plus declared
  preview representations; CAD preview is a provider-contributed renderer.

## Current adapter

`public/python/perigram_graph.py` is the first kernel-free boundary adapter.
It maps discovered operation metadata to the provider-neutral node contract
and translates the current frontend graph into Perigram graph JSON. It fails
closed when operation identity or edge endpoints are missing.

This is intentionally not yet an execution adapter. The Perigram managed
runtime currently documents a Python runtime descriptor, but the actual
Python worker lifecycle, native dependency installation, opaque handle
ownership, and preview artifact transport must be integrated before claiming
native execution support.

## Acceptance sequence

1. Pure Python tests for semantic descriptors, shape independence, and graph
   document conversion.
2. Validate generated definitions with `@perigram/plugin-sdk/nodes`.
3. Validate converted documents with `packages/core` graph validation.
4. Run translated graphs through the Perigram procedural evaluator using a
   provider dispatch adapter.
5. Integrate isolated Python execution with pinned build123d/OCP dependencies.
6. Add scoped geometry outputs and preview renderer.
7. Exercise install, upgrade, uninstall, and browser authoring end to end.

The host owns graph persistence, canvas, matching/evaluation coordination,
and preview workspace. This provider must not ship a competing graph model,
data-tree implementation, editor, or scheduler.
