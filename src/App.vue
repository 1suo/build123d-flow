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
import Accordion from "primevue/accordion";
import AccordionPanel from "primevue/accordionpanel";
import AccordionHeader from "primevue/accordionheader";
import AccordionContent from "primevue/accordioncontent";
import ProgressSpinner from "primevue/progressspinner";
import Message from "primevue/message";
import Splitter from "primevue/splitter";
import SplitterPanel from "primevue/splitterpanel";
import { PythonRuntime } from "./runtime/pythonRuntime";
import { canConnect, createGraphNode, validateGraph } from "./runtime/graph";
import type { GraphDocument, NodeDefinition, EvaluationResult, MeshPreview } from "./types";
import { bootCatalog } from "./catalog/boot";

const runtime = new PythonRuntime();
const status = ref("Loading Python runtime…");
const runtimeError = ref("");
const catalog = ref<NodeDefinition[]>(bootCatalog);
const paletteSearch = ref("");
const contextSearch = ref("");
const expandedGroups = ref<string[]>([]);
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
const { addNodes, addEdges, screenToFlowCoordinate, fitView, zoomIn, zoomOut } = useVueFlow();

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
  if (category === "primitives") return "Primitives";
  if (category.includes("profile") || category.includes("sketch")) return "2D Profiles";
  if (category === "values" || category === "constants" || category === "convert") return "Values";
  if (category === "math") return "Math";
  if (category === "logic" || category === "compare") return "Logic";
  if (category === "string" || category === "strings") return "Text";
  if (category.includes("vector")) return "Vectors";
  if (category === "python") return "Python";
  if (category === "boolean" || id.includes("boolean")) return "Boolean";
  if (/move|rotate|mirror|scale|transform|translate/.test(name)) return "Transforms";
  if (/area|volume|length|center|bounding|measure/.test(name)) return "Measurements";
  if (category.includes("method")) return "Shape Operations";
  if (/extrude|revolve|loft|sweep|fillet|chamfer|shell|offset|fuse|cut|intersect/.test(name)) return "Modeling Operations";
  if (item.kind === "geometry") return "Geometry";
  return "Build123d API";
}

const categories = computed(() => [...new Set(catalog.value.map(paletteGroup))].sort());
const categoryItems = computed(() => categories.value.map((category) => ({
  label: category,
  category,
  icon: categoryIcon(category),
})));
const groupedCatalog = computed(() => categories.value.map((category) => ({
  category,
  items: catalog.value.filter((item) =>
    paletteGroup(item) === category &&
    (item.name + " " + item.id + " " + item.description).toLowerCase().includes(paletteSearch.value.toLowerCase())
  ).sort((a, b) => a.name.localeCompare(b.name)),
})).filter((group) => group.items.length > 0));
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
  if (value.includes("primitive") || value.includes("geometry") || value.includes("solid")) return "/icons/cad/shape.svg";
  if (value.includes("profile") || value.includes("sketch")) return "/icons/cad/rectangle.svg";
  if (value.includes("vector") || value.includes("transform") || value.includes("location")) return "/icons/cad/move.svg";
  if (value.includes("boolean") || value.includes("topology") || value.includes("shape") || value.includes("operation")) return "/icons/cad/union.svg";
  if (value.includes("math") || value.includes("constant") || value.includes("numeric")) return "/icons/cad/math.svg";
  if (value.includes("logic") || value.includes("compare")) return "/icons/cad/logic.svg";
  if (value.includes("string") || value.includes("text") || value.includes("python")) return "/icons/cad/code.svg";
  if (value.includes("value") || value.includes("convert")) return "/icons/cad/value.svg";
  return "/icons/cad/node.svg";
}

function nodeIcon(item: NodeDefinition): string {
  const name = item.name.toLowerCase();
  const id = item.id.toLowerCase();
  if (/^box$/.test(name)) return "/icons/cad/box.svg";
  if (/cylinder/.test(name)) return "/icons/cad/cylinder.svg";
  if (/sphere/.test(name)) return "/icons/cad/sphere.svg";
  if (/union|fuse/.test(name) || id.endsWith(".union")) return "/icons/cad/union.svg";
  if (/difference|subtract|cut/.test(name) || id.endsWith(".difference")) return "/icons/cad/difference.svg";
  if (/intersection|common/.test(name) || id.endsWith(".intersection")) return "/icons/cad/intersection.svg";
  if (/extrude/.test(name)) return "/icons/cad/extrude.svg";
  if (/revolve/.test(name)) return "/icons/cad/revolve.svg";
  if (/fillet/.test(name)) return "/icons/cad/fillet.svg";
  if (/chamfer/.test(name)) return "/icons/cad/chamfer.svg";
  if (/rectangle|square/.test(name)) return "/icons/cad/rectangle.svg";
  if (/circle/.test(name)) return "/icons/cad/circle.svg";
  if (/ellipse/.test(name)) return "/icons/cad/ellipse.svg";
  if (/polygon/.test(name)) return "/icons/cad/polygon.svg";
  if (/move|translate/.test(name)) return "/icons/cad/move.svg";
  if (/rotate/.test(name)) return "/icons/cad/rotate.svg";
  if (/mirror/.test(name)) return "/icons/cad/mirror.svg";
  if (/scale/.test(name)) return "/icons/cad/scale.svg";
  if (item.kind === "geometry") return "/icons/cad/shape.svg";
  if (item.kind === "value") return "/icons/cad/value.svg";
  if (item.category === "Math") return "/icons/cad/math.svg";
  if (item.category === "Logic") return "/icons/cad/logic.svg";
  if (item.category === "Python") return "/icons/cad/code.svg";
  return "/icons/cad/node.svg";
}

onMounted(() => {
  // Paint a usable graph and a small palette before downloading Pyodide.
  addStarterGraph();
  void runtime.initialize((message) => (status.value = message)).then(() => {
    const discovered = runtime.getCatalog();
    const byName = new Map(discovered.map((item) => [item.name, item]));
    catalog.value = discovered;
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
    { id: `e${nextId++}`, source: left.id, sourceHandle: "result", target: combine.id, targetHandle: "a" },
    { id: `e${nextId++}`, source: right.id, sourceHandle: "result", target: combine.id, targetHandle: "b" },
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
  }).catch((error) => {
    console.warn("Full build123d catalog discovery failed", error);
  }).finally(() => {
    fullCatalogLoading = false;
  });
}

function openPalette(category: string, event: MouseEvent) {
  void ensureFullCatalog();
  paletteSearch.value = "";
  expandedGroups.value = [category];
  palettePopover.value?.show(event);
}

watch(paletteSearch, (value) => {
  if (value) expandedGroups.value = groupedCatalog.value.map((group) => group.category);
});

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

function isValidConnection(connection: Connection) {
  return canConnect(graph.value, connection);
}

function onConnect(connection: Connection) {
  if (!isValidConnection(connection)) return;
  addEdges([{ ...connection, id: `e${Date.now()}`, animated: false } as Edge]);
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
    <Dock :model="categoryItems" position="top" class="category-dock">
      <template #item="{ item }">
        <Button text rounded severity="secondary" :aria-label="typeof item.label === 'string' ? item.label : 'Node category'" :title="typeof item.label === 'string' ? item.label : 'Node category'" @click="openPalette(item.category, $event)">
          <template #icon><img class="dock-category-icon" :src="item.icon" alt="" /></template>
        </Button>
      </template>
    </Dock>

    <Splitter class="workspace" :gutter-size="4">
      <SplitterPanel :size="68" :min-size="35" class="graph-panel">
        <div ref="canvasSurface" class="canvas-surface" @drop="onDrop" @dragover.prevent @contextmenu.prevent="openContextMenu">
          <VueFlow
            v-model:nodes="nodes"
            v-model:edges="edges"
            :node-types="nodeTypes"
            :is-valid-connection="isValidConnection"
            fit-view-on-init
            :default-viewport="{ zoom: 0.85, x: 60, y: 60 }"
            @connect="onConnect"
            @nodes-change="scheduleEvaluation"
            @edges-change="scheduleEvaluation"
          >
            <Background pattern-color="#39352e" :gap="22" />
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

    <Popover ref="palettePopover" class="palette-popover">
      <div class="palette-content">
        <InputText v-model="paletteSearch" placeholder="Search nodes…" class="palette-search" />
        <Accordion v-model:value="expandedGroups" multiple class="palette-accordion">
          <AccordionPanel v-for="group in groupedCatalog" :key="group.category" :value="group.category">
            <AccordionHeader>
              <span class="palette-group-title"><img class="palette-group-icon" :src="categoryIcon(group.category)" alt="" />{{ group.category }}</span>
              <span class="palette-group-count">{{ group.items.length }}</span>
            </AccordionHeader>
            <AccordionContent>
              <div class="palette-node-list">
                <Button
                  v-for="item in group.items"
                  :key="item.id"
                  text
                  severity="secondary"
                  class="palette-node"
                  draggable="true"
                  @dragstart="dragNode($event, item)"
                  @click="addDefinition(item)"
                >
                  <template #icon>
                    <img class="palette-node-icon" :src="nodeIcon(item)" alt="" />
                  </template>
                  <template #default><span class="palette-node-label">{{ item.name }}</span></template>
                </Button>
              </div>
            </AccordionContent>
          </AccordionPanel>
        </Accordion>
        <div v-if="!groupedCatalog.length" class="palette-empty">
          <ProgressSpinner v-if="!catalog.length && !errors.length" style="width:20px;height:20px" stroke-width="4" />
          <span v-else>{{ errors.length ? "Python runtime failed" : "No matching nodes" }}</span>
        </div>
      </div>
    </Popover>

    <Popover ref="contextPopover" class="context-popover">
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
