<script setup lang="ts">
import { reactive, ref } from "vue";
import InputNumber from "primevue/inputnumber";
import InputText from "primevue/inputtext";
import Checkbox from "primevue/checkbox";
import Textarea from "primevue/textarea";
import Select from "primevue/select";
import Button from "primevue/button";
import Dialog from "primevue/dialog";
import DataValue from "./DataValue.vue";

const props = defineProps<{
  node: any;
  inputs: any[];
  outputs: any[];
  onParameter: (nodeId: string, name: string, value: unknown) => void;
}>();
const emit = defineEmits<{ close: [] }>();
const drafts = reactive<Record<string, string>>({});
const activeName = ref("");
const editorOpen = ref(false);

function socketTypeLabel(socket: any): string {
  const structure = socket.data_structure;
  if (!structure || structure === "item") return socket.type;
  if (structure === "dict") return `Dict[${socket.key_type || "Any"}, ${socket.value_type || "Any"}]`;
  const itemType = socket.item_type || socket.type;
  const label = structure === "tree" ? "Tree" : structure[0].toUpperCase() + structure.slice(1);
  return `${label}[${itemType}]`;
}
function currentValue(socket: any): unknown {
  return props.node.data.params[socket.name] ?? socket.default ?? null;
}
function textValue(socket: any): string {
  const value = currentValue(socket);
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
function update(socket: any, value: unknown) {
  props.onParameter(props.node.id, socket.name, value === "" ? undefined : value);
}
function openStructured(socket: any) {
  activeName.value = socket.name;
  drafts[socket.name] = textValue(socket);
  editorOpen.value = true;
}
function updateStructured(value: string) {
  drafts[activeName.value] = value;
  if (!value.trim()) {
    props.onParameter(props.node.id, activeName.value, undefined);
    return;
  }
  try { props.onParameter(props.node.id, activeName.value, JSON.parse(value)); }
  catch { /* keep incomplete JSON in the editor until it parses */ }
}
</script>

<template>
  <div class="canvas-inspector-header">
    <div><small>NODE INSPECTOR</small><strong>{{ node.data.definition.name }}</strong></div>
    <Button icon="pi pi-times" text rounded severity="secondary" size="small" aria-label="Close inspector" @click="emit('close')" />
  </div>
  <div class="canvas-inspector-body">
    <section>
      <h4>Inputs</h4>
      <div v-for="socket in inputs" :key="socket.name" class="inspector-socket" :class="{ 'is-linked': socket.connected }">
        <div class="inspector-socket-heading">
          <strong>{{ socket.label || socket.name }}</strong>
          <small>{{ socketTypeLabel(socket) }}{{ socket.connected ? ' · linked' : '' }}</small>
        </div>
        <DataValue v-if="socket.connected" :label="socket.name" :value="socket.value ?? 'Waiting for value'" />
        <div v-else class="inspector-input-control">
          <InputNumber v-if="socket.type === 'Number'" :model-value="currentValue(socket) as number | null"
            :use-grouping="false" :max-fraction-digits="8" @update:model-value="update(socket, $event)" />
          <Checkbox v-else-if="socket.type === 'Boolean'" :model-value="Boolean(currentValue(socket))" binary
            @update:model-value="update(socket, $event)" />
          <Button v-else-if="socket.type === 'Code'" class="node-structured-trigger" severity="secondary" outlined
            :label="String(currentValue(socket) ?? '').replace(/\s+/g, ' ').trim() || 'Edit code…'"
            @click="openStructured(socket)" />
          <InputText v-else-if="socket.type === 'String'" :model-value="String(currentValue(socket) ?? '')"
            @update:model-value="update(socket, $event)" />
          <Select v-else-if="socket.type === 'Enum'" :model-value="String(currentValue(socket) ?? '')"
            :options="socket.options || []" placeholder="Default" @update:model-value="update(socket, $event)" />
          <Button v-else class="node-structured-trigger" severity="secondary" outlined
            :label="textValue(socket).replace(/\s+/g, ' ').trim() || 'Set value'"
            @click="openStructured(socket)" />
        </div>
        <div v-if="!socket.connected && socket.annotation" class="inspector-socket-annotation">{{ socket.annotation }}</div>
      </div>
    </section>
    <section>
      <h4>Outputs</h4>
      <div v-for="socket in outputs" :key="socket.name" class="inspector-socket">
        <div class="inspector-socket-heading"><strong>{{ socket.name }}</strong><small>{{ socketTypeLabel(socket) }}</small></div>
        <DataValue :label="socket.name" :value="socket.value ?? 'Not evaluated'" />
      </div>
    </section>
  </div>
  <Dialog v-model:visible="editorOpen" modal :header="'Edit ' + activeName"
    :style="{ width: 'min(680px, calc(100vw - 32px))' }" :draggable="false">
    <Textarea class="inspector-code-editor" :model-value="drafts[activeName] || ''"
      :auto-resize="false" rows="14" spellcheck="false"
      @update:model-value="updateStructured" />
  </Dialog>
</template>
