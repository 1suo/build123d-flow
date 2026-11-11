<script setup lang="ts">
import { computed, markRaw, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import { VueFlow, useVueFlow, type Connection, type Edge, type Node } from "@vue-flow/core";
import { Background } from "@vue-flow/background";
import ApiNode from "./components/ApiNode.vue";
import GeometryViewport from "./components/GeometryViewport.vue";
import NodeInspector from "./components/NodeInspector.vue";
import Button from "primevue/button";
import ProgressSpinner from "primevue/progressspinner";
import InputText from "primevue/inputtext";
import Dock from "primevue/dock";
import Popover from "primevue/popover";
import Message from "primevue/message";
import Select from "primevue/select";
import Splitter from "primevue/splitter";
import SplitterPanel from "primevue/splitterpanel";
import { PythonRuntime } from "./runtime/pythonRuntime";
import { canConnect, createGraphNode, validateGraph } from "./runtime/graph";
import type { GraphDocument, NodeDefinition, EvaluationResult, MeshPreview } from "./types";

const runtime = new PythonRuntime();
const asset = (path: string) => `${import.meta.env.BASE_URL}${path.replace(/^\//, "")}`;
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
let fullCatalogLoaded = false;
let fullCatalogLoading = false;
let wantsFullCatalog = false;

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
const categoryItems = computed(() => categories.value.map((category) => ({
  label: category,
  category,
  icon: categoryIcon(category),
})));
const activeCategory = ref("3D Primitives");
const exampleOptions = [
  { label: "Voronoi panel", value: "voronoi" },
  { label: "Boolean solids", value: "boolean" },
];
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

const canvasActions = [
  { label: "Fit graph", icon: "pi pi-expand", command: () => fitView({ padding: 0.2 }) },
  { label: "Zoom in", icon: "pi pi-plus", command: () => zoomIn() },
  { label: "Zoom out", icon: "pi pi-minus", command: () => zoomOut() },
  { label: "Export STEP", icon: "pi pi-download", command: () => downloadStep() },
];

function runCanvasAction(label: string) {
  canvasActions.find((item) => item.label === label)?.command();
}

function categoryIcon(category: string): string {
  const value = category.toLowerCase();
  if (value.includes("spatial") || value.includes("distribution") || value.includes("curve")) return asset("icons/cad/polygon.svg");
  if (value.includes("utility")) return asset("icons/cad/math.svg");
  if (value.includes("geometry") || value.includes("primitive") || value.includes("topology")) return asset("icons/cad/shape.svg");
  if (value.includes("boolean") || value.includes("operation") || value.includes("modeling")) return asset("icons/cad/union.svg");
  if (value.includes("data") || value.includes("value") || value.includes("collection") || value.includes("series")) return asset("icons/cad/value.svg");
  if (value.includes("primitive") || value.includes("solid")) return asset("icons/cad/shape.svg");
  if (value.includes("profile") || value.includes("sketch")) return asset("icons/cad/rectangle.svg");
  if (value.includes("finish")) return asset("icons/cad/fillet.svg");
  if (value.includes("measure")) return asset("icons/cad/math.svg");
  if (value.includes("vector") || value.includes("transform")) return asset("icons/cad/move.svg");
  if (value.includes("math")) return asset("icons/cad/math.svg");
  if (value.includes("vector") || value.includes("transform") || value.includes("location")) return asset("icons/cad/move.svg");
  if (value.includes("boolean") || value.includes("topology") || value.includes("shape") || value.includes("operation")) return asset("icons/cad/union.svg");
  if (value.includes("math") || value.includes("constant") || value.includes("numeric")) return asset("icons/cad/math.svg");
  if (value.includes("logic") || value.includes("compare")) return asset("icons/cad/logic.svg");
  if (value.includes("string") || value.includes("text") || value.includes("python")) return asset("icons/cad/code.svg");
  if (value.includes("value") || value.includes("convert")) return asset("icons/cad/value.svg");
  return asset("icons/cad/node.svg");
}

function nodeIcon(item: NodeDefinition): string {
  const name = item.name.toLowerCase().replaceAll("_", " ");
  const id = item.id.toLowerCase();
  const key = `${name} ${id}`;
  const icon = (file: string) => asset(`icons/cad/${file}.svg`);

  if (/\bbox\b|\bblock\b/.test(key)) return icon("box");
  if (/cylinder/.test(key)) return icon("cylinder");
  if (/sphere/.test(key)) return icon("sphere");
  if (/union|fuse|combine/.test(key)) return icon("union");
  if (/difference|subtract|cut/.test(key)) return icon("difference");
  if (/intersection|common/.test(key)) return icon("intersection");
  if (/extrud|loft|sweep|thicken/.test(key)) return icon("extrude");
  if (/revolv/.test(key)) return icon("revolve");
  if (/fillet|round/.test(key)) return icon("fillet");
  if (/chamfer|bevel/.test(key)) return icon("chamfer");
  if (/rectangle|square/.test(key)) return icon("rectangle");
  if (/circle/.test(key)) return icon("circle");
  if (/ellipse/.test(key)) return icon("ellipse");
  if (/voronoi|polygon|polyline|trapezoid|slot|hex grid/.test(key)) return icon("polygon");
  if (/edge|face|vertex|wire|shell|topology|boundary/.test(key)) return icon("shape");
  if (/section|slice|split/.test(key)) return icon("difference");
  if (/grid|pattern|array|polar|distribut|repeat/.test(key)) return icon("shape");
  if (/mirror|reflect/.test(key)) return icon("mirror");
  if (/rotate|rotation/.test(key)) return icon("rotate");
  if (/scale/.test(key)) return icon("scale");
  if (/move|translat|transform|location|position|offset/.test(key)) return icon("move");
  if (/math|numeric|number|measure|area|volume|length|distance|center|mass|inertia/.test(key) || item.category === "Math") return icon("math");
  if (/logic|compare|boolean|condition/.test(key) || item.category === "Logic") return icon("logic");
  if (/string|text|python|code|expression/.test(key) || item.category === "Python") return icon("code");
  if (/list|tuple|dict|set|series|value|constant|convert|partition|weave|transpose|cull|cross reference/.test(key) || item.kind === "value") return icon("value");
  if (/cone|torus|wedge|prism|primitive|shape|solid|compound|part|face|wire|edge|vertex|shell|sketch|plane|axis/.test(key) || item.kind === "geometry") return icon("shape");
  return icon("node");
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
    if (wantsFullCatalog) void ensureFullCatalog();
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

function loadExample() {
  const example = selectedExample.value;
  if (!example) return;
  const byId = (id: string) => catalog.value.find((item) => item.id === id);
  const byName = (name: string) => catalog.value.find((item) => item.name === name);
  const make = (definition: NodeDefinition | undefined, id: string, position: { x: number; y: number }, params: Record<string, unknown> = {}) => {
    if (!definition) throw new Error("Example node unavailable");
    const node = createGraphNode(definition, id, position);
    node.data.params = { ...node.data.params, ...params };
    node.data.icon = nodeIcon(definition);
    node.data.onParameter = onParameter;
    node.data.onVisibility = onVisibility;
    node.data.onCollapse = onCollapse;
    node.data.onInspect = onInspect;
    return node;
  };
  try {
    let exampleNodes: any[] = [];
    let exampleEdges: any[] = [];
    if (example === "voronoi") {
      exampleNodes = [make(byId("core.spatial.voronoi_2d"), "n1", { x: 300, y: 190 })];
    } else if (example === "boolean") {
      const a = make(byName("Box"), "n1", { x: 100, y: 100 }, { length: 30, width: 24, height: 12 });
      const b = make(byName("Box"), "n2", { x: 100, y: 360 }, { length: 22, width: 22, height: 22 });
      const combine = make(byName("Union") ?? byId("build123d_flow.union"), "n3", { x: 520, y: 230 });
      exampleNodes = [a, b, combine];
      exampleEdges = [
        { id: "e1", source: a.id, sourceHandle: "result", target: combine.id, targetHandle: "a", updatable: true },
        { id: "e2", source: b.id, sourceHandle: "result", target: combine.id, targetHandle: "b", updatable: true },
      ];
    }
    nodes.value = exampleNodes as unknown as Node[];
    edges.value = exampleEdges as unknown as Edge[];
    nextId = exampleNodes.length + 1;
    scheduleEvaluation();
    void nextTick(() => fitView({ padding: 0.25 }));
  } catch (error) {
    logActivity("Could not load example: " + String(error), "error");
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

function ensureFullCatalog() {
  wantsFullCatalog = true;
  if (!runtime.isReady || fullCatalogLoaded || fullCatalogLoading) return;
  fullCatalogLoading = true;
  void runtime.discoverCatalog().then((fullCatalog) => {
    catalog.value = fullCatalog;
    const byId = new Map(fullCatalog.map((item) => [item.id, item]));
    for (const node of nodes.value as any[]) {
      const definition = byId.get(node.data.definition.id);
      if (definition) node.data.definition = definition;
      node.data.icon = nodeIcon(node.data.definition);
    }
    fullCatalogLoaded = true;
    console.info("[build123d-flow] API discovery report", runtime.getDiscoveryReport());
  }).catch((error) => {
    console.warn("Full build123d catalog discovery failed", error);
  }).finally(() => {
    fullCatalogLoading = false;
  });
}

function openPalette(category: string, event: MouseEvent) {
  void ensureFullCatalog();
  paletteSearch.value = "";
  activeCategory.value = category;
  palettePopover.value?.show(event);
}

function openContextMenu(event: MouseEvent) {
  void ensureFullCatalog();
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
  inspectorOpen.value = true;
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

watch(() => selectedNode.value?.id, (id) => { if (id) inspectorOpen.value = true; });
watch([nodes, edges], scheduleEvaluation, { deep: true });
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
          <div class="examples-toolbar">
            <Select v-model="selectedExample" :options="exampleOptions" option-label="label" option-value="value" placeholder="Load example…" class="examples-select" :disabled="!runtime.isReady" @change="loadExample" />
            <Button icon="pi pi-sliders-h" text rounded severity="secondary"
              aria-label="Toggle node inspector" title="Node inspector"
              :disabled="!selectedNode" :class="{ 'is-active': inspectorOpen }"
              @click="inspectorOpen = !inspectorOpen" />
            <Button icon="pi pi-trash" text rounded severity="secondary" aria-label="Clear canvas" title="Clear canvas" :disabled="!nodes.length" @click="clearCanvas" />
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
            @nodes-change="scheduleEvaluation"
            @edges-change="scheduleEvaluation"
          >
            <Background pattern-color="#39352e" :gap="22" />
            <Dock v-if="runtime.isReady" :model="categoryItems" position="top" class="category-dock">
              <template #item="{ item }">
                <Button
                  text
                  rounded
                  severity="secondary"
                  :aria-label="item.category"
                  :title="item.category"
                  @click="openPalette(item.category, $event)"
                >
                  <template #icon><img class="dock-category-icon" :src="item.icon" alt="" /></template>
                </Button>
              </template>
            </Dock>
            <Dock :model="canvasActions" position="bottom" class="canvas-dock">
              <template #item="{ item }">
                <Button :icon="item.icon" text rounded severity="secondary" :aria-label="typeof item.label === 'string' ? item.label : 'Canvas action'" :title="typeof item.label === 'string' ? item.label : 'Canvas action'" @click="typeof item.label === 'string' && runCanvasAction(item.label)" />
              </template>
            </Dock>
          </VueFlow>
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
          <img class="palette-group-icon" :src="categoryIcon(activeCategory)" alt="" />
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
            <template #icon><img class="palette-node-icon" :src="nodeIcon(item)" alt="" /></template>
            <template #default><span class="palette-node-label">{{ item.name }}</span></template>
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
            <template #icon>
              <img class="palette-node-icon" :src="nodeIcon(item)" alt="" />
            </template>
            <template #default>
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
