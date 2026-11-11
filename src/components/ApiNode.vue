<script setup lang="ts">
import { computed } from "vue";
import { Handle, Position } from "@vue-flow/core";
import type { NodeDefinition } from "../types";

const props = defineProps<{
  id: string;
  data: { definition: NodeDefinition; params: Record<string, unknown>; error?: string; onParameter?: (nodeId: string, name: string, value: unknown) => void };
}>();

const inputs = computed(() => props.data.definition.inputs);
const outputs = computed(() => props.data.definition.outputs);

function update(name: string, event: Event) {
  const target = event.target as HTMLInputElement;
  const old = inputs.value.find((input) => input.name === name);
  let value: unknown = target.value === "" ? undefined : target.value;
  if (old?.type === "Number" && value !== undefined) value = Number(target.value);
  if (old?.type === "Boolean") value = target.checked;
  props.data.onParameter?.(props.id, name, value);
}
</script>

<template>
  <article class="api-node" :class="{ 'has-error': data.error }">
    <header class="node-header">
      <span class="node-category">{{ data.definition.category }} · {{ data.definition.kind }}</span>
      <strong>{{ data.definition.name }}</strong>
    </header>
    <p v-if="data.definition.description" class="node-description">
      {{ data.definition.description }}
    </p>
    <div class="node-body">
      <div v-for="input in inputs" :key="input.name" class="socket-row input-row">
        <Handle :id="input.name" type="target" :position="Position.Left" />
        <label :title="input.annotation">{{ input.label || input.name }}</label>
        <input
          v-if="input.type === 'Number'"
          :value="data.params[input.name] ?? ''"
          type="number"
          step="any"
          @change="update(input.name, $event)"
        />
        <input
          v-else-if="input.type === 'Boolean'"
          :checked="Boolean(data.params[input.name])"
          type="checkbox"
          @change="update(input.name, $event)"
        />
        <textarea
          v-else-if="input.type === 'Code'"
          :value="String(data.params[input.name] ?? '')"
          rows="5"
          spellcheck="false"
          @change="update(input.name, $event)"
        />
        <input
          v-else-if="input.type === 'String'"
          :value="String(data.params[input.name] ?? '')"
          type="text"
          @change="update(input.name, $event)"
        />
        <select v-else-if="input.type === 'Enum'" :value="String(data.params[input.name] ?? '')" @change="update(input.name, $event)"><option value="">Default</option><option v-for="option in input.options || []" :key="option" :value="option">{{ option }}</option></select>
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