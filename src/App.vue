<script setup lang="ts">
import { computed, markRaw, onMounted, ref, shallowRef, watch } from "vue";
import { VueFlow, useVueFlow, type Connection, type Edge, type Node } from "@vue-flow/core";
import { Background } from "@vue-flow/background";
import { MiniMap } from "@vue-flow/minimap";
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
import Splitter from "primevue/splitter";
import SplitterPanel from "primevue/splitterpanel";
import { PythonRuntime } from "./runtime/pythonRuntime";
import { canConnect, createGraphNode, validateGraph } from "./runtime/graph";
import type { GraphDocument, NodeDefinition, EvaluationResult, MeshPreview } from "./types";

const runtime = new PythonRuntime();
const status = ref("Loading Python runtime…");
const catalog = ref<NodeDefinition[]>([]);
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

const categories = computed(() => [...new Set(catalog.value.map((node) => node.category))].sort());
const categoryItems = computed(() => categories.value.map((category) => ({
  label: category,
  category,
  icon: categoryIcon(category),
})));
const groupedCatalog = computed(() => categories.value.map((category) => ({
  category,
  items: catalog.value.filter((item) =>
    item.category === category &&
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

function categoryIcon(category: string): string {
  const value = category.toLowerCase();
  if (value.includes("primitive") || value.includes("solid")) return "pi pi-box";
  if (value.includes("profile") || value.includes("sketch") || value.includes("face")) return "pi pi-stop";
  if (value.includes("vector") || value.includes("transform") || value.includes("location")) return "pi pi-arrows-alt";
  if (value.includes("boolean") || value.includes("topology") || value.includes("shape")) return "pi pi-sitemap";
  if (value.includes("math") || value.includes("constant") || value.includes("numeric")) return "pi pi-calculator";
  if (value.includes("logic") || value.includes("compare")) return "pi pi-sliders-h";
  if (value.includes("string") || value.includes("text")) return "pi pi-align-left";
  if (value.includes("python")) return "pi pi-code";
  if (value.includes("value") || value.includes("convert")) return "pi pi-hashtag";
  return "pi pi-cog";
}

function nodeIcon(item: NodeDefinition): string {
  if (item.kind === "geometry" || item.outputs.some((output) => ["Shape", "Solid", "Compound", "Face", "Wire", "Edge"].includes(output.type))) return "pi pi-box";
  if (item.kind === "value") return "pi pi-circle";
  return categoryIcon(item.category);
}

function isPreviewable(item: NodeDefinition): boolean {
  return item.outputs.some((output) => ["Shape", "Solid", "Compound", "Face", "Wire", "Edge"].includes(output.type));
}

onMounted(async () => {
  try {
    await runtime.initialize((message) => (status.value = message));
    catalog.value = runtime.getCatalog();
    addStarterGraph();
    status.value = "Ready";
  } catch (error) {
    status.value = "Initialization failed";
    errors.value = [error instanceof Error ? error.message : String(error)];
  }
});

function addStarterGraph() {
  const box = catalog.value.find((item) => item.name === "Box");
  const union = catalog.value.find((item) => item.id === "build123d_flow.union");
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

function openPalette(category: string, event: MouseEvent) {
  paletteSearch.value = "";
  expandedGroups.value = [category];
  palettePopover.value?.show(event);
}

watch(paletteSearch, (value) => {
  if (value) expandedGroups.value = groupedCatalog.value.map((group) => group.category);
});

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
    <Dock :model="categoryItems" position="top" class="category-dock">
      <template #item="{ item }">
        <Button :icon="item.icon" text rounded severity="secondary" :aria-label="item.label" :title="item.label" @click="openPalette(item.category, $event)" />
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
            <MiniMap pannable zoomable />
            <Dock :model="canvasActions" position="bottom" class="canvas-dock">
              <template #item="{ item }">
                <Button :icon="item.icon" text rounded severity="secondary" :aria-label="item.label" :title="item.label" @click="item.command?.()" />
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
              <span class="palette-group-title"><i :class="categoryIcon(group.category)"></i>{{ group.category }}</span>
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
                  :icon="nodeIcon(item)"
                  :label="item.name"
                  draggable="true"
                  @dragstart="dragNode($event, item)"
                  @click="addDefinition(item)"
                />
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
            :icon="nodeIcon(item)"
            @click="addFromContext(item)"
          >
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
