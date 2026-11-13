export type SocketType =
  | "Number" | "Boolean" | "String" | "Vector3" | "Plane" | "Location"
  | "Shape" | "Solid" | "Compound" | "Face" | "Edge" | "Wire" | "Shell" | "Part" | "Sketch"
  | "Vector2" | "Rotation" | "Axis" | "Color" | "Matrix" | "List" | "Tuple" | "Set" | "Dict" | "Enum" | "Code" | "Any";

export interface SocketDefinition {
  name: string;
  label?: string;
  type: SocketType;
  annotation: string;
  required: boolean;
  default?: unknown;
  default_repr?: string;
  options?: string[];
  /** Semantic type of an individual value; independent of its container shape. */
  item_type?: SocketType;
  key_type?: SocketType;
  value_type?: SocketType;
  /** Shape of the data carried by this socket, not its semantic value type. */
  data_structure?: "item" | "list" | "tree" | "tuple" | "set" | "dict";
  /** How an input consumes incoming data: one item at a time or as a whole structure. */
  data_access?: "item" | "structure" | "tree";
  variants?: SocketType[];
}

export interface NodeDefinition {
  id: string;
  kind: "geometry" | "operation" | "value" | "enum";
  name: string;
  category: string;
  /** Explicit API/runtime provenance; independent of node identity and presentation. */
  source: "build123d" | "python" | "flow";
  /** User-facing functional palette category. */
  palette_category: string;
  /** Optional subgroup within a functional palette category. */
  palette_section: string;
  description: string;
  inputs: SocketDefinition[];
  outputs: SocketDefinition[];
  /** How multiple item-access inputs are aligned during evaluation. */
  data_matching?: "longest" | "shortest" | "cross";
  /** Native Python special-method identity for discovered operator nodes. */
  operator?: { method: string; symbol: string };
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
    icon?: string;
    onVisibility?: (nodeId: string, visible: boolean) => void;
    onCollapse?: (nodeId: string, collapsed: boolean) => void;
    onInspect?: (nodeId: string, socketName: string, direction: 'input' | 'output') => void;
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

export interface PythonExecutionScope {
  id: string;
  module: string;
  name: string;
  args: unknown[];
  kwargs: Record<string, unknown>;
  result_attribute?: string;
  has_binding?: boolean;
}

export interface GraphDocument {
  nodes: GraphNode[];
  edges: GraphEdge[];
  execution_scopes?: PythonExecutionScope[];
}

export interface MeshPreview {
  vertices: number[];
  triangles: number[];
  color?: string;
  edges?: number[];
  points?: number[];
  faceColor?: string;
  edgeColor?: string;
  vertexColor?: string;
  opacity?: number;
  showEdges?: boolean;
  showVertices?: boolean;
}

export interface EvaluationResult {
  previews: Record<string, MeshPreview>;
  errors: Record<string, string>;
  order: string[];
  socket_values?: Record<string, Record<string, unknown>>;
  cache?: { hits: number; misses: number };
}