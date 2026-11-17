import type { SocketDefinition } from "../types";

/** Geometry identity is emitted by runtime discovery, not a UI name allowlist. */
export function isGeometryType(socket: Pick<SocketDefinition, "is_geometry">): boolean {
  return socket.is_geometry === true;
}

export function isGeometrySocket(socket: SocketDefinition): boolean {
  return isGeometryType(socket);
}

export function socketTypeLabel(socket: SocketDefinition): string {
  const structure = socket.data_structure;
  const semanticLabel = socket.item_type ?? socket.type;
  if (!structure || structure === "item") return semanticLabel;
  if (structure === "dict") return `Dict[${socket.key_type || "Any"}, ${socket.value_type || "Any"}]`;
  const label = structure === "tree" ? "Tree" : structure[0].toUpperCase() + structure.slice(1);
  return `${label}[${semanticLabel}]`;
}

export function socketColor(socket: SocketDefinition): string {
  if (socket.is_geometry) return "var(--socket-geometry)";
  const type = socket.item_type || socket.value_type || socket.type;
  if (["Number", "Boolean"].includes(type)) return "var(--socket-numeric)";
  if (["String", "Code", "Enum"].includes(type)) return "var(--socket-text)";
  return "var(--socket-generic)";
}
