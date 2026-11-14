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
import Splitter from "primevue/splitter";
import SplitterPanel from "primevue/splitterpanel";
import { PythonRuntime } from "./runtime/pythonRuntime";
import { canConnect, createGraphNode, validateGraph } from "./runtime/graph";
import type { GraphDocument, NodeDefinition, EvaluationResult, MeshPreview } from "./types";

const runtime = new PythonRuntime();
const activePalette = ref("tikal");
const paletteOptions = [
  { value: "alexandria", label: "Alexandria" },
  { value: "alexandria-swapped", label: "Alexandria · swapped" },
  { value: "babylon", label: "Babylon" },
  { value: "babylon-swapped", label: "Babylon · swapped" },
  { value: "knossos", label: "Knossos" },
  { value: "knossos-swapped", label: "Knossos · swapped" },
  { value: "petra", label: "Petra" },
  { value: "petra-swapped", label: "Petra · swapped" },
  { value: "tikal", label: "Tikal" },
  { value: "tikal-swapped", label: "Tikal · swapped" },
  { value: "troy", label: "Troy" },
  { value: "troy-swapped", label: "Troy · swapped" },
];
function persistPalette() {
  const value = activePalette.value;
  document.documentElement.dataset.findspotTheme = value;
  try { localStorage.setItem("findspot-palette", value); } catch { /* Storage may be unavailable. */ }
}
const status = ref("Loading Python runtime…");
const runtimeError = ref("");
const catalog = ref<NodeDefinition[]>([]);
const paletteSearch = ref("");
const activeCategory = ref<string | null>(null);
const activeSource = ref<PaletteSource | null>(null);
let fullCatalogLoaded = false;
let discoveryPromise: Promise<void> | null = null;
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
const executionScopes = ref<NonNullable<GraphDocument["execution_scopes"]>>([]);
const nodeTypes = { api: markRaw(ApiNode) } as any;
const canvasSurface = ref<HTMLElement | null>(null);
const palettePopover = ref<any>();
const settingsPopover = ref<any>();
const pythonFileInput = ref<HTMLInputElement | null>(null);
const pythonSource = ref("");
const isMobile = ref(false);
let mobileMediaQuery: MediaQueryList | undefined;
function updateMobileLayout() { isMobile.value = Boolean(mobileMediaQuery?.matches); }
const contextPopover = ref<any>();
const contextPosition = ref<{ x: number; y: number } | undefined>();
const { addNodes, addEdges, removeEdges, updateEdge, updateNodeInternals, screenToFlowCoordinate, fitView, zoomIn, zoomOut } = useVueFlow();

let nextId = 1;
let evaluationRevision = 0;
let evaluationTimer: ReturnType<typeof setTimeout> | undefined;


const paletteSources = [
  { id: "build123d", label: "build123d" },
  { id: "python", label: "Python & libraries" },
  { id: "flow", label: "Flow nodes" },
] as const;
type PaletteSource = typeof paletteSources[number]["id"];
function paletteOrigin(item: NodeDefinition): PaletteSource {
  return item.source;
}
const dockGroups = computed(() => paletteSources.map((source) => {
  const sourceItems = catalog.value.filter((item) => paletteOrigin(item) === source.id);
  const categories = [...new Set(sourceItems.map((item) => item.palette_category))].sort((a, b) => a.localeCompare(b));
  return { ...source, categories: categories.map((label) => ({
    label, count: sourceItems.filter((item) => item.palette_category === label).length,
  })) };
}));
const activePaletteItems = computed(() => catalog.value.filter((item) => {
  const query = paletteSearch.value.trim().toLowerCase();
  if (query) return (item.name + " " + item.id + " " + item.description + " " + item.palette_category + " " + item.palette_section).toLowerCase().includes(query);
  return activeCategory.value !== null && paletteOrigin(item) === activeSource.value && item.palette_category === activeCategory.value;
}).sort((a, b) => a.palette_section.localeCompare(b.palette_section) || a.name.localeCompare(b.name)));
const activePaletteSections = computed(() => {
  const sections = new Map<string, NodeDefinition[]>();
  for (const item of activePaletteItems.value) {
    const section = item.palette_section;
    if (!sections.has(section)) sections.set(section, []);
    sections.get(section)!.push(item);
  }
  return [...sections].map(([label, items]) => ({ label, items }));
});
const contextResults = computed(() => catalog.value.filter((item) =>
  (item.name + " " + item.category + " " + item.id).toLowerCase().includes(contextSearch.value.toLowerCase())
).sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name)).slice(0, 60));

const graph = computed<GraphDocument>(() => JSON.parse(JSON.stringify({
  nodes: nodes.value.map((node: any) => ({
    id: node.id,
    type: "api",
    position: { x: Number(node.position.x), y: Number(node.position.y) },
    data: {
      definition: node.data.definition,
      params: node.data.params,
      visible: node.data.visible,
      execution_scope_path: node.data.execution_scope_path,
      scope_binding: node.data.scope_binding,
      scope_result: node.data.scope_result,
    },
  })),
  edges: edges.value.map((edge: any) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.sourceHandle,
    targetHandle: edge.targetHandle,
  })),
  execution_scopes: executionScopes.value,
})));

function deleteSelectedFromDock() {
  deleteSelected();
}

function nodeIcon(item: NodeDefinition): string {
  const name = item.name.toLowerCase();
  const icons: Array<[RegExp, string]> = [
    [/^box$|^cube$/, "/icons/cad/box.svg"],
    [/^cylinder$/, "/icons/cad/cylinder.svg"],
    [/^sphere$/, "/icons/cad/sphere.svg"],
    [/extrude/, "/icons/cad/extrude.svg"],
    [/fillet/, "/icons/cad/fillet.svg"],
    [/loft/, "/icons/iconoir/loft-3d.svg"],
    [/sweep/, "/icons/iconoir/sweep-3d.svg"],
    [/pipe/, "/icons/iconoir/pipe-3d.svg"],
    [/mirror/, "/icons/cad/mirror.svg"],
    [/ellipse/, "/icons/cad/ellipse.svg"],
    [/\barc\b/, "/icons/cad/circle.svg"],
    [/project.*curve/, "/icons/iconoir/project-curve-3d.svg"],
    [/\bedges?\b/, "/icons/iconoir/select-edge-3d.svg"],
    [/\bfaces?\b/, "/icons/iconoir/select-face-3d.svg"],
    [/union|fuse/, "/icons/cad/union.svg"],
    [/difference|cut/, "/icons/cad/difference.svg"],
    [/intersection|common/, "/icons/cad/intersection.svg"],
    [/rectangle/, "/icons/cad/rectangle.svg"],
    [/circle/, "/icons/cad/circle.svg"],
    [/polygon/, "/icons/cad/polygon.svg"],
    [/revolve|revolution/, "/icons/cad/revolve.svg"],
  ];
  const path = icons.find(([pattern]) => pattern.test(name))?.[1]
    ?? (item.kind === "value" || item.kind === "enum" ? "/icons/cad/value.svg"
      : item.kind === "operation" ? "/icons/cad/node.svg" : "/icons/cad/shape.svg");
  return `${import.meta.env.BASE_URL}${path.replace(/^\//, "")}`;
}
onMounted(() => {
  try {
    const saved = localStorage.getItem("findspot-palette");
    if (saved && paletteOptions.some((option) => option.value === saved)) activePalette.value = saved;
  } catch { /* Use Tikal when storage is unavailable. */ }
  persistPalette();
  window.addEventListener("keydown", onGlobalKeydown, true);
  mobileMediaQuery = window.matchMedia("(max-width: 760px)");
  updateMobileLayout();
  mobileMediaQuery.addEventListener("change", updateMobileLayout);
  void runtime.initialize(updateRuntimeStatus).then(async () => {
    const discovered = await runtime.getCatalog();
    const byName = new Map(discovered.map((item) => [item.name, item]));
    catalog.value = discovered;
    for (const node of nodes.value as any[]) {
      const resolved = byName.get(node.data.definition.name);
      if (resolved) node.data.definition = resolved;
      node.data.icon = nodeIcon(node.data.definition);
    }
    status.value = "Ready";
    logActivity("Python runtime ready", "success");
    if (import.meta.env.VITE_E2E_TEST_HOOKS === "true") {
      (window as any).__build123dFlowTest = {
        runGraphWorkflowTests: () => runtime.runGraphWorkflowTests(),
      };
    }
    if (nodes.value.length) scheduleEvaluation();
  }).catch((error) => {
    status.value = "Initialization failed";
    logActivity("Python runtime initialization failed", "error");
    runtimeError.value = error instanceof Error ? error.message : String(error);
  });
});


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

async function ensureFullCatalog() {
  if (fullCatalogLoaded || discoveryPromise || !runtime.isReady) return discoveryPromise;
  discoveryPromise = runtime.discoverCatalog().then(async (discovered) => {
    const byId = new Map(discovered.map((item) => [item.id, item]));
    catalog.value = discovered;
    for (const node of nodes.value as any[]) {
      const resolved = byId.get(node.data.definition.id) ?? discovered.find((item) => item.name === node.data.definition.name);
      if (resolved) node.data.definition = resolved;
      node.data.icon = nodeIcon(node.data.definition);
    }
    fullCatalogLoaded = true;
    const report = await runtime.getDiscoveryReport();
    const moduleCount = Array.isArray(report.modules_visited) ? report.modules_visited.length : 0;
    logActivity(`API discovery complete · ${discovered.length} nodes · ${moduleCount} modules`, "success");
  }).catch((error) => logActivity("Full API discovery failed · " + String(error), "warning"))
    .finally(() => { discoveryPromise = null; });
  return discoveryPromise;
}
function openCategory(source: PaletteSource, category: string, event: MouseEvent) {
  activeSource.value = source;
  activeCategory.value = category;
  paletteSearch.value = "";
  palettePopover.value?.show(event);
  void ensureFullCatalog();
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
  addEdges([{ ...connection, id: `e${crypto.randomUUID()}`, animated: false, updatable: true } as Edge]);
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
    previews.value = {};
    socketValues.value = {};
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
    else logActivity("Evaluation complete · " + Object.keys(result.previews).length + " visible geometry · cache " + (result.cache?.hits ?? 0) + " hits", "success");
    for (const node of nodes.value as any[]) node.data.error = result.errors[node.id] ?? "";
  } catch (error) {
    if (revision === evaluationRevision) {
      previews.value = {};
      socketValues.value = {};
      for (const node of nodes.value as any[]) node.data.error = String(error);
      errors.value = [String(error)];
      logActivity(String(error), "error");
    }
  } finally {
    if (revision === evaluationRevision) running.value = false;
  }
}

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onGlobalKeydown, true);
  mobileMediaQuery?.removeEventListener("change", updateMobileLayout);
});

async function downloadStep() {
  if (downloadStatusTimer) clearTimeout(downloadStatusTimer);
  downloadStatus.value = "Preparing STEP export…";
  logActivity("Preparing STEP export");
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  try {
    const bytes = await runtime.exportStep();
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


async function importPythonSource(source: string, sourceLabel: string) {
  if (!source.trim()) {
    downloadStatus.value = "Paste Python code first";
    return;
  }
  if (!runtime.isReady) return;
  if (downloadStatusTimer) clearTimeout(downloadStatusTimer);
  downloadStatus.value = "Parsing Python source…";
  logActivity("Importing Python source · " + sourceLabel);
  try {
    const imported = await runtime.importPython(source);
    const importedNodes = imported.nodes.map((node: any) => ({
      ...node, type: "api", data: {
        ...node.data,
        icon: nodeIcon(node.data.definition),
        onParameter, onVisibility, onCollapse, onInspect,
      }
    }));
    // Replace the Vue Flow store through its public mutation API. Setting the
    // v-model arrays directly can race Vue Flow's internal node/edge store and
    // leave imported wires visible but absent from the graph sent to Pyodide.
    edges.value = [];
    nodes.value = [];
    await nextTick();
    addNodes(importedNodes as Node[]);
    // Target/source handles only exist after the custom nodes have mounted.
    await nextTick();
    addEdges(imported.edges as Edge[]);
    await nextTick();
    executionScopes.value = imported.execution_scopes ?? [];
    nextId = importedNodes.length + 1;
    previews.value = {};
    socketValues.value = {};
    errors.value = [];
    scheduleEvaluation();
    fitView({ padding: 0.16 });
    downloadStatus.value = "Python imported · " + importedNodes.length + " nodes";
    logActivity("Python imported · " + importedNodes.length + " nodes / " + imported.edges.length + " connections", "success");
    settingsPopover.value?.hide();
    return true;
  } catch (error) {
    downloadStatus.value = "Python import failed";
    errors.value = [String(error)];
    logActivity("Python import failed · " + String(error), "error");
    return false;
  } finally {
    downloadStatusTimer = window.setTimeout(() => { downloadStatus.value = ""; }, 5000);
  }
}

async function importPythonFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  const source = await file.text();
  await importPythonSource(source, file.name);
}

async function importPastedPython() {
  const imported = await importPythonSource(pythonSource.value, "pasted code");
  if (imported) pythonSource.value = "";
}

async function downloadPython() {
  if (downloadStatusTimer) clearTimeout(downloadStatusTimer);
  downloadStatus.value = "Generating build123d Python…";
  logActivity("Generating build123d Python source");
  try {
    const source = await runtime.exportPython(graph.value);
    const url = URL.createObjectURL(new Blob([source], { type: "text/x-python;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "model.py";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1500);
    downloadStatus.value = "Python source download started · model.py";
    logActivity("Python source exported · model.py", "success");
    settingsPopover.value?.hide();
  } catch (error) {
    downloadStatus.value = "Python export failed";
    logActivity("Python export failed · " + String(error), "error");
  }
  downloadStatusTimer = window.setTimeout(() => { downloadStatus.value = ""; }, 4500);
}
</script>

<template>
  <main class="app-shell" :data-theme="activePalette">
    <Message v-if="runtimeError" severity="error" :closable="false" class="runtime-error">{{ runtimeError }}</Message>
    <Splitter class="workspace" :layout="isMobile ? 'vertical' : 'horizontal'" :gutter-size="4">
      <template v-if="isMobile">
      <SplitterPanel :size="isMobile ? 42 : 32" :min-size="isMobile ? 25 : 20" class="preview-panel">
        <GeometryViewport
          :previews="previews"
          :selected-node-id="selectedNode?.id"
          :running="running"
          :runtime-status="status"
          :download-status="downloadStatus"
          :errors="errors"
          :logs="activityLogs"
        />
      </SplitterPanel>

      <SplitterPanel :size="isMobile ? 58 : 68" :min-size="isMobile ? 40 : 35" class="graph-panel">
        <div ref="canvasSurface" class="canvas-surface" @drop="onDrop" @dragover.prevent @contextmenu.prevent="openContextMenu">
          <div class="canvas-topbar">
            <nav class="category-strip" aria-label="Node palette">
              <span v-for="source in dockGroups" :key="source.id" class="dock-source-group">
                <span class="dock-source-label">{{ source.label }}</span>
                <Button v-for="category in source.categories" :key="category.label" text severity="secondary"
                  class="category-chip" :class="{ 'is-active': activeSource === source.id && activeCategory === category.label }"
                  :label="category.label" @click="openCategory(source.id, category.label, $event)" />
              </span>
              <span v-if="!runtime.isReady" class="category-loading">Loading runtime…</span>
            </nav>
          </div>
          <div v-if="runtime.isReady && !nodes.length" class="canvas-empty-state">
            <strong>Empty canvas</strong>
            <span>Select a category in the dock or right-click to add a node</span>
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
            <Background :pattern-color="'var(--tikal-border)'" :gap="22" />
          </VueFlow>
          <div class="canvas-bottom-dock" role="toolbar" aria-label="Canvas tools">
            <Button icon="pi pi-expand" text rounded severity="secondary" aria-label="Fit graph" title="Fit graph" @click="fitView({ padding: 0.12 })" />
            <Button icon="pi pi-minus" text rounded severity="secondary" aria-label="Zoom out" title="Zoom out" @click="zoomOut()" />
            <Button icon="pi pi-plus" text rounded severity="secondary" aria-label="Zoom in" title="Zoom in" @click="zoomIn()" />
            <Button text rounded severity="secondary" class="inspector-toggle"
              :class="{ 'is-active': inspectorOpen }" :aria-pressed="inspectorOpen"
              aria-label="Toggle inspector" title="Inspector"
              @click="inspectorOpen = !inspectorOpen">
              <svg class="sidebar-toggle-icon" viewBox="0 0 24 24" aria-hidden="true">
                <rect x="2.5" y="3" width="19" height="18" rx="2" />
                <path d="M9 3v18" />
              </svg>
            </Button>
            <span class="bottom-dock-divider"></span>
            <Button icon="pi pi-trash" text rounded severity="secondary" class="delete-action"
              aria-label="Delete selected nodes or wires" title="Delete selected nodes or wires"
              :disabled="!nodes.some((node: any) => node.selected) && !edges.some((edge: any) => edge.selected)"
              @click="deleteSelectedFromDock" />
            <span class="bottom-dock-divider"></span>
            <Button icon="pi pi-cog" text rounded severity="secondary"
              aria-label="Settings" title="Settings" @click="settingsPopover?.toggle($event)" />
            <Popover ref="settingsPopover" class="settings-popover">
              <label class="theme-switcher settings-theme-switcher">
                <span>Color scheme</span>
                <select v-model="activePalette" aria-label="Findspot color scheme" @change="persistPalette">
                  <option v-for="option in paletteOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
              </label>
              <div class="settings-divider"></div>
              <div class="settings-export-actions">
                <span>Python interchange</span>
                <input ref="pythonFileInput" type="file" accept=".py,text/x-python,text/plain" hidden @change="importPythonFile" />
                <Button icon="pi pi-file-import" label="Import Python script" text severity="secondary" @click="pythonFileInput?.click()" :disabled="!runtime.isReady" />
                <label class="python-paste-label" for="python-source-input">Or paste Python code</label>
                <textarea id="python-source-input" v-model="pythonSource" class="python-source-input"
                  placeholder="from build123d import *&#10;&#10;part = Box(10, 20, 30)"
                  spellcheck="false" :disabled="!runtime.isReady" />
                <Button icon="pi pi-code" label="Import pasted code" text severity="secondary"
                  @click="importPastedPython()" :disabled="!runtime.isReady || !pythonSource.trim()" />
                <span>Export</span>
                <Button icon="pi pi-download" label="STEP model" text severity="secondary" @click="downloadStep(); settingsPopover?.hide()" :disabled="!runtime.isReady" />
                <Button icon="pi pi-file-export" label="Python script" text severity="secondary" @click="downloadPython()" :disabled="!runtime.isReady || !nodes.length" />
              </div>
            </Popover>
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
      </template>
      <template v-else>
      <SplitterPanel :size="isMobile ? 58 : 68" :min-size="isMobile ? 40 : 35" class="graph-panel">
        <div ref="canvasSurface" class="canvas-surface" @drop="onDrop" @dragover.prevent @contextmenu.prevent="openContextMenu">
          <div class="canvas-topbar">
            <nav class="category-strip" aria-label="Node palette">
              <span v-for="source in dockGroups" :key="source.id" class="dock-source-group">
                <span class="dock-source-label">{{ source.label }}</span>
                <Button v-for="category in source.categories" :key="category.label" text severity="secondary"
                  class="category-chip" :class="{ 'is-active': activeSource === source.id && activeCategory === category.label }"
                  :label="category.label" @click="openCategory(source.id, category.label, $event)" />
              </span>
              <span v-if="!runtime.isReady" class="category-loading">Loading runtime…</span>
            </nav>
          </div>
          <div v-if="runtime.isReady && !nodes.length" class="canvas-empty-state">
            <strong>Empty canvas</strong>
            <span>Select a category in the dock or right-click to add a node</span>
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
            <Background :pattern-color="'var(--tikal-border)'" :gap="22" />
          </VueFlow>
          <div class="canvas-bottom-dock" role="toolbar" aria-label="Canvas tools">
            <Button icon="pi pi-expand" text rounded severity="secondary" aria-label="Fit graph" title="Fit graph" @click="fitView({ padding: 0.12 })" />
            <Button icon="pi pi-minus" text rounded severity="secondary" aria-label="Zoom out" title="Zoom out" @click="zoomOut()" />
            <Button icon="pi pi-plus" text rounded severity="secondary" aria-label="Zoom in" title="Zoom in" @click="zoomIn()" />
            <Button text rounded severity="secondary" class="inspector-toggle"
              :class="{ 'is-active': inspectorOpen }" :aria-pressed="inspectorOpen"
              aria-label="Toggle inspector" title="Inspector"
              @click="inspectorOpen = !inspectorOpen">
              <svg class="sidebar-toggle-icon" viewBox="0 0 24 24" aria-hidden="true">
                <rect x="2.5" y="3" width="19" height="18" rx="2" />
                <path d="M9 3v18" />
              </svg>
            </Button>
            <span class="bottom-dock-divider"></span>
            <Button icon="pi pi-trash" text rounded severity="secondary" class="delete-action"
              aria-label="Delete selected nodes or wires" title="Delete selected nodes or wires"
              :disabled="!nodes.some((node: any) => node.selected) && !edges.some((edge: any) => edge.selected)"
              @click="deleteSelectedFromDock" />
            <span class="bottom-dock-divider"></span>
            <Button icon="pi pi-cog" text rounded severity="secondary"
              aria-label="Settings" title="Settings" @click="settingsPopover?.toggle($event)" />
            <Popover ref="settingsPopover" class="settings-popover">
              <label class="theme-switcher settings-theme-switcher">
                <span>Color scheme</span>
                <select v-model="activePalette" aria-label="Findspot color scheme" @change="persistPalette">
                  <option v-for="option in paletteOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
              </label>
              <div class="settings-divider"></div>
              <div class="settings-export-actions">
                <span>Python interchange</span>
                <input ref="pythonFileInput" type="file" accept=".py,text/x-python,text/plain" hidden @change="importPythonFile" />
                <Button icon="pi pi-file-import" label="Import Python script" text severity="secondary" @click="pythonFileInput?.click()" :disabled="!runtime.isReady" />
                <label class="python-paste-label" for="python-source-input">Or paste Python code</label>
                <textarea id="python-source-input" v-model="pythonSource" class="python-source-input"
                  placeholder="from build123d import *&#10;&#10;part = Box(10, 20, 30)"
                  spellcheck="false" :disabled="!runtime.isReady" />
                <Button icon="pi pi-code" label="Import pasted code" text severity="secondary"
                  @click="importPastedPython()" :disabled="!runtime.isReady || !pythonSource.trim()" />
                <span>Export</span>
                <Button icon="pi pi-download" label="STEP model" text severity="secondary" @click="downloadStep(); settingsPopover?.hide()" />
                <Button icon="pi pi-file-export" label="Python script" text severity="secondary" @click="downloadPython()" />
              </div>
            </Popover>
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

      <SplitterPanel :size="isMobile ? 42 : 32" :min-size="isMobile ? 25 : 20" class="preview-panel">
        <GeometryViewport
          :previews="previews"
          :selected-node-id="selectedNode?.id"
          :running="running"
          :runtime-status="status"
          :download-status="downloadStatus"
          :errors="errors"
          :logs="activityLogs"
        />
      </SplitterPanel>
      </template>
    </Splitter>

    <Popover ref="palettePopover" appendTo="self" class="palette-popover">
      <div class="palette-content">
        <div class="palette-heading">
          <span>{{ paletteSearch.trim() ? "Search results" : activeCategory }}</span>
          <span class="palette-group-count">{{ activePaletteItems.length }}</span>
        </div>
        <InputText v-model="paletteSearch" placeholder="Search nodes…" class="palette-search" />
        <div class="palette-node-list">
          <section v-for="section in activePaletteSections" :key="section.label" class="palette-section">
            <div class="palette-section-heading"><span>{{ section.label }}</span><span class="palette-section-rule"></span></div>
            <Button v-for="item in section.items" :key="item.id" text severity="secondary"
              class="palette-node" draggable="true"
              @dragstart="dragNode($event, item)" @click="addDefinition(item)">
              <template #default><span class="palette-node-icon" :style="{ maskImage: `url(${nodeIcon(item)})`, WebkitMaskImage: `url(${nodeIcon(item)})` }" aria-hidden="true"></span>
              <span class="palette-node-label">{{ item.name }}</span></template>
            </Button>
          </section>
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
              <span class="palette-node-icon" :style="{ maskImage: `url(${nodeIcon(item)})`, WebkitMaskImage: `url(${nodeIcon(item)})` }" aria-hidden="true"></span>
              <span class="context-result-main"><span class="context-result-name">{{ item.name }}</span><span class="context-result-category">{{ paletteSources.find((source) => source.id === paletteOrigin(item))?.label }} · {{ item.category }}</span></span>
            </template>
          </Button>
          <span v-if="!contextResults.length" class="context-empty">No matching nodes</span>
        </div>
      </div>
    </Popover>
  </main>
</template>
