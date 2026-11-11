<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { Handle, Position } from "@vue-flow/core";
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

const inputs = computed(() => props.data.definition.inputs);
const outputs = computed(() => props.data.definition.outputs);
const structuredDrafts = reactive<Record<string, string>>({});
const errorPopover = ref<any>();
const valuePopover = ref<any>();
const activeStructuredName = ref("");
const canPreview = computed(() => outputs.value.some((output) => ["Shape", "Solid", "Compound", "Face", "Wire", "Edge"].includes(output.type)));

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
            <Handle :id="input.name" type="target" :position="Position.Left" />
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
            <Handle :id="output.name" type="source" :position="Position.Right" />
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
          v-tooltip.top="data.definition.description" :aria-label="`Description: ${data.definition.name}` />
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
        <div v-for="input in inputs" :key="input.name" class="socket-row input-row">
          <Handle :id="input.name" type="target" :position="Position.Left" />
          <label :title="input.annotation" @click.stop="inspect(input.name, 'input')">{{ input.label || input.name }}</label>
          <InputNumber v-if="input.type === 'Number'"
            :model-value="(data.params[input.name] ?? input.default ?? null) as number | null"
            :use-grouping="false" :max-fraction-digits="8" input-class="node-number"
            @update:model-value="update(input.name, $event)" />
          <Checkbox v-else-if="input.type === 'Boolean'"
            :model-value="Boolean(data.params[input.name] ?? input.default)" binary
            @update:model-value="update(input.name, $event)" />
          <Textarea v-else-if="input.type === 'Code'"
            :model-value="String(data.params[input.name] ?? input.default ?? '')"
            :auto-resize="false" rows="5" spellcheck="false" class="node-code"
            @update:model-value="update(input.name, $event)" />
          <InputText v-else-if="input.type === 'String'"
            :model-value="String(data.params[input.name] ?? input.default ?? '')" class="node-text"
            @update:model-value="update(input.name, $event)" />
          <Select v-else-if="input.type === 'Enum'"
            :model-value="String(data.params[input.name] ?? input.default ?? '')"
            :options="input.options || []" placeholder="Default" class="node-select"
            @update:model-value="update(input.name, $event)" />
          <Button v-else-if="['Vector3', 'Vector2', 'Rotation', 'Plane', 'Location', 'Axis', 'Color', 'List', 'Tuple', 'Set', 'Dict', 'Any'].includes(input.type)"
            class="node-structured-trigger" severity="secondary" outlined
            :label="structuredSummary(input.name)" :title="structuredValue(input.name)"
            @click.stop="openStructuredEditor(input.name, $event)" />
          <span v-else class="socket-type-hint">{{ input.type }}</span>
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
