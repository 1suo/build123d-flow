# Graph value and data-shape contract

This document is normative for the Python translator, graph editor, evaluator, and every CAD-kernel provider.

## 1. Semantic type identity is open and discovered

A socket's semantic type describes the kind of one value. It is a provider-discovered identity (preferably fully qualified), not a closed TypeScript enum and not a list of CAD-specific names maintained by the editor.

- Native classes, inheritance/MRO, constructors, members, and operators are discovered from the active provider.
- The editor preserves unknown type identities. It must not need a source-code change when a kernel adds a class.
- Compatibility is directional: a value of a discovered subtype may feed a socket accepting one of its base types.
- `Any` means unconstrained/unknown semantic type. It is not a non-geometry marker.

## 2. Data shape is independent of semantic type

Every socket carries a recursive `data_shape` descriptor. The legacy flat `data_structure` field is a migration fallback only.

Supported shapes:

- `item(type)`
- `list(item)`
- `tree(item)` for nested list/branch results
- `tuple(items[])`
- `set(item)`
- `map(key, value)`
- `union(options[])` when an annotation admits alternate value layouts

Examples:

- `Shape` is `item(Shape)`.
- `list[Shape]` is `list(item(Shape))`.
- `list[list[Shape]]` is `tree(list(item(Shape)))`.
- `list[Any]` is `list(item(Any))`; it is not a new semantic type.
- `dict[str, list[Shape]]` is `map(item(String), list(item(Shape)))`.

A collection must never be represented by inventing a semantic CAD type such as `ShapeList`. Native classes that happen to implement iteration remain item values unless they explicitly inherit a collection contract.

## 3. Input access is independent of data shape

Input access determines how the node consumes the connected value:

- `item`: participate in item-wise mapping.
- `structure`: receive the complete collection as one value.
- `tree`: receive the complete branch/tree structure.

An iterable domain object is still one item unless its declared type inheritance says it is a collection. Structural ABC subclass hooks such as `issubclass(value, Collection)` must not be used to infer collection identity from duck-typed iteration.

## 4. Matching determines execution shape

Matching is an execution policy, not a socket type or collection type:

- `longest`: align item-access streams to the longest input, repeating the final item where defined by the evaluator contract.
- `shortest`: stop at the shortest input.
- `cross`: Cartesian product across independent streams.
- Nested list/tree levels are preserved in results; mapping one operation over a tree must not flatten it accidentally.

A node is represented once in the graph. The evaluator applies it to matched values and preserves the corresponding result structure. Whole-structure inputs do not create mapping axes.

## 5. Geometry is a discovered runtime capability

Geometry capability comes from the provider's type registry and is attached to semantic types/items. It is not inferred from a hardcoded list such as Shape/Solid/Face/Edge.

For `Any` and `list[Any]`, the static contract is intentionally open. They remain preview-eligible and are inspected at runtime. Preview extraction recursively searches nested lists, tuples, sets, and mapping values for provider geometry handles/values, with cycle protection. A non-geometry `Any` value simply produces no geometry preview.

## 6. Kernel-neutrality and regression gates

The graph/type/data-shape contract must be testable without importing the CAD kernel. The acceptance order is:

1. Type registry and recursive data-shape contract tests without a kernel.
2. Direct syntax-to-node translation tests without a kernel.
3. Native provider execution of the exact graphs produced by step 2.
4. Native graph workflow regressions.
5. Browser/editor regressions.

Do not add Vector/Rotation/Box/Boolean-specific translator primitives or frontend type allowlists to compensate for a missing generic contract. Native operations must come from discovery; provider-specific behavior belongs behind the provider adapter.
