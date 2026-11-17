/** Runtime-discovered semantic type identity. Never maintain a closed CAD type union here. */
export type SocketType = string;

/** Recursive value layout, orthogonal to semantic type identity. */
export type DataShape =
  | { kind: "item"; type: SocketType }
  | { kind: "list" | "set" | "tree"; item: DataShape }
  | { kind: "tuple"; items: DataShape[] }
  | { kind: "map"; key: DataShape; value: DataShape }
  | { kind: "union"; options: DataShape[] };

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
  /** Recursive data layout, independent of semantic type and evaluation policy. */
  data_shape?: DataShape;
  /** Legacy flat shape during migration; new contracts should use data_shape. */
  data_structure?: "item" | "list" | "tree" | "tuple" | "set" | "dict";
  /** How an input consumes incoming data: one item at a time or as a whole structure. */
  data_access?: "item" | "structure" | "tree";
  variants?: SocketType[];
  /** Provider-discovered geometry capability for the semantic value type. */
  is_geometry?: boolean;
  item_is_geometry?: boolean;
  runtime_inspectable?: boolean;
  semantic_type_id?: string;
  item_semantic_type_id?: string;
  runtime_inspectable?: boolean;
}

export interface NodeDefinition {
  /** Kernel-neutral operation contract attached by the active provider adapter. */
  cadOperation?: import("./runtime/cadKernel").CadOperationDefinition;
  id: string;
  kind: "geometry" | "operation" | "value" | "enum";
  name: string;
  category: string;
  /** Explicit API/runtime provenance; independent of node identity and presentation. */
  source: string;
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
    /** Imported Python lexical execution scope path, outermost first. */
    execution_scope_path?: string[];
    /** Virtual node exposing a context manager's __enter__ value. */
    scope_binding?: string;
    /** Virtual node exposing the context manager's completed result. */
    scope_result?: string;
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
  /** Python importer edges are validated by the Python runtime's API contract. */
  pythonImported?: boolean;
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