"""Kernel-free Python-to-graph translator.

The parser is independent of the CAD evaluator. The caller supplies the active
production catalog/runtime contract; no CAD package is imported here.
"""

from __future__ import annotations

import ast
import copy
import enum
import importlib
import importlib.metadata
import inspect
import itertools
import json
import math
import operator
import pkgutil
import hashlib
import re
import sys
import textwrap
import typing
import types
import collections
import collections.abc
from type_catalog import CatalogError, TypeCatalog


_MISSING = object()

class TranslatorServices(typing.NamedTuple):
    """Narrow runtime services required to lower syntax into graph records."""

    catalog_nodes: dict
    internal_node_ids: set
    catalog_callables: dict
    python_getitem: object
    catalog_signatures: dict
    catalog_hint_targets: dict
    python_getattr: object
    socket_data_structure: object
    type_name: object
    discovered_input_type_sets: object
    api_signature: object
    call_with_coercion: object
    python_loop_last: object
    resolve_type_constructor: object


def import_python_code(source: str, services: TranslatorServices, catalog: TypeCatalog):
    """Parse a conservative, non-executing Python subset into the native graph format.

    Imports are inspected, never executed. Unsupported statements fail with a source
    location instead of being silently dropped or evaluated.
    """
    if not isinstance(services, TranslatorServices):
        raise TypeError("Translator services must use the TranslatorServices contract")
    if not isinstance(catalog, TypeCatalog):
        raise TypeError("Translator requires an explicit versioned TypeCatalog")
    # Bind the current production catalog/evaluator contract explicitly for this translation.
    # Translation must never receive or inspect live CAD-kernel objects.
    b3d = None
    _CATALOG = services.catalog_nodes
    _INTERNAL_NODE_IDS = services.internal_node_ids
    _CATALOG_CALLABLES = services.catalog_callables
    _python_getitem = services.python_getitem
    _CATALOG_SIGNATURES = services.catalog_signatures
    _CATALOG_HINT_TARGETS = services.catalog_hint_targets
    _python_getattr = services.python_getattr
    _socket_data_structure = services.socket_data_structure
    _type_name = services.type_name
    _discovered_input_type_sets = services.discovered_input_type_sets
    _api_signature = services.api_signature
    _call_with_coercion = services.call_with_coercion
    _python_loop_last = services.python_loop_last
    _resolve_type_constructor = services.resolve_type_constructor

    import ast
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Python source is empty")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError("Python syntax error at line {}:{}: {}".format(
            exc.lineno or 1, exc.offset or 1, exc.msg
        )) from exc

    aliases = {"build123d": "b3d"}
    imported = {}
    viewer_modules = {}
    viewer_functions = set()
    stdlib_modules = {}
    stdlib_symbols = {}
    stdlib_star_modules = set()
    external_modules = {}
    external_symbols = {}
    safe_stdlib_modules = {"math", "copy", "functools", "itertools", "operator", "logging", "timeit", "random", "typing"}
    viewer_api = {"show", "show_object", "show_all", "reset_show", "set_defaults"}
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for item in statement.names:
                if item.name == "build123d":
                    aliases[item.asname or item.name] = "b3d"
                elif item.name == "ocp_vscode":
                    viewer_modules[item.asname or item.name] = "ocp_vscode"
                elif item.name in safe_stdlib_modules:
                    stdlib_modules[item.asname or item.name] = item.name
                elif item.name == "scipy" or item.name.startswith("scipy."):
                    external_modules[item.asname or item.name.split(".")[0]] = item.name
                else:
                    raise ValueError("Unsupported import at line {}: {}".format(
                        statement.lineno, item.name))
        elif isinstance(statement, ast.ImportFrom):
            if statement.module == "build123d" or (statement.module and statement.module.startswith("build123d.")):
                for item in statement.names:
                    if item.name != "*":
                        imported[item.asname or item.name] = item.name
            elif statement.module == "ocp_vscode":
                for item in statement.names:
                    if item.name == "*":
                        viewer_functions.update(viewer_api)
                    elif item.name in viewer_api:
                        viewer_functions.add(item.asname or item.name)
                    else:
                        raise ValueError("Unsupported ocp_vscode import at line {}: {}".format(
                            statement.lineno, item.name))
            elif statement.module in safe_stdlib_modules:
                module = importlib.import_module(statement.module)
                for item in statement.names:
                    if item.name == "*":
                        stdlib_star_modules.add(statement.module)
                    elif hasattr(module, item.name):
                        stdlib_symbols[item.asname or item.name] = (statement.module, item.name)
                    else:
                        raise ValueError("Unsupported standard-library import at line {}: {}".format(
                            statement.lineno, item.name))
            elif statement.module and statement.module.startswith("scipy."):
                for item in statement.names:
                    if item.name == "*":
                        raise ValueError("Star imports from external modules are not supported (line {})".format(statement.lineno))
                    external_symbols[item.asname or item.name] = (statement.module, item.name)
            else:
                raise ValueError("Only build123d, ocp_vscode, and approved standard-library imports are supported (line {})".format(statement.lineno))

    def is_viewer_call(node):
        if not isinstance(node, ast.Call):
            return False
        function = node.func
        if isinstance(function, ast.Name):
            return function.id in viewer_functions
        if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
            return viewer_modules.get(function.value.id) == "ocp_vscode" and function.attr in viewer_api
        return False

    # Import is an explicit action, so load the full API catalog lazily here.
    # This makes class methods/properties available without slowing normal startup.
    definitions = _CATALOG
    type_catalog = catalog

    def discovered_subtype(actual_type, expected_type):
        try:
            return type_catalog.is_subtype(actual_type, expected_type)
        except CatalogError:
            return False

    def discovered_iterable(type_name):
        try:
            return bool(type_catalog.resolve_type(type_name).get("supports_iteration"))
        except CatalogError:
            return False

    def resolve_member(member_name, receiver_type, property_only=False):
        """Resolve a member by the receiver's discovered Python MRO order."""
        candidates = []
        receiver_mro = []
        if receiver_type and receiver_type != "Any":
            try:
                receiver_mro = type_catalog.resolve_type(receiver_type)["mro"]
            except CatalogError:
                receiver_mro = []
        for candidate_id, candidate in definitions.items():
            is_property = ".property." + member_name in candidate_id or ".field." + member_name in candidate_id
            if property_only:
                if not is_property:
                    continue
            elif candidate_id.rsplit(".", 1)[-1] != member_name:
                continue
            receiver = next((item for item in candidate.get("inputs", [])
                             if item.get("name") in {"self", "cls"}), None)
            if receiver is None:
                continue
            accepted = receiver.get("variants") or [receiver.get("type", "Any")]
            if receiver_type in accepted:
                score = 3
            elif any(discovered_subtype(receiver_type, accepted_type) for accepted_type in accepted):
                score = 2
            elif "Any" in accepted:
                score = 1
            else:
                continue
            mro_rank = len(receiver_mro) + 1
            for accepted_type in accepted:
                try:
                    owner_id = type_catalog.resolve_type(accepted_type)["id"]
                except CatalogError:
                    continue
                if owner_id in receiver_mro:
                    mro_rank = min(mro_rank, receiver_mro.index(owner_id))
            candidates.append((score, -mro_rank, candidate_id))
        # Never guess a receiver type when static inference is unavailable.
        # Selecting an arbitrary discovered method by name binds the wrong
        # implementation. Defer to native getattr/method dispatch instead,
        # preserving Python's runtime MRO and descriptor semantics.
        if not candidates:
            return None, ()
        best_score = max(score for score, _, _ in candidates)
        best_rank = max(rank for score, rank, _ in candidates if score == best_score)
        winners = tuple(sorted(
            candidate_id for score, rank, candidate_id in candidates
            if score == best_score and rank == best_rank
        ))
        return (winners[0], winners) if len(winners) == 1 else (None, winners)

    # Python's subscription protocol is a language-level operation, not a
    # build123d API member. Register it as a reusable graph primitive so
    # indexing works for sequences, mappings, and geometry collections alike.
    _PYTHON_SUBSCRIPT_ID = "core.python.subscript"
    _PYTHON_SLICE_ID = "core.python.slice"
    _PYTHON_ATTRIBUTE_ID = "core.python.attribute"
    _PYTHON_ATTRIBUTE_STRUCTURE_ID = "core.python.attribute.structure"
    _INTERNAL_NODE_IDS.update({
        _PYTHON_SUBSCRIPT_ID, _PYTHON_SLICE_ID,
        _PYTHON_ATTRIBUTE_ID, _PYTHON_ATTRIBUTE_STRUCTURE_ID,
    })
    definitions[_PYTHON_SUBSCRIPT_ID] = {
        "id": _PYTHON_SUBSCRIPT_ID, "name": "Python Subscript",
        "source": "python", "kind": "operation", "category": "Internal",
        "palette_category": "Internal", "palette_section": "Graph lowering",
        "inputs": [
            {"name": "value", "type": "Any", "required": True, "data_access": "structure"},
            {"name": "index", "type": "Any", "required": True},
        ],
        "outputs": [{"name": "result", "type": "Any"}],
    }
    definitions[_PYTHON_SLICE_ID] = {
        "id": _PYTHON_SLICE_ID, "name": "Python Slice",
        "source": "python", "kind": "operation", "category": "Internal",
        "palette_category": "Internal", "palette_section": "Graph lowering",
        "inputs": [
            {"name": "start", "type": "Any", "required": False, "default": None},
            {"name": "stop", "type": "Any", "required": False, "default": None},
            {"name": "step", "type": "Any", "required": False, "default": None},
        ],
        "outputs": [{"name": "result", "type": "Any"}],
    }
    definitions[_PYTHON_ATTRIBUTE_ID] = {
        "id": _PYTHON_ATTRIBUTE_ID, "name": "Python Attribute",
        "source": "python", "kind": "operation", "category": "Internal",
        "palette_category": "Internal", "palette_section": "Graph lowering",
        "inputs": [
            {"name": "value", "type": "Any", "required": True, "data_access": "item"},
            {"name": "name", "type": "String", "required": True},
        ],
        "outputs": [{"name": "result", "type": "Any"}],
    }
    definitions[_PYTHON_ATTRIBUTE_STRUCTURE_ID] = {
        "id": _PYTHON_ATTRIBUTE_STRUCTURE_ID, "name": "Python Collection Attribute",
        "source": "python", "kind": "operation", "category": "Internal",
        "palette_category": "Internal", "palette_section": "Graph lowering",
        "inputs": [
            {"name": "value", "type": "Any", "required": True, "data_access": "structure"},
            {"name": "name", "type": "String", "required": True},
        ],
        "outputs": [{"name": "result", "type": "Any"}],
    }
    _CATALOG_CALLABLES[_PYTHON_SUBSCRIPT_ID] = _python_getitem
    _CATALOG_SIGNATURES[_PYTHON_SUBSCRIPT_ID] = inspect.Signature([
        inspect.Parameter("value", inspect.Parameter.POSITIONAL_ONLY),
        inspect.Parameter("index", inspect.Parameter.POSITIONAL_ONLY),
    ])
    _CATALOG_HINT_TARGETS[_PYTHON_SUBSCRIPT_ID] = False
    _CATALOG_CALLABLES[_PYTHON_SLICE_ID] = slice
    _CATALOG_SIGNATURES[_PYTHON_SLICE_ID] = inspect.Signature([
        inspect.Parameter("start", inspect.Parameter.POSITIONAL_ONLY, default=None),
        inspect.Parameter("stop", inspect.Parameter.POSITIONAL_ONLY, default=None),
        inspect.Parameter("step", inspect.Parameter.POSITIONAL_ONLY, default=None),
    ])
    _CATALOG_HINT_TARGETS[_PYTHON_SLICE_ID] = False
    _CATALOG_CALLABLES[_PYTHON_ATTRIBUTE_ID] = _python_getattr
    _CATALOG_CALLABLES[_PYTHON_ATTRIBUTE_STRUCTURE_ID] = _python_getattr
    _CATALOG_SIGNATURES[_PYTHON_ATTRIBUTE_ID] = inspect.signature(_python_getattr)
    _CATALOG_SIGNATURES[_PYTHON_ATTRIBUTE_STRUCTURE_ID] = inspect.signature(_python_getattr)
    _CATALOG_HINT_TARGETS[_PYTHON_ATTRIBUTE_ID] = False
    _CATALOG_HINT_TARGETS[_PYTHON_ATTRIBUTE_STRUCTURE_ID] = False
    by_name = {}
    for definition_id, definition in definitions.items():
        by_name.setdefault(definition.get("name"), definition_id)
        by_name.setdefault(definition_id.rsplit(".", 1)[-1], definition_id)

    graph_nodes, graph_edges = [], []
    variables = {}
    user_functions = {
        statement.name: statement for statement in tree.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    user_classes = {
        statement.name: statement for statement in tree.body
        if isinstance(statement, ast.ClassDef)
    }
    class_stack = []
    stream_variables = set()
    mapping_matching = "longest"
    active_loop_frames = []
    active_scope_ids = []
    execution_scopes = []
    scope_result_nodes = {}
    active_conditions = []
    scope_counter = 0
    counter = 0

    def add_node(definition_id, params, links, line, map_over_active_loops=True):
        nonlocal counter
        definition = definitions.get(definition_id)
        if definition is None:
            raise ValueError("No registered node for '{}' at line {}".format(definition_id, line))
        counter += 1
        node_id = "import_{}".format(counter)
        builder_scope_active = any(
            next((scope.get("name") for scope in execution_scopes if scope.get("id") == scope_id), None)
            in {"BuildPart", "BuildSketch", "BuildLine"}
            for scope_id in active_scope_ids
        )
        # A Python loop is a data-mapping scope: each operation is represented
        # once and receives one item from every active iteration sequence.
        # Synthetic iteration sockets make even loop-body operations that do
        # not explicitly read the target execute once per iteration.
        node_definition = dict(definition)
        node_definition["inputs"] = [dict(socket) for socket in definition.get("inputs", [])]
        node_definition["outputs"] = [dict(socket) for socket in definition.get("outputs", [])]
        links = list(links)
        for condition_index, condition in enumerate(active_conditions):
            condition_name = "__flow_condition_{}".format(condition_index)
            node_definition["inputs"].append({
                "name": condition_name, "type": "Boolean", "required": True,
                "data_access": "item",
            })
            if condition[0] != "ref":
                raise ValueError("Conditional graph operations require graph-backed predicates")
            links.append((condition_name, condition[1], condition[2]))
        receiver_socket = next(
            (socket for socket in node_definition["inputs"] if socket.get("name") in {"self", "cls"}),
            None,
        )
        receiver_link = next(
            (link for link in links if receiver_socket and link[0] == receiver_socket["name"]),
            None,
        )
        receiver_output = None
        if receiver_link:
            source_node = next((item for item in graph_nodes if item["id"] == receiver_link[1]), None)
            if source_node:
                receiver_output = next(
                    (socket for socket in source_node.get("data", {}).get("definition", {}).get("outputs", [])
                     if socket.get("name") == receiver_link[2]),
                    None,
                )
        linked_item_types = []
        linked_item_sockets = []
        for input_name, source_id, source_output_name in links:
            source_node = next((item for item in graph_nodes if item["id"] == source_id), None)
            if source_node:
                source_output = next(
                    (socket for socket in source_node.get("data", {}).get("definition", {}).get("outputs", [])
                     if socket.get("name") == source_output_name),
                    None,
                )
                if source_output:
                    linked_item_sockets.append(source_output)
                    if source_output.get("item_type") not in (None, "", "Any"):
                        linked_item_types.append(source_output.get("item_type"))
        for output_socket in node_definition["outputs"]:
            # Resolve collection element identity from the discovered runtime
            # type catalog, never from a GroupBy/ShapeList special case.
            if (
                output_socket.get("item_type") in (None, "", "Any")
                and output_socket.get("data_structure") in {"list", "tree", "set"}
            ):
                try:
                    iterable_record = type_catalog.resolve_type(output_socket.get("type", ""))
                    discovered_item_type = iterable_record.get("iterable_item_type")
                    if discovered_item_type:
                        output_socket["item_type"] = discovered_item_type
                except CatalogError:
                    pass
            if receiver_output and output_socket.get("variants"):
                receiver_type = receiver_output.get("type", "Any")
                variants = output_socket["variants"]
                if receiver_type in variants:
                    output_socket["type"] = receiver_type
                    for key in ("is_geometry", "semantic_type_id", "type_mro", "runtime_inspectable"):
                        if key in receiver_output:
                            output_socket[key] = receiver_output[key]
                else:
                    try:
                        receiver_record = type_catalog.resolve_type(receiver_type)
                        compatible = [
                            variant for variant in variants
                            if variant != "Any" and discovered_subtype(receiver_type, variant)
                        ]
                        if compatible:
                            selected_variant = min(
                                compatible,
                                key=lambda variant: len(type_catalog.resolve_type(variant)["mro"]),
                            )
                            output_socket["type"] = selected_variant
                            selected_record = type_catalog.resolve_type(selected_variant)
                            output_socket["semantic_type_id"] = selected_record["id"]
                            output_socket["type_mro"] = selected_record.get("mro", [])
                            if selected_record.get("is_geometry"):
                                output_socket["is_geometry"] = True
                    except CatalogError:
                        pass
            if output_socket.get("item_type") in (None, "", "Any") and receiver_output:
                inherited_item_type = receiver_output.get("item_type")
                if inherited_item_type and inherited_item_type != "Any":
                    output_socket["item_type"] = inherited_item_type
                    for key in ("item_is_geometry", "item_semantic_type_id", "item_type_mro"):
                        if key in receiver_output:
                            output_socket[key] = receiver_output[key]
                    if receiver_output.get("item_types"):
                        output_socket["item_types"] = receiver_output["item_types"]
            if (
                output_socket.get("item_type") in (None, "", "Any")
                and (
                    output_socket.get("data_structure") in {"list", "tree", "set"}
                    or discovered_iterable(output_socket.get("type", ""))
                )
                and linked_item_types
            ):
                output_socket["item_type"] = linked_item_types[0]
                source_item = next(
                    (item for item in linked_item_sockets
                     if item.get("item_type") == linked_item_types[0]),
                    None,
                )
                if source_item:
                    for key in ("item_is_geometry", "item_semantic_type_id", "item_type_mro"):
                        if key in source_item:
                            output_socket[key] = source_item[key]
        iteration_links = []
        for index, frame in enumerate(active_loop_frames if map_over_active_loops else []):
            # Only an item-access input triggers this loop's mapping. A
            # structure-access input may depend on the loop while still
            # carrying a whole collection (for example face.edges()); that
            # value must stay intact while the collection node executes once
            # per enclosing iteration.
            has_mapped_dependency = False
            for input_name, source_id, _ in links:
                input_socket = next(
                    (socket for socket in node_definition["inputs"]
                     if socket.get("name") == input_name),
                    None,
                )
                access = input_socket.get("data_access") if input_socket else "item"
                if access is None and input_socket is not None:
                    access = (
                        "structure"
                        if _socket_data_structure(input_socket) != "item"
                        else "item"
                    )
                if access == "item" and graph_depends_on(source_id, {frame["node_id"]}):
                    has_mapped_dependency = True
                    break
            if has_mapped_dependency:
                continue
            # A lexical context manager may consume the loop stream itself
            # (for example Locations((x, 0)) inside a loop). In that case its
            # builder context already represents the complete mapped set, so
            # body operations that do not otherwise read the loop item execute
            # once inside that context instead of replaying it for every item.
            scope_consumes_loop = False
            for scope_id in active_scope_ids:
                binding = next((
                    item for item in graph_nodes
                    if item.get("data", {}).get("scope_binding") == scope_id
                ), None)
                dependencies = (
                    binding.get("data", {}).get("scope_dependencies", [])
                    if binding else []
                )
                if any(graph_depends_on(source_id, {frame["node_id"]}) for source_id in dependencies):
                    scope_consumes_loop = True
                    break
            if scope_consumes_loop:
                continue
            input_name = "__flow_iteration_{}".format(index)
            node_definition["inputs"].append({
                "name": input_name, "type": "Any", "required": True,
                "data_access": "item",
            })
            iteration_links.append((input_name, frame["node_id"], frame["output_name"]))
        data = {"definition": node_definition, "params": params,
                "execution_scope_path": list(active_scope_ids),
                "visible": not active_conditions and not builder_scope_active and any(
                    socket.get("is_geometry") or socket.get("item_is_geometry")
                    or socket.get("runtime_inspectable")
                    for socket in definition.get("outputs", [])
                )}
        if active_loop_frames and map_over_active_loops:
            data["python_loop_sources"] = [
                {
                    "node_id": frame["node_id"],
                    "output_name": frame["output_name"],
                    "matching": frame.get("matching", "longest"),
                }
                for frame in active_loop_frames
            ]
        if map_over_active_loops and mapping_matching != "longest":
            data["data_matching"] = mapping_matching
        graph_nodes.append({
            "id": node_id, "type": "api",
            "position": {"x": 80 + ((counter - 1) % 4) * 300, "y": 80 + ((counter - 1) // 4) * 190},
            "data": data
        })
        for input_name, source_id, output_name in list(links) + iteration_links:
            graph_edges.append({
                "id": "import_edge_{}".format(len(graph_edges) + 1),
                "source": source_id, "target": node_id,
                "sourceHandle": output_name, "targetHandle": input_name
            })
        return node_id

    def add_collection_node(items, structure, line, map_over_active_loops=True):
        """Build a structural list/tuple value from literal and graph-backed items."""
        source_nodes = {item["id"]: item for item in graph_nodes}
        input_sockets = []
        for index, (kind, value, *extra) in enumerate(items):
            # A scalar/geometry stream item is consumed once per mapped
            # iteration. A collection-valued input remains structural, and
            # add_node supplies a separate iteration dependency when needed.
            access = "structure"
            if kind == "ref":
                source = source_nodes.get(value)
                output_name = extra[0] if extra else "result"
                output = next((
                    socket for socket in source["data"]["definition"].get("outputs", [])
                    if socket.get("name") == output_name
                ), None) if source else None
                is_active_loop_item = any(
                    str(frame.get("node_id")) == str(value)
                    and str(frame.get("output_name", "result")) == str(output_name)
                    for frame in active_loop_frames
                )
                if is_active_loop_item or (
                    output is not None and _socket_data_structure(output) == "item"
                ):
                    access = "item"
            input_sockets.append({
                "name": "item_{}".format(index), "type": "Any",
                "required": True, "data_access": access,
            })
        output_structure = structure
        for kind, value, *extra in items:
            if kind != "ref":
                continue
            source = source_nodes.get(value)
            if source is None:
                continue
            output_name = extra[0] if extra else "result"
            output = next((item for item in source["data"]["definition"].get("outputs", [])
                           if item.get("name") == output_name), None)
            if output and _socket_data_structure(output) in {"list", "tuple", "tree"}:
                output_structure = "tree"
                break
        definition_id = "core.python.collect." + hashlib.sha1(
            (structure + ":" + str(len(items))).encode("utf-8")
        ).hexdigest()[:12]
        item_types = []
        tuple_item_types = []
        for kind, value, *extra in items:
            if kind == "literal":
                materialized = materialize_literal(value)
                item_types.append(_type_name(type(materialized)))
                if isinstance(materialized, (list, tuple)):
                    tuple_item_types.append([
                        _type_name(type(item)) for item in materialized
                    ])
                else:
                    tuple_item_types.append(None)
            else:
                item_types.append(
                    extra[1] if len(extra) > 1 and extra[1]
                    else reference_type((kind, value, extra[0] if extra else "result"))
                )
                tuple_item_types.append(None)
        item_type = (
            item_types[0]
            if item_types and item_types[0] != "Any"
            and all(value_type == item_types[0] for value_type in item_types)
            else "Any"
        )
        output_socket = {
            "name": "result", "type": "Any",
            "data_structure": output_structure,
            "item_type": item_type,
        }
        if (
            structure == "list" and tuple_item_types
            and all(value is not None for value in tuple_item_types)
            and len({len(value) for value in tuple_item_types}) == 1
        ):
            width = len(tuple_item_types[0])
            output_socket["item_types"] = [
                types_found[0]
                if all(row[index] == types_found[0] for row in tuple_item_types)
                else "Any"
                for index in range(width)
                for types_found in [[row[index] for row in tuple_item_types]]
            ]
        definition = {
            "id": definition_id, "name": "Python " + ("Tuple" if structure == "tuple" else "List"),
            "source": "python", "kind": "operation", "category": "Internal",
            "palette_category": "Internal", "palette_section": "Graph lowering",
            "inputs": input_sockets,
            "outputs": [output_socket],
        }
        definitions[definition_id] = definition
        _INTERNAL_NODE_IDS.add(definition_id)
        names = [socket["name"] for socket in input_sockets]
        def collect(**values):
            result = [values[name] for name in names]
            return tuple(result) if structure == "tuple" else result
        collect.__signature__ = inspect.Signature([
            inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY) for name in names
        ])
        _CATALOG_CALLABLES[definition_id] = collect
        _CATALOG_SIGNATURES[definition_id] = collect.__signature__
        _CATALOG_HINT_TARGETS[definition_id] = False
        params, links = {}, []
        for index, (kind, value, *extra) in enumerate(items):
            name = "item_{}".format(index)
            if kind == "literal":
                params[name] = literal_json_value(value)
            else:
                links.append((name, value, extra[0]))
        node_id = add_node(
            definition_id, params, links, line,
            map_over_active_loops=map_over_active_loops,
        )
        return ("ref", node_id, "result")

    def reference_output(value):
        if not value or value[0] != "ref":
            return None
        source_node = next((item for item in graph_nodes if item["id"] == value[1]), None)
        outputs = source_node.get("data", {}).get("definition", {}).get("outputs", []) if source_node else []
        return next((item for item in outputs if item.get("name") == value[2]), None)

    def reference_type(value):
        if value[0] == "literal":
            tagged = value[1]
            if isinstance(tagged, dict):
                if isinstance(tagged.get("__flow_object__"), str):
                    return tagged["__flow_object__"]
                if isinstance(tagged.get("__flow_type__"), str):
                    try:
                        return type_catalog.resolve_type(tagged["__flow_type__"])["name"]
                    except CatalogError:
                        pass
            return _type_name(type(materialize_literal(value[1])))
        if len(value) > 3 and value[3] and value[3] != "Any":
            return value[3]
        output = reference_output(value)
        return output.get("type", "Any") if output else "Any"

    def iterable_item_metadata(value):
        output = reference_output(value)
        if output is None:
            return "Any", None
        return output.get("item_type", "Any"), output.get("item_types")

    def add_python_binary_node(method, left, right, line):
        operations = {
            "__add__": operator.add, "__sub__": operator.sub,
            "__mul__": operator.mul, "__truediv__": operator.truediv,
            "__floordiv__": operator.floordiv, "__mod__": operator.mod,
            "__pow__": operator.pow, "__matmul__": operator.matmul, "__and__": operator.and_,
            "__or__": operator.or_, "__xor__": operator.xor,
            "__lshift__": operator.lshift, "__rshift__": operator.rshift,
        }
        reverse_methods = {
            "__add__": "__radd__", "__sub__": "__rsub__",
            "__mul__": "__rmul__", "__truediv__": "__rtruediv__",
            "__floordiv__": "__rfloordiv__", "__mod__": "__rmod__",
            "__pow__": "__rpow__", "__matmul__": "__rmatmul__", "__and__": "__rand__",
            "__or__": "__ror__", "__xor__": "__rxor__",
            "__lshift__": "__rlshift__", "__rshift__": "__rrshift__",
        }
        operation = operations.get(method)
        if operation is None:
            raise ValueError("Unsupported Python scalar operator {} at line {}".format(method, line))
        def operand_type(operand):
            if operand[0] == "ref":
                return reference_type(operand)
            value = operand[1]
            # Value constructors remain typed Python values even when their
            # arguments are compile-time literals. Preserve that native type
            # through operator dispatch instead of treating the encoded object
            # as an untyped dictionary.
            if isinstance(value, dict) and isinstance(value.get("__flow_object__"), str):
                return value["__flow_object__"]
            try:
                return _type_name(type(materialize_literal(value)))
            except Exception:
                return None

        left_type, right_type = operand_type(left), operand_type(right)

        def operator_contract(receiver_type, member_name):
            definition_id, winners = resolve_member(member_name, receiver_type)
            if not definition_id:
                return None
            definition = definitions.get(definition_id)
            if not definition:
                return None
            receiver = next((
                socket for socket in definition.get("inputs", [])
                if socket.get("name") in {"self", "cls"}
            ), None)
            if receiver is None:
                return None
            accepted = receiver.get("variants") or [receiver.get("type", "Any")]
            if receiver_type not in accepted and not any(
                discovered_subtype(receiver_type, accepted_type)
                for accepted_type in accepted
            ):
                return None
            argument = next((
                socket for socket in definition.get("inputs", [])
                if socket.get("name") not in {"self", "cls"}
            ), None)
            output = next(iter(definition.get("outputs", [])), None)
            return definition, receiver, argument, output

        contract = operator_contract(left_type, method)
        receiver_side = "left"
        if contract is None:
            contract = operator_contract(right_type, reverse_methods.get(method))
            receiver_side = "right" if contract is not None else "left"

        def access_for(socket):
            if socket is None:
                return "item"
            access = socket.get("data_access")
            if access is not None:
                return access
            return "structure" if _socket_data_structure(socket) != "item" else "item"

        left_access = right_access = "item"
        left_port_type = right_port_type = "Any"
        result_output = {"name": "result", "type": "Any"}
        if contract is not None:
            definition, receiver_socket, argument_socket, output_socket = contract
            if receiver_side == "left":
                left_access = access_for(receiver_socket)
                right_access = access_for(argument_socket)
                left_port_type = receiver_socket.get("type", "Any")
                right_port_type = argument_socket.get("type", "Any") if argument_socket else "Any"
            else:
                right_access = access_for(receiver_socket)
                left_access = access_for(argument_socket)
                right_port_type = receiver_socket.get("type", "Any")
                left_port_type = argument_socket.get("type", "Any") if argument_socket else "Any"
            if output_socket:
                result_output["type"] = output_socket.get("type", "Any")
                for key in ("data_structure", "item_type", "variants", "semantic_type"):
                    if key in output_socket:
                        result_output[key] = output_socket[key]
        elif method == "__add__" and left_type == right_type == "String":
            result_output["type"] = "String"
        elif left_type in {"Number", "Integer", "Float"} and right_type in {"Number", "Integer", "Float"}:
            result_output["type"] = "Number"

        definition_id = "core.python.binary." + method
        definition = {
            "id": definition_id, "name": "Python " + method,
            "source": "python", "kind": "operation", "category": "Internal",
            "palette_category": "Internal", "palette_section": "Graph lowering",
            "inputs": [
                {"name": "left", "type": left_port_type, "required": True, "data_access": left_access},
                {"name": "right", "type": right_port_type, "required": True, "data_access": right_access},
            ],
            "outputs": [result_output],
        }
        definitions[definition_id] = definition
        _INTERNAL_NODE_IDS.add(definition_id)
        def apply_binary(left, right):
            return operation(left, right)
        _CATALOG_CALLABLES[definition_id] = apply_binary
        _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
            inspect.Parameter("left", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("right", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ])
        _CATALOG_HINT_TARGETS[definition_id] = False
        params, links = {}, []
        for name, operand in (("left", left), ("right", right)):
            if operand[0] == "literal":
                params[name] = literal_json_value(operand[1])
            else:
                links.append((name, operand[1], operand[2]))
        created = add_node(definition_id, params, links, line)
        return ("ref", created, "result", result_output["type"])

    def resolve_callable(node, call_node=None):
        symbol_name = None
        definition_id = None
        if isinstance(node, ast.Name):
            name = imported.get(node.id, node.id)
            symbol_name = name
            try:
                exported_symbol = type_catalog.resolve_symbol(name)
            except CatalogError:
                exported_symbol = None
            if exported_symbol and exported_symbol.get("kind") == "callable":
                exported_id = exported_symbol.get("id")
                definition_id = exported_id if exported_id in definitions else None
            else:
                definition_id = None
            definition_id = definition_id or by_name.get(name) or by_name.get(node.id)
        elif isinstance(node, ast.Attribute):
            path = []
            current = node
            while isinstance(current, ast.Attribute):
                path.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                root = current.id
                owner_name = None
                member_name = None
                if len(path) == 1:
                    owner_name = imported.get(root, root)
                    member_name = path[0]
                elif root in aliases and aliases[root] == "b3d" and len(path) == 2:
                    owner_name, member_name = path[1], path[0]
                if owner_name and member_name:
                    try:
                        owner_symbol = type_catalog.resolve_symbol(owner_name)
                        if owner_symbol.get("kind") == "type":
                            owner_record = type_catalog.resolve_type(owner_symbol["type_id"])
                            if any(
                                ".property." + member_name in member_id
                                or ".field." + member_name in member_id
                                for member_id in owner_record.get("member_node_ids", [])
                            ):
                                return ("literal", {"__flow_attrgetter__": member_name})
                    except CatalogError:
                        pass
                if root in user_classes and len(path) == 1:
                    return resolve_user_class_attribute(user_classes[root], path[0])
                enum_type_name = None
                enum_member_name = None
                if root in aliases and aliases[root] == "b3d":
                    enum_path = list(reversed(path))
                    if len(enum_path) == 2:
                        enum_type_name, enum_member_name = enum_path
                elif len(path) == 1:
                    enum_type_name = imported.get(root, root)
                    enum_member_name = path[0]
                if enum_type_name and enum_member_name:
                    try:
                        return (
                            "literal",
                            type_catalog.resolve_enum_member(
                                enum_type_name, enum_member_name
                            ),
                        )
                    except CatalogError:
                        try:
                            class_value = type_catalog.resolve_class_value(
                                enum_type_name, enum_member_name
                            )
                        except CatalogError:
                            pass
                        else:
                            return ("literal", class_value)
                if root in aliases and aliases[root] == "b3d":
                    symbol_name = path[0] if len(path) == 1 else None
                    definition_id = (
                        by_name.get(path[0]) if len(path) == 1
                        else by_name.get(".".join(reversed(path)))
                    )
                elif root in imported:
                    qualified_name = imported[root] + "." + ".".join(reversed(path))
                    definition_id = by_name.get(qualified_name)
                elif hasattr(b3d, root):
                    qualified_name = root + "." + ".".join(reversed(path))
                    definition_id = by_name.get(qualified_name)
        # Resolve overloaded public classes before returning the ordinary
        # name lookup. by_name contains the canonical class entry too, so an
        # early return here silently bypasses overload selection for classes
        # such as Location.
        # Public build123d aliases (for example Pos) can refer to an overloaded
        # class whose catalog entries are named Pos(v) / Pos(X, Y, Z), rather
        # than a single Pos node. Resolve the alias to its canonical class and
        # choose the overload that accepts this call's positional/keyword form.
        candidate = (
            getattr(b3d, symbol_name, None)
            if b3d is not None and symbol_name else None
        )
        if candidate is None and b3d is not None and isinstance(node, ast.Name):
            candidate = getattr(b3d, imported.get(node.id, node.id), None)
        if inspect.isclass(candidate):
            canonical_name = getattr(candidate, "__name__", "")
            canonical_id = getattr(candidate, "__module__", "") + "." + canonical_name
        else:
            try:
                type_record = type_catalog.resolve_type(symbol_name or "")
                canonical_name = type_record["name"]
                canonical_id = type_record["id"]
            except CatalogError:
                return definition_id
        prefix = canonical_id + ".overload-"
        overload_ids = sorted(
            candidate_id for candidate_id in definitions
            if candidate_id.startswith(prefix)
        )
        if not overload_ids:
            return definition_id or by_name.get(canonical_name)

        if call_node is None:
            return overload_ids[0]
        keyword_names = {
            keyword.arg for keyword in call_node.keywords if keyword.arg is not None
        }

        def literal_argument_shape(argument):
            if isinstance(argument, ast.Tuple):
                return "tuple"
            if isinstance(argument, ast.List):
                return "list"
            if isinstance(argument, ast.Set):
                return "set"
            if isinstance(argument, ast.Dict):
                return "dict"
            if isinstance(argument, ast.Constant):
                return "scalar"
            # Arithmetic expressions over Python numeric values are scalar
            # arguments too. Treating them as unknown makes overloaded
            # constructors (notably Pos/Location and Axis) tie-break to the
            # first discovered overload, often a Vector/sequence overload.
            if isinstance(argument, (ast.BinOp, ast.UnaryOp)):
                numeric_operators = (
                    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
                    ast.Mod, ast.Pow, ast.UAdd, ast.USub,
                )
                if isinstance(argument.op if isinstance(argument, ast.BinOp) else argument.op, numeric_operators):
                    return "scalar"
            return None

        def overload_argument_score(argument, socket):
            shape = literal_argument_shape(argument)
            if shape is None:
                return 0
            access = socket.get("data_access")
            structure = socket.get("data_structure")
            target_types = set(socket.get("variants") or [socket.get("type", "Any")])
            if shape in {"tuple", "list", "set", "dict"}:
                score = 0
                if structure == shape:
                    score += 8
                if access == "structure":
                    score += 5
                elif access == "item":
                    score -= 5
                geometry_types, object_types = _discovered_input_type_sets()
                if target_types.intersection(geometry_types | object_types):
                    score -= 3
                return score
            score = 1 if access in (None, "item") else -1
            value = argument.value
            if isinstance(value, bool):
                score += 3 if "Boolean" in target_types else 0
            elif isinstance(value, (int, float)):
                score += 3 if target_types.intersection({"Number", "Integer", "Float"}) else 0
            elif isinstance(value, str):
                score += 3 if "String" in target_types else 0
            return score

        candidates = []
        for overload_index, overload_id in enumerate(overload_ids):
            sockets = definitions[overload_id].get("inputs", [])
            positional_sockets = [
                socket for socket in sockets
                if socket.get("kind") in {"positional_only", "positional_or_keyword"}
            ]
            has_var_positional = any(socket.get("kind") == "var_positional" for socket in sockets)
            if len(call_node.args) > len(positional_sockets) and not has_var_positional:
                continue
            supplied = {
                positional_sockets[index]["name"]
                for index in range(min(len(call_node.args), len(positional_sockets)))
            }
            if supplied.intersection(keyword_names):
                continue
            known = {socket["name"] for socket in sockets}
            if keyword_names - known:
                continue
            required = {
                socket["name"] for socket in sockets
                if socket.get("required")
                and socket.get("kind") not in {"var_positional", "var_keyword"}
            }
            if required - supplied - keyword_names:
                continue
            score = sum(
                overload_argument_score(argument, positional_sockets[index])
                for index, argument in enumerate(call_node.args[:len(positional_sockets)])
            )
            keyword_sockets = {socket["name"]: socket for socket in sockets}
            score += sum(
                overload_argument_score(keyword.value, keyword_sockets[keyword.arg])
                for keyword in call_node.keywords
                if keyword.arg in keyword_sockets
            )
            candidates.append((score, -overload_index, overload_id))
        if candidates:
            return max(candidates)[2]
        return overload_ids[0]

    def safe_stdlib_value(module_name, attribute):
        module = importlib.import_module(module_name)
        value = getattr(module, attribute, None)
        if callable(value) or isinstance(value, (int, float, str, bool, tuple)):
            return value
        return None

    def resolve_safe_stdlib_name(name):
        if name in stdlib_symbols:
            module_name, attribute = stdlib_symbols[name]
            return safe_stdlib_value(module_name, attribute)
        for module_name in stdlib_star_modules:
            value = safe_stdlib_value(module_name, name)
            if value is not None:
                return value
        return None

    def resolve_safe_stdlib_attribute(node):
        path = []
        current = node
        while isinstance(current, ast.Attribute):
            path.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name) or current.id not in stdlib_modules:
            return None
        value = importlib.import_module(stdlib_modules[current.id])
        for part in reversed(path):
            value = getattr(value, part, None)
            if value is None:
                return None
        return value

    def literal_json_value(value):
        if isinstance(value, enum.Enum):
            return value.name
        if isinstance(value, dict) and "__flow_enum__" in value:
            return value["__flow_enum__"][2]
        if b3d is not None and isinstance(value, b3d.Axis):
            return {
                "__flow_object__": "Axis",
                "value": [list(value.position), list(value.direction)],
            }
        if b3d is not None and isinstance(value, b3d.Plane):
            return {
                "__flow_object__": "Plane",
                "origin": list(value.origin),
                "x_dir": list(value.x_dir),
                "z_dir": list(value.z_dir),
            }
        if b3d is not None and isinstance(value, b3d.Location):
            return {"__flow_object__": "Location", "value": list(value.to_tuple())}
        if b3d is not None and isinstance(value, b3d.Vector):
            return {"__flow_object__": "Vector", "value": list(value)}
        if isinstance(value, dict):
            if "__flow_object__" in value:
                object_type = value["__flow_object__"]
                if all(key in value for key in ("origin", "x_dir", "z_dir")):
                    return {
                        "__flow_object__": object_type,
                        **{
                            key: literal_json_value(value[key])
                            for key in ("origin", "x_dir", "z_dir")
                        },
                    }
                if "value" in value:
                    return {
                        "__flow_object__": object_type,
                        "value": literal_json_value(value["value"]),
                    }
                args = [literal_json_value(item) for item in value.get("args", [])]
                kwargs = {key: literal_json_value(item)
                          for key, item in value.get("kwargs", {}).items()}
                return {
                    "__flow_object__": object_type,
                    "args": args,
                    "kwargs": kwargs,
                }
            return {key: literal_json_value(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return {"__flow_tuple__": [literal_json_value(item) for item in value]}
        if isinstance(value, list):
            return [literal_json_value(item) for item in value]
        return value

    def materialize_literal(value):
        if isinstance(value, dict):
            if "__flow_callable__" in value:
                module_name, qualified_name = value["__flow_callable__"]
                target = importlib.import_module(module_name)
                for part in qualified_name.split("."):
                    target = getattr(target, part)
                return target
            if "__flow_object__" in value:
                if b3d is None:
                    return value
                constructor = getattr(b3d, value["__flow_object__"])
                if all(key in value for key in ("origin", "x_dir", "z_dir")):
                    return constructor(
                        origin=materialize_literal(value["origin"]),
                        x_dir=materialize_literal(value["x_dir"]),
                        z_dir=materialize_literal(value["z_dir"]),
                    )
                if "value" in value:
                    payload = materialize_literal(value["value"])
                    try:
                        return constructor(payload)
                    except TypeError:
                        return constructor(*payload)
                args = [materialize_literal(item) for item in value.get("args", [])]
                kwargs = {key: materialize_literal(item)
                          for key, item in value.get("kwargs", {}).items()}
                return constructor(*args, **kwargs)
            return {key: materialize_literal(item) for key, item in value.items()}
        if isinstance(value, list):
            return [materialize_literal(item) for item in value]
        if isinstance(value, tuple):
            return tuple(materialize_literal(item) for item in value)
        return value

    def resolve_value_constructor(function_node, expected_type):
        """Resolve value constructors from discovered type/socket metadata."""
        class_name = None
        if isinstance(function_node, ast.Name):
            class_name = imported.get(function_node.id, function_node.id)
        elif isinstance(function_node, ast.Attribute):
            parts = []
            current = function_node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if (
                isinstance(current, ast.Name)
                and current.id in aliases
                and aliases[current.id] == "b3d"
            ):
                path = list(reversed(parts))
                if len(path) == 1:
                    class_name = path[0]
        if not class_name:
            return None
        try:
            symbol = type_catalog.resolve_symbol(class_name)
            if symbol.get("kind") != "type":
                return None
            type_record = type_catalog.resolve_type(symbol["type_id"])
        except CatalogError:
            return None
        constructor_type = type_record.get("socket_type", type_record["name"])
        if type_record.get("is_abstract") or type_record.get("is_enum"):
            return None
        # Geometry-producing constructors remain ordinary discovered API nodes
        # unless a typed value socket explicitly requires a concrete geometry.
        if (
            type_record.get("is_geometry")
            and constructor_type != "Face"
            and expected_type in (None, "Any")
        ):
            return None
        if expected_type not in (None, "Any", constructor_type):
            return None
        return (
            type_record["id"], constructor_type, type_record["name"],
            bool(
                type_record.get("supports_iteration")
                or type_record.get("supports_context_manager")
                or type_record.get("member_node_ids")
            ),
            type_record.get("iterable_item_type"),
        )

    def resolve_build123d_class_callable(function_node):
        """Resolve class-level API calls from discovered node metadata."""
        if not isinstance(function_node, ast.Attribute):
            return None
        member_name = function_node.attr
        current = function_node.value
        owner_name = None
        if isinstance(current, ast.Name):
            owner_name = imported.get(current.id, current.id)
        elif isinstance(current, ast.Attribute) and isinstance(current.value, ast.Name):
            root = current.value.id
            if root in aliases and aliases[root] == "b3d":
                owner_name = current.attr
        if not owner_name:
            return None
        try:
            symbol = type_catalog.resolve_symbol(owner_name)
            if symbol.get("kind") != "type":
                return None
            owner = type_catalog.resolve_type(symbol["type_id"])
        except CatalogError:
            return None
        candidates = []
        for node_id in owner.get("member_node_ids", []):
            if not (node_id.endswith(".static." + member_name)
                    or node_id.endswith(".classmethod." + member_name)):
                continue
            definition = definitions.get(node_id)
            if definition and not any(
                socket.get("name") in {"self", "cls"}
                for socket in definition.get("inputs", [])
            ):
                candidates.append(node_id)
        if len(candidates) > 1:
            raise ValueError("Ambiguous discovered class method {}.{}: {}".format(
                owner["name"], member_name, ", ".join(sorted(candidates))))
        return candidates[0] if candidates else None

    def add_build123d_class_call(definition_id, call_node):
        """Lower a discovered class method from its serialized socket contract."""
        definition = definitions[definition_id]
        sockets = definition.get("inputs", [])
        positional = [s for s in sockets if s.get("kind", "positional_or_keyword")
                      in {"positional_only", "positional_or_keyword"}]
        var_positional = next((s for s in sockets if s.get("kind") == "var_positional"), None)
        var_keyword = next((s for s in sockets if s.get("kind") == "var_keyword"), None)
        params, links, supplied = {}, [], set()
        index = 0
        starred_arguments = [item for item in call_node.args if isinstance(item, ast.Starred)]
        if len(starred_arguments) > 1:
            raise ValueError("Only one expanded positional argument is supported at line {}".format(call_node.lineno))
        positional_keywords = {
            item.arg for item in call_node.keywords
            if item.arg is not None and any(socket["name"] == item.arg for socket in positional)
        }
        expanded_arguments = []
        for argument in call_node.args:
            if not isinstance(argument, ast.Starred):
                expanded_arguments.append((None, argument))
                continue
            expanded = expression(argument.value)
            if expanded[0] == "literal" and isinstance(expanded[1], (list, tuple)):
                expanded_arguments.extend((None, ast.Constant(value=item)) for item in expanded[1])
                continue
            if var_positional is not None:
                expanded_arguments.append((var_positional, argument.value))
                continue
            remaining = len(positional) - sum(
                1 for item in call_node.args if not isinstance(item, ast.Starred)
            ) - len(positional_keywords)
            if expanded[0] != "ref" or remaining < 0:
                raise ValueError("Expanded positional arguments do not match the discovered signature at line {}".format(call_node.lineno))
            for item_index in range(remaining):
                subscript_id = add_node(
                    _PYTHON_SUBSCRIPT_ID, {"index": item_index},
                    [("value", expanded[1], expanded[2])], call_node.lineno,
                )
                expanded_arguments.append((
                    None,
                    ("precomputed", ("ref", subscript_id, "result", "Any")),
                ))
        for socket_override, value_node in expanded_arguments:
            if socket_override is not None:
                socket = socket_override
            else:
                if index >= len(positional):
                    if var_positional is None:
                        raise ValueError("Too many positional arguments for {} at line {}".format(
                            definition.get("name"), call_node.lineno))
                    socket = var_positional
                else:
                    socket = positional[index]
                    index += 1
            name = socket["name"]
            if name in supplied and socket is not var_positional:
                raise ValueError("Multiple values for argument '{}' at line {}".format(name, call_node.lineno))
            supplied.add(name)
            value = (
                value_node[1] if isinstance(value_node, tuple) and value_node[0] == "precomputed"
                else expression(value_node)
            )
            if value[0] == "literal":
                params[name] = literal_json_value(value[1])
            else:
                links.append((name, value[1], value[2]))
        for keyword in call_node.keywords:
            if keyword.arg is None:
                if var_keyword is None:
                    raise ValueError("Expanded keyword arguments require **kwargs at line {}".format(call_node.lineno))
                socket = var_keyword
            else:
                socket = next((s for s in sockets if s.get("name") == keyword.arg), None)
                if socket is None:
                    if var_keyword is None:
                        raise ValueError("Unknown keyword argument '{}' at line {}".format(keyword.arg, call_node.lineno))
                    socket = var_keyword
            name = socket["name"]
            if name in supplied and socket is not var_keyword:
                raise ValueError("Multiple values for argument '{}' at line {}".format(name, call_node.lineno))
            supplied.add(name)
            value = expression(keyword.value)
            if value[0] == "literal":
                params[name] = literal_json_value(value[1])
            else:
                links.append((name, value[1], value[2]))
        missing = [s["name"] for s in sockets if s.get("required")
                   and s.get("kind") not in {"var_positional", "var_keyword"}
                   and s["name"] not in supplied]
        if missing:
            raise ValueError("Missing required arguments for {} at line {}: {}".format(
                definition.get("name"), call_node.lineno, ", ".join(missing)))
        created = add_node(definition_id, params, links, call_node.lineno)
        output = next(iter(definition.get("outputs", [])), {})
        return ("ref", created, output.get("name", "result"), output.get("type", "Any"))

    _STATIC_UNRESOLVED = object()

    def evaluate_static_expression(node, scope):
        """Evaluate only side-effect-free literal class constants and comprehensions."""
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in scope:
                return scope[node.id]
            value = variables.get(node.id)
            if value is not None and value[0] == "literal":
                return materialize_literal(value[1])
            if node.id in user_classes:
                return {"__flow_class__": node.id}
            if node.id in {"True", "False", "None"}:
                return {"True": True, "False": False, "None": None}[node.id]
            safe_value = resolve_safe_stdlib_name(node.id)
            if safe_value is not None and not callable(safe_value):
                return safe_value
            return _STATIC_UNRESOLVED
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            items = [evaluate_static_expression(item, scope) for item in node.elts]
            if any(item is _STATIC_UNRESOLVED for item in items):
                return _STATIC_UNRESOLVED
            return items if isinstance(node, ast.List) else tuple(items) if isinstance(node, ast.Tuple) else set(items)
        if isinstance(node, ast.Dict):
            keys = [evaluate_static_expression(item, scope) for item in node.keys]
            values = [evaluate_static_expression(item, scope) for item in node.values]
            if any(item is _STATIC_UNRESOLVED for item in keys + values):
                return _STATIC_UNRESOLVED
            return dict(zip(keys, values))
        if isinstance(node, ast.BinOp):
            functions = {
                ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
                ast.Mod: operator.mod, ast.Pow: operator.pow,
                ast.BitOr: operator.or_, ast.BitAnd: operator.and_,
                ast.BitXor: operator.xor,
            }
            left = evaluate_static_expression(node.left, scope)
            right = evaluate_static_expression(node.right, scope)
            function = functions.get(type(node.op))
            if function is None or left is _STATIC_UNRESOLVED or right is _STATIC_UNRESOLVED:
                return _STATIC_UNRESOLVED
            try:
                return function(left, right)
            except Exception:
                return _STATIC_UNRESOLVED
        if isinstance(node, ast.UnaryOp):
            functions = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_, ast.Invert: operator.invert}
            value = evaluate_static_expression(node.operand, scope)
            function = functions.get(type(node.op))
            if function is None or value is _STATIC_UNRESOLVED:
                return _STATIC_UNRESOLVED
            try:
                return function(value)
            except Exception:
                return _STATIC_UNRESOLVED
        if isinstance(node, ast.Subscript):
            value = evaluate_static_expression(node.value, scope)
            index = evaluate_static_expression(node.slice, scope)
            if value is _STATIC_UNRESOLVED or index is _STATIC_UNRESOLVED:
                return _STATIC_UNRESOLVED
            try:
                return operator.getitem(value, index)
            except Exception:
                return _STATIC_UNRESOLVED
        if isinstance(node, ast.IfExp):
            condition = evaluate_static_expression(node.test, scope)
            if condition is _STATIC_UNRESOLVED:
                return _STATIC_UNRESOLVED
            return evaluate_static_expression(node.body if condition else node.orelse, scope)
        if isinstance(node, ast.ListComp):
            result = []
            local_scope = dict(scope)

            def evaluate_generator(index):
                if index == len(node.generators):
                    return evaluate_static_expression(node.elt, local_scope)
                generator = node.generators[index]
                if generator.is_async:
                    return _STATIC_UNRESOLVED
                iterable = evaluate_static_expression(generator.iter, local_scope)
                if iterable is _STATIC_UNRESOLVED:
                    return _STATIC_UNRESOLVED
                try:
                    iterator = iter(iterable)
                except TypeError:
                    return _STATIC_UNRESOLVED
                for item in iterator:
                    if isinstance(generator.target, ast.Name):
                        local_scope[generator.target.id] = item
                    elif isinstance(generator.target, (ast.Tuple, ast.List)) and isinstance(item, (tuple, list)):
                        if len(generator.target.elts) != len(item) or not all(isinstance(part, ast.Name) for part in generator.target.elts):
                            return _STATIC_UNRESOLVED
                        for target, value in zip(generator.target.elts, item):
                            local_scope[target.id] = value
                    else:
                        return _STATIC_UNRESOLVED
                    include = True
                    for condition_node in generator.ifs:
                        condition = evaluate_static_expression(condition_node, local_scope)
                        if condition is _STATIC_UNRESOLVED:
                            return _STATIC_UNRESOLVED
                        if not condition:
                            include = False
                            break
                    if include:
                        value = evaluate_generator(index + 1)
                        if value is _STATIC_UNRESOLVED:
                            return _STATIC_UNRESOLVED
                        result.append(value)
                return None

            completed = evaluate_generator(0)
            return _STATIC_UNRESOLVED if completed is _STATIC_UNRESOLVED else result
        if isinstance(node, ast.Call):
            function = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
                builtins = {
                    "range": range, "enumerate": enumerate, "zip": zip,
                    "len": len, "min": min, "max": max, "abs": abs,
                    "round": round, "int": int, "float": float, "str": str,
                    "tuple": tuple, "list": list, "set": set, "sorted": sorted,
                }
                function = builtins.get(name) or resolve_safe_stdlib_name(name)
            elif isinstance(node.func, ast.Attribute):
                function = resolve_safe_stdlib_attribute(node.func)
            if not callable(function):
                return _STATIC_UNRESOLVED
            args = [evaluate_static_expression(item, scope) for item in node.args]
            kwargs = {
                item.arg: evaluate_static_expression(item.value, scope)
                for item in node.keywords if item.arg is not None
            }
            if any(item is _STATIC_UNRESOLVED for item in args + list(kwargs.values())):
                return _STATIC_UNRESOLVED
            if any(item.arg is None for item in node.keywords):
                return _STATIC_UNRESOLVED
            try:
                return function(*args, **kwargs)
            except Exception:
                return _STATIC_UNRESOLVED
        return _STATIC_UNRESOLVED

    def resolve_user_class_attribute(class_node, attribute_name):
        saved = dict(variables)
        class_values = {}
        try:
            for class_statement in class_node.body:
                if isinstance(class_statement, ast.Assign):
                    targets = class_statement.targets
                    value_node = class_statement.value
                elif isinstance(class_statement, ast.AnnAssign):
                    targets = [class_statement.target]
                    value_node = class_statement.value
                else:
                    continue
                if value_node is None:
                    continue
                static_value = evaluate_static_expression(value_node, class_values)
                value = (
                    ("literal", static_value)
                    if static_value is not _STATIC_UNRESOLVED
                    else expression(value_node)
                )
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    class_values[target.id] = materialize_literal(value[1]) if value[0] == "literal" else _STATIC_UNRESOLVED
                    variables[target.id] = value
                    if target.id == attribute_name:
                        return value
            raise ValueError(
                "Unknown class attribute {}.{} at line {}".format(
                    class_node.name, attribute_name, class_node.lineno
                )
            )
        finally:
            variables.clear()
            variables.update(saved)

    def expression(node, expected_type=None):
        nonlocal mapping_matching
        if isinstance(node, ast.JoinedStr):
            template_parts, formatted_values = [], []
            all_literal = True
            for part in node.values:
                if isinstance(part, ast.Constant):
                    template_parts.append(str(part.value))
                    continue
                if not isinstance(part, ast.FormattedValue):
                    raise ValueError("Unsupported f-string component at line {}".format(node.lineno))
                value = expression(part.value)
                conversion = {ord("r"): "!r", ord("s"): "!s", ord("a"): "!a"}.get(part.conversion, "")
                format_spec = ""
                if part.format_spec is not None:
                    spec_value = expression(part.format_spec)
                    if spec_value[0] != "literal":
                        raise ValueError("Dynamic f-string format specifications are unsupported (line {})".format(node.lineno))
                    format_spec = ":" + str(spec_value[1])
                if value[0] == "literal":
                    raw = materialize_literal(value[1])
                    if part.conversion == ord("r"):
                        raw = repr(raw)
                    elif part.conversion == ord("a"):
                        raw = ascii(raw)
                    elif part.conversion == ord("s"):
                        raw = str(raw)
                    template_parts.append(format(raw, format_spec[1:]) if format_spec else str(raw))
                else:
                    all_literal = False
                    placeholder = "{{{}}}".format(len(formatted_values))
                    if conversion:
                        placeholder = "{{{}{}{}}}".format(
                            len(formatted_values), conversion, format_spec
                        )
                    elif format_spec:
                        placeholder = "{{{}:{}}}".format(len(formatted_values), format_spec[1:])
                    template_parts.append(placeholder)
                    formatted_values.append(value)
            template = "".join(template_parts)
            if all_literal:
                return ("literal", template)
            definition_id = "core.python.format"
            def format_python_string(template, _value_count=len(formatted_values), **values):
                ordered = [values["arg_{}".format(i)] for i in range(_value_count)]
                return template.format(*ordered)
            supplied = [("template", ("literal", template))]
            supplied.extend(("arg_{}".format(i), value) for i, value in enumerate(formatted_values))
            definitions[definition_id] = {
                "id": definition_id, "name": "Python f-string",
                "source": "python", "kind": "operation", "category": "Internal",
                "palette_category": "Python", "palette_section": "Expressions",
                "inputs": [{"name": name, "type": "String" if name == "template" else "Any", "required": True}
                           for name, _ in supplied],
                "outputs": [{"name": "result", "type": "String"}],
            }
            _INTERNAL_NODE_IDS.add(definition_id)
            _CATALOG_CALLABLES[definition_id] = format_python_string
            _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY) for name, _ in supplied
            ])
            _CATALOG_HINT_TARGETS[definition_id] = False
            params, links = {"template": template}, []
            for name, value in supplied[1:]:
                if value[0] == "literal":
                    params[name] = literal_json_value(value[1])
                else:
                    links.append((name, value[1], value[2]))
            created = add_node(definition_id, params, links, node.lineno)
            return ("ref", created, "result", "String")
        if isinstance(node, ast.IfExp):
            condition = expression(node.test)
            if condition[0] == "literal":
                return expression(node.body if bool(condition[1]) else node.orelse)
            true_value = expression(node.body)
            false_value = expression(node.orelse)
            definition_id = "core.python.if_expression"
            def choose_value(test, when_true, when_false):
                return when_true if test else when_false
            definitions[definition_id] = {
                "id": definition_id, "name": "Python Conditional Expression",
                "source": "python", "kind": "operation", "category": "Internal",
                "palette_category": "Python", "palette_section": "Expressions",
                "inputs": [
                    {"name": "condition", "type": "Boolean", "required": True},
                    {"name": "when_true", "type": "Any", "required": True},
                    {"name": "when_false", "type": "Any", "required": True},
                ],
                "outputs": [{"name": "result", "type": "Any"}],
            }
            _INTERNAL_NODE_IDS.add(definition_id)
            _CATALOG_CALLABLES[definition_id] = choose_value
            _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                inspect.Parameter("test", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                inspect.Parameter("when_true", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                inspect.Parameter("when_false", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            ])
            _CATALOG_HINT_TARGETS[definition_id] = False
            params, links = {}, []
            for name, value in (("condition", condition), ("when_true", true_value), ("when_false", false_value)):
                if value[0] == "literal":
                    params[name] = literal_json_value(value[1])
                else:
                    links.append((name, value[1], value[2]))
            created = add_node(definition_id, params, links, node.lineno)
            return ("ref", created, "result", "Any")
        if isinstance(node, ast.Constant):
            return ("literal", node.value)
        if isinstance(node, ast.Set):
            raise ValueError("Set literals are not yet importable (line {})".format(node.lineno))
        if isinstance(node, (ast.List, ast.Tuple)):
            values = [expression(item) for item in node.elts]
            if all(item[0] == "literal" for item in values):
                raw = [item[1] for item in values]
                return ("literal", raw if isinstance(node, ast.List) else tuple(raw))
            return add_collection_node(
                values, "list" if isinstance(node, ast.List) else "tuple", node.lineno
            )
        if isinstance(node, ast.ListComp):
            saved_variables = dict(variables)
            saved_stream_variables = set(stream_variables)
            saved_matching = mapping_matching
            generator_streams = []
            saved_loop_depth = len(active_loop_frames)
            def graph_depends_on(node_id, ancestors):
                pending = [node_id]
                visited = set()
                while pending:
                    current_id = pending.pop()
                    if current_id in ancestors:
                        return True
                    if current_id in visited:
                        continue
                    visited.add(current_id)
                    for edge in graph_edges:
                        if edge.get("target") != current_id:
                            continue
                        source_id = edge.get("source")
                        if source_id in ancestors:
                            return True
                        pending.append(source_id)
                return False
            def compile_generator(index):
                nonlocal mapping_matching
                generator = node.generators[index]
                if generator.is_async or not (
                    isinstance(generator.target, ast.Name)
                    or isinstance(generator.target, (ast.Tuple, ast.List))
                    and all(isinstance(item, ast.Name) for item in generator.target.elts)
                ):
                    raise ValueError("Comprehension targets must be simple names or tuples of names (line {})".format(node.lineno))
                iterable = expression(generator.iter)
                if iterable[0] == "literal" and isinstance(iterable[1], (list, tuple)):
                    iterable = add_collection_node(
                        [("literal", item) for item in iterable[1]], "list",
                        generator.iter.lineno, map_over_active_loops=False,
                    )
                if iterable[0] != "ref":
                    raise ValueError("A comprehension iterable must be a sequence or graph output (line {})".format(generator.iter.lineno))
                outer_ids = {stream[1] for stream in generator_streams}
                outer_ids.update(frame["node_id"] for frame in active_loop_frames)
                if outer_ids:
                    select_loop_mapping(iterable[1], outer_ids, generator.iter.lineno)
                item_type, item_types = iterable_item_metadata(iterable)
                typed_iterable = (
                    "ref", iterable[1], iterable[2], item_type, item_types
                )
                target_names = (
                    [generator.target.id] if isinstance(generator.target, ast.Name)
                    else [item.id for item in generator.target.elts]
                )
                generator_streams.append(iterable)
                active_loop_frames.append({
                    "node_id": iterable[1], "output_name": iterable[2],
                    "written_names": set(target_names),
                    "matching": mapping_matching,
                })
                if isinstance(generator.target, ast.Name):
                    variables[generator.target.id] = typed_iterable
                else:
                    unpack_id = "core.python.unpack"
                    definitions[unpack_id] = {
                        "id": unpack_id, "name": "Python Tuple Unpack",
                        "source": "python", "kind": "operation", "category": "Internal",
                        "palette_category": "Python", "palette_section": "Assignments",
                        "inputs": [
                            {"name": "value", "type": "Any", "required": True, "data_access": "item"},
                            {"name": "index", "type": "Number", "required": True},
                        ],
                        "outputs": [{"name": "result", "type": "Any"}],
                    }
                    _INTERNAL_NODE_IDS.add(unpack_id)
                    _CATALOG_CALLABLES[unpack_id] = _python_getitem
                    _CATALOG_SIGNATURES[unpack_id] = inspect.Signature([
                        inspect.Parameter("value", inspect.Parameter.POSITIONAL_ONLY),
                        inspect.Parameter("index", inspect.Parameter.POSITIONAL_ONLY),
                    ])
                    _CATALOG_HINT_TARGETS[unpack_id] = False
                    for element_index, name in enumerate(target_names):
                        element_type = (
                            item_types[element_index]
                            if item_types and element_index < len(item_types) else "Any"
                        )
                        unpack_node = add_node(
                            unpack_id, {"index": element_index},
                            [("value", iterable[1], iterable[2])],
                            node.lineno,
                        )
                        variables[name] = ("ref", unpack_node, "result", element_type)
                stream_variables.update(target_names)
                for condition in generator.ifs:
                    resolved = expression(condition)
                    if resolved[0] == "literal":
                        if not bool(resolved[1]):
                            return ("literal", [])
                        continue
                    if resolved[0] != "ref":
                        raise ValueError("Comprehension filter must be boolean (line {})".format(condition.lineno))
                    active_loop_frames.pop()
                    filter_id = "core.python.filter"
                    def filter_values(items, predicates):
                        return [item for item, keep in zip(items, predicates) if keep]
                    definitions[filter_id] = {
                        "id": filter_id, "name": "Python filter", "source": "python",
                        "kind": "operation", "category": "Internal", "palette_category": "Python",
                        "palette_section": "Collections",
                        "inputs": [
                            {"name": "items", "type": "List", "required": True, "data_access": "structure"},
                            {"name": "predicates", "type": "List", "required": True, "data_access": "structure"},
                        ],
                        "outputs": [{"name": "result", "type": "List", "data_structure": "list", "item_type": item_type}],
                    }
                    _INTERNAL_NODE_IDS.add(filter_id)
                    _CATALOG_CALLABLES[filter_id] = filter_values
                    _CATALOG_SIGNATURES[filter_id] = inspect.Signature([
                        inspect.Parameter("items", inspect.Parameter.KEYWORD_ONLY),
                        inspect.Parameter("predicates", inspect.Parameter.KEYWORD_ONLY),
                    ])
                    _CATALOG_HINT_TARGETS[filter_id] = False
                    filtered = add_node(
                        filter_id, {},
                        [("items", iterable[1], iterable[2]), ("predicates", resolved[1], resolved[2])],
                        condition.lineno,
                    )
                    iterable = ("ref", filtered, "result", item_type, item_types)
                    generator_streams[-1] = iterable
                    active_loop_frames.append({
                        "node_id": filtered, "output_name": "result",
                        "written_names": set(target_names), "matching": mapping_matching,
                    })
                    if isinstance(generator.target, ast.Name):
                        variables[generator.target.id] = iterable
                    else:
                        for element_index, name in enumerate(target_names):
                            unpack_id = "core.python.unpack"
                            definitions[unpack_id] = {
                                "id": unpack_id, "name": "Python Tuple Unpack", "source": "python",
                                "kind": "operation", "category": "Internal", "palette_category": "Python",
                                "palette_section": "Assignments",
                                "inputs": [
                                    {"name": "value", "type": "Any", "required": True, "data_access": "item"},
                                    {"name": "index", "type": "Number", "required": True},
                                ],
                                "outputs": [{"name": "result", "type": "Any"}],
                            }
                            _INTERNAL_NODE_IDS.add(unpack_id)
                            _CATALOG_CALLABLES[unpack_id] = _python_getitem
                            _CATALOG_SIGNATURES[unpack_id] = inspect.Signature([
                                inspect.Parameter("value", inspect.Parameter.POSITIONAL_ONLY),
                                inspect.Parameter("index", inspect.Parameter.POSITIONAL_ONLY),
                            ])
                            _CATALOG_HINT_TARGETS[unpack_id] = False
                            unpacked = add_node(
                                unpack_id, {"index": element_index},
                                [("value", filtered, "result")], condition.lineno,
                            )
                            variables[name] = ("ref", unpacked, "result", "Any")
                if index + 1 < len(node.generators):
                    return compile_generator(index + 1)
                return expression(node.elt)
            try:
                result = compile_generator(0)
                if result[0] == "literal":
                    # A literal element still repeats once per generator item.
                    # The iteration sockets injected by add_node turn this into
                    # a mapped constant without expanding the comprehension.
                    loop_value_id = "core.python.comprehension_constant"
                    value_type = _type_name(type(materialize_literal(result[1])))
                    loop_value_definition = {
                        "id": loop_value_id, "name": "Python Loop Value",
                        "source": "python", "kind": "operation", "category": "Internal",
                        "palette_category": "Internal", "palette_section": "Graph lowering",
                        "inputs": [{"name": "value", "type": value_type,
                                    "required": True, "data_access": "structure"}],
                        "outputs": [{"name": "result", "type": value_type}],
                    }
                    definitions[loop_value_id] = loop_value_definition
                    _INTERNAL_NODE_IDS.add(loop_value_id)
                    _CATALOG_CALLABLES[loop_value_id] = lambda value: value
                    _CATALOG_SIGNATURES[loop_value_id] = inspect.Signature([
                        inspect.Parameter("value", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                    ])
                    _CATALOG_HINT_TARGETS[loop_value_id] = False
                    created = add_node(
                        loop_value_id, {"value": literal_json_value(result[1])},
                        [], node.lineno,
                    )
                    result = ("ref", created, "result")
                return result
            finally:
                del active_loop_frames[saved_loop_depth:]
                variables.clear()
                variables.update(saved_variables)
                stream_variables.clear()
                stream_variables.update(saved_stream_variables)
                mapping_matching = saved_matching
        if isinstance(node, ast.Dict):
            keys = [expression(item) for item in node.keys]
            values = [expression(item) for item in node.values]
            if any(key[0] != "literal" for key in keys) or any(value[0] != "literal" for value in values):
                raise ValueError("Connected dictionaries are not yet importable (line {})".format(node.lineno))
            return ("literal", {key[1]: value[1] for key, value in zip(keys, values)})
        if isinstance(node, ast.Name):
            if node.id in variables:
                return variables[node.id]
            if node.id in stdlib_modules:
                return ("literal", {"__flow_module__": stdlib_modules[node.id]})
            if node.id in aliases and aliases[node.id] == "b3d":
                return ("literal", {"__flow_module__": "build123d"})
            if node.id in user_classes:
                return ("literal", {"__flow_class__": node.id})
            if node.id == "__name__":
                return ("literal", "__main__")
            if node.id in {"True", "False", "None"}:
                return ("literal", {"True": True, "False": False, "None": None}[node.id])
            standard_value = resolve_safe_stdlib_name(node.id)
            if standard_value is not None:
                if callable(standard_value):
                    return ("literal", {
                        "__flow_callable__": [
                            getattr(standard_value, "__module__", ""),
                            getattr(standard_value, "__qualname__", getattr(standard_value, "__name__", "")),
                        ]
                    })
                return ("literal", standard_value)
            try:
                symbol = type_catalog.resolve_symbol(imported.get(node.id, node.id))
            except CatalogError:
                symbol = None
            if symbol is not None:
                if symbol.get("kind") == "value":
                    return ("literal", symbol["value"])
                if symbol.get("kind") == "type":
                    return ("literal", {"__flow_type__": symbol["type_id"]})
                if symbol.get("kind") == "callable":
                    return ("literal", {"__flow_callable__": symbol["id"]})
            build123d_value = getattr(b3d, imported.get(node.id, node.id), None)
            if b3d is not None and isinstance(build123d_value, (int, float, str, bool, enum.Enum,
                                             b3d.Axis, b3d.Plane, b3d.Location, b3d.Vector)): 
                return ("literal", build123d_value)
            raise ValueError("Unresolved name '{}' at line {}".format(node.id, node.lineno))
        if isinstance(node, ast.Attribute):
            stored_attribute = variables.get(ast.unparse(node))
            if stored_attribute is not None:
                return stored_attribute
            standard_value = resolve_safe_stdlib_attribute(node)
            if standard_value is not None:
                if callable(standard_value):
                    return ("literal", {
                        "__flow_callable__": [
                            getattr(standard_value, "__module__", ""),
                            getattr(standard_value, "__qualname__", getattr(standard_value, "__name__", "")),
                        ]
                    })
                return ("literal", standard_value)
            receiver_root = node.value
            while isinstance(receiver_root, ast.Attribute):
                receiver_root = receiver_root.value
            receiver = None
            if (
                (isinstance(receiver_root, ast.Name) and receiver_root.id in variables)
                or isinstance(receiver_root, (ast.Call, ast.Subscript, ast.BinOp, ast.UnaryOp))
            ):
                receiver = expression(node.value)
            if receiver and receiver[0] == "literal":
                try:
                    return ("literal", getattr(materialize_literal(receiver[1]), node.attr))
                except AttributeError:
                    if isinstance(receiver[1], dict) and (
                        "__flow_object__" in receiver[1] or "__flow_type__" in receiver[1]
                    ):
                        created = add_node(
                            _PYTHON_ATTRIBUTE_ID,
                            {"name": node.attr, "value": literal_json_value(receiver[1])},
                            [], node.lineno,
                        )
                        return ("ref", created, "result", "Any")
            if receiver and receiver[0] == "ref":
                receiver_node = next((item for item in graph_nodes if item["id"] == receiver[1]), None)
                receiver_type = reference_type(receiver)
                if receiver_node and receiver_node.get("data", {}).get("scope_result"):
                    scope_id = receiver_node["data"]["scope_result"]
                    descriptor = next((item for item in execution_scopes if item.get("id") == scope_id), {})
                    if node.attr == descriptor.get("result_attribute"):
                        return receiver
                if receiver_node and receiver_node.get("data", {}).get("scope_binding"):
                    scope_id = receiver_node["data"]["scope_binding"]
                    descriptor = next((item for item in execution_scopes if item.get("id") == scope_id), {})
                    result_node_id = scope_result_nodes.get(scope_id)
                    if result_node_id and node.attr == descriptor.get("result_attribute"):
                        result_node = next((item for item in graph_nodes if item["id"] == result_node_id), None)
                        result_output = next(iter(result_node.get("data", {}).get("definition", {}).get("outputs", [])), {}) if result_node else {}
                        return ("ref", result_node_id, "result", result_output.get("type", "Any"))
                definition_id, candidates = resolve_member(
                    node.attr, receiver_type, property_only=True
                )
                if definition_id:
                    # Attribute reads must use Python's native descriptor lookup.
                    # Discovered property getters are catalog metadata, but invoking
                    # their synthetic lambdas through graph data-mapping loses the
                    # receiver for streamed values (for example, face.area in a loop).
                    # The generic attribute node preserves descriptor and instance
                    # semantics while still mapping each receiver item independently.
                    property_definition = definitions[definition_id]
                    property_type = next(
                        (item.get("type", "Any") for item in property_definition.get("outputs", [])),
                        "Any",
                    )
                    if property_type == "Any" and node.attr in {
                        "X", "Y", "Z", "length", "area", "volume",
                        "radius", "height", "width", "depth",
                    }:
                        property_type = "Number"
                    property_receiver = next((
                        item for item in property_definition.get("inputs", [])
                        if item.get("name") in {"self", "cls"}
                    ), None)
                    receiver_access = (
                        property_receiver.get("data_access")
                        if property_receiver else "item"
                    )
                    if receiver_access is None and property_receiver is not None:
                        receiver_access = (
                            "structure"
                            if _socket_data_structure(property_receiver) != "item"
                            else "item"
                        )
                    attribute_id = (
                        _PYTHON_ATTRIBUTE_STRUCTURE_ID
                        if receiver_access == "structure"
                        else _PYTHON_ATTRIBUTE_ID
                    )
                    created = add_node(
                        attribute_id, {"name": node.attr},
                        [("value", receiver[1], receiver[2])], node.lineno,
                    )
                    return ("ref", created, "result", property_type)
                if candidates:
                    # When static receiver typing cannot distinguish same-named
                    # properties (for example Axis.Y, Vector.Y and Vertex.Y),
                    # preserve Python semantics and defer attribute lookup to
                    # the actual runtime object instead of guessing a class.
                    candidate_types = {
                        output.get("type", "Any")
                        for candidate_id in candidates
                        for output in definitions.get(candidate_id, {}).get("outputs", [])
                        if output.get("type", "Any") != "Any"
                    }
                    property_type = next(iter(candidate_types)) if len(candidate_types) == 1 else "Any"
                    if property_type == "Any" and node.attr in {
                        "X", "Y", "Z", "length", "area", "volume",
                        "radius", "height", "width", "depth",
                    }:
                        property_type = "Number"
                    created = add_node(
                        _PYTHON_ATTRIBUTE_ID, {"name": node.attr},
                        [("value", receiver[1], receiver[2])], node.lineno,
                    )
                    return ("ref", created, "result", property_type)
                # Python permits dynamic instance attributes and extension
                # descriptors that are not declared on the discovered class.
                # Preserve native getattr semantics rather than rejecting them.
                created = add_node(
                    _PYTHON_ATTRIBUTE_ID, {"name": node.attr},
                    [("value", receiver[1], receiver[2])], node.lineno,
                )
                return ("ref", created, "result", "Any")
            path = []
            current = node
            while isinstance(current, ast.Attribute):
                path.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                root = current.id
                enum_type_name = None
                enum_member_name = None
                if root in aliases and aliases[root] == "b3d":
                    enum_path = list(reversed(path))
                    if len(enum_path) == 2:
                        enum_type_name, enum_member_name = enum_path
                elif len(path) == 1:
                    enum_type_name = imported.get(root, root)
                    enum_member_name = path[0]
                if enum_type_name and enum_member_name:
                    try:
                        return (
                            "literal",
                            type_catalog.resolve_enum_member(
                                enum_type_name, enum_member_name
                            ),
                        )
                    except CatalogError:
                        try:
                            class_value = type_catalog.resolve_class_value(
                                enum_type_name, enum_member_name
                            )
                        except CatalogError:
                            pass
                        else:
                            return ("literal", class_value)
                if root in user_classes and len(path) == 1:
                    return resolve_user_class_attribute(user_classes[root], path[0])
                if len(path) == 1:
                    owner_name = imported.get(root, root)
                    try:
                        owner_symbol = type_catalog.resolve_symbol(owner_name)
                    except CatalogError:
                        owner_symbol = None
                    if owner_symbol and owner_symbol.get("kind") == "type":
                        property_id, property_candidates = resolve_member(
                            path[0], owner_name, property_only=True
                        )
                        if property_id or property_candidates:
                            return ("literal", {"__flow_attrgetter__": path[0]})
                if root in aliases and aliases[root] == "b3d" and len(path) == 1:
                    try:
                        symbol = type_catalog.resolve_symbol(path[0])
                    except CatalogError:
                        symbol = None
                    if symbol is not None:
                        if symbol.get("kind") == "value":
                            return ("literal", symbol["value"])
                        if symbol.get("kind") == "type":
                            return ("literal", {"__flow_type__": symbol["type_id"]})
                if root in aliases and aliases[root] == "b3d":
                    path = list(reversed(path))
                    if path:
                        value = getattr(b3d, path[0], None)
                        for part in path[1:]:
                            value = getattr(value, part, None)
                        if b3d is not None and isinstance(value, (enum.Enum, b3d.Axis, b3d.Plane, b3d.Location, b3d.Vector)): 
                            return ("literal", value)
                elif hasattr(b3d, root):
                    value = getattr(b3d, root, None)
                    for part in reversed(path):
                        value = getattr(value, part, None)
                    if b3d is not None and isinstance(value, (enum.Enum, b3d.Axis, b3d.Plane, b3d.Location, b3d.Vector)): 
                        return ("literal", value)
            raise ValueError("Unsupported attribute expression at line {}".format(node.lineno))
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "__init__"
                and isinstance(node.func.value, ast.Call)
                and isinstance(node.func.value.func, ast.Name)
                and node.func.value.func.id == "super"
                and class_stack
            ):
                base_name = class_stack[-1]
                synthetic = ast.Call(
                    func=ast.Name(id=base_name, ctx=ast.Load()),
                    args=node.args, keywords=node.keywords,
                )
                ast.copy_location(synthetic, node)
                base_result = expression(synthetic)
                variables["self"] = base_result
                return base_result
            if isinstance(node.func, ast.Attribute):
                stdlib_root = node.func.value
                while isinstance(stdlib_root, ast.Attribute):
                    stdlib_root = stdlib_root.value
                if (
                    isinstance(stdlib_root, ast.Name)
                    and stdlib_root.id in stdlib_modules
                    and stdlib_modules[stdlib_root.id] == "logging"
                ):
                    return ("literal", None)
                literal_receiver = expression(node.func.value)
                if literal_receiver[0] == "literal":
                    # Never constant-fold methods on discovered API values.
                    # Doing so only works when the CAD kernel is installed and
                    # makes Gate 2 translate a different graph from Gate 1.
                    try:
                        type_catalog.resolve_type(reference_type(literal_receiver))
                        discovered_api_receiver = True
                    except CatalogError:
                        discovered_api_receiver = False
                    if not discovered_api_receiver:
                        bound_method = getattr(
                            materialize_literal(literal_receiver[1]), node.func.attr, None
                        )
                        if callable(bound_method):
                            bound_args = [expression(item) for item in node.args]
                            bound_kwargs = {
                                item.arg: expression(item.value) for item in node.keywords
                                if item.arg is not None
                            }
                            if any(item.arg is None for item in node.keywords):
                                raise ValueError("Expanded bound-method keywords are unsupported (line {})".format(node.lineno))
                            if all(item[0] == "literal" for item in bound_args) and all(
                                item[0] == "literal" for item in bound_kwargs.values()
                            ):
                                try:
                                    return ("literal", bound_method(
                                        *[materialize_literal(item[1]) for item in bound_args],
                                        **{name: materialize_literal(item[1]) for name, item in bound_kwargs.items()}
                                    ))
                                except Exception as exc:
                                    raise ValueError("Literal method call failed at line {}: {}".format(node.lineno, exc))
            if isinstance(node.func, ast.Subscript):
                selected_callable = expression(node.func)
                if (
                    selected_callable[0] == "literal"
                    and isinstance(selected_callable[1], dict)
                    and isinstance(selected_callable[1].get("__flow_class__"), str)
                    and selected_callable[1]["__flow_class__"] in user_classes
                ):
                    class_name = selected_callable[1]["__flow_class__"]
                    return inline_user_class(user_classes[class_name], node)
            if isinstance(node.func, ast.Name) and node.func.id in user_classes:
                return inline_user_class(user_classes[node.func.id], node)
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "__init__"
                and isinstance(node.func.value, ast.Call)
                and isinstance(node.func.value.func, ast.Name)
                and node.func.value.func.id == "super"
                and class_stack
            ):
                base_name = class_stack[-1]
                synthetic = ast.Call(
                    func=ast.Name(id=base_name, ctx=ast.Load()),
                    args=node.args, keywords=node.keywords,
                )
                ast.copy_location(synthetic, node)
                base_result = expression(synthetic)
                variables["self"] = base_result
                return base_result
            if (
                isinstance(node.func, ast.Name) and node.func.id == "filter"
                and len(node.args) == 2 and isinstance(node.args[0], ast.Lambda)
                and not node.keywords
            ):
                iterable = expression(node.args[1])
                if iterable[0] != "ref":
                    iterable = add_collection_node(
                        [("literal", item) for item in iterable[1]],
                        "list", node.lineno, map_over_active_loops=False,
                    )
                item_type, item_types = iterable_item_metadata(iterable)
                lambda_node = node.args[0]
                if len(lambda_node.args.args) != 1 or lambda_node.args.vararg or lambda_node.args.kwarg:
                    raise ValueError("filter lambda must have one simple parameter (line {})".format(node.lineno))
                parameter_name = lambda_node.args.args[0].arg
                saved_variables = dict(variables)
                saved_stream = set(stream_variables)
                frame = {
                    "node_id": iterable[1], "output_name": iterable[2],
                    "written_names": {parameter_name}, "matching": "longest",
                }
                variables[parameter_name] = ("ref", iterable[1], iterable[2], item_type)
                stream_variables.add(parameter_name)
                active_loop_frames.append(frame)
                try:
                    predicate = expression(lambda_node.body)
                finally:
                    active_loop_frames.pop()
                    variables.clear()
                    variables.update(saved_variables)
                    stream_variables.clear()
                    stream_variables.update(saved_stream)
                definition_id = "core.python.filter"
                def filter_values(items, predicates):
                    return [item for item, keep in zip(items, predicates) if keep]
                definitions[definition_id] = {
                    "id": definition_id, "name": "Python filter",
                    "source": "python", "kind": "operation", "category": "Internal",
                    "palette_category": "Python", "palette_section": "Collections",
                    "inputs": [
                        {"name": "items", "type": "List", "required": True, "data_access": "structure"},
                        {"name": "predicates", "type": "List", "required": True, "data_access": "structure"},
                    ],
                    "outputs": [{"name": "result", "type": "List", "data_structure": "list", "item_type": item_type}],
                }
                _INTERNAL_NODE_IDS.add(definition_id)
                _CATALOG_CALLABLES[definition_id] = filter_values
                _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                    inspect.Parameter("items", inspect.Parameter.KEYWORD_ONLY),
                    inspect.Parameter("predicates", inspect.Parameter.KEYWORD_ONLY),
                ])
                _CATALOG_HINT_TARGETS[definition_id] = False
                params, links = {}, [("items", iterable[1], iterable[2])]
                if predicate[0] == "literal":
                    params["predicates"] = [bool(predicate[1])]
                else:
                    links.append(("predicates", predicate[1], predicate[2]))
                created = add_node(definition_id, params, links, node.lineno, map_over_active_loops=False)
                return ("ref", created, "result", "List")
            if isinstance(node.func, ast.Name) and node.func.id in user_functions:
                return inline_user_function(user_functions[node.func.id], node)
            if isinstance(node.func, ast.Name) and node.func.id == "locals":
                local_names = set(variables) | set(imported) | set(viewer_functions) | set(stdlib_symbols)
                return ("literal", {name: True for name in local_names})
            safe_callable = None
            if isinstance(node.func, ast.Name):
                candidate = resolve_safe_stdlib_name(node.func.id)
                if candidate is not None and callable(candidate):
                    safe_callable = candidate
            elif isinstance(node.func, ast.Attribute):
                candidate = resolve_safe_stdlib_attribute(node.func)
                if candidate is not None and callable(candidate):
                    safe_callable = candidate
            if safe_callable is not None:
                safe_args = [expression(argument) for argument in node.args]
                safe_kwargs = {}
                for keyword in node.keywords:
                    if keyword.arg is None:
                        raise ValueError("Expanded keyword arguments are unsupported at line {}".format(node.lineno))
                    safe_kwargs[keyword.arg] = expression(keyword.value)
                if all(item[0] == "literal" for item in safe_args) and all(item[0] == "literal" for item in safe_kwargs.values()):
                    try:
                        result = safe_callable(
                            *[materialize_literal(item[1]) for item in safe_args],
                            **{name: materialize_literal(item[1]) for name, item in safe_kwargs.items()}
                        )
                        if isinstance(result, range) or hasattr(result, "__next__"):
                            result = list(result)
                        return ("literal", result)
                    except Exception as exc:
                        raise ValueError("Standard-library call failed at line {}: {}".format(node.lineno, exc))
                else:
                    supplied = [("arg_{}".format(i), value) for i, value in enumerate(safe_args)]
                    supplied.extend(safe_kwargs.items())
                    definition_id = "core.python.stdlib." + hashlib.sha1(
                        (getattr(safe_callable, "__module__", "") + "." +
                         getattr(safe_callable, "__qualname__", getattr(safe_callable, "__name__", "call")) +
                         ":" + ",".join(name for name, _ in supplied)).encode("utf-8")
                    ).hexdigest()[:16]
                    definitions[definition_id] = {
                        "id": definition_id, "name": "Python " + getattr(safe_callable, "__name__", "call"),
                        "source": "python", "kind": "operation", "category": "Internal",
                        "palette_category": "Internal", "palette_section": "Graph lowering",
                        "inputs": [{"name": name, "type": "Any", "required": True} for name, _ in supplied],
                        "outputs": [{"name": "result", "type": "Any"}],
                    }
                    _INTERNAL_NODE_IDS.add(definition_id)
                    positional_names = [name for name, _ in supplied if name.startswith("arg_")]
                    def invoke_safe(**values):
                        positional = [values.pop(name) for name in positional_names]
                        return safe_callable(*positional, **values)
                    _CATALOG_CALLABLES[definition_id] = invoke_safe
                    _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                        inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY)
                        for name, _ in supplied
                    ])
                    _CATALOG_HINT_TARGETS[definition_id] = False
                    params, links = {}, []
                    for name, value in supplied:
                        if value[0] == "literal":
                            params[name] = literal_json_value(value[1])
                        else:
                            links.append((name, value[1], value[2]))
                    created = add_node(definition_id, params, links, node.lineno)
                    return ("ref", created, "result", "Any")
            external_spec = None
            if isinstance(node.func, ast.Name) and node.func.id in external_symbols:
                external_spec = external_symbols[node.func.id]
            elif isinstance(node.func, ast.Attribute):
                parts = []
                current = node.func
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name) and current.id in external_modules:
                    external_spec = (
                        external_modules[current.id],
                        ".".join(reversed(parts)),
                    )
            if external_spec is not None:
                module_name, attribute_name = external_spec
                supplied = [("arg_{}".format(i), expression(value)) for i, value in enumerate(node.args)]
                supplied.extend(
                    (keyword.arg, expression(keyword.value))
                    for keyword in node.keywords if keyword.arg is not None
                )
                if any(keyword.arg is None for keyword in node.keywords):
                    raise ValueError("Expanded external keyword arguments are unsupported at line {}".format(node.lineno))
                definition_id = "core.python.external." + hashlib.sha1(
                    (module_name + "." + attribute_name).encode("utf-8")
                ).hexdigest()[:16]
                positional_names = [name for name, _ in supplied if name.startswith("arg_")]
                def external_call(__module=module_name, __attribute=attribute_name, **kwargs):
                    target = importlib.import_module(__module)
                    for part in __attribute.split("."):
                        target = getattr(target, part)
                    positional = [kwargs.pop(name) for name in positional_names]
                    return target(*positional, **kwargs)
                definitions[definition_id] = {
                    "id": definition_id, "name": "External " + attribute_name,
                    "source": "python", "kind": "operation", "category": "External",
                    "palette_category": "Python", "palette_section": "External calls",
                    "inputs": [{"name": name, "type": "Any", "required": True} for name, _ in supplied],
                    "outputs": [{"name": "result", "type": "Any"}],
                }
                _INTERNAL_NODE_IDS.add(definition_id)
                _CATALOG_CALLABLES[definition_id] = external_call
                _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                    inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY)
                    for name, _ in supplied
                ])
                _CATALOG_HINT_TARGETS[definition_id] = False
                params, links = {}, []
                for name, value in supplied:
                    if value[0] == "literal":
                        params[name] = literal_json_value(value[1])
                    else:
                        links.append((name, value[1], value[2]))
                created = add_node(definition_id, params, links, node.lineno)
                return ("ref", created, "result", "Any")
            if isinstance(node.func, ast.Name) and node.func.id in {
                "range", "enumerate", "zip", "len", "min", "max", "abs", "round", "print",
                "str", "int", "float", "bool", "list", "tuple", "sorted", "reversed",
                "all", "any", "sum", "divmod", "pow", "dict", "set", "frozenset"
            }:
                builtin_args = []
                for argument in node.args:
                    if isinstance(argument, ast.Starred):
                        expanded = expression(argument.value)
                        if expanded[0] == "literal" and isinstance(expanded[1], (list, tuple)):
                            builtin_args.extend(("literal", item) for item in expanded[1])
                        elif node.func.id in {"max", "min"} and not builtin_args:
                            # max(*iterable) and min(*iterable) are equivalent
                            # to max(iterable) and min(iterable).
                            builtin_args.append(expanded)
                        else:
                            raise ValueError("Expanded builtin arguments are unsupported at line {}".format(node.lineno))
                    else:
                        builtin_args.append(expression(argument))
                builtin_kwargs = {
                    keyword.arg: expression(keyword.value) for keyword in node.keywords
                    if keyword.arg is not None
                }
                if node.func.id == "print":
                    definition_id = "core.python.builtin.print"
                    supplied = [("arg_{}".format(i), value) for i, value in enumerate(builtin_args)]
                    supplied.extend(builtin_kwargs.items())
                    definitions[definition_id] = {
                        "id": definition_id, "name": "Python print", "source": "python",
                        "kind": "operation", "category": "Internal", "palette_category": "Python",
                        "palette_section": "Builtins",
                        "inputs": [{"name": name, "type": "Any", "required": True} for name, _ in supplied],
                        "outputs": [{"name": "result", "type": "Any"}],
                    }
                    _INTERNAL_NODE_IDS.add(definition_id)
                    def invoke_print(_names=tuple(name for name, _ in supplied if name.startswith("arg_")), **values):
                        positional = [values.pop(name) for name in _names]
                        return print(*positional, **values)
                    _CATALOG_CALLABLES[definition_id] = invoke_print
                    _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                        inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY) for name, _ in supplied
                    ])
                    _CATALOG_HINT_TARGETS[definition_id] = False
                    params, links = {}, []
                    for name, value in supplied:
                        if value[0] == "literal":
                            params[name] = literal_json_value(value[1])
                        else:
                            links.append((name, value[1], value[2]))
                    created = add_node(definition_id, params, links, node.lineno)
                    return ("ref", created, "result", "Any")
                if node.func.id == "print":
                    definition_id = "core.python.builtin.print"
                    supplied = [("arg_{}".format(i), value) for i, value in enumerate(builtin_args)]
                    supplied.extend(builtin_kwargs.items())
                    definitions[definition_id] = {
                        "id": definition_id, "name": "Python print", "source": "python",
                        "kind": "operation", "category": "Internal", "palette_category": "Python",
                        "palette_section": "Builtins",
                        "inputs": [{"name": name, "type": "Any", "required": True} for name, _ in supplied],
                        "outputs": [{"name": "result", "type": "Any"}],
                    }
                    _INTERNAL_NODE_IDS.add(definition_id)
                    def invoke_print(_names=tuple(name for name, _ in supplied if name.startswith("arg_")), **values):
                        positional = [values.pop(name) for name in _names]
                        return print(*positional, **values)
                    _CATALOG_CALLABLES[definition_id] = invoke_print
                    _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                        inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY) for name, _ in supplied
                    ])
                    _CATALOG_HINT_TARGETS[definition_id] = False
                    params, links = {}, []
                    for name, value in supplied:
                        if value[0] == "literal":
                            params[name] = literal_json_value(value[1])
                        else:
                            links.append((name, value[1], value[2]))
                    created = add_node(definition_id, params, links, node.lineno)
                    return ("ref", created, "result", "Any")
                if all(item[0] == "literal" for item in builtin_args) and all(item[0] == "literal" for item in builtin_kwargs.values()):
                    functions = {
                        "range": range, "enumerate": enumerate, "zip": zip, "len": len,
                        "min": min, "max": max, "abs": abs, "round": round, "str": str,
                        "int": int, "float": float, "bool": bool, "list": list, "tuple": tuple,
                        "sorted": sorted, "reversed": reversed, "all": all, "any": any,
                        "sum": sum, "divmod": divmod, "pow": pow,
                        "dict": dict, "set": set, "frozenset": frozenset,
                    }
                    result = functions[node.func.id](
                        *[materialize_literal(item[1]) for item in builtin_args],
                        **{name: materialize_literal(item[1]) for name, item in builtin_kwargs.items()}
                    )
                    if isinstance(result, (range, enumerate, zip, reversed)) or hasattr(result, "__next__"):
                        result = list(result)
                    return ("literal", result)
                elif node.func.id in {"abs", "round", "pow", "str", "int", "float", "bool"}:
                    functions = {
                        "abs": abs, "round": round, "pow": pow, "str": str,
                        "int": int, "float": float, "bool": bool,
                    }
                    function = functions[node.func.id]
                    supplied = [("arg_{}".format(i), value) for i, value in enumerate(builtin_args)]
                    supplied.extend(builtin_kwargs.items())
                    definition_id = "core.python.builtin." + node.func.id
                    definitions[definition_id] = {
                        "id": definition_id, "name": "Python " + node.func.id,
                        "source": "python", "kind": "operation", "category": "Internal",
                        "palette_category": "Python", "palette_section": "Builtins",
                        "inputs": [{"name": name, "type": "Any", "required": True} for name, _ in supplied],
                        "outputs": [{"name": "result", "type": "Any"}],
                    }
                    _INTERNAL_NODE_IDS.add(definition_id)
                    def invoke_builtin(_function=function, _names=tuple(
                        name for name, _ in supplied if name.startswith("arg_")
                    ), **values):
                        positional = [values.pop(name) for name in _names]
                        return _function(*positional, **values)
                    _CATALOG_CALLABLES[definition_id] = invoke_builtin
                    _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                        inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY)
                        for name, _ in supplied
                    ])
                    _CATALOG_HINT_TARGETS[definition_id] = False
                    params, links = {}, []
                    for name, value in supplied:
                        if value[0] == "literal":
                            params[name] = literal_json_value(value[1])
                        else:
                            links.append((name, value[1], value[2]))
                    created = add_node(definition_id, params, links, node.lineno)
                    return ("ref", created, "result", "Any")
            # Reduction builtins consume a complete collection rather than
            # mapping over it. Lower graph-backed sum/max/min/len explicitly
            # so they cannot be mistaken for build123d's capitalized Sum node.
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in {"sum", "max", "min", "len"}
            ):
                builtin_name = node.func.id
                # The ordinary builtin path above has already lowered these
                # expressions. Reuse its refs; evaluating a comprehension twice
                # would leave duplicate, orphaned graph operations behind.
                dynamic_args = builtin_args
                dynamic_kwargs = builtin_kwargs
                if any(item[0] == "ref" for item in dynamic_args + list(dynamic_kwargs.values())):
                    if builtin_name == "sum" and 1 <= len(dynamic_args) <= 2 and not dynamic_kwargs:
                        names = ["iterable", "start"][:len(dynamic_args)]
                        input_sockets = [
                            {"name": "iterable", "type": "Any", "required": True, "data_access": "structure"},
                            {"name": "start", "type": "Any", "required": False, "default": 0, "data_access": "structure"},
                        ]
                        output_type = reference_type(dynamic_args[1]) if len(dynamic_args) == 2 else "Number"
                        callable_obj = sum
                    elif builtin_name in {"max", "min"} and len(dynamic_args) == 1 and not dynamic_kwargs:
                        names = ["iterable"]
                        input_sockets = [
                            {"name": "iterable", "type": "Any", "required": True, "data_access": "structure"}
                        ]
                        output_type = reference_type(dynamic_args[0])
                        callable_obj = max if builtin_name == "max" else min
                    elif builtin_name == "len" and len(dynamic_args) == 1 and not dynamic_kwargs:
                        names = ["obj"]
                        input_sockets = [
                            {"name": "obj", "type": "Any", "required": True, "data_access": "structure"}
                        ]
                        output_type = "Number"
                        callable_obj = len
                    else:
                        raise ValueError("Unsupported graph-backed {} call at line {}".format(builtin_name, node.lineno))
                    definition_id = "core.python.builtin." + builtin_name
                    definitions[definition_id] = {
                        "id": definition_id, "name": "Python " + builtin_name,
                        "source": "python", "kind": "operation", "category": "Internal",
                        "palette_category": "Internal", "palette_section": "Graph lowering",
                        "inputs": input_sockets,
                        "outputs": [{"name": "result", "type": output_type}],
                    }
                    _INTERNAL_NODE_IDS.add(definition_id)
                    _CATALOG_CALLABLES[definition_id] = callable_obj
                    _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                        inspect.Parameter(
                            name,
                            inspect.Parameter.POSITIONAL_ONLY
                            if name in {"iterable", "obj"}
                            else inspect.Parameter.POSITIONAL_OR_KEYWORD,
                            default=0 if builtin_name == "sum" and name == "start" else inspect.Parameter.empty,
                        )
                        for name in names
                    ])
                    _CATALOG_HINT_TARGETS[definition_id] = False
                    params, links = {}, []
                    for name, value in zip(names, dynamic_args):
                        if value[0] == "literal":
                            params[name] = literal_json_value(value[1])
                        else:
                            links.append((name, value[1], value[2]))
                    created = add_node(definition_id, params, links, node.lineno)
                    return ("ref", created, "result", output_type)

            # tuple(item) is common in build123d list comprehensions: a
            # mapped Vertex/Vector becomes one coordinate tuple per item.
            # Preserve that as a graph operation instead of trying to execute
            # the conversion against a graph reference during import.
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "tuple"
                and len(node.args) == 1
                and not node.keywords
            ):
                tuple_input = expression(node.args[0])
                if tuple_input[0] == "ref":
                    per_item = (
                        isinstance(node.args[0], ast.Name)
                        and node.args[0].id in stream_variables
                    )
                    definition_id = "core.python.builtin.tuple"
                    definition = {
                        "id": definition_id, "name": "Python tuple",
                        "source": "python", "kind": "operation", "category": "Internal",
                        "palette_category": "Internal", "palette_section": "Graph lowering",
                        "inputs": [{
                            "name": "value", "type": "Any", "required": True,
                            "data_access": "item" if per_item else "structure",
                        }],
                        "outputs": [{
                            "name": "result", "type": "Tuple",
                            "data_structure": "tuple", "item_type": "Number",
                        }],
                    }
                    definitions[definition_id] = definition
                    _INTERNAL_NODE_IDS.add(definition_id)
                    _CATALOG_CALLABLES[definition_id] = tuple
                    _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                        inspect.Parameter("value", inspect.Parameter.POSITIONAL_ONLY),
                    ])
                    _CATALOG_HINT_TARGETS[definition_id] = False
                    created = add_node(
                        definition_id, {},
                        [("value", tuple_input[1], tuple_input[2])],
                        node.lineno,
                    )
                    return ("ref", created, "result", "Tuple")

            # Resolve build123d value constructors from the destination socket's
            # declared type. This shares the runtime's type contract instead of
            # maintaining one importer exception per value class.
            value_constructor = resolve_value_constructor(node.func, expected_type)
            if value_constructor is not None:
                constructor_type_id, constructor_type, constructor_name, force_node, iterable_item_type = value_constructor
                value_arguments = [
                    (expression(argument.value), True)
                    if isinstance(argument, ast.Starred)
                    else (expression(argument), False)
                    for argument in node.args
                ]
                values = [item[0] for item in value_arguments]
                kwargs = {}
                for keyword in node.keywords:
                    if keyword.arg is None:
                        raise ValueError("Expanded constructor arguments are unsupported at line {}".format(node.lineno))
                    kwargs[keyword.arg] = expression(keyword.value)
                if node.args and node.keywords and constructor_type in {"Plane", "Location"}:
                    raise ValueError("{} cannot mix positional and keyword arguments at line {}".format(
                        constructor_type, node.lineno))
                if not force_node and all(item[0] == "literal" for item in values) and all(
                    item[0] == "literal" for item in kwargs.values()
                ):
                    positional_values = []
                    for (kind, value, *extra), (_, starred) in zip(values, value_arguments):
                        materialized = materialize_literal(value)
                        if starred:
                            positional_values.extend(materialized)
                        else:
                            positional_values.append(materialized)
                    return ("literal", {
                        "__flow_object__": constructor_name,
                        "args": [literal_json_value(item) for item in positional_values],
                        "kwargs": {name: item[1] for name, item in kwargs.items()},
                    })

                # Value constructors such as Plane(face) and Location(vertex)
                # are real graph operations when their inputs depend on geometry.
                # Keep one constructor node and let the normal item-mapping runtime
                # apply it to each item in the incoming stream.
                positional_names = ["arg_{}".format(index) for index in range(len(values))]
                starred_flags = [item[1] for item in value_arguments]
                supplied_names = positional_names + list(kwargs)
                if len(set(supplied_names)) != len(supplied_names):
                    raise ValueError("Duplicate constructor argument at line {}".format(node.lineno))
                definition_id = "core.python.construct." + constructor_name + "." + hashlib.sha1(
                    ",".join(supplied_names).encode("utf-8")
                ).hexdigest()[:10]
                def is_loop_mapped_collection(value):
                    if value[0] != "ref":
                        return False
                    source_node = next(
                        (item for item in graph_nodes if item["id"] == value[1]),
                        None,
                    )
                    return bool(
                        source_node
                        and source_node.get("data", {}).get("python_loop_sources")
                    )

                structured_constructor_arguments = {
                    "arg_{}".format(index)
                    for index, (argument, value) in enumerate(zip(node.args, values))
                    if not isinstance(argument, ast.Starred)
                    and isinstance(argument, (ast.Tuple, ast.List, ast.Dict))
                    and not is_loop_mapped_collection(value)
                }
                structured_constructor_arguments.update(
                    keyword.arg
                    for keyword in node.keywords
                    if keyword.arg is not None
                    and isinstance(keyword.value, (ast.Tuple, ast.List, ast.Dict))
                    and not is_loop_mapped_collection(kwargs[keyword.arg])
                )
                input_sockets = [
                    {
                        "name": name, "type": "Any", "required": True,
                        **({"data_access": "structure"} if name in structured_constructor_arguments else {}),
                    }
                    for name in supplied_names
                ]
                definitions[definition_id] = {
                    "id": definition_id, "name": "Python " + constructor_name,
                    "source": "python", "kind": "operation", "category": "Internal",
                    "palette_category": "Internal", "palette_section": "Graph lowering",
                    "inputs": input_sockets,
                    "outputs": [{
                        "name": "result", "type": constructor_type,
                        **({"item_type": iterable_item_type} if iterable_item_type else {}),
                    }],
                }
                _INTERNAL_NODE_IDS.add(definition_id)

                def construct_value(
                    _constructor_type_id=constructor_type_id,
                    _constructor_name=constructor_name,
                    _positional_names=tuple(positional_names),
                    _starred_flags=tuple(starred_flags),
                    **supplied_values
                ):
                    positional = []
                    for name, starred in zip(_positional_names, _starred_flags):
                        value = supplied_values.pop(name)
                        if starred:
                            positional.extend(value)
                        else:
                            positional.append(value)
                    _constructor = _resolve_type_constructor(_constructor_type_id)
                    if _constructor is None:
                        raise RuntimeError("CAD kernel is required to execute value constructor " + _constructor_name)
                    try:
                        constructor_signature = _api_signature(_constructor)
                    except (TypeError, ValueError):
                        constructor_signature = inspect.signature(_constructor)
                    constructor_parameters = list(constructor_signature.parameters.values())
                    mapped_values = {}
                    positional_index = 0
                    for parameter in constructor_parameters:
                        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
                            mapped_values[parameter.name] = tuple(positional[positional_index:])
                            positional_index = len(positional)
                            break
                        if positional_index >= len(positional):
                            break
                        mapped_values[parameter.name] = positional[positional_index]
                        positional_index += 1
                    if positional_index < len(positional):
                        raise TypeError("Too many positional arguments for {}".format(_constructor_name))
                    mapped_values.update(supplied_values)
                    try:
                        return _call_with_coercion(
                            _constructor, mapped_values, signature=constructor_signature
                        )
                    except Exception as exc:
                        diagnostic_inputs = {
                            name: {
                                "type": type(value).__name__,
                                "value": repr(value)[:240]
                                if isinstance(value, (type(None), bool, int, float, str, tuple, list, dict))
                                else None,
                            }
                            for name, value in mapped_values.items()
                        }
                        raise TypeError(
                            "Python {} constructor inputs {}: {}".format(
                                _constructor_name, diagnostic_inputs, exc
                            )
                        ) from exc

                _CATALOG_CALLABLES[definition_id] = construct_value
                _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                    inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY)
                    for name in supplied_names
                ])
                _CATALOG_HINT_TARGETS[definition_id] = False
                params, links = {}, []
                for name, item in zip(supplied_names, values + list(kwargs.values())):
                    if item[0] == "literal":
                        params[name] = literal_json_value(item[1])
                    else:
                        links.append((name, item[1], item[2]))
                created = add_node(definition_id, params, links, node.lineno)
                return ("ref", created, "result")

            class_callable = resolve_build123d_class_callable(node.func)
            if class_callable is not None:
                return add_build123d_class_call(class_callable, node)

            definition_id = resolve_callable(node.func, node)
            receiver = None
            receiver_type = None
            if isinstance(node.func, ast.Attribute):
                # A dotted name can resolve through the imported API namespace even
                # when its left-hand side is an instance expression. Resolve the
                # receiver whenever the selected definition is an instance method;
                # otherwise imported methods silently become receiver-less nodes.
                selected = definitions.get(definition_id) if definition_id else None
                expects_receiver = selected is None or any(
                    item.get("name") in {"self", "cls"}
                    for item in selected.get("inputs", [])
                )
                if expects_receiver:
                    receiver = expression(node.func.value)
                    if receiver[0] == "ref":
                        upstream = next((item for item in graph_nodes if item["id"] == receiver[1]), None)
                        if upstream:
                            receiver_type = reference_type(receiver)
                    elif receiver[0] == "literal":
                        receiver_type = reference_type(receiver)
                    if not definition_id:
                        definition_id, candidates = resolve_member(node.func.attr, receiver_type)
                        # If static typing cannot select one declared
                        # implementation, defer to the receiver's native Python
                        # attribute lookup through a generic method-call node.
            if not definition_id and isinstance(node.func, ast.Attribute) and receiver is not None:
                method_name = node.func.attr
                supplied = []
                starred_flags = []
                for argument in node.args:
                    if isinstance(argument, ast.Starred):
                        expanded = expression(argument.value)
                        if expanded[0] == "literal" and isinstance(expanded[1], (list, tuple)):
                            for item in expanded[1]:
                                supplied.append(("arg_{}".format(len(starred_flags)), ("literal", item)))
                                starred_flags.append(False)
                        else:
                            supplied.append(("arg_{}".format(len(starred_flags)), expanded))
                            starred_flags.append(True)
                    else:
                        supplied.append(("arg_{}".format(len(starred_flags)), expression(argument)))
                        starred_flags.append(False)
                for keyword in node.keywords:
                    if keyword.arg is None:
                        raise ValueError("Expanded dynamic method keywords are unsupported at line {}".format(node.lineno))
                    supplied.append((keyword.arg, expression(keyword.value)))
                names = [name for name, _ in supplied]
                if len(names) != len(set(names)):
                    raise ValueError("Duplicate dynamic method argument at line {}".format(node.lineno))
                definition_id = "core.python.method." + hashlib.sha1(
                    (method_name + ":" + ",".join(names)).encode("utf-8")
                ).hexdigest()[:16]
                positional_names = [name for name in names if name.startswith("arg_")]
                def invoke_dynamic_method(
                    self,
                    _method_name=method_name,
                    _positional_names=tuple(positional_names),
                    _starred_flags=tuple(starred_flags),
                    **values
                ):
                    positional = []
                    for name, starred in zip(_positional_names, _starred_flags):
                        value = values.pop(name)
                        if starred:
                            positional.extend(value)
                        else:
                            positional.append(value)
                    return getattr(self, _method_name)(*positional, **values)
                definitions[definition_id] = {
                    "id": definition_id, "name": "Python method " + method_name,
                    "source": "python", "kind": "operation", "category": "Internal",
                    "palette_category": "Python", "palette_section": "Dynamic dispatch",
                    "inputs": [
                        {"name": "self", "type": "Any", "required": True},
                        *[{"name": name, "type": "Any", "required": True, "kind": "keyword_only"}
                          for name in names],
                    ],
                    "outputs": [{"name": "result", "type": "Any"}],
                }
                _INTERNAL_NODE_IDS.add(definition_id)
                _CATALOG_CALLABLES[definition_id] = invoke_dynamic_method
                _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                    inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                    *[inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY) for name in names],
                ])
                _CATALOG_HINT_TARGETS[definition_id] = False
                params, links = {}, []
                if receiver[0] == "literal":
                    params["self"] = literal_json_value(receiver[1])
                else:
                    links.append(("self", receiver[1], receiver[2]))
                for name, value in supplied:
                    if value[0] == "literal":
                        params[name] = literal_json_value(value[1])
                    else:
                        links.append((name, value[1], value[2]))
                created = add_node(definition_id, params, links, node.lineno)
                return ("ref", created, "result", "Any")
            if not definition_id:
                raise ValueError("Unsupported or ambiguous call at line {}: {}".format(
                    node.lineno, ast.unparse(node.func)))
            definition = definitions[definition_id]
            sockets = definition.get("inputs", [])
            params, links = {}, []
            receiver_socket = next((item for item in sockets if item.get("name") in {"self", "cls"}), None)
            # A discovered instance-method definition is authoritative about
            # its receiver contract. If namespace resolution selected that
            # definition before the expression resolver identified the bound
            # object, recover the receiver from the attribute expression here.
            if (
                receiver is None and receiver_socket is not None
                and isinstance(node.func, ast.Attribute)
            ):
                receiver = expression(node.func.value)
            if receiver is not None and receiver_socket is not None:
                if receiver[0] == "ref":
                    links.append((receiver_socket["name"], receiver[1], receiver[2]))
                else:
                    params[receiver_socket["name"]] = literal_json_value(receiver[1])
            # The full runtime catalog is authoritative for bound receiver
            # sockets. Reconcile it here before lowering arguments so an
            # incomplete/stale local definition cannot turn a method call into
            # a receiver-less node.
            if receiver_socket is None and isinstance(node.func, ast.Attribute):
                catalog_definition = _CATALOG.get(definition_id)
                catalog_receiver = next((
                    item for item in catalog_definition.get("inputs", [])
                    if item.get("name") in {"self", "cls"}
                ), None) if catalog_definition else None
                if catalog_receiver is not None:
                    definition = catalog_definition
                    definitions[definition_id] = definition
                    sockets = definition.get("inputs", [])
                    receiver_socket = catalog_receiver
            if receiver_socket is not None and isinstance(node.func, ast.Attribute):
                if receiver is None:
                    receiver = expression(node.func.value)
                if receiver[0] == "ref":
                    receiver_name = receiver_socket["name"]
                    links = [link for link in links if link[0] != receiver_name]
                    links.append((receiver_name, receiver[1], receiver[2]))
                elif receiver[0] == "literal":
                    params[receiver_socket["name"]] = literal_json_value(receiver[1])
            if receiver_socket is not None and isinstance(node.func, ast.Attribute):
                if receiver is None:
                    raise ValueError(
                        "Bound method {} has no resolved receiver at line {}".format(
                            node.func.attr, node.lineno
                        )
                    )
                if receiver[0] == "ref" and not any(
                    link[0] == receiver_socket["name"] for link in links
                ):
                    raise ValueError(
                        "Bound method {} failed to connect receiver {} from {} at line {}".format(
                            node.func.attr, receiver_socket["name"], repr(receiver),
                            node.lineno
                        )
                    )
            var_positional_socket = next(
                (socket for socket in sockets if socket.get("kind") == "var_positional"), None
            )
            argument_sockets = [
                socket for socket in sockets
                if socket is not receiver_socket
                and socket.get("kind") not in {"keyword_only", "var_keyword", "var_positional"}
            ]
            positional_arguments = []
            deferred_starred = None
            for argument in node.args:
                if isinstance(argument, ast.Starred):
                    expanded = expression(argument.value)
                    if expanded[0] == "literal" and isinstance(expanded[1], (list, tuple)):
                        positional_arguments.extend(
                            ast.Constant(value=item) for item in expanded[1]
                        )
                    elif var_positional_socket is not None and deferred_starred is None:
                        deferred_starred = expanded
                    else:
                        raise ValueError("Expanded positional arguments must be literal or a single variadic input (line {})".format(node.lineno))
                else:
                    positional_arguments.append(argument)
            if len(positional_arguments) > len(argument_sockets) and var_positional_socket is None:
                raise ValueError("Too many positional arguments for {} at line {}".format(
                    definition.get("name"), node.lineno))
            supplied = {}
            for index, value in enumerate(positional_arguments[:len(argument_sockets)]):
                supplied[argument_sockets[index]["name"]] = value

            # Python constructors such as Bezier(*points) expose a single
            # VAR_POSITIONAL parameter. Lower its positional arguments as a
            # structural list; the evaluator expands that list back into
            # positional arguments when invoking the native callable.
            var_positional_value = None
            extra_positional = positional_arguments[len(argument_sockets):]
            if deferred_starred is not None:
                if extra_positional:
                    raise ValueError("Mixed expanded and additional variadic positional arguments are unsupported (line {})".format(node.lineno))
                var_positional_value = deferred_starred
            if extra_positional:
                if var_positional_socket is None:
                    raise ValueError("Too many positional arguments for {} at line {}".format(
                        definition.get("name"), node.lineno))
                if any(isinstance(value, ast.Starred) for value in extra_positional):
                    if len(extra_positional) != 1 or not isinstance(extra_positional[0], ast.Starred):
                        raise ValueError("Mixed starred positional arguments are unsupported at line {}".format(
                            node.lineno))
                    var_positional_value = expression(extra_positional[0].value)
                else:
                    var_positional_value = add_collection_node(
                        [expression(value) for value in extra_positional], "list", node.lineno
                    )

            for keyword in node.keywords:
                if keyword.arg is None:
                    raise ValueError("Expanded keyword arguments are unsupported at line {}".format(node.lineno))
                if var_positional_socket is not None and keyword.arg == var_positional_socket["name"]:
                    raise ValueError("Variadic positional parameter '{}' cannot be passed by keyword at line {}".format(
                        keyword.arg, node.lineno))
                supplied[keyword.arg] = keyword.value
            known = {socket["name"] for socket in sockets}
            unknown = set(supplied) - known
            if unknown and isinstance(node.func, ast.Attribute) and receiver is not None:
                method_name = node.func.attr
                dynamic_values = [("arg_{}".format(i), expression(value), False)
                                  for i, value in enumerate(node.args)]
                dynamic_values.extend(
                    (keyword.arg, expression(keyword.value), False)
                    for keyword in node.keywords if keyword.arg is not None
                )
                dynamic_names = [name for name, _, _ in dynamic_values]
                if len(dynamic_names) != len(set(dynamic_names)):
                    raise ValueError("Duplicate dynamic method argument at line {}".format(node.lineno))
                positional_names = [name for name in dynamic_names if name.startswith("arg_")]
                def invoke_dynamic_method(
                    self,
                    _method_name=method_name,
                    _positional_names=tuple(positional_names),
                    **values
                ):
                    positional = [values.pop(name) for name in _positional_names]
                    return getattr(self, _method_name)(*positional, **values)
                dynamic_id = "core.python.method." + hashlib.sha1(
                    (method_name + ":" + ",".join(dynamic_names)).encode("utf-8")
                ).hexdigest()[:16]
                definitions[dynamic_id] = {
                    "id": dynamic_id, "name": "Python method " + method_name,
                    "source": "python", "kind": "operation", "category": "Internal",
                    "palette_category": "Python", "palette_section": "Dynamic dispatch",
                    "inputs": [
                        {"name": "self", "type": "Any", "required": True},
                        *[{"name": name, "type": "Any", "required": True, "kind": "keyword_only"}
                          for name in dynamic_names],
                    ],
                    "outputs": [{"name": "result", "type": "Any"}],
                }
                _INTERNAL_NODE_IDS.add(dynamic_id)
                _CATALOG_CALLABLES[dynamic_id] = invoke_dynamic_method
                _CATALOG_SIGNATURES[dynamic_id] = inspect.Signature([
                    inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                    *[inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY) for name in dynamic_names],
                ])
                _CATALOG_HINT_TARGETS[dynamic_id] = False
                dynamic_params, dynamic_links = {}, []
                if receiver[0] == "literal":
                    dynamic_params["self"] = literal_json_value(receiver[1])
                else:
                    dynamic_links.append(("self", receiver[1], receiver[2]))
                for name, value, _ in dynamic_values:
                    if value[0] == "literal":
                        dynamic_params[name] = literal_json_value(value[1])
                    else:
                        dynamic_links.append((name, value[1], value[2]))
                dynamic_node = add_node(dynamic_id, dynamic_params, dynamic_links, node.lineno)
                return ("ref", dynamic_node, "result", "Any")
            if unknown:
                raise ValueError("Unknown argument(s) for {} at line {}: {}".format(
                    definition.get("name"), node.lineno, ", ".join(sorted(unknown))))
            for socket in sockets:
                name = socket["name"]
                if socket is receiver_socket:
                    continue
                if socket is var_positional_socket:
                    if var_positional_value is not None:
                        kind, value, *extra = var_positional_value
                        if kind == "literal":
                            params[name] = literal_json_value(value)
                        else:
                            links.append((name, value, extra[0]))
                    continue
                if name not in supplied:
                    if "default" in socket:
                        params[name] = socket["default"]
                    elif socket.get("required"):
                        raise ValueError("Missing '{}' for {} at line {}".format(
                            name, definition.get("name"), node.lineno))
                    continue
                kind, value, *extra = expression(supplied[name], socket.get("type"))
                if kind == "literal":
                    params[name] = literal_json_value(value)
                else:
                    links.append((name, value, extra[0]))
            created = add_node(definition_id, params, links, node.lineno)
            return ("ref", created, definition.get("outputs", [{}])[0].get("name", "result"))
        if isinstance(node, ast.Subscript):
            # Preserve Python's native __getitem__ semantics. The receiver and
            # index are ordinary graph dependencies, so this also handles
            # geometry collections and values produced by earlier API calls.
            receiver = expression(node.value)
            index_node = node.slice
            if isinstance(index_node, ast.Slice):
                slice_params, slice_links = {}, []
                for socket_name, component in (
                    ("start", index_node.lower), ("stop", index_node.upper),
                    ("step", index_node.step),
                ):
                    if component is None:
                        slice_params[socket_name] = None
                        continue
                    kind, value, *extra = expression(component)
                    if kind == "literal":
                        slice_params[socket_name] = literal_json_value(value)
                    else:
                        slice_links.append((socket_name, value, extra[0]))
                index_node_id = add_node(_PYTHON_SLICE_ID, slice_params, slice_links, node.lineno)
                index = ("ref", index_node_id, "result")
            else:
                index = expression(index_node)
            if receiver[0] == "literal" and index[0] == "literal":
                try:
                    return ("literal", operator.getitem(
                        materialize_literal(receiver[1]),
                        materialize_literal(index[1]),
                    ))
                except (TypeError, KeyError, IndexError) as exc:
                    raise ValueError("Invalid subscript at line {}: {}".format(node.lineno, exc))
            params, links = {}, []
            for socket_name, operand in (("value", receiver), ("index", index)):
                if operand[0] == "literal":
                    params[socket_name] = operand[1]
                else:
                    links.append((socket_name, operand[1], operand[2]))
            item_type, item_types = iterable_item_metadata(receiver)
            created = add_node(_PYTHON_SUBSCRIPT_ID, params, links, node.lineno)
            return ("ref", created, "result", item_type, item_types)
        if isinstance(node, ast.Compare):
            operators = {
                ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
                ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
                ast.In: lambda left, right: left in right,
                ast.NotIn: lambda left, right: left not in right,
                ast.Is: operator.is_, ast.IsNot: operator.is_not,
            }
            names = {
                ast.Eq: "eq", ast.NotEq: "ne", ast.Lt: "lt",
                ast.LtE: "le", ast.Gt: "gt", ast.GtE: "ge",
                ast.In: "in", ast.NotIn: "not_in",
                ast.Is: "is", ast.IsNot: "is_not",
            }
            operands = [expression(node.left)] + [expression(item) for item in node.comparators]
            if all(item[0] == "literal" for item in operands):
                values = [materialize_literal(item[1]) for item in operands]
                result = True
                for index, operation in enumerate(node.ops):
                    function = operators.get(type(operation))
                    if function is None:
                        raise ValueError("Unsupported comparison at line {}".format(node.lineno))
                    try:
                        result = result and function(values[index], values[index + 1])
                    except (TypeError, ValueError) as exc:
                        raise ValueError("Invalid comparison at line {}: {}".format(node.lineno, exc))
                return ("literal", result)
            result = None
            for index, operation in enumerate(node.ops):
                operation_name = names.get(type(operation))
                if operation_name is None:
                    raise ValueError("Unsupported comparison at line {}".format(node.lineno))
                definition_id = "core.python.compare." + operation_name
                definitions[definition_id] = {
                    "id": definition_id, "name": "Python compare " + operation_name,
                    "source": "python", "kind": "operation", "category": "Internal",
                    "palette_category": "Python", "palette_section": "Expressions",
                    "inputs": [
                        {"name": "left", "type": "Any", "required": True},
                        {"name": "right", "type": "Any", "required": True},
                    ],
                    "outputs": [{"name": "result", "type": "Boolean"}],
                }
                _INTERNAL_NODE_IDS.add(definition_id)
                compare_operation = operators[type(operation)]
                def invoke_compare(left, right, _operation=compare_operation):
                    return _operation(left, right)
                _CATALOG_CALLABLES[definition_id] = invoke_compare
                _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                    inspect.Parameter("left", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                    inspect.Parameter("right", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                ])
                _CATALOG_HINT_TARGETS[definition_id] = False
                params, links = {}, []
                for name, operand in (("left", operands[index]), ("right", operands[index + 1])):
                    if operand[0] == "literal":
                        params[name] = literal_json_value(operand[1])
                    else:
                        links.append((name, operand[1], operand[2]))
                created = add_node(definition_id, params, links, node.lineno)
                result = ("ref", created, "result", "Boolean")
                if index > 0:
                    result = add_python_binary_node("__and__", previous, result, node.lineno)
                previous = result
            return result
        if isinstance(node, ast.BoolOp):
            values = [expression(item) for item in node.values]
            if all(item[0] == "literal" for item in values):
                raw = [materialize_literal(item[1]) for item in values]
                return ("literal", all(raw) if isinstance(node.op, ast.And) else any(raw))
            operation_name = "and" if isinstance(node.op, ast.And) else "or"
            definition_id = "core.python.bool." + operation_name
            def boolean_operation(left, right):
                return right if left else left if operation_name == "and" else left if left else right
            if operation_name == "and":
                boolean_operation = lambda left, right: right if left else left
            else:
                boolean_operation = lambda left, right: left if left else right
            definitions[definition_id] = {
                "id": definition_id, "name": "Python " + operation_name,
                "source": "python", "kind": "operation", "category": "Internal",
                "palette_category": "Python", "palette_section": "Expressions",
                "inputs": [
                    {"name": "left", "type": "Any", "required": True},
                    {"name": "right", "type": "Any", "required": True},
                ],
                "outputs": [{"name": "result", "type": "Any"}],
            }
            _INTERNAL_NODE_IDS.add(definition_id)
            _CATALOG_CALLABLES[definition_id] = boolean_operation
            _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                inspect.Parameter("left", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                inspect.Parameter("right", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            ])
            _CATALOG_HINT_TARGETS[definition_id] = False
            result = values[0]
            for value in values[1:]:
                params, links = {}, []
                for name, operand in (("left", result), ("right", value)):
                    if operand[0] == "literal":
                        params[name] = literal_json_value(operand[1])
                    else:
                        links.append((name, operand[1], operand[2]))
                created = add_node(definition_id, params, links, node.lineno)
                result = ("ref", created, "result", "Any")
            return result
        if isinstance(node, ast.UnaryOp):
            methods = {
                ast.USub: ("__neg__", operator.neg),
                ast.UAdd: ("__pos__", operator.pos),
                ast.Invert: ("__invert__", operator.invert),
                ast.Not: ("__not__", operator.not_),
            }
            operation = methods.get(type(node.op))
            if operation is None:
                raise ValueError("Unsupported unary operator at line {}".format(node.lineno))
            method, apply_operator = operation
            operand = expression(node.operand)
            # Unary operators on Python literals are ordinary compile-time values,
            # not graph operations. This is required for common syntax such as
            # items[-1], negative dimensions, and signed numeric arguments.
            if operand[0] == "literal":
                try:
                    return ("literal", apply_operator(materialize_literal(operand[1])))
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(
                        "Invalid {} operand at line {}: {}".format(method, node.lineno, exc)
                    )
            if operand[0] != "ref":
                raise ValueError("No compatible graph node for unary operator at line {}".format(node.lineno))
            # Unary dispatch is a Python protocol operation. Selecting the
            # first discovered .operator.__neg__ node globally binds (for
            # example) Location.__neg__ to an integer loop variable. Invoke
            # the native protocol on each mapped item instead; this preserves
            # runtime MRO and works for both builtin and CAD value types.
            definition_id = "core.python.unary." + method
            output_type = reference_type(operand)
            if type(node.op) is ast.Not:
                output_type = "Boolean"
            definitions[definition_id] = {
                "id": definition_id, "name": "Python " + method,
                "source": "python", "kind": "operation", "category": "Internal",
                "palette_category": "Python", "palette_section": "Operators",
                "inputs": [{"name": "value", "type": "Any", "required": True}],
                "outputs": [{"name": "result", "type": output_type}],
            }
            _INTERNAL_NODE_IDS.add(definition_id)
            _CATALOG_CALLABLES[definition_id] = apply_operator
            _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                inspect.Parameter("value", inspect.Parameter.POSITIONAL_ONLY)
            ])
            _CATALOG_HINT_TARGETS[definition_id] = False
            created = add_node(
                definition_id, {}, [("value", operand[1], operand[2])], node.lineno
            )
            return ("ref", created, "result", output_type)
        if isinstance(node, ast.BinOp):
            # Keep this table aligned with Python's complete binary-operator AST.
            # The importer must not silently assume arithmetic means only + - * /.
            methods = {
                ast.Add: "__add__", ast.Sub: "__sub__", ast.Mult: "__mul__",
                ast.Div: "__truediv__", ast.FloorDiv: "__floordiv__",
                ast.Mod: "__mod__", ast.Pow: "__pow__", ast.MatMult: "__matmul__",
                ast.BitAnd: "__and__", ast.BitOr: "__or__", ast.BitXor: "__xor__",
                ast.LShift: "__lshift__", ast.RShift: "__rshift__",
            }
            method = methods.get(type(node.op))
            if not method:
                raise ValueError("Unsupported binary operator at line {}".format(node.lineno))
            left, right = expression(node.left), expression(node.right)
            if (
                left[0] == "literal" and right[0] == "literal"
                and not any(
                    isinstance(value, dict) and "__flow_object__" in value
                    for value in (left[1], right[1])
                )
            ):
                operations = {
                    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
                    ast.Mod: operator.mod, ast.Pow: operator.pow, ast.MatMult: operator.matmul,
                    ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor,
                    ast.LShift: operator.lshift, ast.RShift: operator.rshift,
                }
                try:
                    return ("literal", operations[type(node.op)](
                        materialize_literal(left[1]), materialize_literal(right[1])
                    ))
                except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
                    raise ValueError("Invalid binary operation at line {}: {}".format(node.lineno, exc))
            # Resolve the operator from the left operand's concrete class MRO.
            # Catalog insertion order is unrelated to Python dispatch: selecting
            # the first matching dunder can bind (for example) Mixin1D.__add__
            # to a Solid even though that class does not inherit from Mixin1D.
            if left[0] == "ref":
                receiver_type = reference_type(left)
            else:
                encoded_value = left[1]
                if isinstance(encoded_value, dict) and isinstance(encoded_value.get("__flow_object__"), str):
                    receiver_type = encoded_value["__flow_object__"]
                else:
                    try:
                        receiver_type = _type_name(type(materialize_literal(encoded_value)))
                    except Exception:
                        receiver_type = None
            if receiver_type in {
                "Number", "Integer", "Float", "String", "Boolean", "Any", None,
            }:
                return add_python_binary_node(method, left, right, node.lineno)
            receiver_identity = None
            if left[0] == "ref":
                receiver_node = next(
                    (item for item in graph_nodes if item["id"] == left[1]), None
                )
                if receiver_node:
                    receiver_definition_id = (
                        receiver_node.get("data", {}).get("definition", {}).get("id")
                    )
                    if isinstance(receiver_definition_id, str):
                        candidate_identity = receiver_definition_id.split(
                            ".overload-", 1
                        )[0]
                        if candidate_identity in type_catalog.types:
                            receiver_identity = candidate_identity
            if receiver_identity is None and receiver_type:
                try:
                    receiver_identity = type_catalog.resolve_type(receiver_type)["id"]
                except CatalogError:
                    receiver_identity = None

            definition_id = None
            if receiver_identity is not None:
                try:
                    definition_id = type_catalog.resolve_operator(receiver_identity, method)
                except CatalogError:
                    definition_id = None
            if definition_id is None:
                # Unknown or non-build123d receiver types may use a native
                # operator only when discovery leaves exactly one candidate.
                candidates = [
                    key for key in definitions if key.endswith(".operator." + method)
                ]
                if len(candidates) == 1:
                    definition_id = candidates[0]
                elif candidates:
                    # Keep Python's own operator dispatch when the static
                    # receiver annotation is too broad to select one MRO owner.
                    return add_python_binary_node(method, left, right, node.lineno)
            if not definition_id:
                return add_python_binary_node(method, left, right, node.lineno)
            definition = definitions[definition_id]
            params, links = {}, []
            operands = [left, right]
            for socket, operand in zip(definition.get("inputs", []), operands):
                if operand[0] == "literal": params[socket["name"]] = literal_json_value(operand[1])
                else: links.append((socket["name"], operand[1], operand[2]))
            created = add_node(definition_id, params, links, node.lineno)
            created_node = next(item for item in graph_nodes if item["id"] == created)
            created_output = next(
                socket for socket in created_node["data"]["definition"].get("outputs", [])
                if socket.get("name") == "result"
            )
            for typevar_name, input_name in created_output.get("generic_type_sources", {}).items():
                input_index = next(
                    (index for index, socket in enumerate(definition.get("inputs", []))
                     if socket.get("name") == input_name),
                    None,
                )
                if input_index is None or input_index >= len(operands):
                    continue
                source_operand = operands[input_index]
                source_output = reference_output(source_operand)
                source_is_collection = bool(
                    source_output
                    and source_output.get("data_structure") in {"list", "tree", "tuple"}
                )
                if source_is_collection and created_output.get(
                    "generic_type_collection", {}
                ).get(typevar_name):
                    source_container_type = source_output.get("type", "List")
                    created_output.update({
                        "type": (
                            source_container_type
                            if source_container_type not in {"List", "Any"}
                            else "List"
                        ),
                        "data_structure": "list",
                        "item_type": source_output.get("item_type", "Any"),
                        "data_access": "structure",
                    })
                else:
                    created_output["type"] = reference_type(source_operand)
                    created_output.pop("data_structure", None)
                    created_output.pop("item_type", None)
                    created_output["data_access"] = "item"
            return ("ref", created, "result")
        raise ValueError("Unsupported expression '{}' at line {}".format(
            type(node).__name__, getattr(node, "lineno", "?")))

    def encode_scope_value(value):
        if isinstance(value, enum.Enum):
            return {"__flow_enum__": [value.__class__.__module__,
                                      value.__class__.__name__, value.name]}
        if b3d is not None and isinstance(value, b3d.Plane):
            return {
                "__flow_object__": "Plane",
                "origin": list(value.origin), "x_dir": list(value.x_dir),
                "z_dir": list(value.z_dir),
            }
        if b3d is not None and isinstance(value, b3d.Location):
            return {"__flow_object__": "Location", "value": list(value.to_tuple())}
        if b3d is not None and isinstance(value, b3d.Vector):
            return {"__flow_object__": "Vector", "value": list(value)}
        if b3d is not None and isinstance(value, b3d.Axis):
            return {"__flow_object__": "Axis",
                    "args": [list(value.position), list(value.direction)], "kwargs": {}}
        if isinstance(value, tuple):
            return {"__flow_tuple__": [encode_scope_value(item) for item in value]}
        if isinstance(value, list):
            return [encode_scope_value(item) for item in value]
        if isinstance(value, dict):
            return {key: encode_scope_value(item) for key, item in value.items()}
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise ValueError("Unsupported context-manager argument value: " + type(value).__name__)

    def scope_expression(node):
        kind, value, *extra = expression(node)
        if kind == "literal":
            return encode_scope_value(value)
        if kind == "ref":
            return {"__flow_ref__": [value, extra[0]]}
        raise ValueError("Unsupported context-manager expression at line {}".format(node.lineno))

    def scope_reference_ids(value):
        references = []

        def visit(item):
            if isinstance(item, dict):
                if "__flow_ref__" in item:
                    references.append(str(item["__flow_ref__"][0]))
                    return
                for child in item.values():
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(value)
        return list(dict.fromkeys(references))

    def resolve_context_manager(node):
        if not isinstance(node, ast.Call):
            raise ValueError("A with-block must call a build123d context manager (line {})".format(node.lineno))
        function = node.func
        manager_name = None
        if isinstance(function, ast.Name):
            manager_name = imported.get(function.id, function.id)
        elif isinstance(function, ast.Attribute):
            parts = []
            current = function
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name) and current.id in aliases and aliases[current.id] == "b3d":
                path = list(reversed(parts))
                if len(path) == 1:
                    manager_name = path[0]
        try:
            manager = type_catalog.resolve_type(manager_name) if manager_name else None
        except CatalogError:
            manager = None
        if manager is None:
            raise ValueError("Unsupported build123d context manager at line {}: {}".format(
                node.lineno, ast.unparse(function)))
        if not manager.get("supports_context_manager"):
            raise ValueError("{} is not a context manager (line {})".format(
                manager["name"], node.lineno))
        args = [
            {"__flow_starred__": scope_expression(argument.value)}
            if isinstance(argument, ast.Starred)
            else scope_expression(argument)
            for argument in node.args
        ]
        kwargs = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                raise ValueError("Expanded context-manager arguments are unsupported (line {})".format(node.lineno))
            kwargs[keyword.arg] = scope_expression(keyword.value)
        return manager, args, kwargs

    def add_scope_node(scope_id, role, line, output_type="Any", visible=False, dependencies=()):
        nonlocal counter
        counter += 1
        node_id = "import_{}".format(counter)
        definition = {
            "id": "core.python.scope." + role,
            "name": "Python Scope " + role.title(),
            "source": "python", "kind": "value",
            "category": "Python", "palette_category": "Scripting",
            "palette_section": "Execution",
            "description": "Internal value node used to preserve a Python context-manager scope.",
            "inputs": [], "outputs": [{"name": "result", "type": output_type}],
        }
        data = {
            "definition": definition, "params": {},
            "execution_scope_path": list(active_scope_ids),
            "visible": visible,
        }
        if role == "binding":
            data["scope_binding"] = scope_id
            data["scope_dependencies"] = list(dict.fromkeys(dependencies))
        else:
            data["scope_result"] = scope_id
        graph_nodes.append({
            "id": node_id, "type": "api",
            "position": {"x": 80 + ((counter - 1) % 4) * 300, "y": 80 + ((counter - 1) // 4) * 190},
            "data": data,
        })
        return node_id

    def graph_depends_on(node_id, ancestors):
        """Return whether a graph value is derived from any supplied node."""
        pending = [node_id]
        visited = set()
        while pending:
            current_id = pending.pop()
            if current_id in ancestors:
                return True
            if current_id in visited:
                continue
            visited.add(current_id)
            for edge in graph_edges:
                if edge.get("target") == current_id:
                    pending.append(edge.get("source"))
        return False

    def select_loop_mapping(iterable_id, ancestors, line):
        """Choose a representable mapping mode and reject mixed nested semantics."""
        nonlocal mapping_matching
        selected = "longest" if graph_depends_on(iterable_id, ancestors) else "cross"
        prior_modes = [frame.get("matching", "longest") for frame in active_loop_frames[1:]]
        if prior_modes and any(mode != selected for mode in prior_modes):
            raise ValueError(
                "Mixed independent and dependent nested loops require per-scope mapping, "
                "which is not yet supported (line {})".format(line)
            )
        mapping_matching = selected
        return selected

    def last_sequence_item(value, line, fallback=_MISSING):
        """Lower Python's post-loop binding, including zero-iteration fallback."""
        if value[0] == "literal":
            sequence = materialize_literal(value[1])
            if not isinstance(sequence, (list, tuple)):
                raise ValueError("Loop iterable is not a sequence at line {}".format(line))
            if sequence:
                return ("literal", sequence[-1])
            if fallback is not _MISSING:
                return fallback
            raise ValueError("Loop target is unbound because the iterable is empty (line {})".format(line))
        source_node = next((item for item in graph_nodes if item["id"] == value[1]), None)
        source_outputs = source_node.get("data", {}).get("definition", {}).get("outputs", []) if source_node else []
        source_output = next((item for item in source_outputs if item.get("name") == value[2]), None)
        output_type = (
            value[3] if len(value) > 3 and value[3]
            else source_output.get("type", "Any") if source_output else "Any"
        )
        definition_id = "core.python.loop_last"
        definition = {
            "id": definition_id, "name": "Python Loop Last",
            "source": "python", "kind": "operation", "category": "Internal",
            "palette_category": "Internal", "palette_section": "Graph lowering",
            "inputs": [
                {"name": "sequence", "type": "Any", "required": True, "data_access": "structure"},
                {"name": "fallback", "type": "Any", "required": False, "default": None, "data_access": "structure"},
                {"name": "has_fallback", "type": "Boolean", "required": True},
                {"name": "preserve_depth", "type": "Number", "required": True},
            ],
            "outputs": [{"name": "result", "type": output_type}],
        }
        definitions[definition_id] = definition
        _INTERNAL_NODE_IDS.add(definition_id)
        _CATALOG_CALLABLES[definition_id] = _python_loop_last
        _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
            inspect.Parameter("sequence", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("fallback", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=None),
            inspect.Parameter("has_fallback", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=False),
            inspect.Parameter("preserve_depth", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=0),
        ])
        _CATALOG_HINT_TARGETS[definition_id] = False
        params = {
            "has_fallback": fallback is not _MISSING,
            "preserve_depth": len(active_loop_frames),
        }
        links = [("sequence", value[1], value[2])]
        if fallback is not _MISSING:
            if fallback[0] == "literal":
                params["fallback"] = literal_json_value(fallback[1])
            else:
                links.append(("fallback", fallback[1], fallback[2]))
        node_id = add_node(definition_id, params, links, line)
        node = next(item for item in graph_nodes if item["id"] == node_id)
        node["data"]["visible"] = output_type in {"Shape", "Solid", "Compound", "Part", "Sketch"}
        return ("ref", node_id, "result")

    def process_statements(statements):
        nonlocal scope_counter, mapping_matching

        def assign_target(target, assigned, line):
            if isinstance(target, ast.Name):
                variables[target.id] = assigned
                for frame_index, frame in enumerate(active_loop_frames):
                    frame["written_names"].add(target.id)
                    if frame_index < len(active_loop_frames) - 1:
                        frame.setdefault("nested_written_names", set()).add(target.id)
                return
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                variables[ast.unparse(target)] = assigned
                return
            if isinstance(target, ast.Attribute):
                receiver = expression(target.value)
                if receiver[0] != "ref":
                    raise ValueError("Attribute assignment requires a graph-backed receiver (line {})".format(line))
                definition_id = "core.python.setattr"
                def set_attribute(value, name, assigned):
                    setattr(value, name, assigned)
                    return value
                definitions[definition_id] = {
                    "id": definition_id, "name": "Python Set Attribute",
                    "source": "python", "kind": "operation", "category": "Internal",
                    "palette_category": "Python", "palette_section": "Assignments",
                    "inputs": [
                        {"name": "value", "type": "Any", "required": True},
                        {"name": "name", "type": "String", "required": True},
                        {"name": "assigned", "type": "Any", "required": True},
                    ],
                    "outputs": [{"name": "result", "type": reference_type(receiver)}],
                }
                _INTERNAL_NODE_IDS.add(definition_id)
                _CATALOG_CALLABLES[definition_id] = set_attribute
                _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                    inspect.Parameter("value", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                    inspect.Parameter("name", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                    inspect.Parameter("assigned", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                ])
                _CATALOG_HINT_TARGETS[definition_id] = False
                params, links = {"name": target.attr}, [("value", receiver[1], receiver[2])]
                if assigned[0] == "literal":
                    params["assigned"] = literal_json_value(assigned[1])
                else:
                    links.append(("assigned", assigned[1], assigned[2]))
                add_node(definition_id, params, links, line)
                return
            if isinstance(target, (ast.Tuple, ast.List)):
                if assigned[0] == "literal" and isinstance(assigned[1], (list, tuple)):
                    values = [("literal", item) for item in assigned[1]]
                elif assigned[0] == "ref":
                    values = []
                    element_types = assigned[4] if len(assigned) > 4 else None
                    for index in range(len(target.elts)):
                        index_node = add_node(
                            _PYTHON_SUBSCRIPT_ID, {"index": index},
                            [("value", assigned[1], assigned[2])], line
                        )
                        # Tuple-unpacking subscripts are part of the loop body:
                        # apply them to each tuple item, unlike ordinary Python
                        # indexing of a collection, which consumes the collection
                        # as a structure and must not trigger implicit mapping.
                        unpack_node = next(item for item in graph_nodes if item["id"] == index_node)
                        unpack_value = next(
                            socket for socket in unpack_node["data"]["definition"]["inputs"]
                            if socket["name"] == "value"
                        )
                        unpack_value["data_access"] = "item"
                        element_type = (
                            element_types[index]
                            if element_types and index < len(element_types) else "Any"
                        )
                        values.append(("ref", index_node, "result", element_type))
                else:
                    raise ValueError("Cannot unpack this assignment at line {}".format(line))
                if len(values) != len(target.elts):
                    raise ValueError("Unpacking length mismatch at line {}".format(line))
                for child, value in zip(target.elts, values):
                    assign_target(child, value, line)
                return
            raise ValueError("Unsupported assignment target at line {}".format(line))

        for statement_index, statement in enumerate(statements):
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(statement, (ast.Global, ast.Nonlocal)):
                continue
            if isinstance(statement, ast.Try):
                try:
                    process_statements(statement.body)
                except ValueError as exc:
                    message = str(exc)
                    if (
                        "Unresolved augmented-assignment target" in message
                        or "Invalid subscript" in message
                    ) and statement.handlers:
                        process_statements(statement.handlers[0].body)
                    else:
                        raise
                else:
                    process_statements(statement.orelse)
                process_statements(statement.finalbody)
                continue
            if isinstance(statement, ast.With):
                entered_contexts = []
                for item in statement.items:
                    manager, args, kwargs = resolve_context_manager(item.context_expr)
                    scope_counter += 1
                    scope_id = "python_scope_{}".format(scope_counter)
                    manager_name = manager["name"]
                    result_attribute = {
                        "BuildPart": "part", "BuildSketch": "sketch", "BuildLine": "line",
                    }.get(manager_name)
                    optional = item.optional_vars
                    if optional is not None and not isinstance(optional, ast.Name):
                        raise ValueError("Only a simple name is supported after 'as' (line {})".format(statement.lineno))
                    descriptor = {
                        "id": scope_id, "module": manager["module"],
                        "name": manager_name, "args": args, "kwargs": kwargs,
                        "result_attribute": result_attribute,
                        "has_binding": optional is not None,
                    }
                    execution_scopes.append(descriptor)
                    dependencies = scope_reference_ids([args, kwargs])
                    active_scope_ids.append(scope_id)
                    binding_type = {
                        "BuildPart": "BuildPart", "BuildSketch": "BuildSketch",
                        "BuildLine": "BuildLine",
                    }.get(manager_name, manager_name)
                    binding_node = add_scope_node(
                        scope_id, "binding", statement.lineno,
                        output_type=binding_type,
                        dependencies=dependencies,
                    )
                    if optional is not None:
                        variables[optional.id] = ("ref", binding_node, "result")
                    entered_contexts.append((scope_id, manager_name, optional))
                process_statements(statement.body)
                for scope_id, manager_name, optional in reversed(entered_contexts):
                    active_scope_ids.pop()
                    output_type = {
                        "BuildPart": "Part", "BuildSketch": "Sketch", "BuildLine": "Wire",
                    }.get(manager_name, "Any")
                    should_emit_result = optional is not None or manager_name in {"BuildPart", "BuildSketch", "BuildLine"}
                    if should_emit_result:
                        result_node = add_scope_node(
                            scope_id, "result", statement.lineno, output_type,
                            output_type in {"Shape", "Part", "Sketch", "Wire"}
                        )
                        scope_result_nodes[scope_id] = result_node
                continue
            if isinstance(statement, ast.Expr):
                if is_viewer_call(statement.value):
                    for argument in statement.value.args:
                        expression(argument)
                    for keyword in statement.value.keywords:
                        expression(keyword.value)
                    continue
                # Python list.extend mutates its receiver and returns None.
                # Represent that mutation as a new list node and rebind the
                # variable so later reads observe the extended collection.
                call = statement.value
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in {"append", "extend"}
                    and isinstance(call.func.value, ast.Name)
                    and len(call.args) == 1
                    and not call.keywords
                ):
                    target_name = call.func.value.id
                    receiver = expression(call.func.value)
                    value = expression(call.args[0])
                    if active_loop_frames:
                        active_loop_frames[-1]["written_names"].add(target_name)
                        for outer_frame in active_loop_frames[:-1]:
                            outer_frame.setdefault("nested_written_names", set()).add(target_name)
                    if receiver[0] == "literal" and value[0] == "literal" and isinstance(receiver[1], list):
                        updated = list(receiver[1])
                        if call.func.attr == "append":
                            updated.append(materialize_literal(value[1]))
                        else:
                            updated.extend(materialize_literal(value[1]))
                        variables[target_name] = ("literal", updated)
                        continue
                    if receiver[0] not in {"ref", "literal"} or value[0] not in {"ref", "literal"}:
                        expression(statement.value)
                        continue
                    definition_id = (
                        "core.list.append" if call.func.attr == "append"
                        else "core.list.extend"
                    )
                    value_socket = "value" if call.func.attr == "append" else "values"
                    params, links = {}, []
                    if receiver[0] == "literal":
                        params["items"] = literal_json_value(receiver[1])
                    else:
                        links.append(("items", receiver[1], receiver[2]))
                    if value[0] == "literal":
                        params[value_socket] = literal_json_value(value[1])
                    else:
                        links.append((value_socket, value[1], value[2]))
                    updated_list = add_node(
                        definition_id, params, links, statement.lineno,
                    )
                    variables[target_name] = ("ref", updated_list, "result", "List")
                    continue
                expression(statement.value)
                continue
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value_node = statement.value
                if value_node is None:
                    continue
                result = expression(value_node)
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                for target in targets:
                    assign_target(target, result, statement.lineno)
                continue
            if isinstance(statement, ast.For):
                iterable = expression(statement.iter)
                if (
                    iterable[0] == "literal"
                    and isinstance(iterable[1], (list, tuple, range))
                    and not active_loop_frames
                    and len(iterable[1]) <= 32
                    and any(
                        isinstance(child, ast.If)
                        for body_statement in statement.body
                        for child in ast.walk(body_statement)
                    )
                ):
                    saved_variables = dict(variables)
                    for loop_value in iterable[1]:
                        if isinstance(statement.target, ast.Name):
                            variables[statement.target.id] = ("literal", loop_value)
                        else:
                            assign_target(statement.target, ("literal", loop_value), statement.lineno)
                        process_statements(statement.body)
                    if not iterable[1]:
                        variables.clear()
                        variables.update(saved_variables)
                    continue
                if iterable[0] == "literal" and isinstance(iterable[1], (list, tuple, range)):
                    iterable = add_collection_node(
                        [("literal", item) for item in iterable[1]], "list",
                        statement.iter.lineno, map_over_active_loops=False,
                    )
                if iterable[0] != "ref":
                    raise ValueError(
                        "A for-loop iterable must be a sequence or graph output (line {})".format(statement.lineno)
                    )

                saved_variables = dict(variables)
                saved_stream_variables = set(stream_variables)
                saved_matching = mapping_matching
                item_type, item_types = iterable_item_metadata(iterable)
                typed_iterable = (
                    "ref", iterable[1], iterable[2], item_type, item_types
                )
                if isinstance(statement.target, ast.Name):
                    target_names = [statement.target]
                    variables[statement.target.id] = typed_iterable
                elif isinstance(statement.target, (ast.Tuple, ast.List)):
                    target_names = [item for item in statement.target.elts if isinstance(item, ast.Name)]
                    if len(target_names) != len(statement.target.elts):
                        raise ValueError("Only simple names are supported in a for-loop unpack target (line {})".format(statement.lineno))
                    assign_target(statement.target, typed_iterable, statement.lineno)
                else:
                    raise ValueError("Unsupported for-loop target (line {})".format(statement.lineno))

                # Independent nested iterables form a Cartesian product;
                # iterables derived from the outer item are zipped/longest-
                # matched. The body itself is still compiled exactly once.
                if active_loop_frames:
                    outer_ids = {frame["node_id"] for frame in active_loop_frames}
                    select_loop_mapping(iterable[1], outer_ids, statement.iter.lineno)
                for target in target_names:
                    for outer_frame in active_loop_frames:
                        outer_frame["written_names"].add(target.id)
                        outer_frame.setdefault("nested_written_names", set()).add(target.id)
                frame = {
                    "node_id": iterable[1], "output_name": iterable[2],
                    "written_names": set(target.id for target in target_names),
                    "matching": mapping_matching,
                }
                active_loop_frames.append(frame)
                stream_variables.update(target.id for target in target_names)
                process_statements(statement.body)
                body_variables = dict(variables)
                active_loop_frames.pop()
                stream_variables.clear()
                stream_variables.update(saved_stream_variables)
                mapping_matching = saved_matching

                # Only materialize post-loop bindings that are actually read
                # later. An unused loop target must not introduce an artificial
                # failure for an empty iterable.
                future_names = set()
                future_statements = list(statements[statement_index + 1:]) + list(statement.orelse)
                for future_statement in future_statements:
                    future_names.update(
                        item.id for item in ast.walk(future_statement)
                        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
                    )
                # A nested loop's post-loop binding is itself a mapped value:
                # its loop_last node executes for each enclosing-loop item.
                # The enclosing loop can then map its own loop_last over that
                # sequence, preserving Python's last-value binding at each scope.

                # Python leaves a used loop target bound to its last value.
                # Lower only the bindings whose values are observed afterward.
                variables.clear()
                variables.update(saved_variables)
                for name in sorted(frame["written_names"] & future_names):
                    if name in frame.get("nested_written_names", set()):
                        nested_value = body_variables.get(name, _MISSING)
                        if nested_value is _MISSING or nested_value == saved_variables.get(name, _MISSING):
                            raise ValueError(
                                "A value assigned in a nested loop and read after its enclosing loop "
                                "requires nested loop-result grouping (line {}): {}".format(
                                    statement.lineno, name
                                )
                            )
                    if name not in body_variables:
                        continue
                    candidate = body_variables[name]
                    if candidate[0] == "literal":
                        # A literal assignment inside the loop still occurs
                        # once per iteration; the token node preserves empty-
                        # sequence behavior and the sequence dependency.
                        loop_value_id = "core.python.loop_value"
                        literal_type = _type_name(type(materialize_literal(candidate[1])))
                        loop_value_definition = {
                            "id": loop_value_id, "name": "Python Loop Value",
                            "source": "python", "kind": "operation", "category": "Internal",
                            "palette_category": "Internal", "palette_section": "Graph lowering",
                            "inputs": [
                                {"name": "value", "type": literal_type, "required": True, "data_access": "structure"},
                                {"name": "iteration", "type": "Any", "required": True, "data_access": "item"},
                            ],
                            "outputs": [{"name": "result", "type": literal_type}],
                        }
                        definitions[loop_value_id] = loop_value_definition
                        _INTERNAL_NODE_IDS.add(loop_value_id)
                        _CATALOG_CALLABLES[loop_value_id] = lambda value, iteration: value
                        _CATALOG_SIGNATURES[loop_value_id] = inspect.Signature([
                            inspect.Parameter("value", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                            inspect.Parameter("iteration", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                        ])
                        _CATALOG_HINT_TARGETS[loop_value_id] = False
                        carried_id = add_node(
                            loop_value_id, {"value": literal_json_value(candidate[1])},
                            [("iteration", iterable[1], iterable[2])], statement.lineno,
                        )
                        if frame["matching"] != "longest":
                            next(item for item in graph_nodes if item["id"] == carried_id)["data"]["data_matching"] = frame["matching"]
                        candidate = ("ref", carried_id, "result")
                    elif not graph_depends_on(candidate[1], {iterable[1]}):
                        source_node = next((item for item in graph_nodes if item["id"] == candidate[1]), None)
                        source_outputs = source_node.get("data", {}).get("definition", {}).get("outputs", []) if source_node else []
                        source_output = next((item for item in source_outputs if item.get("name") == candidate[2]), None)
                        carried_type = source_output.get("type", "Any") if source_output else "Any"
                        loop_value_definition = {
                            "id": "core.python.loop_value", "name": "Python Loop Value",
                            "source": "python", "kind": "operation", "category": "Internal",
                            "palette_category": "Internal", "palette_section": "Graph lowering",
                            "inputs": [
                                {"name": "value", "type": carried_type, "required": True, "data_access": "structure"},
                                {"name": "iteration", "type": "Any", "required": True, "data_access": "item"},
                            ],
                            "outputs": [{"name": "result", "type": carried_type}],
                        }
                        definitions["core.python.loop_value"] = loop_value_definition
                        _INTERNAL_NODE_IDS.add("core.python.loop_value")
                        _CATALOG_CALLABLES["core.python.loop_value"] = lambda value, iteration: value
                        _CATALOG_SIGNATURES["core.python.loop_value"] = inspect.Signature([
                            inspect.Parameter("value", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                            inspect.Parameter("iteration", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                        ])
                        _CATALOG_HINT_TARGETS["core.python.loop_value"] = False
                        carried_id = add_node(
                            "core.python.loop_value", {},
                            [("value", candidate[1], candidate[2]),
                             ("iteration", iterable[1], iterable[2])], statement.lineno,
                        )
                        if frame["matching"] != "longest":
                            next(item for item in graph_nodes if item["id"] == carried_id)["data"]["data_matching"] = frame["matching"]
                        candidate = ("ref", carried_id, "result")
                    variables[name] = last_sequence_item(
                        candidate, statement.lineno, saved_variables.get(name, _MISSING)
                    )

                if statement.orelse:
                    # With break rejected below, a for-else suite always runs.
                    process_statements(statement.orelse)
                continue
            if isinstance(statement, ast.AugAssign):
                if not isinstance(statement.target, (ast.Name, ast.Attribute)):
                    raise ValueError("Augmented assignment requires a name or attribute target (line {})".format(statement.lineno))
                if isinstance(statement.target, ast.Name) and statement.target.id not in variables:
                    raise ValueError("Unresolved augmented-assignment target '{}' at line {}".format(
                        statement.target.id, statement.lineno))
                combined = ast.BinOp(
                    left=statement.target,
                    op=statement.op,
                    right=statement.value,
                )
                ast.copy_location(combined, statement)
                assigned = expression(combined)
                if isinstance(statement.target, ast.Name):
                    variables[statement.target.id] = assigned
                else:
                    assign_target(statement.target, assigned, statement.lineno)
                continue
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                user_functions[statement.name] = statement
                continue
            if isinstance(statement, ast.ClassDef):
                user_classes[statement.name] = statement
                continue
            if isinstance(statement, ast.Return):
                raise _FunctionReturn(
                    expression(statement.value) if statement.value is not None
                    else ("literal", None)
                )
            if isinstance(statement, ast.If):
                condition = expression(statement.test)
                if condition[0] == "literal":
                    process_statements(statement.body if bool(condition[1]) else statement.orelse)
                    continue
                if condition[0] != "ref":
                    raise ValueError("if condition must be a boolean value or graph output (line {})".format(statement.lineno))
                def assigned_names(statements):
                    names = set()
                    for root in statements:
                        for child in ast.walk(root):
                            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                                names.add(child.id)
                            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child is not root:
                                continue
                    return names
                changed_names = assigned_names(statement.body) | assigned_names(statement.orelse)
                saved_variables = dict(variables)
                active_conditions.append(condition)
                try:
                    process_statements(statement.body)
                finally:
                    active_conditions.pop()
                true_variables = dict(variables)
                variables.clear()
                variables.update(saved_variables)
                false_condition_id = "core.python.not"
                definitions[false_condition_id] = {
                    "id": false_condition_id, "name": "Python not",
                    "source": "python", "kind": "operation", "category": "Internal",
                    "palette_category": "Python", "palette_section": "Expressions",
                    "inputs": [{"name": "value", "type": "Boolean", "required": True, "data_access": "item"}],
                    "outputs": [{"name": "result", "type": "Boolean"}],
                }
                _INTERNAL_NODE_IDS.add(false_condition_id)
                _CATALOG_CALLABLES[false_condition_id] = operator.not_
                _CATALOG_SIGNATURES[false_condition_id] = inspect.Signature([
                    inspect.Parameter("value", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                ])
                _CATALOG_HINT_TARGETS[false_condition_id] = False
                false_node = add_node(
                    false_condition_id, {},
                    [("value", condition[1], condition[2])],
                    statement.lineno,
                )
                false_condition = ("ref", false_node, "result", "Boolean")
                active_conditions.append(false_condition)
                try:
                    process_statements(statement.orelse)
                finally:
                    active_conditions.pop()
                false_variables = dict(variables)
                variables.clear()
                variables.update(saved_variables)
                for name in sorted(changed_names):
                    true_value = true_variables.get(name, saved_variables.get(name, ("literal", None)))
                    false_value = false_variables.get(name, saved_variables.get(name, ("literal", None)))
                    if true_value == false_value:
                        variables[name] = true_value
                        continue
                    select_id = "core.python.select"
                    definitions[select_id] = {
                        "id": select_id, "name": "Python conditional value",
                        "source": "python", "kind": "operation", "category": "Internal",
                        "palette_category": "Python", "palette_section": "Control flow",
                        "inputs": [
                            {"name": "condition", "type": "Boolean", "required": True, "data_access": "item"},
                            {"name": "if_true", "type": "Any", "required": True, "data_access": "item"},
                            {"name": "if_false", "type": "Any", "required": True, "data_access": "item"},
                        ],
                        "outputs": [{"name": "result", "type": "Any"}],
                    }
                    _INTERNAL_NODE_IDS.add(select_id)
                    _CATALOG_CALLABLES[select_id] = lambda condition, if_true, if_false: if_true if condition else if_false
                    _CATALOG_SIGNATURES[select_id] = inspect.Signature([
                        inspect.Parameter("condition", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                        inspect.Parameter("if_true", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                        inspect.Parameter("if_false", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                    ])
                    _CATALOG_HINT_TARGETS[select_id] = False
                    params, links = {}, []
                    for input_name, value in (
                        ("condition", condition), ("if_true", true_value), ("if_false", false_value)
                    ):
                        if value[0] == "literal":
                            params[input_name] = literal_json_value(value[1])
                        else:
                            links.append((input_name, value[1], value[2]))
                    selected = add_node(select_id, params, links, statement.lineno)
                    variables[name] = ("ref", selected, "result", "Any")
                continue
            if isinstance(statement, ast.Assert):
                condition = expression(statement.test)
                message = (
                    ("literal", ast.unparse(statement.msg))
                    if statement.msg is not None and isinstance(statement.msg, ast.JoinedStr)
                    else expression(statement.msg) if statement.msg is not None
                    else ("literal", None)
                )
                definition_id = "core.python.assert"
                def assert_condition(condition, message=None):
                    if not condition:
                        raise AssertionError(message)
                    return True
                definitions[definition_id] = {
                    "id": definition_id, "name": "Python Assert",
                    "source": "python", "kind": "operation", "category": "Internal",
                    "palette_category": "Python", "palette_section": "Control flow",
                    "inputs": [
                        {"name": "condition", "type": "Boolean", "required": True},
                        {"name": "message", "type": "Any", "required": False, "default": None},
                    ],
                    "outputs": [{"name": "result", "type": "Boolean"}],
                }
                _INTERNAL_NODE_IDS.add(definition_id)
                _CATALOG_CALLABLES[definition_id] = assert_condition
                _CATALOG_SIGNATURES[definition_id] = inspect.Signature([
                    inspect.Parameter("condition", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                    inspect.Parameter("message", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=None),
                ])
                _CATALOG_HINT_TARGETS[definition_id] = False
                params, links = {}, []
                for name, value in (("condition", condition), ("message", message)):
                    if value[0] == "literal":
                        params[name] = literal_json_value(value[1])
                    else:
                        links.append((name, value[1], value[2]))
                add_node(definition_id, params, links, statement.lineno)
                continue
            if isinstance(statement, ast.Pass):
                continue
            raise ValueError("Unsupported statement '{}' at line {}".format(
                type(statement).__name__, getattr(statement, "lineno", "?")))

    class _FunctionReturn(Exception):
        def __init__(self, value):
            self.value = value

    def inline_user_function(function_node, call_node, keep_names=()):
        declared_globals = {
            name for child in ast.walk(ast.Module(body=function_node.body, type_ignores=[]))
            if isinstance(child, (ast.Global, ast.Nonlocal))
            for name in child.names
        }
        keep_names = tuple(set(keep_names) | declared_globals)
        arguments = function_node.args
        positional_parameters = list(arguments.posonlyargs) + list(arguments.args)
        defaults = list(arguments.defaults)
        default_offset = len(positional_parameters) - len(defaults)
        bound = {}
        supplied = set()
        for index, argument in enumerate(call_node.args):
            if isinstance(argument, ast.Starred):
                expanded = expression(argument.value)
                if expanded[0] != "literal" or not isinstance(expanded[1], (list, tuple)):
                    raise ValueError("Expanded user-function arguments must be literal sequences (line {})".format(call_node.lineno))
                values = list(expanded[1])
            else:
                values = [None]
            if isinstance(argument, ast.Starred):
                for value in values:
                    if index >= len(positional_parameters):
                        raise ValueError("Too many arguments for function {} (line {})".format(function_node.name, call_node.lineno))
                    name = positional_parameters[index].arg
                    bound[name] = ("literal", value)
                    supplied.add(name)
                    index += 1
                continue
            if index >= len(positional_parameters):
                if arguments.vararg is None:
                    raise ValueError("Too many arguments for function {} (line {})".format(function_node.name, call_node.lineno))
                name = arguments.vararg.arg
                prior = bound.setdefault(name, ("literal", []))
                bound[name] = ("literal", list(prior[1]) + [expression(argument)[1]])
            else:
                name = positional_parameters[index].arg
                bound[name] = expression(argument)
                supplied.add(name)
        for keyword in call_node.keywords:
            if keyword.arg is None:
                expanded = expression(keyword.value)
                if expanded[0] != "literal" or not isinstance(expanded[1], dict):
                    raise ValueError("Expanded user-function keywords must be literal mappings (line {})".format(call_node.lineno))
                pairs = expanded[1].items()
            else:
                pairs = [(keyword.arg, keyword.value)]
            for name, value_node in pairs:
                parameter = next((item for item in positional_parameters if item.arg == name), None)
                if parameter is None and arguments.kwarg is None:
                    raise ValueError("Unknown keyword '{}' for function {} (line {})".format(name, function_node.name, call_node.lineno))
                if name in supplied:
                    raise ValueError("Multiple values for argument '{}' (line {})".format(name, call_node.lineno))
                bound[name] = expression(value_node) if isinstance(value_node, ast.AST) else ("literal", value_node)
                supplied.add(name)
        for index, parameter in enumerate(positional_parameters):
            if parameter.arg not in bound:
                if index >= default_offset:
                    bound[parameter.arg] = expression(defaults[index - default_offset])
                else:
                    raise ValueError("Missing argument '{}' for function {} (line {})".format(parameter.arg, function_node.name, call_node.lineno))
        if arguments.vararg and arguments.vararg.arg not in bound:
            bound[arguments.vararg.arg] = ("literal", [])
        if arguments.kwarg and arguments.kwarg.arg not in bound:
            bound[arguments.kwarg.arg] = ("literal", {})
        saved = dict(variables)
        saved_stream = set(stream_variables)
        try:
            variables.update(bound)
            try:
                process_statements(function_node.body)
            except _FunctionReturn as returned:
                return returned.value
            return ("literal", None)
        finally:
            kept = {name: variables[name] for name in keep_names if name in variables}
            variables.clear()
            variables.update(saved)
            variables.update(kept)
            stream_variables.clear()
            stream_variables.update(saved_stream)

    def inline_user_class(class_node, call_node):
        init_method = next(
            (item for item in class_node.body
             if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
             and item.name == "__init__"),
            None,
        )
        if init_method is None:
            raise ValueError("Custom class {} must define __init__ (line {})".format(
                class_node.name, class_node.lineno))
        if not class_node.bases:
            raise ValueError("Custom class {} must inherit a discovered build123d type".format(class_node.name))
        base_expression = class_node.bases[0]
        base_name = (
            base_expression.id if isinstance(base_expression, ast.Name)
            else ast.unparse(base_expression).split(".")[-1]
        )
        try:
            type_catalog.resolve_symbol(imported.get(base_name, base_name))
        except CatalogError as exc:
            raise ValueError("Custom class {} has an unknown base {}".format(
                class_node.name, base_name)) from exc
        method = copy.copy(init_method)
        method.args = copy.copy(init_method.args)
        if method.args.posonlyargs:
            method.args.posonlyargs = list(method.args.posonlyargs[1:])
        elif method.args.args:
            method.args.args = list(method.args.args[1:])
        else:
            raise ValueError("Custom class {} __init__ has no self parameter".format(class_node.name))
        class_stack.append(imported.get(base_name, base_name))
        variables["self"] = ("literal", {"__flow_custom_self__": class_node.name})
        try:
            inline_user_function(method, call_node, keep_names=("self",))
            result = variables.get("self")
            if result is None or (
                result[0] == "literal"
                and isinstance(result[1], dict)
                and "__flow_custom_self__" in result[1]
            ):
                raise ValueError("Custom class {} did not initialize its base object".format(class_node.name))
            return result
        finally:
            class_stack.pop()
            variables.pop("self", None)
            for name in [name for name in variables if name.startswith("self.")]:
                variables.pop(name, None)

    process_statements(tree.body)
    if not graph_nodes:
        raise ValueError("No supported build123d operations were found")
    return {"nodes": graph_nodes, "edges": graph_edges, "execution_scopes": execution_scopes}
