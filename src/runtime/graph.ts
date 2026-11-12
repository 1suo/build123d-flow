import type { GraphDocument, GraphNode, NodeDefinition, SocketDefinition } from "../types";

export function createGraphNode(
  definition: NodeDefinition,
  id: string,
  position: { x: number; y: number },
): GraphNode {
  const params: Record<string, unknown> = {};
  for (const input of definition.inputs) {
    if (input.default !== undefined) params[input.name] = input.default;
  }
  return {
    id,
    type: "api" as const,
    position,
    data: {
      definition,
      params,
      visible: definition.outputs.some((output) => ["Shape", "Solid", "Compound", "Face", "Wire", "Edge"].includes(output.type)),
    },
  };
}

function compatibleTypes(source: string, target: string): boolean {
  if (target === "Any" || source === "Any" || source === target) return true;
  if (target === "Shape" && ["Solid", "Compound", "Face", "Edge", "Wire", "Shell", "Part", "Sketch"].includes(source)) return true;
  return false;
}

function structureOf(socket: SocketDefinition): NonNullable<SocketDefinition["data_structure"]> {
  if (socket.data_structure) return socket.data_structure;
  if (["List", "Tuple", "Set", "Dict"].includes(socket.type)) {
    return socket.type.toLowerCase() as "list" | "tuple" | "set" | "dict";
  }
  return "item";
}

function accessOf(socket: SocketDefinition): NonNullable<SocketDefinition["data_access"]> {
  return socket.data_access ?? (
    ["List", "Tuple", "Set", "Dict"].includes(socket.type) ? "structure" : "item"
  );
}

function effectiveOutput(
  graph: GraphDocument,
  node: GraphNode,
  output: SocketDefinition,
  visited = new Set<string>(),
): SocketDefinition {
  if (structureOf(output) !== "item" || visited.has(node.id)) return output;
  const nextVisited = new Set(visited).add(node.id);
  let inferred: "list" | "tree" | undefined;
  for (const edge of graph.edges.filter((item) => item.target === node.id)) {
    const input = node.data.definition.inputs.find((item) => item.name === edge.targetHandle);
    if (!input || accessOf(input) !== "item") continue;
    const upstream = graph.nodes.find((item) => item.id === edge.source);
    const upstreamOutput = upstream?.data.definition.outputs.find((item) => item.name === edge.sourceHandle);
    if (!upstream || !upstreamOutput) continue;
    const resolved = effectiveOutput(graph, upstream, upstreamOutput, nextVisited);
    const structure = structureOf(resolved);
    if (structure === "tree") inferred = "tree";
    else if (["list", "tuple"].includes(structure) && inferred !== "tree") inferred = "list";
  }
  return inferred ? { ...output, data_structure: inferred, item_type: output.type } : output;
}

function compatible(source: SocketDefinition, target: SocketDefinition): boolean {
  const sourceStructure = structureOf(source);
  const targetAccess = accessOf(target);
  if (sourceStructure !== "item" && targetAccess === "item") {
    if (sourceStructure === "set" || sourceStructure === "dict") return false;
    const sourceItemType = source.item_type ?? (["List", "Tuple", "Set", "Dict"].includes(source.type) ? "Any" : source.type);
    const targetTypes = target.variants?.length ? target.variants : [target.type];
    return targetTypes.some((targetType) => compatibleTypes(sourceItemType, targetType));
  }
  if (sourceStructure !== "item" && targetAccess !== "item") {
    const targetStructure = structureOf(target);
    if (targetStructure !== "item" && targetStructure !== sourceStructure &&
        !(targetStructure === "tree" && sourceStructure === "list")) return false;
    const sourceItemType = source.item_type ?? (["List", "Tuple", "Set", "Dict"].includes(source.type) ? "Any" : source.type);
    const targetItemType = target.item_type ?? target.type;
    return compatibleTypes(sourceItemType, targetItemType);
  }
  if (sourceStructure === "item" && targetAccess !== "item") return false;
  const sourceTypes = source.variants?.length ? source.variants : [source.type];
  const targetTypes = target.variants?.length ? target.variants : [target.type];
  return sourceTypes.some((sourceType) => targetTypes.some((targetType) => compatibleTypes(sourceType, targetType)));
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

  const output = source.data.definition.outputs.find((socket) => socket.name === connection.sourceHandle);
  const input = target.data.definition.inputs.find((socket) => socket.name === connection.targetHandle);
  return Boolean(output && input && compatible(effectiveOutput(graph, source, output), input));
}

export function validateGraph(graph: GraphDocument): string[] {
  const errors: string[] = [];
  const ids = new Set(graph.nodes.map((node) => node.id));

  for (const edge of graph.edges) {
    if (!ids.has(edge.source)) errors.push(`Edge ${edge.id}: missing source node`);
    if (!ids.has(edge.target)) errors.push(`Edge ${edge.id}: missing target node`);
    if (!edge.sourceHandle || !edge.targetHandle) {
      errors.push(`Edge ${edge.id}: missing socket handle`);
      continue;
    }
    const source = graph.nodes.find((node) => node.id === edge.source);
    const target = graph.nodes.find((node) => node.id === edge.target);
    const output = source?.data.definition.outputs.find((socket) => socket.name === edge.sourceHandle);
    const input = target?.data.definition.inputs.find((socket) => socket.name === edge.targetHandle);
    if (!output || !input) errors.push(`Edge ${edge.id}: unknown socket`);
    else if (!compatible(effectiveOutput(graph, source!, output), input)) errors.push(`Edge ${edge.id}: incompatible ${output.type} → ${input.type}`);
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