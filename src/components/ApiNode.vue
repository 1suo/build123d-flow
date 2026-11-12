<script setup lang="ts">
import { computed, ref } from "vue";
import { Handle, Position } from "@vue-flow/core";
import Button from "primevue/button";
import Card from "primevue/card";
import Popover from "primevue/popover";
import type { NodeDefinition } from "../types";
import { isGeometrySocket, socketColor, socketTypeLabel } from "../runtime/socketPresentation";

const props = defineProps<{
  id: string;
  data: {
    definition: NodeDefinition;
    params: Record<string, unknown>;
    visible?: boolean;
    collapsed?: boolean;
    icon?: string;
    error?: string;
    onParameter?: (nodeId: string, name: string, value: unknown) => void;
    onVisibility?: (nodeId: string, visible: boolean) => void;
    onCollapse?: (nodeId: string, collapsed: boolean) => void;
    onInspect?: (nodeId: string, socketName: string, direction: 'input' | 'output') => void;
  };
}>();

const inputs = computed(() => props.data.definition.inputs);
const outputs = computed(() => props.data.definition.outputs);
const errorPopover = ref<any>();
const canPreview = computed(() => outputs.value.some(isGeometrySocket));
function socketTitle(socket: any, direction: string): string {
  return direction + ": " + socket.name + " · " + socketTypeLabel(socket) + (socket.annotation ? " | " + socket.annotation : "");
}

function toggleVisibility() {
  props.data.onVisibility?.(props.id, !props.data.visible);
}

function inspect(name: string, direction: 'input' | 'output') { props.data.onInspect?.(props.id, name, direction); }

function toggleCollapse() {
  props.data.onCollapse?.(props.id, !props.data.collapsed);
}

</script>

<template>
  <Card class="api-node" :class="{ 'has-error': data.error, 'is-collapsed': data.collapsed }">
    <template #title>
      <div v-if="data.collapsed" class="node-header collapsed-node-header">
        <Button icon="pi pi-chevron-right" text rounded size="small" severity="secondary"
          class="node-collapse" aria-label="Expand node" title="Expand node" @click.stop="toggleCollapse" />
        <div class="collapsed-socket-group collapsed-inputs" aria-label="Inputs">
          <span v-for="input in inputs" :key="input.name" class="collapsed-socket" :title="socketTitle(input, 'Input')">
            <Handle :id="input.name" type="target" :position="Position.Top" class="socket-handle" :style="{ '--socket-color': socketColor(input) }" />
          </span>
        </div>
        <div class="collapsed-node-title">
          
          <strong>{{ data.definition.name }}</strong>
          <Button v-if="data.definition.description" icon="pi pi-info-circle" text rounded size="small"
            severity="secondary" class="node-description-trigger nodrag nopan"
            v-tooltip.top="data.definition.description"
            :aria-label="`Description: ${data.definition.name}`" />
        </div>
        <div class="collapsed-socket-group collapsed-outputs" aria-label="Outputs">
          <span v-for="output in outputs" :key="output.name" class="collapsed-socket" :title="socketTitle(output, 'Output')">
            <Handle :id="output.name" type="source" :position="Position.Bottom" class="socket-handle" :style="{ '--socket-color': socketColor(output) }" />
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
        <div v-for="input in inputs" :key="input.name" class="socket-row input-row">
          <Handle :id="input.name" type="target" :position="Position.Left" class="socket-handle"
            :title="socketTitle(input, 'Input')" :style="{ '--socket-color': socketColor(input) }" />
          <label :title="input.annotation" @click.stop="inspect(input.name, 'input')">{{ input.label || input.name }} <small>{{ socketTypeLabel(input) }}</small></label>
        </div>
        <div v-for="output in outputs" :key="output.name" class="socket-row output-row">
          <span class="output-socket-inspect" :title="`Inspect ${output.name}`"
            @click.stop="inspect(output.name, 'output')">{{ output.name }} <small>{{ socketTypeLabel(output) }}</small></span>
          <Handle :id="output.name" type="source" :position="Position.Right" class="socket-handle"
            :title="socketTitle(output, 'Output')" :style="{ '--socket-color': socketColor(output) }" />
        </div>
      </div>
    </template>
  </Card>
</template>
