<script setup lang="ts">
import { computed } from "vue";
import { Handle, Position } from "@vue-flow/core";
import InputNumber from "primevue/inputnumber";
import InputText from "primevue/inputtext";
import Checkbox from "primevue/checkbox";
import Textarea from "primevue/textarea";
import Select from "primevue/select";
import Button from "primevue/button";
import type { NodeDefinition } from "../types";

const props = defineProps<{
  id: string;
  data: { definition: NodeDefinition; params: Record<string, unknown>; visible?: boolean; error?: string; onParameter?: (nodeId: string, name: string, value: unknown) => void; onVisibility?: (nodeId: string, visible: boolean) => void };
}>();

const inputs = computed(() => props.data.definition.inputs);
const outputs = computed(() => props.data.definition.outputs);
const canPreview = computed(() => outputs.value.some((output) => ["Shape", "Solid", "Compound", "Face", "Wire", "Edge"].includes(output.type)));

function toggleVisibility() {
  props.data.onVisibility?.(props.id, !props.data.visible);
}

function update(name: string, value: unknown) {
  props.data.onParameter?.(props.id, name, value === "" ? undefined : value);
}
</script>

<template>
  <article class="api-node" :class="{ 'has-error': data.error }">
    <header class="node-header">
      <div class="node-heading">
        <span class="node-category">{{ data.definition.category }} · {{ data.definition.kind }}</span>
        <strong>{{ data.definition.name }}</strong>
      </div>
      <Button
        v-if="canPreview"
        :icon="data.visible ? 'pi pi-eye' : 'pi pi-eye-slash'"
        text
        rounded
        size="small"
        severity="secondary"
        class="node-visibility"
        :class="{ 'is-visible': data.visible }"
        :aria-label="data.visible ? 'Hide in viewer' : 'Show in viewer'"
        :title="data.visible ? 'Hide in viewer' : 'Show in viewer'"
        @click.stop="toggleVisibility"
      />
    </header>
    <p v-if="data.definition.description" class="node-description">{{ data.definition.description }}</p>
    <div class="node-body">
      <div v-for="input in inputs" :key="input.name" class="socket-row input-row">
        <Handle :id="input.name" type="target" :position="Position.Left" />
        <label :title="input.annotation">{{ input.label || input.name }}</label>
        <InputNumber
          v-if="input.type === 'Number'"
          :model-value="Number(data.params[input.name] ?? input.default ?? 0)"
          :use-grouping="false"
          :max-fraction-digits="8"
          input-class="node-number"
          @update:model-value="update(input.name, $event)"
        />
        <Checkbox
          v-else-if="input.type === 'Boolean'"
          :model-value="Boolean(data.params[input.name] ?? input.default)"
          binary
          @update:model-value="update(input.name, $event)"
        />
        <Textarea
          v-else-if="input.type === 'Code'"
          :model-value="String(data.params[input.name] ?? input.default ?? '')"
          :auto-resize="false"
          rows="5"
          spellcheck="false"
          class="node-code"
          @update:model-value="update(input.name, $event)"
        />
        <InputText
          v-else-if="input.type === 'String'"
          :model-value="String(data.params[input.name] ?? input.default ?? '')"
          class="node-text"
          @update:model-value="update(input.name, $event)"
        />
        <Select
          v-else-if="input.type === 'Enum'"
          :model-value="String(data.params[input.name] ?? input.default ?? '')"
          :options="input.options || []"
          placeholder="Default"
          class="node-select"
          @update:model-value="update(input.name, $event)"
        />
        <span v-else class="socket-type-hint">{{ input.type }}</span>
      </div>
      <div v-for="output in outputs" :key="output.name" class="socket-row output-row">
        <span>{{ output.name }} <small>{{ output.type }}</small></span>
        <Handle :id="output.name" type="source" :position="Position.Right" />
      </div>
    </div>
    <footer v-if="data.error" class="node-error">{{ data.error }}</footer>
  </article>
</template>
