<script setup lang="ts">
import { computed, markRaw, nextTick, onBeforeUnmount, onMounted, ref, shallowRef } from "vue";
import { VueFlow, useVueFlow, type Connection, type Edge, type Node } from "@vue-flow/core";
import { Background } from "@vue-flow/background";
import ApiNode from "./components/ApiNode.vue";
import GeometryViewport from "./components/GeometryViewport.vue";
import NodeInspector from "./components/NodeInspector.vue";
import Button from "primevue/button";
import ProgressSpinner from "primevue/progressspinner";
import InputText from "primevue/inputtext";
import Popover from "primevue/popover";
import Message from "primevue/message";
import Select from "primevue/select";
import Splitter from "primevue/splitter";
import SplitterPanel from "primevue/splitterpanel";
import { PythonRuntime } from "./runtime/pythonRuntime";
import { canConnect, createGraphNode, validateGraph } from "./runtime/graph";
import type { GraphDocument, NodeDefinition, EvaluationResult, MeshPreview } from "./types";

const runtime = new PythonRuntime();
const status = ref("Loading Python runtime…");
const runtimeError = ref("");
const catalog = ref<NodeDefinition[]>([]);
const paletteSearch = ref("");
const contextSearch = ref("");
const errors = ref<string[]>([]);
const downloadStatus = ref("");
const activityLogs = ref<Array<{ time: string; level: "info" | "success" | "warning" | "error"; message: string }>>([]);
let downloadStatusTimer: ReturnType<typeof setTimeout> | undefined;

function logActivity(message: string, level: "info" | "success" | "warning" | "error" = "info") {
  const previous = activityLogs.value[0];
  if (previous?.message === message && previous.level === level) return;
  activityLogs.value.unshift({ time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }), level, message });
  if (activityLogs.value.length > 40) activityLogs.value.length = 40;
}

function updateRuntimeStatus(message: string) {
  status.value = message;
  logActivity(message, /failed|error/i.test(message) ? "error" : "info");
}
const previews = shallowRef<Record<string, MeshPreview>>({});
const socketValues = shallowRef<Record<string, Record<string, unknown>>>({});
const inspectorOpen = ref(false);
const selectedNode = computed(() => nodes.value.find((node: any) => node.selected) as any ?? null);
const selectedNodeInputs = computed(() => {
  const node = selectedNode.value;
  if (!node) return [];
  return node.data.definition.inputs.map((socket: any) => {
    const edge = edges.value.find((item: any) => item.target === node.id && item.targetHandle === socket.name) as any;
    const value = edge ? socketValues.value[edge.source]?.[edge.sourceHandle || "result"] : node.data.params[socket.name] ?? socket.default;
    return { ...socket, connected: Boolean(edge), value };
  });
});
const selectedNodeOutputs = computed(() => {
  const node = selectedNode.value;
  if (!node) return [];
  return node.data.definition.outputs.map((socket: any) => ({
    ...socket, value: socketValues.value[node.id]?.[socket.name] ?? null,
  }));
});
const running = ref(false);
const nodes = ref<Node[]>([]);
const edges = ref<Edge[]>([]);
const nodeTypes = { api: markRaw(ApiNode) } as any;
const canvasSurface = ref<HTMLElement | null>(null);
const palettePopover = ref<any>();
const contextPopover = ref<any>();
const contextPosition = ref<{ x: number; y: number } | undefined>();
const { addNodes, addEdges, removeEdges, updateEdge, updateNodeInternals, screenToFlowCoordinate, fitView, zoomIn, zoomOut } = useVueFlow();

let nextId = 1;
let evaluationRevision = 0;
let evaluationTimer: ReturnType<typeof setTimeout> | undefined;

function paletteGroup(item: NodeDefinition): string {
  const category = item.category.toLowerCase();
  const name = item.name.toLowerCase();
  const id = item.id.toLowerCase();

  if (id.startsWith("core.spatial.")) return "Spatial & Distributions";
  if (id.startsWith("core.utility.")) return "Utilities";
  if (id.startsWith("core.list.") || id.startsWith("core.dict.") ||
      id.startsWith("core.tuple.") || id.startsWith("core.set.") ||
      id.startsWith("core.series.")) return "Collections & Series";
  if (id.startsWith("core.value.") || id.startsWith("core.constant.")) return "Values";
  if (id.startsWith("core.math.") || id.startsWith("core.compare.") ||
      id.startsWith("core.logic.")) return "Math & Logic";
  if (id.startsWith("core.vector.")) return "Vectors";
  if (id.startsWith("core.string.") || id.startsWith("core.convert.") ||
      id.startsWith("core.python.")) return "Data & Code";

  if (/^(box|cylinder|sphere|cone|torus|wedge|extrusion|revolution)$/.test(name)
      || /objects_part|primitives/.test(category)) return "3D Primitives";
  if (/objects_curve|objects_surface|curves|surfaces/.test(category)) return "Curves & Surfaces";
  if (/rectangle|circle|ellipse|polygon|slot|regularpolygon|trapezoid|polyline/.test(name)
      || /objects_sketch|profiles/.test(category)) return "2D Profiles";
  if (/extrude|revolve|loft|sweep|sweep|thicken/.test(name)
      || /operations_generic|operations_part/.test(category)) return "Modeling";
  if (/union|difference|intersection|fuse|cut|common|split|intersect/.test(name)) return "Boolean";
  if (/fillet|chamfer|shell|offset|draft|hollow/.test(name)) return "Finishing";
  if (/plane|axis|datum|frame|location|rotation/.test(name) || /plane|datum|reference/.test(category)) return "Reference Geometry";
  if (/translate|rotate|mirror|scale|move|transform|location/.test(name)
      || /location|plane/.test(category)) return "Transforms";
  if (/area|volume|length|center|bounding|measure|distance|mass|inertia/.test(name)
      || /measure|properties/.test(category)) return "Measurements";
  if (/^(shape|solid|compound|part|face|wire|edge|vertex|shell)$/.test(name)
      || /topology/.test(category)) return "Topology";
  if (/^build123d\./.test(id) && item.kind === "geometry") return "3D Primitives";
  if (item.kind === "geometry") return "Geometry";
  return "Other API";
}

const categories = computed(() => {
  const groups = new Set(catalog.value.map(paletteGroup));
  const order = ["3D Primitives", "2D Profiles", "Curves & Surfaces", "Spatial & Distributions", "Modeling", "Boolean", "Finishing", "Transforms", "Reference Geometry", "Measurements", "Topology", "Vectors", "Math & Logic", "Collections & Series", "Values", "Utilities", "Data & Code", "Geometry", "Other API"];
  return order.filter((group) => groups.has(group)).concat(
    [...groups].filter((group) => !order.includes(group)).sort()
  );
});
const categoryItems = computed(() => categories.value);
const activeCategory = ref("3D Primitives");
const exampleOptions = computed(() => {
  const names = new Set(catalog.value.map((item) => item.name.toLowerCase()));
  const ids = new Set(catalog.value.map((item) => item.id));
  const options: Array<{ label: string; value: string }> = [];
  if (ids.has("core.spatial.xy_grid") && ids.has("core.spatial.voronoi_2d")) {
    options.push({ label: "Voronoi panel field", value: "voronoi" });
  }
  if (names.has("rectangle") && names.has("extrude")) {
    options.push({ label: "Profile extrusion", value: "extrude" });
  }
  if (names.has("box") && names.has("cylinder") &&
      ids.has("build123d_flow.union") && ids.has("build123d_flow.difference")) {
    options.push({ label: "Boolean assembly", value: "boolean" });
  }
  return options;
});
const selectedExample = ref<string | null>(null);
const activePaletteItems = computed(() => catalog.value.filter((item) =>
  paletteGroup(item) === activeCategory.value &&
  (item.name + " " + item.id + " " + item.description).toLowerCase().includes(paletteSearch.value.toLowerCase())
).sort((a, b) => a.name.localeCompare(b.name)));
const contextResults = computed(() => catalog.value.filter((item) =>
  (item.name + " " + item.category + " " + item.id).toLowerCase().includes(contextSearch.value.toLowerCase())
).sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name)).slice(0, 60));

const graph = computed<GraphDocument>(() => ({
  nodes: nodes.value.map((node: any) => ({
    id: node.id,
    type: "api",
    position: node.position,
    data: node.data,
  })),
  edges: edges.value.map((edge: any) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.sourceHandle,
    targetHandle: edge.targetHandle,
  })),
}));

function deleteSelectedFromDock() {
  deleteSelected();
}

function nodeIcon(item: NodeDefinition): string {
  return item.kind;
}

onMounted(() => {
  window.addEventListener("keydown", onGlobalKeydown, true);
  void runtime.initialize(updateRuntimeStatus).then(() => {
    const discovered = runtime.getCatalog();
    const byName = new Map(discovered.map((item) => [item.name, item]));
    catalog.value = discovered;
    for (const node of nodes.value as any[]) {
      const resolved = byName.get(node.data.definition.name);
      if (resolved) node.data.definition = resolved;
      node.data.icon = nodeIcon(node.data.definition);
    }
    status.value = "Ready";
    logActivity("Python runtime ready", "success");
    if (nodes.value.length) scheduleEvaluation();
  }).catch((error) => {
    status.value = "Initialization failed";
    logActivity("Python runtime initialization failed", "error");
    runtimeError.value = error instanceof Error ? error.message : String(error);
  });
});


function clearCanvas() {
  nodes.value = [];
  edges.value = [];
  previews.value = {};
  socketValues.value = {};
  selectedExample.value = null;
}

async function loadExample() {
  const example = selectedExample.value;
  if (!example) return;

  const byId = (id: string): NodeDefinition => {
    const definition = catalog.value.find((item) => item.id === id);
    if (!definition) throw new Error("This example requires unavailable node: " + id);
    return definition;
  };
  const byName = (name: string): NodeDefinition => {
    const matches = catalog.value.filter((item) => item.name.toLowerCase() === name.toLowerCase());
    const definition = matches.find((item) => item.kind === "geometry") ?? matches[0];
    if (!definition) throw new Error("This example requires unavailable node: " + name);
    return definition;
  };
  const make = (definition: NodeDefinition, id: string, position: { x: number; y: number }, params: Record<string, unknown> = {}) => {
    const node = createGraphNode(definition, id, position);
    node.data.params = { ...node.data.params, ...params };
    node.data.visible = false;
    node.data.icon = nodeIcon(definition);
    node.data.onParameter = onParameter;
    node.data.onVisibility = onVisibility;
    node.data.onCollapse = onCollapse;
    node.data.onInspect = onInspect;
    return node;
  };

  const candidate: GraphDocument = { nodes: [], edges: [] };
  const add = (definition: NodeDefinition, id: string, position: { x: number; y: number }, params: Record<string, unknown> = {}) => {
    const node = make(definition, id, position, params);
    candidate.nodes.push(node);
    return node;
  };
  const connect = (source: ReturnType<typeof make>, target: ReturnType<typeof make>, requestedInput?: string) => {
    const occupied = new Set(candidate.edges.filter((edge) => edge.target === target.id).map((edge) => edge.targetHandle));
    const pairs = source.data.definition.outputs.flatMap((output) =>
      target.data.definition.inputs
        .filter((input) => (!requestedInput || input.name === requestedInput) && !occupied.has(input.name))
        .map((input) => ({ output, input })),
    );
    const pair = pairs.find(({ output, input }) => canConnect(candidate, {
      source: source.id, sourceHandle: output.name, target: target.id, targetHandle: input.name,
    }));
    if (!pair) {
      throw new Error(
        "No compatible socket connection: " + source.data.definition.name + " → " +
        target.data.definition.name + (requestedInput ? "." + requestedInput : ""),
      );
    }
    candidate.edges.push({
      id: "e" + (candidate.edges.length + 1),
      source: source.id, sourceHandle: pair.output.name,
      target: target.id, targetHandle: pair.input.name,
    });
  };

  try {
    if (example === "voronoi") {
      const grid = add(byId("core.spatial.xy_grid"), "n1", { x: 360, y: 180 }, {
        x_count: 7, y_count: 5, spacing_x: 18, spacing_y: 18, origin_x: 10, origin_y: 10,
      });
      const cells = add(byId("core.spatial.voronoi_2d"), "n2", { x: 850, y: 210 }, {
        width: 140, height: 100,
      });
      connect(grid, cells, "points");
      cells.data.visible = true;
    } else if (example === "extrude") {
      const profile = add(byName("Rectangle"), "n1", { x: 160, y: 220 }, {
        width: 48, height: 30,
      });
      const extrusion = add(byName("extrude"), "n2", { x: 680, y: 220 }, {
        amount: 18,
      });
      connect(profile, extrusion);
      extrusion.data.visible = true;
    } else if (example === "boolean") {
      const base = add(byName("Box"), "n1", { x: 80, y: 130 }, {
        length: 70, width: 55, height: 24,
      });
      const second = add(byName("Box"), "n2", { x: 80, y: 420 }, {
        length: 42, width: 36, height: 32,
      });
      const union = add(byId("build123d_flow.union"), "n3", { x: 490, y: 250 });
      const cutter = add(byName("Cylinder"), "n4", { x: 490, y: 550 }, {
        radius: 11, height: 48,
      });
      const difference = add(byId("build123d_flow.difference"), "n5", { x: 900, y: 320 });
      connect(base, union);
      connect(second, union);
      connect(union, difference);
      connect(cutter, difference);
      difference.data.visible = true;
    } else {
      throw new Error("Unknown example: " + example);
    }

    const validation = validateGraph(candidate);
    if (validation.length) throw new Error("Invalid example graph: " + validation.join("; "));
    if (!candidate.nodes.length || !candidate.edges.length) throw new Error("Example graph must contain connected nodes");

    // Evaluate before replacing the current canvas. A bad example never leaves
    // the user with a graph that only looks connected.
    const result = await runtime.evaluate(candidate);
    const evaluationErrors = Object.entries(result.errors).map(([id, message]) => id + ": " + message);
    if (evaluationErrors.length) throw new Error("Example evaluation failed: " + evaluationErrors.join("; "));
    nodes.value = candidate.nodes as unknown as Node[];
    edges.value = candidate.edges as unknown as Edge[];
    nextId = candidate.nodes.length + 1;
    errors.value = [];
    previews.value = result.previews;
    socketValues.value = result.socket_values ?? {};
    logActivity("Loaded and evaluated " + example + " example · " + candidate.nodes.length + " nodes · " + candidate.edges.length + " wires", "success");
    void nextTick(() => fitView({ padding: 0.2 }));
  } catch (error) {
    logActivity("Example not loaded: " + String(error), "error");
  }
}
function addDefinition(definition: NodeDefinition, position?: { x: number; y: number }) {
  const id = `n${nextId++}`;
  const rect = canvasSurface.value?.getBoundingClientRect();
  const center = rect
    ? screenToFlowCoordinate({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 })
    : { x: 160, y: 120 };
  const node = createGraphNode(definition, id, position ?? center);
  node.data.icon = nodeIcon(definition);
  node.data.onParameter = onParameter;
  node.data.onVisibility = onVisibility;
  node.data.onCollapse = onCollapse;
  node.data.onInspect = onInspect;
  addNodes([node as unknown as Node]);
  scheduleEvaluation();
}

function openPalette(category: string, event: MouseEvent) {
  paletteSearch.value = "";
  activeCategory.value = category;
  palettePopover.value?.show(event);
}

function openContextMenu(event: MouseEvent) {
  contextPosition.value = screenToFlowCoordinate({ x: event.clientX, y: event.clientY });
  contextSearch.value = "";
  contextPopover.value?.show(event);
}

function addFromContext(definition: NodeDefinition) {
  addDefinition(definition, contextPosition.value);
  contextPopover.value?.hide();
}

function onDrop(event: DragEvent) {
  event.preventDefault();
  const id = event.dataTransfer?.getData("application/build123d-node");
  const definition = catalog.value.find((item) => item.id === id);
  if (!definition) return;
  addDefinition(definition, screenToFlowCoordinate({ x: event.clientX, y: event.clientY }));
}

function deleteSelected() {
  const selectedIds = new Set<string>(
    nodes.value.filter((node: any) => node.selected).map((node: any) => String(node.id)),
  );
  const selectedEdgeIds = new Set<string>(
    edges.value.filter((edge: any) => edge.selected).map((edge: any) => String(edge.id)),
  );
  if (!selectedIds.size && !selectedEdgeIds.size) return;

  for (let i = nodes.value.length - 1; i >= 0; i--) {
    if (selectedIds.has(String((nodes.value[i] as any).id))) nodes.value.splice(i, 1);
  }
  for (let i = edges.value.length - 1; i >= 0; i--) {
    const edge = edges.value[i] as any;
    if (selectedEdgeIds.has(String(edge.id)) ||
        selectedIds.has(String(edge.source)) || selectedIds.has(String(edge.target))) {
      edges.value.splice(i, 1);
    }
  }
  previews.value = Object.fromEntries(
    Object.entries(previews.value).filter(([id]) => !selectedIds.has(id)),
  );
  scheduleEvaluation();
}

function onGlobalKeydown(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null;
  if (target?.closest("input, textarea, select, [contenteditable='true'], .p-inputtext, .p-textarea")) return;
  const selected = nodes.value.filter((node: any) => node.selected) as any[];
  const selectedEdges = edges.value.some((edge: any) => edge.selected);

  if (event.key.toLowerCase() === "h" && !event.ctrlKey && !event.altKey && !event.metaKey && selected.length) {
    const collapse = selected.some((node) => !node.data.collapsed);
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    for (const node of selected) node.data.collapsed = collapse;
    requestAnimationFrame(() => updateNodeInternals(selected.map((node) => node.id)));
    return;
  }
  if (event.key !== "Delete" && event.key !== "Backspace") return;
  if (!selected.length && !selectedEdges) return;
  event.preventDefault();
  event.stopPropagation();
  event.stopImmediatePropagation();
  deleteSelected();
}

function isValidConnection(connection: Connection) {
  return canConnect(graph.value, connection);
}

function onConnect(connection: Connection) {
  if (!isValidConnection(connection)) return;
  const replaced: string[] = [];
  for (const edge of edges.value as unknown as Array<{ id: string; target: string; targetHandle: string | null }>) {
    if (edge.target === connection.target && edge.targetHandle === connection.targetHandle) replaced.push(edge.id);
  }
  if (replaced.length) removeEdges(replaced);
  addEdges([{ ...connection, id: `e${Date.now()}`, animated: false, updatable: true } as Edge]);
  scheduleEvaluation();
}

function onEdgeUpdate({ edge, connection }: { edge: Edge; connection: Connection }) {
  if (!canConnect(graph.value, connection, edge.id)) return;
  const replaced: string[] = [];
  for (const candidate of edges.value as unknown as Array<{ id: string; target: string; targetHandle: string | null }>) {
    if (candidate.id !== edge.id &&
        candidate.target === connection.target && candidate.targetHandle === connection.targetHandle) {
      replaced.push(candidate.id);
    }
  }
  if (replaced.length) removeEdges(replaced);
  updateEdge(edge as any, connection);
  scheduleEvaluation();
}

function onParameter(nodeId: string, name: string, value: unknown) {
  const node = nodes.value.find((item: any) => item.id === nodeId) as any;
  if (!node) return;
  if (value === undefined) delete node.data.params[name];
  else node.data.params[name] = value;
  scheduleEvaluation();
}

function onVisibility(nodeId: string, visible: boolean) {
  const node = nodes.value.find((item: any) => item.id === nodeId) as any;
  if (!node) return;
  node.data.visible = visible;
  scheduleEvaluation();
}

function onInspect(nodeId: string, socketName: string, direction: "input" | "output") {
  for (const node of nodes.value as any[]) node.selected = node.id === nodeId;
}

function onCollapse(nodeId: string, collapsed: boolean) {
  const node = nodes.value.find((item: any) => item.id === nodeId) as any;
  if (node) {
    node.data.collapsed = collapsed;
    requestAnimationFrame(() => updateNodeInternals([nodeId]));
  }
}

function dragNode(event: DragEvent, definition: NodeDefinition) {
  event.dataTransfer?.setData("application/build123d-node", definition.id);
  if (event.dataTransfer) event.dataTransfer.effectAllowed = "copy";
}

function scheduleEvaluation() {
  if (!runtime.isReady) return;
  if (evaluationTimer) clearTimeout(evaluationTimer);
  evaluationTimer = setTimeout(() => void evaluate(), 180);
}

async function evaluate() {
  const revision = ++evaluationRevision;
  const validation = validateGraph(graph.value);
  if (validation.length) {
    errors.value = validation;
    return;
  }
  running.value = true;
  errors.value = [];
  logActivity("Evaluating graph");
  try {
    const result: EvaluationResult = await runtime.evaluate(graph.value);
    if (revision !== evaluationRevision) return;
    previews.value = result.previews;
    socketValues.value = result.socket_values ?? {};
    errors.value = Object.entries(result.errors).map(([id, error]) => `${id}: ${error}`);
    if (errors.value.length) errors.value.forEach((error) => logActivity(error, "error"));
    else logActivity(`Evaluation complete · ${Object.keys(result.previews).length} visible geometry`, "success");
    for (const node of nodes.value as any[]) node.data.error = result.errors[node.id] ?? "";
  } catch (error) {
    if (revision === evaluationRevision) {
      errors.value = [String(error)];
      logActivity(String(error), "error");
    }
  } finally {
    if (revision === evaluationRevision) running.value = false;
  }
}

onBeforeUnmount(() => window.removeEventListener("keydown", onGlobalKeydown, true));

async function downloadStep() {
  if (downloadStatusTimer) clearTimeout(downloadStatusTimer);
  downloadStatus.value = "Preparing STEP export…";
  logActivity("Preparing STEP export");
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  try {
    const bytes = runtime.exportStep();
    if (!bytes) {
      downloadStatus.value = "Nothing to export";
      logActivity("STEP export skipped · no evaluated solid", "warning");
    } else {
      const url = URL.createObjectURL(new Blob([bytes], { type: "application/step" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "model.step";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1500);
      downloadStatus.value = "STEP download started · model.step";
      logActivity(`STEP download started · ${(bytes.byteLength / 1024).toFixed(1)} KB`, "success");
    }
  } catch (error) {
    downloadStatus.value = "STEP export failed";
    logActivity(String(error), "error");
  }
  downloadStatusTimer = window.setTimeout(() => { downloadStatus.value = ""; }, 4500);
}
</script>

<template>
  <main class="app-shell">
    <Message v-if="runtimeError" severity="error" :closable="false" class="runtime-error">{{ runtimeError }}</Message>
    <Splitter class="workspace" :gutter-size="4">
      <SplitterPanel :size="68" :min-size="35" class="graph-panel">
        <div ref="canvasSurface" class="canvas-surface" @drop="onDrop" @dragover.prevent @contextmenu.prevent="openContextMenu">
          <div class="canvas-topbar">
            <Select v-model="selectedExample" :options="exampleOptions" option-label="label" option-value="value"
              placeholder="Examples" class="examples-select" :disabled="!runtime.isReady" @change="loadExample" />
            <span class="topbar-divider"></span>
            <nav class="category-strip" aria-label="Node categories">
              <Button v-for="category in categoryItems" :key="category" text severity="secondary"
                class="category-chip" :label="category" @click="openPalette(category, $event)" />
              <span v-if="!categoryItems.length" class="category-loading">Loading node catalog…</span>
            </nav>
            <Button icon="pi pi-sliders-h" text rounded severity="secondary"
              class="inspector-toggle" :class="{ 'is-active': inspectorOpen }"
              :aria-pressed="inspectorOpen" aria-label="Toggle inspector" title="Inspector follows selected node"
              @click="inspectorOpen = !inspectorOpen" />
          </div>
          <div v-if="runtime.isReady && !nodes.length" class="canvas-empty-state">
            <strong>Empty canvas</strong>
            <span>Choose an example, open a category, or right-click to add a node</span>
          </div>
          <VueFlow
            v-model:nodes="nodes"
            v-model:edges="edges"
            :node-types="nodeTypes"
            :is-valid-connection="isValidConnection"
            fit-view-on-init
            :delete-key-code="null"
            :edges-updatable="true"
            :default-viewport="{ zoom: 0.85, x: 60, y: 60 }"
            @connect="onConnect"
            @edge-update="onEdgeUpdate"
          >
            <Background pattern-color="#39352e" :gap="22" />
          </VueFlow>
          <div class="canvas-bottom-dock" role="toolbar" aria-label="Canvas tools">
            <Button icon="pi pi-expand" text rounded severity="secondary" aria-label="Fit graph" title="Fit graph" @click="fitView({ padding: 0.12 })" />
            <Button icon="pi pi-minus" text rounded severity="secondary" aria-label="Zoom out" title="Zoom out" @click="zoomOut()" />
            <Button icon="pi pi-plus" text rounded severity="secondary" aria-label="Zoom in" title="Zoom in" @click="zoomIn()" />
            <span class="bottom-dock-divider"></span>
            <Button icon="pi pi-download" text rounded severity="secondary" aria-label="Export STEP" title="Export STEP" @click="downloadStep()" />
            <Button icon="pi pi-trash" text rounded severity="secondary" class="delete-action"
              aria-label="Delete selected nodes or wires" title="Delete selected nodes or wires"
              :disabled="!nodes.some((node: any) => node.selected) && !edges.some((edge: any) => edge.selected)"
              @click="deleteSelectedFromDock" />
          </div>
          <aside v-if="selectedNode && inspectorOpen" class="canvas-node-inspector">
            <NodeInspector
              :node="selectedNode"
              :inputs="selectedNodeInputs"
              :outputs="selectedNodeOutputs"
              :on-parameter="onParameter"
              @close="inspectorOpen = false"
            />
          </aside>
          
        </div>
      </SplitterPanel>

      <SplitterPanel :size="32" :min-size="20" class="preview-panel">
        <GeometryViewport
          :previews="previews"
          :running="running"
          :runtime-status="status"
          :download-status="downloadStatus"
          :errors="errors"
          :logs="activityLogs"
        />
      </SplitterPanel>
    </Splitter>

    <Popover ref="palettePopover" appendTo="self" class="palette-popover">
      <div class="palette-content">
        <div class="palette-heading">
          <span>{{ activeCategory }}</span>
          <span class="palette-group-count">{{ activePaletteItems.length }}</span>
        </div>
        <InputText v-model="paletteSearch" placeholder="Search nodes…" class="palette-search" />
        <div class="palette-node-list">
          <Button
            v-for="item in activePaletteItems"
            :key="item.id"
            text
            severity="secondary"
            class="palette-node"
            draggable="true"
            @dragstart="dragNode($event, item)"
            @click="addDefinition(item)"
          >
            <template #default>
              <span class="palette-node-kind">{{ item.kind }}</span>
              <span class="palette-node-label">{{ item.name }}</span>
            </template>
          </Button>
        </div>
        <div v-if="!activePaletteItems.length" class="palette-empty">
          <ProgressSpinner v-if="!catalog.length && !errors.length" style="width:20px;height:20px" stroke-width="4" />
          <span v-else-if="!runtime.isReady">Waiting for Python runtime…</span>
          <span v-else>{{ errors.length ? "Python runtime failed" : "No matching nodes" }}</span>
        </div>
      </div>
    </Popover>

    <Popover ref="contextPopover" appendTo="self" class="context-popover">
      <div class="context-content">
        <InputText v-model="contextSearch" placeholder="Add node…" class="context-search" autofocus />
        <div class="context-results">
          <Button
            v-for="item in contextResults"
            :key="item.id"
            text
            severity="secondary"
            class="context-result"
            @click="addFromContext(item)"
          >
            <template #default>
              <span class="palette-node-kind">{{ item.kind }}</span>
              <span class="context-result-name">{{ item.name }}</span>
              <span class="context-result-category">{{ item.category }}</span>
            </template>
          </Button>
          <span v-if="!contextResults.length" class="context-empty">No matching nodes</span>
        </div>
      </div>
    </Popover>
  </main>
</template>
