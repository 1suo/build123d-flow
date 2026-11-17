import type { GraphDocument, GraphNode, NodeDefinition } from "../types";
import type { CadPortDefinition, CadCollectionShape } from "./cadKernel";
import { adaptRuntimeOperation, isCadGeometryPort, validateCadOperation } from "./cadKernel";

export function createGraphNode(
  definition: NodeDefinition,
  id: string,
  position: { x: number; y: number },
): GraphNode {
  const portableDefinition = definition.cadOperation
    ? definition
    : { ...definition, cadOperation: adaptRuntimeOperation(definition) };
  const params: Record<string, unknown> = {};
  for (const input of portableDefinition.cadOperation!.inputs) {
    if (input.defaultValue !== undefined) params[input.id] = input.defaultValue;
  }
  return {
    id,
    type: "api" as const,
    position,
    data: {
      definition: portableDefinition,
      params,
      visible: portableDefinition.cadOperation!.outputs.some(isCadGeometryPort),
    },
  };
}

function compatibleTypes(
  sourceType: string,
  targetType: string,
  source: CadPortDefinition,
  target: CadPortDefinition,
): boolean {
  if (targetType === "any" || sourceType === "any" || sourceType === targetType) return true;
  const targetIdentity = target.typeId ?? targetType;
  // Python's discovered MRO is the authority for nominal subtype compatibility.
  return Boolean(targetIdentity && source.typeMro?.includes(targetIdentity));
}

function shapeElement(shape: import("../types").SocketDataShape | undefined): import("../types").SocketDataShape | undefined {
  if (!shape) return undefined;
  if (shape.kind === "list" || shape.kind === "tree" || shape.kind === "set") return shape.item;
  if (shape.kind === "map") return shape.value;
  if (shape.kind === "tuple") return { kind: "union", options: shape.items };
  if (shape.kind === "union") {
    return {
      kind: "union",
      options: shape.options.map((option) => shapeElement(option) ?? option),
    };
  }
  return shape;
}

function semanticShapeCompatible(
  source: import("../types").SocketDataShape,
  target: import("../types").SocketDataShape,
  sourcePort: CadPortDefinition,
): boolean {
  if (target.kind === "item" && target.type === "Any") return true;
  if (source.kind === "item" && source.type === "Any") return true;
  if (source.kind === "union") {
    return source.options.every((option) =>
      target.kind === "union"
        ? target.options.some((candidate) => semanticShapeCompatible(option, candidate, sourcePort))
        : semanticShapeCompatible(option, target, sourcePort)
    );
  }
  if (target.kind === "union") {
    return target.options.some((option) => semanticShapeCompatible(source, option, sourcePort));
  }
  if (source.kind === "item" && target.kind === "item") {
    const sourceIdentity = source.type_id ?? source.type ?? sourcePort.valueType;
    const targetIdentity = target.type_id ?? target.type ?? "Any";
    if (sourceIdentity === targetIdentity || targetIdentity === "Any") return true;
    return (source.type_mro ?? sourcePort.typeMro ?? []).includes(targetIdentity);
  }
  if (source.kind === "list" || source.kind === "tree" || source.kind === "set") {
    if (target.kind !== source.kind && !(source.kind === "list" && target.kind === "tree")) return false;
    return semanticShapeCompatible(source.item, target.item, sourcePort);
  }
  if (source.kind === "tuple" && target.kind === "tuple") {
    if (source.items.length !== target.items.length) return false;
    return source.items.every((item, index) =>
      semanticShapeCompatible(item, target.items[index], sourcePort)
    );
  }
  if (source.kind === "map" && target.kind === "map") {
    return semanticShapeCompatible(source.key, target.key, sourcePort)
      && semanticShapeCompatible(source.value, target.value, sourcePort);
  }
  return false;
}

function portTypesCompatible(
  source: CadPortDefinition,
  target: CadPortDefinition,
  sourceItems = false,
  targetItems = false,
): boolean {
  const sourceShape = sourceItems ? shapeElement(source.dataShape) : source.dataShape;
  const targetShape = targetItems ? shapeElement(target.dataShape) : target.dataShape;
  if (sourceShape && targetShape) {
    return semanticShapeCompatible(sourceShape, targetShape, source);
  }
  const sourceTypes = sourceItems
    ? [itemType(source), ...(source.variants ?? [])]
    : [source.valueType, ...(source.variants ?? [])];
  const targetTypes = targetItems
    ? [itemType(target), ...(target.variants ?? [])]
    : [target.valueType, ...(target.variants ?? [])];
  return sourceTypes.some((sourceType) =>
    targetTypes.some((targetType) => compatibleTypes(sourceType, targetType, source, target))
  );
}

function structureOf(port: CadPortDefinition): CadCollectionShape {
  return port.collection;
}

function accessOf(port: CadPortDefinition): "item" | "collection" | "tree" {
  return port.access ?? (port.collection === "item" ? "item" : "collection");
}

function itemType(port: CadPortDefinition): string {
  return port.itemType ?? port.valueType;
}

function effectiveOutput(
  graph: GraphDocument,
  node: GraphNode,
  output: CadPortDefinition,
  visited = new Set<string>(),
): CadPortDefinition {
  if (structureOf(output) !== "item" || visited.has(node.id)) return output;
  const nextVisited = new Set(visited).add(node.id);
  let inferred: "list" | "tree" | undefined;
  for (const edge of graph.edges.filter((item) => item.target === node.id)) {
    const input = node.data.definition.cadOperation?.inputs.find((item) => item.id === edge.targetHandle);
    if (!input || accessOf(input) !== "item") continue;
    const upstream = graph.nodes.find((item) => item.id === edge.source);
    const upstreamOutput = upstream?.data.definition.cadOperation?.outputs.find((item) => item.id === edge.sourceHandle);
    if (!upstream || !upstreamOutput) continue;
    const resolved = effectiveOutput(graph, upstream, upstreamOutput, nextVisited);
    const structure = structureOf(resolved);
    if (structure === "tree") inferred = "tree";
    else if (["list", "tuple"].includes(structure) && inferred !== "tree") inferred = "list";
  }
  const itemShape = output.dataShape ?? {
    kind: "item" as const,
    type: output.typeLabel ?? output.valueType,
    ...(output.typeId ? { type_id: output.typeId } : {}),
    ...(output.typeMro ? { type_mro: [...output.typeMro] } : {}),
    ...(output.isGeometry ? { is_geometry: true } : {}),
  };
  return inferred ? {
    ...output,
    collection: inferred,
    itemType: output.valueType,
    itemTypeLabel: output.typeLabel,
    itemGeometry: output.isGeometry,
    dataShape: { kind: inferred, item: itemShape },
  } : output;
}

function compatible(source: CadPortDefinition, target: CadPortDefinition): boolean {
  const sourceStructure = structureOf(source);
  const targetAccess = accessOf(target);
  if (sourceStructure !== "item" && targetAccess === "item") {
    if (sourceStructure === "set" || sourceStructure === "map") return false;
    return portTypesCompatible(source, target, true, false);
  }
  if (sourceStructure !== "item" && targetAccess !== "item") {
    const targetStructure = structureOf(target);
    if (targetStructure !== "item" && targetStructure !== sourceStructure &&
        !(targetStructure === "tree" && sourceStructure === "list")) return false;
    return portTypesCompatible(source, target, false, false);
  }
  if (sourceStructure === "item" && targetAccess !== "item") return false;
  return portTypesCompatible(source, target);
}

function nodeInput(node: GraphNode, portId: string | null | undefined): CadPortDefinition | undefined {
  return node.data.definition.cadOperation?.inputs.find((port) => port.id === portId);
}

function nodeOutput(node: GraphNode, portId: string | null | undefined): CadPortDefinition | undefined {
  return node.data.definition.cadOperation?.outputs.find((port) => port.id === portId);
}

export function canConnect(
  graph: GraphDocument,
  connection: { source: string | null; target: string | null; sourceHandle?: string | null; targetHandle?: string | null },
  excludeEdgeId?: string,
): boolean {
  if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle) return false;
  const source = graph.nodes.find((node) => node.id === connection.source);
  const target = graph.nodes.find((node) => node.id === connection.target);
  if (!source || !target) return false;

  // An input socket accepts only one upstream value.
  if (graph.edges.some((edge) => edge.id !== excludeEdgeId && edge.source === source.id &&
      edge.target === target.id && edge.sourceHandle === connection.sourceHandle &&
      edge.targetHandle === connection.targetHandle)) return false;

  const output = nodeOutput(source, connection.sourceHandle);
  const input = nodeInput(target, connection.targetHandle);
  return Boolean(output && input && compatible(effectiveOutput(graph, source, output), input));
}

export function validateGraph(graph: GraphDocument): string[] {
  const errors: string[] = [];
  const ids = new Set(graph.nodes.map((node) => node.id));

  for (const node of graph.nodes) {
    const operation = node.data.definition.cadOperation;
    if (!operation) errors.push(`Node ${node.id}: missing kernel-neutral CAD operation contract`);
    else errors.push(...validateCadOperation(operation).map((error) => `Node ${node.id}: ${error}`));
  }

  for (const edge of graph.edges) {
    if (!ids.has(edge.source)) errors.push(`Edge ${edge.id}: missing source node`);
    if (!ids.has(edge.target)) errors.push(`Edge ${edge.id}: missing target node`);
    if (!edge.sourceHandle || !edge.targetHandle) {
      errors.push(`Edge ${edge.id}: missing socket handle`);
      continue;
    }
    const source = graph.nodes.find((node) => node.id === edge.source);
    const target = graph.nodes.find((node) => node.id === edge.target);
    const output = source && nodeOutput(source, edge.sourceHandle);
    const input = target && nodeInput(target, edge.targetHandle);
    if (!output || !input) errors.push(`Edge ${edge.id}: unknown socket`);
    else if (!edge.pythonImported && source && !compatible(effectiveOutput(graph, source, output), input)) {
      errors.push(`Edge ${edge.id}: incompatible ${output.valueType} → ${input.valueType}`);
    }
  }

  const occupiedInputs = new Set<string>();
  for (const edge of graph.edges) {
    const key = `${edge.target}:${edge.targetHandle}`;
    if (occupiedInputs.has(key)) errors.push(`Input ${key} has multiple connections`);
    occupiedInputs.add(key);
  }

  const incoming = new Map<string, number>();
  const outgoing = new Map<string, string[]>();
  for (const node of graph.nodes) {
    incoming.set(node.id, 0);
    outgoing.set(node.id, []);
  }
  for (const edge of graph.edges) {
    if (!ids.has(edge.source) || !ids.has(edge.target)) continue;
    incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1);
    outgoing.get(edge.source)?.push(edge.target);
  }

  const queue = [...incoming].filter(([, count]) => count === 0).map(([id]) => id);
  let visited = 0;
  while (queue.length) {
    const id = queue.shift()!;
    visited++;
    for (const target of outgoing.get(id) ?? []) {
      const count = (incoming.get(target) ?? 0) - 1;
      incoming.set(target, count);
      if (count === 0) queue.push(target);
    }
  }
  if (visited !== graph.nodes.length) errors.push("Graph contains a cycle");
  return errors;
}
