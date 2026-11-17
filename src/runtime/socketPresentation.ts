import type { SocketDataShape, SocketDefinition } from "../types";

function shapeContainsGeometry(shape: SocketDataShape | undefined): boolean {
  if (!shape) return false;
  if (shape.kind === "item") return shape.is_geometry === true;
  if (shape.kind === "list" || shape.kind === "tree" || shape.kind === "set") {
    return shapeContainsGeometry(shape.item);
  }
  if (shape.kind === "map") return shapeContainsGeometry(shape.value);
  if (shape.kind === "tuple") return shape.items.some(shapeContainsGeometry);
  return shape.options.some(shapeContainsGeometry);
}

/** Geometry identity is emitted by runtime discovery, not a UI name allowlist. */
export function isGeometryType(shape: SocketDataShape | undefined): boolean {
  return shapeContainsGeometry(shape);
}

export function isGeometrySocket(socket: SocketDefinition): boolean {
  return socket.is_geometry === true || shapeContainsGeometry(socket.data_shape);
}

function shapeLabel(shape: SocketDataShape): string {
  if (shape.kind === "item") return shape.type ?? shape.type_id ?? "Any";
  if (shape.kind === "list" || shape.kind === "tree" || shape.kind === "set") {
    const label = shape.kind === "tree" ? "Tree" : shape.kind[0].toUpperCase() + shape.kind.slice(1);
    return `${label}[${shapeLabel(shape.item)}]`;
  }
  if (shape.kind === "map") return `Map[${shapeLabel(shape.key)}, ${shapeLabel(shape.value)}]`;
  if (shape.kind === "tuple") return `Tuple[${shape.items.map(shapeLabel).join(", ")}]`;
  return `OneOf[${shape.options.map(shapeLabel).join(" | ")}]`;
}

export function socketTypeLabel(socket: SocketDefinition): string {
  if (socket.data_representation === "value" && socket.type_id) {
    return socket.type && socket.type !== "Any"
      ? socket.type
      : socket.type_id.split(".").pop() ?? "Any";
  }
  if (socket.data_shape) return shapeLabel(socket.data_shape);
  const structure = socket.data_structure;
  const semanticLabel = socket.item_type ?? socket.type;
  if (!structure || structure === "item") return semanticLabel;
  if (structure === "dict") return `Dict[${socket.key_type || "Any"}, ${socket.value_type || "Any"}]`;
  const label = structure === "tree" ? "Tree" : structure[0].toUpperCase() + structure.slice(1);
  return `${label}[${semanticLabel}]`;
}

export function socketColor(socket: SocketDefinition): string {
  if (isGeometrySocket(socket)) return "var(--socket-geometry)";
  const type = socket.item_type || socket.value_type || socket.type;
  if (["Number", "Boolean"].includes(type)) return "var(--socket-numeric)";
  if (["String", "Code", "Enum"].includes(type)) return "var(--socket-text)";
  return "var(--socket-generic)";
}
