<script setup lang="ts">
import { computed, markRaw, onMounted, ref, shallowRef, watch } from "vue";
import { VueFlow, useVueFlow, type Connection, type Edge, type Node } from "@vue-flow/core";
import { Background } from "@vue-flow/background";
import { Controls } from "@vue-flow/controls";
import { MiniMap } from "@vue-flow/minimap";
import ApiNode from "./components/ApiNode.vue";
import GeometryViewport from "./components/GeometryViewport.vue";
import Button from "primevue/button";
import InputText from "primevue/inputtext";
import Select from "primevue/select";
import Splitter from "primevue/splitter";
import SplitterPanel from "primevue/splitterpanel";
import { PythonRuntime } from "./runtime/pythonRuntime";
import { canConnect, createGraphNode, validateGraph } from "./runtime/graph";
import type { GraphDocument, NodeDefinition, EvaluationResult, MeshPreview } from "./types";

const runtime = new PythonRuntime();
const status = ref("Not initialized");
const catalog = ref<NodeDefinition[]>([]);
const search = ref("");
const selectedCategory = ref("All");
const errors = ref<string[]>([]);
const previews = shallowRef<Record<string, MeshPreview>>({});
const running = ref(false);
const nodes = ref<Node[]>([]);
const edges = ref<Edge[]>([]);
const nodeTypes = { api: markRaw(ApiNode) };
const { addNodes, addEdges, screenToFlowCoordinate, fitView } = useVueFlow();

let nextId = 1;
let evaluationRevision = 0;
let evaluationTimer: ReturnType<typeof setTimeout> | undefined;

const categories = computed(() => ["All", ...new Set(catalog.value.map((n) => n.category))]);
const filteredCatalog = computed(() => catalog.value.filter((item) =>
  (selectedCategory.value === "All" || item.category === selectedCategory.value) &&
  (item.name + " " + item.id).toLowerCase().includes(search.value.toLowerCase())
));

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

onMounted(async () => {
  try {
    await runtime.initialize((message) => (status.value = message));
    catalog.value = runtime.getCatalog();
    status.value += ` · ${catalog.value.length} nodes discovered`;
    addStarterGraph();
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
  const point = position ?? { x: 180 + Math.random() * 120, y: 100 + Math.random() * 120 };
  const node = createGraphNode(definition, id, point);
  node.data.onParameter = onParameter;
  addNodes([node as unknown as Node]);
  scheduleEvaluation();
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
    for (const node of nodes.value as any[]) {
      node.data.error = result.errors[node.id] ?? "";
    }
  } catch (error) {
    if (revision === evaluationRevision) errors.value = [String(error)];
  } finally {
    if (revision === evaluationRevision) running.value = false;
  }
}

function dragNode(event: DragEvent, definition: NodeDefinition) {
  event.dataTransfer?.setData("application/build123d-node", definition.id);
  if (event.dataTransfer) event.dataTransfer.effectAllowed = "copy";
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
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark">b3d</span>
        <strong>build123d-flow</strong>
        <span class="brand-divider"></span>
        <span class="document-name">Untitled</span>
      </div>
      <div class="runtime-status">
        <span class="status-dot" :class="{ ready: runtime.isReady }"></span>
        <span>{{ status }}</span>
      </div>
      <div class="top-actions">
        <span v-if="running" class="running-label">Evaluating…</span>
        <Button label="Evaluate" icon="pi pi-play" size="small" severity="secondary" :disabled="!runtime.isReady" @click="evaluate" />
        <Button label="Export STEP" icon="pi pi-download" size="small" :disabled="!runtime.isReady" @click="downloadStep" />
      </div>
    </header>

    <Splitter class="workspace" :gutterSize="5">
      <SplitterPanel :size="19" :minSize="14" class="library-panel">
        <div class="panel-heading">
          <div><span class="eyebrow">BUILD</span><strong>Node library</strong></div>
          <span class="panel-count">{{ catalog.length }}</span>
        </div>
        <div class="library-tools">
          <InputText v-model="search" placeholder="Find a node…" size="small" class="search-input" />
          <Select v-model="selectedCategory" :options="categories" size="small" class="category-select" />
        </div>
        <div class="catalog-list">
          <button
            v-for="item in filteredCatalog"
            :key="item.id"
            class="catalog-item"
            draggable="true"
            @dragstart="dragNode($event, item)"
            @dblclick="addDefinition(item)"
          >
            <span class="catalog-item-name">{{ item.name }}</span>
            <span class="catalog-item-category">{{ item.category }}</span>
          </button>
          <p v-if="!filteredCatalog.length" class="empty-state">No matching nodes.</p>
        </div>
        <div class="panel-footnote">Drag onto canvas · double-click to add</div>
      </SplitterPanel>

      <SplitterPanel :size="81" :minSize="35" class="main-panel">
        <Splitter class="main-splitter" :gutterSize="5">
          <SplitterPanel :size="72" :minSize="40" class="graph-panel">
            <div class="canvas-toolbar">
              <span class="eyebrow">WORKSPACE</span>
              <strong>Graph</strong>
              <span class="canvas-hint">Pan · zoom · connect sockets</span>
            </div>
            <div class="canvas-surface" @drop="onDrop" @dragover.prevent>
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
                <Controls />
              </VueFlow>
            </div>
          </SplitterPanel>

          <SplitterPanel :size="28" :minSize="18" class="preview-panel">
            <div class="panel-heading preview-heading">
              <div><span class="eyebrow">OUTPUT</span><strong>Geometry preview</strong></div>
              <span class="panel-count">{{ Object.keys(previews).length }}</span>
            </div>
            <GeometryViewport :previews="previews" />
            <div class="preview-details">
              <div v-for="(mesh, id) in previews" :key="id" class="mesh-summary">
                <strong>{{ id }}</strong>
                <span>{{ mesh.vertices.length / 3 }} vertices · {{ mesh.triangles.length / 3 }} triangles</span>
              </div>
              <div v-if="!Object.keys(previews).length && !errors.length" class="preview-placeholder">
                <span class="placeholder-mark">◇</span>
                <span>Geometry output appears here</span>
              </div>
              <div v-if="errors.length" class="error-list">
                <strong>Evaluation errors</strong>
                <pre v-for="(error, index) in errors" :key="index">{{ error }}</pre>
              </div>
            </div>
          </SplitterPanel>
        </Splitter>
      </SplitterPanel>
    </Splitter>

    <footer class="statusbar">
      <span>PYTHON · PYODIDE · BUILD123D · OCCT</span>
      <span>{{ running ? "Evaluating graph…" : errors.length ? errors.length + " errors" : runtime.isReady ? "Ready" : "Initializing" }}</span>
    </footer>
  </main>
</template>