# Engineering Contract — build123d-flow

This file is binding project guidance for every contributor and coding agent. Read it before changing code.

## 1. What this project is

**build123d-flow is a visual, bidirectional engineering environment for build123d.**

It is not a toy node editor, a collection of hand-authored CAD approximations, a Python-shaped UI, or a demo that merely renders a few primitives.

The product lets an engineer:
1. Build parametric CAD definitions as a visual dataflow graph.
2. Use the real installed build123d API, with accurate signatures, types, defaults, and behavior.
3. Import supported build123d Python programs into an equivalent graph.
4. Export graphs to standalone build123d Python.
5. Execute the graph in the browser using the real Python/build123d/OCCT runtime.
6. Inspect resulting geometry and export real CAD geometry, including STEP.

The graph, imported Python, exported Python, and evaluated B-rep must agree semantically. The graph is an alternate representation of an engineering program, not a simplified substitute.

The intended users are engineers. Correct geometry, predictable dataflow, semantic fidelity, and trustworthy errors take priority over visual polish, implementation convenience, or passing toy tests.

## 2. Non-negotiable engineering rules

### 2.1 Use the real upstream API; do not monkey-patch it

**Never monkey-patch, wrap, replace, or mutate build123d, OCP, OCCT, Pyodide, or other upstream runtime classes, methods, functions, descriptors, modules, or prototypes to make translator behavior or tests pass.**

This explicitly forbids:
- Assigning replacement functions or lambdas to upstream class attributes.
- Wrapping or replacing upstream methods at import/bootstrap time.
- Modifying upstream modules, class dictionaries, descriptors, or prototypes.
- Replacing a failing upstream operation with a hand-written approximation while presenting it as native behavior.
- Catching an upstream exception and silently changing the meaning of the operation.
- Hiding version-specific patches in runtime initialization.

A failing upstream call is evidence to investigate, not permission to alter the dependency.

The only exception is a separately approved, documented upstream fork/vendor patch that is itself the intended dependency implementation, reviewed as such, and tested independently. It must never be smuggled into application runtime code.

**Correct approach:** find the defect in our own discovery, parsing, lowering, type/shape inference, argument binding, graph evaluation, or serialization. Correct our code to call the supported upstream API with the correct receiver, arguments, types, ordering, and lifecycle. If the upstream library itself is defective, pin a known-good upstream release or raise/fix the upstream issue; do not silently mutate it at runtime.

### 2.2 No test weakening

Never remove, skip, quarantine, xfail, or filter a failing upstream example to get a green run. Never reduce acceptance coverage, replace real browser execution with mocks, weaken assertions, loosen expected geometry, hide errors, or treat timeouts as success.

A test may change only when its previous expectation is demonstrably incorrect relative to the documented product contract or upstream build123d behavior. Explain the evidence and add a regression test.

### 2.3 No false completion claims

A commit is not a fix. A green typecheck is not a working CAD translator. A successful build is not an acceptance pass. A passing subset is not a passing suite.

Never claim fixed, ready to test, complete, or deployed without reporting exact checks and results. Distinguish unit tests, Python runtime tests, full upstream examples acceptance, Playwright browser tests, production build, deployment, and deployed-app verification. Do not infer CI success from a commit existing.

## 3. Product architecture and source of truth

- **Frontend:** Vue 3 + TypeScript + Vue Flow; owns the graph editor and interaction.
- **Execution:** Pyodide in a dedicated browser worker.
- **Geometry:** the actual pinned build123d and OCP.wasm/OCCT packages. Exact B-rep objects remain inside Python.
- **Rendering:** Three.js displays meshes derived from runtime geometry. A mesh is a preview, not the authoritative CAD model.
- **Catalog:** discover the installed build123d API from runtime introspection. App-owned Flow utilities and Python facilities are registered by separate providers with explicit provenance and palette metadata.
- **Interchange:** Python AST import lowers supported Python semantics into graph structure; export emits executable build123d Python. Neither direction may claim support for semantics it does not preserve.

Do not duplicate build123d behavior with a parallel CAD kernel or invent lookalike nodes where the real API can be represented. Do not infer API ownership or palette placement from names, IDs, or module-name heuristics.

## 4. Graph and Python semantic contract

The graph is a dataflow representation of Python/build123d operations. It must preserve Python evaluation semantics wherever a construct is accepted.

### 4.1 Node invocation and list mapping

A node represents one operation. When an input socket receives a collection designated for item-wise mapping, **the node is invoked once per item**, and its outputs are collected in corresponding order.

For example, one node receiving a list of faces maps over those faces and returns a list of results. The user must not need to duplicate the node for each face.

Mapping is a graph evaluation rule, not a different implementation of the underlying build123d operation. The callable receives the individual item, not the entire list, unless its actual API signature expects a list.

### 4.2 Nested and multiple streams

Nested mapping must preserve structure. A nested collection produces a nested result tree at the corresponding depth; it must not be flattened, zipped, or converted into an accidental Cartesian product.

The mapping policy must be explicit and deterministic:
- Single mapped stream: invoke once per item, preserving order.
- Nested stream: recurse at each collection level and preserve each level in the result.
- Multiple related streams: follow the graph's declared dependency/iteration relationship; do not guess between zip, nested mapping, and Cartesian product.
- Independent Cartesian-product iteration is valid only when explicitly represented by source/graph semantics.
- Incompatible or ambiguous stream shapes must produce a clear, node-scoped error rather than silently changing structure.

Loop-carried dependencies, dependent inner iterables, scalar values, tuple unpacking, and post-loop values must preserve their Python meaning. Do not flatten nested results merely to make downstream sockets easier to handle.

### 4.3 Python import/export

The importer must parse source without executing it during import. It must preserve source semantics for every construct it claims to support.

Unsupported syntax must fail explicitly with a useful source location and reason. Never silently drop a statement, substitute a different operation, or claim successful import with missing behavior.

Exported Python must be executable against the pinned build123d API. For supported programs, import → graph → export must preserve behavior. Validate by executing both sides and comparing meaningful results, especially geometry, topology, dimensions, and collection structure.

### 4.4 Types and arguments

- Derive catalog signatures, annotations, defaults, and callable behavior from the installed runtime.
- Respect Python positional-only, keyword-only, variadic, default, bound-method, and constructor semantics.
- Never coerce arguments merely because a conversion makes a test pass. Every conversion must be justified by Python/build123d semantics and covered by tests.
- Distinguish scalar values from iterable values. A float must never be treated as an iterable because of mistaken shape inference or mapping.
- Preserve None, booleans, numbers, strings, tuples, lists, dictionaries, sets, and geometry objects as distinct runtime values.

### 4.5 Stateful builders and lifecycle

build123d builder/context-manager APIs have lifecycle and scope semantics. Do not expose them as ordinary one-shot nodes unless the graph model explicitly represents their enter/body/exit lifecycle and nesting. A construct that cannot be represented faithfully must be reported as unsupported, not approximated.

### 4.6 Kernel-agnostic procedural node boundary

The procedural graph and Python translator are kernel-independent. build123d is one provider, not the graph's ontology or execution model.

- The graph IR describes operations, values, dependencies, collection structure, execution scopes, and effects. It must not require build123d class names, module paths, or OCP objects to define core graph semantics.
- Kernel/provider adapters own discovery and binding of native constructors, functions, methods, properties, operators, type relationships, and runtime values. A provider maps its real Python API into the shared graph contracts; it does not rewrite that API.
- Node identity is provider-qualified and stable. Display names and concrete class names are not dispatch rules. Calls resolve through provider metadata and Python's actual callable/MRO semantics, including overloads and argument kinds.
- The importer is a language front end: parse Python AST, resolve Python names and protocols, then lower supported constructs into the shared IR. It must not contain a growing list of build123d-specific special cases in place of general Python semantics.
- The evaluator executes IR operations through the selected provider. It preserves Python values and collection shape, and it distinguishes scalar values, collection structure, item mapping, and nested mapping.
- Builder/context-manager lifecycles, loop scopes, class/function scopes, and side effects are explicit IR constructs when supported. They must never be approximated as stateless calls.
- A provider-neutral test fixture must exercise discovery, call resolution, argument binding, operator dispatch, collection mapping, scopes, and import/export without importing build123d. build123d acceptance tests then validate the provider adapter against the real kernel.
- Any feature not yet represented faithfully must fail with a source-located diagnostic. Do not special-case an upstream example to make it appear supported.

This section is an architectural requirement, not a claim that the current implementation already satisfies it. Delivery status must identify any remaining build123d coupling in the importer, evaluator, or graph schema.

## 5. Testing contract

The authoritative validation target is the real browser application using the pinned runtime and actual upstream build123d examples.

For every engineering change, add focused regression coverage at the layer where the defect exists, then run the broader suite.

Before declaring delivery, verify:

1. **Production build first:** run the production build. If it fails, stop and fix it before proceeding to other checks.
2. **Typecheck:** run the complete TypeScript/Vue typecheck.
3. **Python tests:** run the complete Python unit/regression suite.
4. **Graph workflow tests:** run all graph semantic/workflow tests, including nested mapping and import/export behavior.
5. **Upstream acceptance:** run the full configured upstream build123d examples acceptance suite. No hand-picked subset may stand in for it.
6. **Playwright:** run the complete browser suite against the built application and real browser-side Python/OCCT runtime. Verify import, graph evaluation, geometry preview, export, and failure reporting.
7. **CI:** inspect actual workflow and job results for the exact commit being delivered.
8. **Deployment:** deploy only after all required checks pass. Then open the deployed app and verify its version and critical workflows in a real browser.

If an environment, quota, dependency, or infrastructure problem prevents a check, report it as blocked/not run. Never represent it as passed.

## 6. Engineering workflow

1. Read this contract and inspect the current implementation before editing.
2. Reproduce the failure with the smallest faithful case.
3. Identify the responsible layer and explain the root cause in code-level terms.
4. Implement the semantic fix in application-owned code, using supported upstream APIs.
5. Add a regression test that fails before the fix and passes after it.
6. Run build-first validation and the required broader suites.
7. Inspect the diff for accidental behavior changes, test weakening, hidden fallbacks, and upstream monkey-patching.
8. Commit only coherent, validated changes. Keep main deployable; do not present experimental patches as delivery.
9. Verify CI and, when eligible, the deployed application.

Do not respond to a blocked tool/environment by inventing progress, making an unverified commit, or asking the user to do routine engineering work that can be performed autonomously.

## 7. Definition of done

The project is ready for engineering use only when:
- The visual graph correctly represents and evaluates supported build123d/Python semantics.
- One-node-per-item mapping and nested result-tree behavior are correct and covered.
- Supported upstream examples import and execute successfully in the real browser runtime.
- Graph export produces executable, semantically equivalent build123d Python.
- Geometry remains real build123d/OCCT B-rep geometry and can be inspected/exported.
- Unsupported constructs fail explicitly without corrupting the graph.
- All required build, typecheck, Python, upstream acceptance, Playwright, and CI checks pass on the delivered commit.
- The deployed application is verified after deployment.

**Do not lower this definition of done to match the current implementation. Improve the implementation until it meets the contract, or state precisely what remains incomplete.**

## 8. Repository CI and deployment source of truth

- The authoritative pipeline is `.github/workflows/ci.yml` (workflow name: **Build and deploy**).
- It runs on pushes to `main`, pull requests, and manual dispatch. The production bundle build is the first validation step; typecheck, Python compilation/unit tests, and Playwright follow it.
- The full browser acceptance suite is `tests/e2e/upstream-examples.spec.ts`; do not confuse its 67-example upstream corpus with the focused graph workflow tests staged separately for the browser worker.
- Production deployment is GitHub Pages through `actions/upload-pages-artifact` and `actions/deploy-pages`. Do not report Vercel as the production deployment target for this repository.
- Verify the workflow run for the exact delivered commit, including the build job and deploy job where applicable. A helper that returns only pull-request-triggered workflow runs is not sufficient evidence for a push-triggered `main` run; use the repository's actual Actions run and job results.
- After deployment, verify the GitHub Pages application URL and the deployed commit/version. A successful Pages deployment job alone does not prove the browser CAD workflows work.
