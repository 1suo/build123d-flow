import type { SocketDefinition } from "../types";

const GEOMETRY_TYPES = new Set([
  "Shape", "Solid", "Compound", "Face", "Edge", "Wire", "Shell", "Part", "Sketch",
]);

export function isGeometrySocket(socket: SocketDefinition): boolean {
  return GEOMETRY_TYPES.has(socket.type) || GEOMETRY_TYPES.has(socket.item_type ?? "");
}

export function socketTypeLabel(socket: SocketDefinition): string {
  const structure = socket.data_structure;
  if (!structure || structure === "item") return socket.type;
  if (structure === "dict") return `Dict[${socket.key_type || "Any"}, ${socket.value_type || "Any"}]`;
  const itemType = socket.item_type || socket.type;
  const label = structure === "tree" ? "Tree" : structure[0].toUpperCase() + structure.slice(1);
  return `${label}[${itemType}]`;
}

export function socketColor(socket: SocketDefinition): string {
  const type = socket.item_type || socket.value_type || socket.type;
  if (GEOMETRY_TYPES.has(type)) return "var(--socket-geometry)";
  if (["Number", "Boolean", "Vector2", "Vector3", "Rotation", "Plane", "Location", "Axis", "Color"].includes(type)) return "var(--socket-numeric)";
  if (["String", "Code", "Enum"].includes(type)) return "var(--socket-text)";
  return "var(--socket-generic)";
}
