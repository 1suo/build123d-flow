<script setup lang="ts">
import { computed, markRaw, onMounted, ref, shallowRef, watch } from "vue";
import { VueFlow, useVueFlow, type Connection, type Edge, type Node } from "@vue-flow/core";
import { Background } from "@vue-flow/background";
import { Controls } from "@vue-flow/controls";
import { MiniMap } from "@vue-flow/minimap";
import ApiNode from "./components/ApiNode.vue";
import GeometryViewport from "./components/GeometryViewport.vue";
import { PythonRuntime } from "./runtime/pythonRuntime";
import { createGraphNode, validateGraph } from "./runtime/graph";
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
  if (!box) return;
  addDefinition(box, { x: 180, y: 120 });
  fitView({ padding: 0.25 });
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

function onConnect(connection: Connection) {
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
      <div class="brand"><span class="brand-mark">b3d</span><strong>build123d-flow</strong></div>
      <div class="runtime-status"><span class="status-dot" :class="{ ready: runtime.isReady }"></span>{{ status }}</div>
      <div class="top-actions">
        <span v-if="running" class="running-label">Evaluating…</span>
        <button class="secondary-button" :disabled="!runtime.isReady" @click="evaluate">Evaluate</button>
        <button class="primary-button" :disabled="!runtime.isReady" @click="downloadStep">Export STEP</button>
      </div>
    </header>

    <section class="workspace">
      <aside class="catalog-panel">
        <div class="panel-heading"><strong>Build123d API</strong><span>{{ catalog.length }}</span></div>
        <input v-model="search" class="search-input" placeholder="Search functions and classes…" />
        <select v-model="selectedCategory" class="category-select">
          <option v-for="category in categories" :key="category">{{ category }}</option>
        </select>
        <div class="catalog-list">
          <button
            v-for="item in filteredCatalog"
            :key="item.id"
            class="catalog-item"
            draggable="true"
            @dragstart="dragNode($event, item)"
            @dblclick="addDefinition(item)"
          >
            <span class="catalog-name">{{ item.name }}</span>
            <span class="catalog-category">{{ item.category }}</span>
          </button>
          <p v-if="!filteredCatalog.length" class="empty-state">No matching API entries.</p>
        </div>
        <p class="catalog-hint">Drag a node onto the canvas, or double-click to add it.</p>
      </aside>

      <section class="graph-panel" @drop="onDrop" @dragover.prevent>
        <div class="canvas-label">NODE GRAPH</div>
        <VueFlow
          v-model:nodes="nodes"
          v-model:edges="edges"
          :node-types="nodeTypes"
          fit-view-on-init
          :default-viewport="{ zoom: 0.85, x: 60, y: 60 }"
          @connect="onConnect"
          @nodes-change="scheduleEvaluation"
          @edges-change="scheduleEvaluation"
        >
          <Background pattern-color="#30343b" :gap="20" />
          <MiniMap pannable zoomable />
          <Controls />
        </VueFlow>
      </section>

      <aside class="preview-panel">
        <div class="panel-heading"><strong>Geometry preview</strong><span>{{ Object.keys(previews).length }} meshes</span></div>
        <GeometryViewport :previews="previews" />
        <div v-for="(mesh, id) in previews" :key="id" class="mesh-summary">
          <strong>{{ id }}</strong>
          <span>{{ mesh.vertices.length / 3 }} vertices · {{ mesh.triangles.length / 3 }} triangles</span>
        </div>
        <div v-if="errors.length" class="error-list">
          <strong>Evaluation errors</strong>
          <pre v-for="(error, index) in errors" :key="index">{{ error }}</pre>
        </div>
      </aside>
    </section>
    <footer class="statusbar">Python + Pyodide · build123d / OCP.wasm · Vue Flow</footer>
  </main>
</template>