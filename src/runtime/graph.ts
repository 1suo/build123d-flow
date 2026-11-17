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
  source: string,
  target: string,
  sourceGeometry = false,
  sourceLineage: readonly string[] = [],
  sourceIdentity = source,
  targetIdentity = target,
): boolean {
  if (target === "any" || source === "any" || source === target ||
      sourceLineage.includes(targetIdentity)) return true;
  if (target === "geometry") return sourceGeometry;
  return false;
}

function portTypesCompatible(
  source: CadPortDefinition,
  target: CadPortDefinition,
  sourceItems = false,
  targetItems = false,
): boolean {
  const sourceTypes = sourceItems
    ? [itemType(source), ...(source.variants ?? [])]
    : [source.valueType, ...(source.variants ?? [])];
  const targetTypes = targetItems
    ? [itemType(target), ...(target.variants ?? [])]
    : [target.valueType, ...(target.variants ?? [])];
  const sourceGeometry = sourceItems ? source.itemGeometry === true : source.geometry === true;
  const sourceLineage = sourceItems ? source.itemTypeLineage ?? [] : source.typeLineage ?? [];
  const sourceIdentity = sourceItems ? source.itemTypeIdentity ?? itemType(source) : source.typeIdentity ?? source.valueType;
  const targetIdentity = targetItems ? target.itemTypeIdentity ?? itemType(target) : target.typeIdentity ?? target.valueType;
  return sourceTypes.some((sourceType) =>
    targetTypes.some((targetType) =>
      compatibleTypes(sourceType, targetType, sourceGeometry, sourceLineage, sourceIdentity, targetIdentity))
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
  return inferred ? { ...output, collection: inferred, itemType: output.valueType } : output;
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
    return portTypesCompatible(source, target, true, true);
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
