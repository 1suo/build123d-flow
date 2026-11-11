<script setup lang="ts">
import { computed, markRaw, onMounted, ref, shallowRef, watch } from "vue";
import { VueFlow, useVueFlow, type Connection, type Edge, type Node } from "@vue-flow/core";
import { Background } from "@vue-flow/background";
import ApiNode from "./components/ApiNode.vue";
import GeometryViewport from "./components/GeometryViewport.vue";
import Button from "primevue/button";
import InputText from "primevue/inputtext";
import Dock from "primevue/dock";
import Popover from "primevue/popover";
import ProgressSpinner from "primevue/progressspinner";
import Message from "primevue/message";
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
const previews = shallowRef<Record<string, MeshPreview>>({});
const running = ref(false);
const nodes = ref<Node[]>([]);
const edges = ref<Edge[]>([]);
const nodeTypes = { api: markRaw(ApiNode) } as any;
const canvasSurface = ref<HTMLElement | null>(null);
const palettePopover = ref<any>();
const contextPopover = ref<any>();
const contextPosition = ref<{ x: number; y: number } | undefined>();
const { addNodes, addEdges, removeNodes, removeEdges, updateEdge, screenToFlowCoordinate, fitView, zoomIn, zoomOut } = useVueFlow();

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

  if (item.id.startsWith("core.")) {
    if (/^core\.value|^core\.constant/.test(id)) return "Values";
    if (/^core\.math|^core\.compare|^core\.logic/.test(id)) return "Math & Logic";
    if (/^core\.vector/.test(id)) return "Vectors";
    if (/^core\.string|^core\.convert|^core\.python/.test(id)) return "Data & Code";
  }

  if (/^(box|cylinder|sphere|cone|torus|wedge|extrusion|revolution)$/.test(name)
      || /objects_part|primitives/.test(category)) return "3D Primitives";
  if (/rectangle|circle|ellipse|polygon|slot|regularpolygon|trapezoid|polyline/.test(name)
      || /objects_sketch|profiles/.test(category)) return "2D Profiles";
  if (/extrude|revolve|loft|sweep|sweep|thicken/.test(name)
      || /operations_generic|operations_part/.test(category)) return "Modeling";
  if (/union|difference|intersection|fuse|cut|common|split|intersect/.test(name)) return "Boolean";
  if (/fillet|chamfer|shell|offset|draft|hollow/.test(name)) return "Finishing";
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
  const order = ["3D Primitives", "2D Profiles", "Modeling", "Boolean", "Finishing", "Transforms", "Measurements", "Topology", "Vectors", "Math & Logic", "Values", "Data & Code", "Geometry", "Other API"];
  return order.filter((group) => groups.has(group)).concat(
    [...groups].filter((group) => !order.includes(group)).sort()
  );
});
const categoryItems = computed(() => categories.value.map((category) => ({
  label: category,
  category,
  icon: categoryIcon(category),
})));
const activeCategory = ref("Geometry");
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
  if (value.includes("geometry") || value.includes("primitive") || value.includes("topology")) return asset("icons/cad/shape.svg");
  if (value.includes("boolean") || value.includes("operation") || value.includes("modeling")) return asset("icons/cad/union.svg");
  if (value.includes("data") || value.includes("value")) return asset("icons/cad/value.svg");
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
  const name = item.name.toLowerCase();
  const id = item.id.toLowerCase();
  if (/^box$/.test(name)) return asset("icons/cad/box.svg");
  if (/cylinder/.test(name)) return asset("icons/cad/cylinder.svg");
  if (/sphere/.test(name)) return asset("icons/cad/sphere.svg");
  if (/union|fuse/.test(name) || id.endsWith(".union")) return asset("icons/cad/union.svg");
  if (/difference|subtract|cut/.test(name) || id.endsWith(".difference")) return asset("icons/cad/difference.svg");
  if (/intersection|common/.test(name) || id.endsWith(".intersection")) return asset("icons/cad/intersection.svg");
  if (/extrude/.test(name)) return asset("icons/cad/extrude.svg");
  if (/revolve/.test(name)) return asset("icons/cad/revolve.svg");
  if (/fillet/.test(name)) return asset("icons/cad/fillet.svg");
  if (/chamfer/.test(name)) return asset("icons/cad/chamfer.svg");
  if (/rectangle|square/.test(name)) return asset("icons/cad/rectangle.svg");
  if (/circle/.test(name)) return asset("icons/cad/circle.svg");
  if (/ellipse/.test(name)) return asset("icons/cad/ellipse.svg");
  if (/polygon/.test(name)) return asset("icons/cad/polygon.svg");
  if (/move|translate/.test(name)) return asset("icons/cad/move.svg");
  if (/rotate/.test(name)) return asset("icons/cad/rotate.svg");
  if (/mirror/.test(name)) return asset("icons/cad/mirror.svg");
  if (/scale/.test(name)) return asset("icons/cad/scale.svg");
  if (item.kind === "geometry") return asset("icons/cad/shape.svg");
  if (item.kind === "value") return asset("icons/cad/value.svg");
  if (item.category === "Math") return asset("icons/cad/math.svg");
  if (item.category === "Logic") return asset("icons/cad/logic.svg");
  if (item.category === "Python") return asset("icons/cad/code.svg");
  return asset("icons/cad/node.svg");
}

onMounted(() => {
  void runtime.initialize((message) => (status.value = message)).then(() => {
    const discovered = runtime.getCatalog();
    const byName = new Map(discovered.map((item) => [item.name, item]));
    catalog.value = discovered;
    addStarterGraph();
    for (const node of nodes.value as any[]) {
      const resolved = byName.get(node.data.definition.name);
      if (resolved) node.data.definition = resolved;
    }
    status.value = "Ready";
    scheduleEvaluation();
    if (wantsFullCatalog) void ensureFullCatalog();
  }).catch((error) => {
    status.value = "Initialization failed";
    runtimeError.value = error instanceof Error ? error.message : String(error);
  });
});

function addStarterGraph() {
  const box = catalog.value.find((item) => item.name === "Box");
  const union = catalog.value.find((item) => item.name === "Union");
  if (!box || !union) return;
  const left = createGraphNode(box, `n${nextId++}`, { x: 100, y: 100 });
  const right = createGraphNode(box, `n${nextId++}`, { x: 100, y: 360 });
  const combine = createGraphNode(union, `n${nextId++}`, { x: 500, y: 230 });
  left.data.params = { ...left.data.params, length: 20, width: 20, height: 10 };
  right.data.params = { ...right.data.params, length: 15, width: 15, height: 15 };
  left.data.onParameter = onParameter;
  right.data.onParameter = onParameter;
  combine.data.onParameter = onParameter;
  left.data.onVisibility = onVisibility;
  right.data.onVisibility = onVisibility;
  combine.data.onVisibility = onVisibility;
  addNodes([left, right, combine] as unknown as Node[]);
  addEdges([
    { id: `e${nextId++}`, source: left.id, sourceHandle: "result", target: combine.id, targetHandle: "a", updatable: true },
    { id: `e${nextId++}`, source: right.id, sourceHandle: "result", target: combine.id, targetHandle: "b", updatable: true },
  ] as Edge[]);
  fitView({ padding: 0.25 });
  scheduleEvaluation();
}

function addDefinition(definition: NodeDefinition, position?: { x: number; y: number }) {
  const id = `n${nextId++}`;
  const rect = canvasSurface.value?.getBoundingClientRect();
  const center = rect
    ? screenToFlowCoordinate({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 })
    : { x: 160, y: 120 };
  const node = createGraphNode(definition, id, position ?? center);
  node.data.onParameter = onParameter;
  node.data.onVisibility = onVisibility;
  addNodes([node as unknown as Node]);
  scheduleEvaluation();
}

function ensureFullCatalog() {
  wantsFullCatalog = true;
  if (!runtime.isReady || fullCatalogLoaded || fullCatalogLoading) return;
  fullCatalogLoading = true;
  void runtime.discoverCatalog().then((fullCatalog) => {
    catalog.value = fullCatalog;
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
  const selectedIds = new Set(nodes.value.filter((node: any) => node.selected).map((node) => node.id));
  if (!selectedIds.size) return;
  removeEdges(edges.value.filter((edge: any) => selectedIds.has(edge.source) || selectedIds.has(edge.target)).map((edge) => edge.id));
  removeNodes([...selectedIds]);
  previews.value = Object.fromEntries(Object.entries(previews.value).filter(([id]) => !selectedIds.has(id)));
  scheduleEvaluation();
}

function isValidConnection(connection: Connection) {
  return canConnect(graph.value, connection);
}

function onConnect(connection: Connection) {
  if (!isValidConnection(connection)) return;
  const replaced = edges.value
    .filter((edge: any) => edge.target === connection.target && edge.targetHandle === connection.targetHandle)
    .map((edge) => edge.id);
  if (replaced.length) removeEdges(replaced);
  addEdges([{ ...connection, id: `e${Date.now()}`, animated: false, updatable: true } as Edge]);
  scheduleEvaluation();
}

function onEdgeUpdate({ edge, connection }: { edge: Edge; connection: Connection }) {
  if (!canConnect(graph.value, connection, edge.id)) return;
  const replaced = edges.value
    .filter((candidate: any) => candidate.id !== edge.id &&
      candidate.target === connection.target && candidate.targetHandle === connection.targetHandle)
    .map((candidate) => candidate.id);
  if (replaced.length) removeEdges(replaced);
  updateEdge(edge, connection);
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
  try {
    const result: EvaluationResult = await runtime.evaluate(graph.value);
    if (revision !== evaluationRevision) return;
    previews.value = result.previews;
    errors.value = Object.entries(result.errors).map(([id, error]) => `${id}: ${error}`);
    for (const node of nodes.value as any[]) node.data.error = result.errors[node.id] ?? "";
  } catch (error) {
    if (revision === evaluationRevision) errors.value = [String(error)];
  } finally {
    if (revision === evaluationRevision) running.value = false;
  }
}

watch([nodes, edges], scheduleEvaluation, { deep: true });

function downloadStep() {
  const bytes = runtime.exportStep();
  if (!bytes) return;
  const url = URL.createObjectURL(new Blob([bytes], { type: "application/step" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "model.step";
  anchor.click();
  URL.revokeObjectURL(url);
}
</script>

<template>
  <main class="app-shell">
    <div v-if="!runtime.isReady && !runtimeError" class="runtime-indicator">
      <ProgressSpinner style="width:16px;height:16px" stroke-width="4" />
      <span>{{ status }}</span>
    </div>
    <Message v-if="runtimeError" severity="error" :closable="false" class="runtime-error">{{ runtimeError }}</Message>
    <Splitter class="workspace" :gutter-size="4">
      <SplitterPanel :size="68" :min-size="35" class="graph-panel">
        <div ref="canvasSurface" class="canvas-surface" @drop="onDrop" @dragover.prevent @contextmenu.prevent="openContextMenu">
          <VueFlow
            v-model:nodes="nodes"
            v-model:edges="edges"
            :node-types="nodeTypes"
            :is-valid-connection="isValidConnection"
            fit-view-on-init
            delete-key-code="Delete"
            :default-viewport="{ zoom: 0.85, x: 60, y: 60 }"
            @connect="onConnect"
            @edge-update="onEdgeUpdate"
            @keydown.delete.stop.prevent="deleteSelected"
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
        </div>
      </SplitterPanel>

      <SplitterPanel :size="32" :min-size="20" class="preview-panel">
        <GeometryViewport :previews="previews" :running="running" />
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
