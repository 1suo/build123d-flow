export type BuiltinSocketType =
  | "Number" | "Boolean" | "String" | "List" | "Tuple" | "Set" | "Dict" | "Enum" | "Code" | "Any";

/** Discovered nominal API types are open-world; no CAD class allowlist lives in the UI. */
export type SocketType = BuiltinSocketType | (string & {});

/** Recursive Python data layout, independent from the semantic value type. */
export type SocketDataShape =
  | { kind: "item"; type?: SocketType; type_id?: string; type_provider?: string; type_mro?: string[]; is_geometry?: boolean }
  | { kind: "list" | "tree" | "set"; item: SocketDataShape }
  | { kind: "tuple"; items: SocketDataShape[] }
  | { kind: "map"; key: SocketDataShape; value: SocketDataShape }
  | { kind: "union"; options: SocketDataShape[] };

export interface SocketDefinition {
  name: string;
  label?: string;
  type: SocketType;
  annotation: string;
  required: boolean;
  default?: unknown;
  default_repr?: string;
  options?: string[];
  /** Canonical identity and provider emitted by runtime discovery. */
  type_id?: string;
  type_provider?: string;
  /** Discovered Python MRO identities, used for nominal subtype compatibility. */
  type_mro?: string[];
  /** True only when discovery confirms this nominal type is CAD geometry. */
  is_geometry?: boolean;
  /** Semantic type of an individual value; independent of its container shape. */
  item_type?: SocketType;
  key_type?: SocketType;
  value_type?: SocketType;
  /** Recursive structure, including tuple positions and map key/value types. */
  data_shape?: SocketDataShape;
  /** Whether a collection-shaped alternative encodes one nominal value or a stream. */
  data_representation?: "value" | "collection" | "dynamic";
  /** Legacy outer shape retained for older catalog consumers. */
  data_structure?: "item" | "list" | "tree" | "tuple" | "set" | "dict";
  /** How an input consumes incoming data: one item at a time or as a whole structure. */
  data_access?: "item" | "structure" | "tree";
  variants?: SocketType[];
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