import type { GraphDocument, NodeDefinition } from "../types";

export function createGraphNode(
  definition: NodeDefinition,
  id: string,
  position: { x: number; y: number },
) {
  const params: Record<string, unknown> = {};
  for (const input of definition.inputs) {
    if (input.default !== undefined) params[input.name] = input.default;
  }
  return {
    id,
    type: "api" as const,
    position,
    data: { definition, params },
  };
}

function compatible(source: string, target: string): boolean {
  if (target === "Any" || source === "Any" || source === target) return true;
  if (target === "Shape" && ["Solid", "Compound", "Face", "Edge", "Wire"].includes(source)) return true;
  return false;
}

export function canConnect(
  graph: GraphDocument,
  connection: { source: string | null; target: string | null; sourceHandle?: string | null; targetHandle?: string | null },
): boolean {
  if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle) return false;
  const source = graph.nodes.find((node) => node.id === connection.source);
  const target = graph.nodes.find((node) => node.id === connection.target);
  if (!source || !target) return false;

  // An input socket accepts only one upstream value.
  if (graph.edges.some((edge) => edge.target === target.id && edge.targetHandle === connection.targetHandle)) {
    return false;
  }

  const output = source.data.definition.outputs.find((socket) => socket.name === connection.sourceHandle);
  const input = target.data.definition.inputs.find((socket) => socket.name === connection.targetHandle);
  return Boolean(output && input && compatible(output.type, input.type));
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
    else if (!compatible(output.type, input.type)) errors.push(`Edge ${edge.id}: incompatible ${output.type} → ${input.type}`);
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