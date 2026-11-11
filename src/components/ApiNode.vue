<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { Handle, Position, useVueFlow } from "@vue-flow/core";
import InputNumber from "primevue/inputnumber";
import InputText from "primevue/inputtext";
import Checkbox from "primevue/checkbox";
import Textarea from "primevue/textarea";
import Select from "primevue/select";
import Button from "primevue/button";
import Card from "primevue/card";
import Popover from "primevue/popover";
import type { NodeDefinition } from "../types";

const props = defineProps<{
  id: string;
  data: {
    definition: NodeDefinition;
    params: Record<string, unknown>;
    visible?: boolean;
    collapsed?: boolean;
    error?: string;
    onParameter?: (nodeId: string, name: string, value: unknown) => void;
    onVisibility?: (nodeId: string, visible: boolean) => void;
    onCollapse?: (nodeId: string, collapsed: boolean) => void;
    onInspect?: (nodeId: string, socketName: string, direction: 'input' | 'output') => void;
  };
}>();

const { getEdges } = useVueFlow();
const inputs = computed(() => props.data.definition.inputs);
const outputs = computed(() => props.data.definition.outputs);
const structuredDrafts = reactive<Record<string, string>>({});
const errorPopover = ref<any>();
const valuePopover = ref<any>();
const activeStructuredName = ref("");
const canPreview = computed(() => outputs.value.some((output) => ["Shape", "Solid", "Compound", "Face", "Wire", "Edge"].includes(output.type)));
const connectedInputs = computed(() => new Set(getEdges().filter((edge) => edge.target === props.id && edge.targetHandle).map((edge) => String(edge.targetHandle))));
function isConnected(name: string): boolean { return connectedInputs.value.has(name); }
function socketColor(type: string): string {
  if (["Shape","Solid","Compound","Face","Edge","Wire","Shell","Part","Sketch"].includes(type)) return "#7CFC00";
  if (["Number","Boolean","Vector2","Vector3","Rotation","Plane","Location","Axis","Color"].includes(type)) return "#DAA520";
  if (["List","Tuple","Set","Dict"].includes(type)) return "#BDB76B";
  if (["String","Code","Enum"].includes(type)) return "#458b74";
  return "#F5DEB3";
}
function socketTitle(socket: { name: string; type: string; annotation?: string }, direction: string): string {
  return direction + ": " + socket.name + " · " + socket.type + (socket.annotation ? " | " + socket.annotation : "");
}

function toggleVisibility() {
  props.data.onVisibility?.(props.id, !props.data.visible);
}

function inspect(name: string, direction: 'input' | 'output') { props.data.onInspect?.(props.id, name, direction); }

function toggleCollapse() {
  props.data.onCollapse?.(props.id, !props.data.collapsed);
}

function update(name: string, value: unknown) {
  props.data.onParameter?.(props.id, name, value === "" ? undefined : value);
}

function structuredValue(name: string): string {
  if (name in structuredDrafts) return structuredDrafts[name];
  const value = props.data.params[name] ?? inputs.value.find((input) => input.name === name)?.default;
  if (value === undefined || value === null) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function structuredSummary(name: string): string {
  const value = structuredValue(name);
  return value.replace(/\s+/g, " ").trim() || "Default";
}

function openStructuredEditor(name: string, event: MouseEvent) {
  activeStructuredName.value = name;
  structuredDrafts[name] = structuredValue(name);
  valuePopover.value?.show(event);
}

function updateStructured(value: string) {
  const name = activeStructuredName.value;
  if (!name) return;
  structuredDrafts[name] = value;
  if (!value.trim()) {
    update(name, undefined);
    return;
  }
  try {
    update(name, JSON.parse(value));
  } catch {
    // Keep incomplete JSON in the popup without sending it to graph evaluation.
  }
}
</script>

<template>
  <Card class="api-node" :class="{ 'has-error': data.error, 'is-collapsed': data.collapsed }">
    <template #title>
      <div v-if="data.collapsed" class="node-header collapsed-node-header">
        <Button icon="pi pi-chevron-right" text rounded size="small" severity="secondary"
          class="node-collapse" aria-label="Expand node" title="Expand node" @click.stop="toggleCollapse" />
        <div class="collapsed-socket-group collapsed-inputs">
          <span v-for="input in inputs" :key="input.name" class="collapsed-socket" :title="input.name + ' · ' + input.type">
            <Handle :id="input.name" type="target" :position="Position.Left" class="socket-handle" :style="{ '--socket-color': socketColor(input.type) }" />
          </span>
        </div>
        <div class="collapsed-node-title">
          <strong>{{ data.definition.name }}</strong>
          <Button v-if="data.definition.description" icon="pi pi-info-circle" text rounded size="small"
            severity="secondary" class="node-description-trigger nodrag nopan"
            v-tooltip.top="data.definition.description"
            :aria-label="`Description: ${data.definition.name}`" />
        </div>
        <div class="collapsed-socket-group collapsed-outputs">
          <span v-for="output in outputs" :key="output.name" class="collapsed-socket" :title="output.name + ' · ' + output.type">
            <Handle :id="output.name" type="source" :position="Position.Right" class="socket-handle" :style="{ '--socket-color': socketColor(output.type) }" />
          </span>
        </div>
      </div>
      <div v-else class="node-header">
        <Button icon="pi pi-chevron-down" text rounded size="small" severity="secondary"
          class="node-collapse" aria-label="Collapse node" title="Collapse node" @click.stop="toggleCollapse" />
        <div class="node-heading">
          <span class="node-category">{{ data.definition.category }} · {{ data.definition.kind }}</span>
          <strong>{{ data.definition.name }}</strong>
        </div>
        <Button v-if="data.definition.description" icon="pi pi-info-circle" text rounded size="small"
          severity="secondary" class="node-description-trigger nodrag nopan"
          v-tooltip.top="data.definition.description" :aria-label="`Description: ${data.definition.name}`" />
        <Button v-if="data.error" icon="pi pi-exclamation-circle" text rounded size="small" severity="danger"
          class="node-error-trigger" aria-label="Show node error" title="Show node error"
          @click.stop="errorPopover?.toggle($event)" />
        <Popover ref="errorPopover" append-to="body" class="node-error-popover">
          <div class="node-error-content">{{ data.error }}</div>
        </Popover>
        <Button v-if="canPreview" :icon="data.visible ? 'pi pi-eye' : 'pi pi-eye-slash'"
          text rounded size="small" severity="secondary" class="node-visibility"
          :class="{ 'is-visible': data.visible }" :aria-label="data.visible ? 'Hide in viewer' : 'Show in viewer'"
          :title="data.visible ? 'Hide in viewer' : 'Show in viewer'" @click.stop="toggleVisibility" />
      </div>
    </template>
    <template #content>
      <div v-if="!data.collapsed" class="node-body">
        <div v-for="input in inputs" :key="input.name" class="socket-row input-row" :class="{ 'is-connected': isConnected(input.name) }">
          <Handle :id="input.name" type="target" :position="Position.Left" />
          <label :title="socketTitle(input, 'Input')" @click.stop="inspect(input.name, 'input')">{{ input.label || input.name }}</label>
          <InputNumber v-if="input.type === 'Number'"
            :model-value="(data.params[input.name] ?? input.default ?? null) as number | null"
            :use-grouping="false"  :max-fraction-digits="8" input-class="node-number" :disabled="isConnected(input.name)"
            @update:model-value="update(input.name, $event)" />
          <Checkbox v-else-if="input.type === 'Boolean'"
            :model-value="Boolean(data.params[input.name] ?? input.default)" binary :disabled="isConnected(input.name)"
            @update:model-value="update(input.name, $event)" />
          <Textarea v-else-if="input.type === 'Code'"
            :model-value="String(data.params[input.name] ?? input.default ?? '')"
            :auto-resize="false" rows="5" spellcheck="false" class="node-code" :disabled="isConnected(input.name)"
            @update:model-value="update(input.name, $event)" />
          <InputText v-else-if="input.type === 'String'"
            :model-value="String(data.params[input.name] ?? input.default ?? '')" class="node-text" :disabled="isConnected(input.name)"
            @update:model-value="update(input.name, $event)" />
          <Select v-else-if="input.type === 'Enum'"
            :model-value="String(data.params[input.name] ?? input.default ?? '')"
            :options="input.options || []" placeholder="Default" class="node-select" :disabled="isConnected(input.name)"
            @update:model-value="update(input.name, $event)" />
          <Button v-else-if="['Vector3', 'Vector2', 'Rotation', 'Plane', 'Location', 'Axis', 'Color', 'List', 'Tuple', 'Set', 'Dict', 'Any'].includes(input.type)"
            class="node-structured-trigger" severity="secondary" outlined
            :label="structuredSummary(input.name)" :title="structuredValue(input.name)" :disabled="isConnected(input.name)"
            @click.stop="openStructuredEditor(input.name, $event)" />
          <span v-else class="socket-type-hint" :title="socketTitle(input, 'Input')">{{ input.type }}</span>
        </div>
        <div v-for="output in outputs" :key="output.name" class="socket-row output-row">
          <span class="output-socket-inspect" :title="`Inspect ${output.name}`"
            @click.stop="inspect(output.name, 'output')">{{ output.name }} <small>{{ output.type }}</small></span>
          <Handle :id="output.name" type="source" :position="Position.Right" />
        </div>
      </div>
      <Popover v-if="!data.collapsed" ref="valuePopover" append-to="body" class="node-value-popover">
        <div class="node-value-editor">
          <strong>{{ activeStructuredName }}</strong>
          <Textarea :model-value="structuredDrafts[activeStructuredName] || ''"
            :auto-resize="false" rows="7" spellcheck="false" class="node-value-textarea"
            @update:model-value="updateStructured" />
        </div>
      </Popover>
    </template>
  </Card>
</template>
