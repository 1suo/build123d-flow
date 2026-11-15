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
export type CadExecutionMode = "pure" | "stateful" | "effect";

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
}

export interface CadProceduralConnection {
  readonly sourceNode: string;
  readonly sourcePort: string;
  readonly targetNode: string;
  readonly targetPort: string;
}

export interface CadProceduralGraph {
  readonly nodes: readonly CadProceduralNode[];
  readonly connections: readonly CadProceduralConnection[];
}

export interface CadEvaluationResult {
  readonly values: Readonly<Record<string, Readonly<Record<string, unknown>>>>;
  readonly geometry: Readonly<Record<string, CadGeometryHandle>>;
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
