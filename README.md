# build123d-flow

A browser-based visual programming environment for build123d.

The project combines Vue Flow with the browser-side Python/OCCT runtime demonstrated by [jojain/build123d-sandbox](https://github.com/jojain/build123d-sandbox). It is intended to turn build123d's real Python API into a typed, inspectable node catalog and evaluate connected node graphs into CAD geometry.

## Architecture

- **Vue 3 + TypeScript + Vue Flow** — graph editor and generated node UI.
- **Pyodide** — Python runtime in the browser.
- **build123d + OCP.wasm** — exact B-rep/NURBS geometry, running in WebAssembly.
- **Python introspection** — discovers installed build123d callables and signatures at runtime; a curated core catalog exposes Python math, operators, strings, conversions and vector operations.
- **Graph evaluator** — validates dependencies, evaluates nodes in topological order and reports node-scoped errors.
- **Three.js viewport** — renders tessellated previews produced inside Pyodide; exact OCCT shapes remain in the Python runtime for downstream operations and STEP export.

There is no required Python server. Python, build123d and the geometry kernel run in the browser.

## Current status

Early scaffold. The initial implementation includes dynamic API discovery, a Vue Flow graph, Python graph evaluation and a Three.js mesh preview. Browser package loading and CAD behavior still need validation against the pinned OCP.wasm wheels.

## Development

Requirements: Node.js 22+ and npm.

```sh
npm install
npm run dev
```

The browser downloads Pyodide and the CAD wheels on first launch. This is a relatively large initial download.

## Current limitations

- Each graph evaluation currently recomputes the full graph in topological order; incremental caching is not implemented yet.
- Socket inference is annotation-driven. Unsupported or ambiguous Python types appear as `Any` and are not directly editable. The core dataflow catalog currently includes scalar arithmetic, math functions/constants, comparisons, boolean logic, string operations, conversions and build123d Vector operations.
- The first preview path tessellates output shapes into triangle meshes; the authoritative B-rep stays inside Pyodide. The browser bootstrap installs only the OCP.wasm wheels and build123d; it deliberately omits `ocp_vscode` and `ocp-tessellate`, because the app has its own Three.js mesh viewer. Python bootstrap output is surfaced in the runtime status. The initial canvas now opens with two parameterized `Box` nodes connected to a `Union` node, so the first run exercises a real multi-node graph.
- Graph persistence, incremental evaluation caching, and multi-output nodes are not implemented yet.

## Design principles

1. Discover the installed Python API; do not hand-maintain a parallel list of build123d functions.
2. Separate API discovery from node eligibility and presentation policy.
3. Keep graph documents declarative and serializable. Never persist Python object instances.
4. Treat Python objects as runtime values; keep them inside the Pyodide interpreter.
5. Re-evaluate on graph changes with stale-run suppression; incremental caching is a follow-up.
6. Make node/socket types explicit, with controlled compatibility and conversions.
7. Keep helpers and implementation details out of the user-facing node catalog.
8. Never silently expose arbitrary Python execution as a graph node.

## License

Apache-2.0.
