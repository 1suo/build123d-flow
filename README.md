# build123d-flow

A browser-based visual programming environment for build123d.

The project combines Vue Flow with the browser-side Python/OCCT runtime demonstrated by [jojain/build123d-sandbox](https://github.com/jojain/build123d-sandbox). It is intended to turn build123d's real Python API into a typed, inspectable node catalog and evaluate connected node graphs into CAD geometry.

## Architecture

- **Vue 3 + TypeScript + Vue Flow** — graph editor and generated node UI.
- **PrimeVue Aura** — the sole component design system for controls, node cards, inputs, palette, popovers and split layout; app CSS is limited to workspace layout and Vue Flow geometry.
- **CAD iconography** — vendored FreeCAD Part/Draft SVGs for modeling, sketch and transform operations, plus a small in-house glyph set for dataflow concepts. See `public/icons/cad/README.md` for attribution and licenses.
- **Pyodide** — Python runtime in the browser.
- **build123d + OCP.wasm** — exact B-rep/NURBS geometry, running in WebAssembly.
- **Catalog providers** — native build123d constructors/functions/methods/properties come from Python introspection; Flow utilities and the Python Function node are registered by separate app-owned providers. Every node declares its provenance and palette placement where it is registered. Neither IDs nor names nor implementation modules are used to guess ownership or category.
- **Python introspection** — the core build123d catalog is registered during runtime bootstrap; full module discovery is lazy on first category selection. Discovery records constructors, functions, shape methods and properties with signatures, annotations, defaults and docstrings. Provenance groups in the dock are labels only; functional categories are the selectable controls.
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

The GitHub Actions workflow typechecks and builds the static client, then deploys it to GitHub Pages on pushes to `main`. The empty workspace and core node catalog render synchronously. Example graphs are loaded explicitly from the canvas dropdown. Pyodide is downloaded only after the app mounts; OCP.wasm and build123d are installed in the background. Full API introspection is deferred until the first category is selected. Repeat-visit caching is implemented for deterministic graph nodes and their mesh previews; worker isolation remains a future optimization.

## Current limitations

- Graph evaluation remains topological, but deterministic nodes reuse cached Python results and tessellated previews. Cache keys include the node definition, literal parameters and upstream dependency keys. Stateful builder nodes, Python Function nodes, and discovered instance methods/properties bypass caching; their downstream dependents also bypass it. The in-memory cache is bounded to 128 entries and scoped to the current Pyodide session.
- Socket inference is annotation-driven. Unsupported or ambiguous Python types appear as `Any` and are not directly editable. Flow-owned nodes include editable Number/String/Boolean literals, scalar arithmetic, math functions/constants, comparisons, boolean logic, string operations, conversions, build123d Vector utilities, collections, series, spatial distributions and Grasshopper-style list operations. These are explicit Flow primitives, not discovered build123d API. Union/Difference/Intersection are also app-owned Flow convenience nodes that invoke the corresponding overloaded `Shape` operators; they are deliberately labeled Flow, not build123d. The native build123d constructors, functions, methods and properties are discovered from Python modules and signatures. Callables that expose only variadic parameters or positional-only signatures are still omitted; variadic parameters are ignored when named parameters can be represented, and ambiguous annotations appear as Any. The Python Function node receives optional `a`, `b`, and `c` inputs, has access to `math`, `operator`, `b3d`, and `Vector`, and must assign its output to `result`.
- The first preview path tessellates explicitly visible geometry-node outputs into triangle meshes; per-node eye controls toggle viewer visibility. The authoritative B-rep stays inside Pyodide. Viewer camera framing is initialized once and is preserved across subsequent evaluations. The browser bootstrap installs OCP.wasm and build123d; it deliberately omits `lib3mf`, `ocp_vscode` and `ocp-tessellate`, because the app exports STEP and has its own Three.js mesh viewer. Python bootstrap output is surfaced in the runtime status. The curated node layer includes spatial distributions (XY grid, polar points, clipped 2D Voronoi), numeric remapping/interpolation, and Grasshopper-style list operations (partition, repeat, cull pattern, shift and weave). The discovered build123d API remains available alongside these higher-level nodes.
- Graph persistence and multi-output nodes are not implemented yet. Deterministic graph results and mesh previews are session-local; live B-rep objects are not serialized to browser storage.

## Browser-side runtime persistence

The app registers a small service worker in production. It caches versioned app assets, the Python bootstrap files and the self-hosted Pyodide runtime; the cache is replaced when the service-worker cache version changes. Pyodide core files and the micropip dependency wheels are staged from the checksum-pinned official release during the GitHub Actions build, so the runtime loader and its package manager do not depend on a third-party CDN. Additional Python wheels are installed from PyPI by micropip. The browser's HTTP cache and micropip's browser wheel cache remain additional cache layers.

The installed Python environment is mounted from IndexedDB through Pyodide's `fsInit` hook. Its marker includes the Pyodide version and a SHA-256 fingerprint of `public/python/setup.py`; a mismatch clears the old site-packages directory before reinstalling. If IndexedDB is unavailable, blocked, or full, the runtime falls back to an in-memory environment and remains usable. Browser storage can still be cleared or evicted by the user or browser. This stores installed packages, not graph results or live Python/B-rep objects.

## Design principles

1. Discover the installed build123d API; keep app-owned utilities in a separately declared Flow provider.
2. Require every catalog entry to declare provenance and palette placement. Never infer ownership from a node ID, name or module, and never default missing provenance to build123d.
3. Keep graph documents declarative and serializable. Never persist Python object instances.
4. Treat Python objects as runtime values; keep them inside the Pyodide interpreter.
5. Re-evaluate on graph changes with stale-run suppression and reuse deterministic results through bounded session-local caching.
6. Make node/socket types explicit, with controlled compatibility and conversions.
7. Keep helpers and implementation details out of the user-facing node catalog.
8. Never silently expose arbitrary Python execution; user-authored code stays explicit in a clearly named Python Function node.

## License

Apache-2.0.


## Catalog ownership and provenance

The runtime assembles the catalog from three explicit providers:

- `discover()` reflects the installed build123d package and registers discovered API constructors, functions, methods and properties with `source="build123d"`.
- `_register_flow_nodes()` registers app-owned math, logic, data, collection, spatial and utility primitives with `source="flow"`.
- `_register_flow_boolean_nodes()` registers the Union/Difference/Intersection convenience nodes. They call overloaded `Shape` operators, but the nodes themselves are app-authored adapters and therefore have `source="flow"`.
- `_register_python_nodes()` registers the Python Function node with `source="python"`.

Each provider also supplies `palette_category` and `palette_section`. The old name-substring taxonomy and the default-to-build123d provenance fallback have been removed. Catalog construction rejects entries that omit either field. The three Boolean node IDs retain their historical `build123d_flow.*` spelling for graph compatibility; the explicit `source` field is authoritative.

## Discovery and evaluation

The dock separates API provenance (build123d, Python/libraries, Flow) from functional categories. Provenance headings are non-interactive; each functional category is an individual selector. Selecting a category opens its node list. Search inside the list spans the complete discovered catalog.

Full API discovery is deferred until the first category is opened. It walks build123d modules, then inspects public classes/functions and public shape-class properties/methods without evaluating descriptors. The discovery report records visited modules, import failures, accepted/rejected candidates and inferred socket types. Stateful context-manager builders are excluded because their lifecycle cannot be represented by a single node call. Instance methods/properties are limited to shape and value-like geometry receivers; private, uninspectable and non-build123d callables are rejected. A separate `_NODE_METADATA_OVERRIDES` policy can hide or rename canonical node IDs and can explicitly set palette category/section. It cannot change provenance. Missing source or palette metadata fails catalog validation.

Evaluation cache entries retain Python objects inside Pyodide (including exact B-rep values); only JSON-safe graph parameters and dependency keys participate in hashing. Mesh previews are cached separately. No Python object is serialized into the graph document or browser storage.


## Findspot palettes

The canvas theme selector uses the palettes from [1suo/findspot](https://github.com/1suo/findspot) as its source of truth: Alexandria, Babylon, Knossos, Petra, Tikal and Troy. Each palette also has a `-swapped` option that exchanges only its background and surface roles, matching the Findspot variants. The selected palette is persisted locally in the browser. Theme tokens drive the canvas, nodes, controls, status UI and 3D viewport; Tikal is the fallback.
