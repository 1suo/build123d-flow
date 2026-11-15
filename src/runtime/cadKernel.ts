import type { NodeDefinition, SocketDefinition, GraphDocument, GraphNode, EvaluationResult, MeshPreview } from "../types";
import type { PythonRuntime } from "./pythonRuntime";

/**
 * Kernel-neutral procedural CAD contract.
 *
 * The graph editor speaks this contract; a kernel provider is responsible for
 * translating operation identifiers and opaque geometry values to its native
 * API. No OpenCascade/build123d class or method belongs in this module.
 */

export type CadValueType =
  | "number" | "integer" | "boolean" | "string"
  | "vector2" | "vector3" | "transform" | "plane" | "axis"
  | "geometry" | "solid" | "shell" | "face" | "wire" | "edge" | "vertex"
  | "curve" | "surface" | "mesh" | "material" | "enum" | "any";

export type CadCollectionShape = "item" | "list" | "tree" | "tuple" | "set" | "map";
export type CadInputAccess = "item" | "collection" | "tree";
export type CadExecutionMode = "pure" | "stateful" | "effect" | "unspecified";

/** Opaque value identity: the editor never inspects or mutates kernel objects. */
export interface CadGeometryHandle {
  readonly provider: string;
  readonly handle: string;
  readonly kind: CadValueType;
}

export interface CadPortDefinition {
  readonly id: string;
  readonly label: string;
  readonly valueType: CadValueType;
  readonly collection: CadCollectionShape;
  readonly itemType?: CadValueType;
  readonly access?: CadInputAccess;
  readonly required?: boolean;
  readonly defaultValue?: unknown;
  readonly description?: string;
  readonly enumValues?: readonly string[];
  readonly variants?: readonly CadValueType[];
}

export interface CadOperationDefinition {
  /** Stable provider-qualified identifier, never a display-name-derived id. */
  readonly id: string;
  readonly label: string;
  readonly category: string;
  readonly description?: string;
  readonly inputs: readonly CadPortDefinition[];
  readonly outputs: readonly CadPortDefinition[];
  readonly execution: CadExecutionMode;
  readonly mapping: {
    /** One operation node maps over a stream; it is not duplicated per item. */
    readonly mode: "scalar" | "map";
    /** Preserve each nested collection level in the corresponding output tree. */
    readonly preserveNesting: boolean;
    /** Multiple mapped inputs require an explicit alignment policy. */
    readonly alignment: "single" | "zip-shortest" | "zip-longest" | "cartesian";
  };
  /** Provider-native operation key; opaque to the graph editor. */
  readonly nativeOperation: string;
}

export interface CadKernelCapabilities {
  readonly geometryKinds: readonly CadValueType[];
  readonly importFormats: readonly string[];
  readonly exportFormats: readonly string[];
  readonly supportsExactBrep: boolean;
}

export interface CadEvaluationRequest {
  readonly graph: CadProceduralGraph;
}

export interface CadProceduralNode {
  readonly id: string;
  readonly operationId: string;
  readonly parameters: Readonly<Record<string, unknown>>;
  readonly position: Readonly<{ x: number; y: number }>;
  /** Opaque provider-owned state required to preserve native program semantics. */
  readonly providerData?: unknown;
}

export interface CadProceduralConnection {
  readonly sourceNode: string;
  readonly sourcePort: string;
  readonly targetNode: string;
  readonly targetPort: string;
  readonly pythonImported?: boolean;
}

export interface CadProceduralGraph {
  readonly nodes: readonly CadProceduralNode[];
  readonly connections: readonly CadProceduralConnection[];
  /** Opaque provider-owned graph state (e.g. lexical builder scopes). */
  readonly providerData?: unknown;
}

export interface CadEvaluationResult {
  readonly values: Readonly<Record<string, Readonly<Record<string, unknown>>>>;
  readonly geometry: Readonly<Record<string, CadGeometryHandle>>;
  readonly previews: Readonly<Record<string, MeshPreview>>;
  readonly cache?: EvaluationResult["cache"];
  readonly errors: Readonly<Record<string, string>>;
}

export interface CadKernelProvider {
  readonly id: string;
  readonly label: string;
  readonly version: string;
  readonly capabilities: CadKernelCapabilities;
  discoverOperations(): Promise<readonly CadOperationDefinition[]>;
  evaluate(request: CadEvaluationRequest): Promise<CadEvaluationResult>;
  importProgram?(source: string, format: string): Promise<CadProceduralGraph>;
  exportProgram?(graph: CadProceduralGraph, format: string): Promise<string | Uint8Array>;
}

/** Rejects ambiguous map semantics before a provider is asked to evaluate. */
export function validateCadOperation(operation: CadOperationDefinition): string[] {
  const errors: string[] = [];
  const inputIds = new Set<string>();
  const outputIds = new Set<string>();
  for (const input of operation.inputs) {
    if (!input.id || inputIds.has(input.id)) errors.push(`Operation ${operation.id}: duplicate/empty input id ${input.id}`);
    inputIds.add(input.id);
  }
  for (const output of operation.outputs) {
    if (!output.id || outputIds.has(output.id)) errors.push(`Operation ${operation.id}: duplicate/empty output id ${output.id}`);
    outputIds.add(output.id);
  }
  if (!operation.id || !operation.nativeOperation) errors.push("Operation and native operation identifiers are required");
  if (operation.mapping.mode === "scalar" && operation.mapping.alignment !== "single") {
    errors.push(`Operation ${operation.id}: scalar operations cannot declare stream alignment`);
  }
  if (operation.mapping.mode === "map" && !operation.mapping.preserveNesting) {
    errors.push(`Operation ${operation.id}: mapped CAD operations must preserve nested collection shape`);
  }
  if (operation.mapping.alignment === "cartesian" && operation.mapping.mode !== "map") {
    errors.push(`Operation ${operation.id}: Cartesian alignment requires map mode`);
  }
  return errors;
}


const RUNTIME_TYPE_MAP: Readonly<Record<string, CadValueType>> = {
  Number: "number", Boolean: "boolean", String: "string", Vector2: "vector2",
  Vector3: "vector3", Rotation: "transform", Location: "transform", Plane: "plane",
  Axis: "axis", Shape: "geometry", Solid: "solid", Compound: "geometry",
  Face: "face", Edge: "edge", Wire: "wire", Shell: "shell", Part: "solid",
  Sketch: "geometry", Curve: "curve", Surface: "surface", Mesh: "mesh",
  Color: "material", Enum: "enum", Any: "any", Code: "string",
};

function portCollection(socket: SocketDefinition): CadCollectionShape {
  if (socket.data_structure) return socket.data_structure === "dict" ? "map" : socket.data_structure;
  switch (socket.type) {
    case "List": return "list";
    case "Tuple": return "tuple";
    case "Set": return "set";
    case "Dict": return "map";
    default: return "item";
  }
}

function cadPort(socket: SocketDefinition): CadPortDefinition {
  const collection = portCollection(socket);
  const sourceType = socket.item_type ?? socket.value_type ?? socket.type;
  return {
    id: socket.name,
    label: socket.label ?? socket.name,
    valueType: RUNTIME_TYPE_MAP[sourceType] ?? "any",
    collection,
    ...(collection !== "item" ? { itemType: RUNTIME_TYPE_MAP[sourceType] ?? "any" } : {}),
    access: socket.data_access === "structure"
      ? "collection"
      : socket.data_access ?? (collection === "item" ? "item" : "collection"),
    ...(socket.required !== undefined ? { required: socket.required } : {}),
    ...(socket.default !== undefined ? { defaultValue: socket.default } : {}),
    ...(socket.annotation ? { description: socket.annotation } : {}),
    ...(socket.options ? { enumValues: socket.options } : {}),
    ...(socket.variants?.length ? { variants: socket.variants.map((type) => RUNTIME_TYPE_MAP[type] ?? "any") } : {}),
  };
}

/**
 * Translate a discovered runtime operation into the kernel-neutral graph
 * contract. This is metadata adaptation only: the native operation remains
 * owned and invoked by its provider.
 */
export function adaptRuntimeOperation(definition: NodeDefinition): CadOperationDefinition {
  // Runtime item-access is the default for scalar ports. Collection ports
  // remain structural unless the runtime explicitly declares item access.
  // Keep this default identical to flow_runtime.py so provider metadata and
  // actual evaluation cannot disagree about whether a node maps over a stream.
  const mapped = definition.inputs.some((input) =>
    input.data_access === "item" ||
    (input.data_access === undefined && portCollection(input) === "item"),
  );
  const alignment = !mapped ? "single"
    : definition.data_matching === "cross" ? "cartesian"
      : definition.data_matching === "shortest" ? "zip-shortest"
        : "zip-longest";
  return {
    id: definition.id,
    label: definition.name,
    category: definition.palette_category,
    description: definition.description,
    inputs: definition.inputs.map(cadPort),
    outputs: definition.outputs.map(cadPort),
    execution: "unspecified",
    mapping: {
      mode: mapped ? "map" : "scalar",
      preserveNesting: true,
      alignment: mapped ? alignment : "single",
    },
    nativeOperation: definition.id,
  };
}

const CAD_GEOMETRY_TYPES = new Set<CadValueType>([
  "geometry", "solid", "shell", "face", "wire", "edge", "vertex", "curve", "surface", "mesh",
]);

export function isCadGeometryPort(port: CadPortDefinition): boolean {
  return CAD_GEOMETRY_TYPES.has(port.valueType) ||
    (port.itemType !== undefined && CAD_GEOMETRY_TYPES.has(port.itemType));
}

export function cadPortTypeLabel(port: CadPortDefinition): string {
  const value = port.itemType ?? port.valueType;
  if (port.collection === "item") return value;
  const label = port.collection === "map" ? "Map" : port.collection[0].toUpperCase() + port.collection.slice(1);
  return `${label}[${value}]`;
}

export function cadPortColor(port: CadPortDefinition): string {
  const type = port.itemType ?? port.valueType;
  if (CAD_GEOMETRY_TYPES.has(type)) return "var(--socket-geometry)";
  if (["number", "integer", "vector2", "vector3", "transform", "plane", "axis"].includes(type)) return "var(--socket-numeric)";
  if (["string", "enum"].includes(type)) return "var(--socket-text)";
  return "var(--socket-generic)";
}


export function toCadProceduralGraph(graph: GraphDocument): CadProceduralGraph {
  return {
    nodes: graph.nodes.map((node) => ({
      id: node.id,
      operationId: node.data.definition.cadOperation?.id ?? node.data.definition.id,
      parameters: node.data.params,
      position: node.position,
      providerData: {
        definition: node.data.definition,
        data: {
          visible: node.data.visible,
          execution_scope_path: node.data.execution_scope_path,
          scope_binding: node.data.scope_binding,
          scope_result: node.data.scope_result,
          python_loop_sources: (node.data as any).python_loop_sources,
          data_matching: (node.data as any).data_matching,
        },
      },
    })),
    connections: graph.edges.map((edge) => ({
      sourceNode: edge.source,
      sourcePort: edge.sourceHandle ?? "",
      targetNode: edge.target,
      targetPort: edge.targetHandle ?? "",
      pythonImported: edge.pythonImported,
    })),
    providerData: { execution_scopes: graph.execution_scopes },
  };
}

/**
 * First concrete provider adapter. The editor and its procedural node
 * definitions stay kernel-neutral; this adapter owns all build123d/Pyodide
 * graph translation and execution.
 */
export class Build123dKernelProvider implements CadKernelProvider {
  readonly id = "build123d";
  readonly label = "build123d";
  readonly version = "runtime-discovered";
  readonly capabilities: CadKernelCapabilities = {
    geometryKinds: ["geometry", "solid", "shell", "face", "wire", "edge", "vertex", "curve", "surface", "mesh"],
    importFormats: ["python"],
    exportFormats: ["python", "step"],
    supportsExactBrep: true,
  };
  private definitions = new Map<string, NodeDefinition>();

  constructor(private readonly runtime: PythonRuntime) {}

  setDefinitions(definitions: readonly NodeDefinition[]): void {
    this.definitions = new Map(definitions.map((definition) => [
      definition.id,
      definition.cadOperation ? definition : { ...definition, cadOperation: adaptRuntimeOperation(definition) },
    ]));
  }

  async discoverOperations(): Promise<readonly CadOperationDefinition[]> {
    const definitions = this.definitions.size
      ? [...this.definitions.values()]
      : await this.runtime.getCatalog();
    this.setDefinitions(definitions);
    return [...this.definitions.values()].map((definition) => definition.cadOperation!);
  }

  async evaluate(request: CadEvaluationRequest): Promise<CadEvaluationResult> {
    const nativeGraph: GraphDocument = {
      nodes: request.graph.nodes.map((node) => {
        const providerData = node.providerData as { definition?: NodeDefinition; data?: Record<string, unknown> } | undefined;
        const definition = providerData?.definition ?? this.definitions.get(node.operationId);
        if (!definition) throw new Error(`Unknown build123d operation: ${node.operationId}`);
        return {
          id: node.id,
          type: "api",
          position: { x: node.position.x, y: node.position.y },
          data: {
            ...(providerData?.data ?? {}),
            definition,
            params: { ...node.parameters },
          },
        } as GraphNode;
      }),
      edges: request.graph.connections.map((connection, index) => ({
        id: `cad-edge-${index}`,
        source: connection.sourceNode,
        sourceHandle: connection.sourcePort,
        target: connection.targetNode,
        targetHandle: connection.targetPort,
        pythonImported: connection.pythonImported,
      })),
      execution_scopes: (request.graph.providerData as { execution_scopes?: GraphDocument["execution_scopes"] } | undefined)?.execution_scopes,
    };
    const result = await this.runtime.evaluate(nativeGraph);
    const geometry: Record<string, CadGeometryHandle> = {};
    for (const nodeId of Object.keys(result.previews)) {
      geometry[nodeId] = { provider: this.id, handle: nodeId, kind: "geometry" };
    }
    return {
      values: result.socket_values ?? {},
      geometry,
      previews: result.previews,
      cache: result.cache,
      errors: result.errors,
    };
  }

  async importProgram(source: string, format: string): Promise<CadProceduralGraph> {
    if (format !== "python") throw new Error(`Unsupported build123d import format: ${format}`);
    const graph = await this.runtime.importPython(source);
    this.setDefinitions(graph.nodes.map((node) => node.data.definition));
    return toCadProceduralGraph(graph);
  }

  async exportProgram(graph: CadProceduralGraph, format: string): Promise<string | Uint8Array> {
    if (format === "step") {
      const nativeGraph = this.toGraphDocument(graph);
      await this.runtime.evaluate(nativeGraph);
      const bytes = await this.runtime.exportStep();
      if (!bytes) throw new Error("The graph produced no STEP-exportable geometry");
      return bytes;
    }
    if (format !== "python") throw new Error(`Unsupported build123d export format: ${format}`);
    return this.runtime.exportPython(this.toGraphDocument(graph));
  }

  toGraphDocument(graph: CadProceduralGraph): GraphDocument {
    return {
      nodes: graph.nodes.map((node) => {
        const providerData = node.providerData as { definition?: NodeDefinition; data?: Record<string, unknown> } | undefined;
        const definition = providerData?.definition ?? this.definitions.get(node.operationId);
        if (!definition) throw new Error(`Unknown build123d operation: ${node.operationId}`);
        return {
          id: node.id, type: "api", position: { x: node.position.x, y: node.position.y },
          data: { ...(providerData?.data ?? {}), definition, params: { ...node.parameters } },
        } as GraphNode;
      }),
      edges: graph.connections.map((connection, index) => ({
        id: `cad-edge-${index}`, source: connection.sourceNode, sourceHandle: connection.sourcePort,
        target: connection.targetNode, targetHandle: connection.targetPort, pythonImported: connection.pythonImported,
      })),
      execution_scopes: (graph.providerData as { execution_scopes?: GraphDocument["execution_scopes"] } | undefined)?.execution_scopes,
    };
  }
}
