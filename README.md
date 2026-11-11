# build123d-flow

A browser-based visual programming environment for build123d.

The project combines Vue Flow with the browser-side Python/OCCT runtime demonstrated by [jojain/build123d-sandbox](https://github.com/jojain/build123d-sandbox). It is intended to turn build123d's real Python API into a typed, inspectable node catalog and evaluate connected node graphs into CAD geometry.

## Architecture

- **Vue 3 + TypeScript + Vue Flow** — graph editor and generated node UI.
- **PrimeVue Aura** — the sole component design system for controls, node cards, inputs, palette, popovers and split layout; app CSS is limited to workspace layout and Vue Flow geometry.
- **CAD iconography** — vendored FreeCAD Part/Draft SVGs for modeling, sketch and transform operations, plus a small in-house glyph set for dataflow concepts. See `public/icons/cad/README.md` for attribution and licenses.
- **Pyodide** — Python runtime in the browser.
- **build123d + OCP.wasm** — exact B-rep/NURBS geometry, running in WebAssembly.
- **Python introspection** — the core catalog is registered during runtime bootstrap; the full build123d API is discovered only when the user opens the palette, keeping reflection off the first-geometry path.
- **Graph evaluator** — validates dependencies, evaluates nodes in topological order and reports node-scoped errors.
- **Three.js viewport** — renders tessellated previews produced inside Pyodide; exact OCCT shapes remain in the Python runtime for downstream operations and STEP export.

There is no required Python server. Python, build123d and the geometry kernel run in the browser.

## Current status

Early scaffold. The initial implementation includes dynamic API discovery, curated geometry constructors (Box, Cylinder, Sphere, Cone, Torus, Rectangle, Circle, Ellipse and RegularPolygon), a Vue Flow graph, Python graph evaluation and a Three.js mesh preview. Browser package loading and CAD behavior still need validation against the pinned OCP.wasm wheels.

## Development

Requirements: Node.js 22+ and npm.

```sh
npm install
npm run dev
```

The GitHub Actions workflow typechecks and builds the static client, then deploys it to GitHub Pages on pushes to `main`. The workspace, starter graph and small node catalog render synchronously. Pyodide is downloaded only after the app mounts; OCP.wasm and build123d are installed in the background. Full API introspection is deferred until the palette is opened. Repeat-visit caching and worker isolation remain future optimizations.

## Current limitations

- Each graph evaluation currently recomputes the full graph in topological order; incremental caching is not implemented yet.
- Socket inference is annotation-driven. Unsupported or ambiguous Python types appear as `Any` and are not directly editable. The core dataflow catalog currently includes editable Number/String/Boolean literals, scalar arithmetic, math functions/constants, comparisons, boolean logic, string operations, conversions, build123d Vector operations, and a Python Function node. Common solid and 2D profile constructors are explicitly registered so they remain available even when generic reflection cannot infer a usable signature. The rest of the installed build123d API is discovered from Python modules and signatures; callables that expose only variadic parameters or positional-only signatures are still omitted; variadic parameters are ignored when named parameters can be represented, and ambiguous annotations appear as Any. The Python Function node receives optional `a`, `b`, and `c` inputs, has access to `math`, `operator`, `b3d`, and `Vector`, and must assign its output to `result`.
- The first preview path tessellates explicitly visible geometry-node outputs into triangle meshes; per-node eye controls toggle viewer visibility. The authoritative B-rep stays inside Pyodide. Viewer camera framing is initialized once and is preserved across subsequent evaluations. The browser bootstrap installs OCP.wasm and build123d; it deliberately omits `lib3mf`, `ocp_vscode` and `ocp-tessellate`, because the app exports STEP and has its own Three.js mesh viewer. Python bootstrap output is surfaced in the runtime status. The initial canvas now opens with two parameterized `Box` nodes connected to a `Union` node, so the first run exercises a real multi-node graph.
- Graph persistence, incremental evaluation caching, and multi-output nodes are not implemented yet.

## Design principles

1. Discover the installed Python API; do not hand-maintain a parallel list of build123d functions.
2. Separate API discovery from node eligibility and presentation policy.
3. Keep graph documents declarative and serializable. Never persist Python object instances.
4. Treat Python objects as runtime values; keep them inside the Pyodide interpreter.
5. Re-evaluate on graph changes with stale-run suppression; incremental caching is a follow-up.
6. Make node/socket types explicit, with controlled compatibility and conversions.
7. Keep helpers and implementation details out of the user-facing node catalog.
8. Never silently expose arbitrary Python execution; user-authored code stays explicit in a clearly named Python Function node.

## License

Apache-2.0.
