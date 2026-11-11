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

export function validateGraph(graph: GraphDocument): string[] {
  const errors: string[] = [];
  const ids = new Set(graph.nodes.map((node) => node.id));

  for (const edge of graph.edges) {
    if (!ids.has(edge.source)) errors.push(`Edge ${edge.id}: missing source node`);
    if (!ids.has(edge.target)) errors.push(`Edge ${edge.id}: missing target node`);
    if (!edge.sourceHandle || !edge.targetHandle) {
      errors.push(`Edge ${edge.id}: missing socket handle`);
    }
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