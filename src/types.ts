export type SocketType =
  | "Number" | "Boolean" | "String" | "Vector3" | "Plane" | "Location"
  | "Shape" | "Solid" | "Compound" | "Face" | "Edge" | "Wire" | "Shell" | "Part" | "Sketch"
  | "Vector2" | "Rotation" | "Axis" | "Color" | "List" | "Dict" | "Enum" | "Code" | "Any";

export interface SocketDefinition {
  name: string;
  label?: string;
  type: SocketType;
  annotation: string;
  required: boolean;
  default?: unknown;
  default_repr?: string;
  options?: string[];
  item_type?: SocketType;
  variants?: SocketType[];
}

export interface NodeDefinition {
  id: string;
  kind: "geometry" | "operation" | "value" | "enum";
  name: string;
  category: string;
  description: string;
  inputs: SocketDefinition[];
  outputs: SocketDefinition[];
}

export interface GraphNode {
  id: string;
  type: "api";
  position: { x: number; y: number };
  data: {
    definition: NodeDefinition;
    params: Record<string, unknown>;
    visible?: boolean;
    collapsed?: boolean;
    onVisibility?: (nodeId: string, visible: boolean) => void;
    onCollapse?: (nodeId: string, collapsed: boolean) => void;
    onParameter?: (nodeId: string, name: string, value: unknown) => void;
  };
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle: string | null;
  targetHandle: string | null;
}

export interface GraphDocument {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface MeshPreview {
  vertices: number[];
  triangles: number[];
  color?: string;
}

export interface EvaluationResult {
  previews: Record<string, MeshPreview>;
  errors: Record<string, string>;
  order: string[];
  socket_values?: Record<string, Record<string, unknown>>;
}