<script setup lang="ts">
import { computed } from "vue";
defineOptions({ name: "DataValue" });

const props = defineProps<{ label: string; value: unknown }>();

const record = computed(() => props.value && typeof props.value === "object" && !Array.isArray(props.value)
  ? props.value as Record<string, unknown>
  : null);
const entries = computed(() => {
  if (Array.isArray(props.value)) return props.value.map((value, index) => [String(index), value] as const);
  if (!record.value) return [];
  if ("value" in record.value) return [];
  const nested = record.value.items ?? record.value.attributes;
  if (Array.isArray(nested)) return nested.map((value, index) => [String(index), value] as const);
  if (nested && typeof nested === "object") return Object.entries(nested as Record<string, unknown>);
  return Object.entries(record.value).filter(([key]) => !["type", "length", "repr", "truncated"].includes(key));
});
const expandable = computed(() => entries.value.length > 0);
const isContainer = computed(() => Array.isArray(props.value) || Boolean(record.value && ("items" in record.value || (!("value" in record.value) && !("repr" in record.value)))));
const typeName = computed(() => String(record.value?.type ?? (Array.isArray(props.value) ? "list" : props.value === null ? "null" : typeof props.value)));
const displayValue = computed(() => {
  if (record.value && "value" in record.value) return String(record.value.value);
  if (record.value && "repr" in record.value) return String(record.value.repr);
  if (isContainer.value) return `${typeName.value} (${countLabel.value})`;
  return props.value === null || props.value === undefined ? "—" : String(props.value);
});
const countLabel = computed(() => record.value?.length !== undefined ? String(record.value.length) : String(entries.value.length));
</script>

<template>
  <div class="data-value">
    <details v-if="expandable">
      <summary><span>{{ label }}</span><small>{{ typeName }} · {{ countLabel }}</small></summary>
      <div class="data-value-children">
        <DataValue v-for="[key, value] in entries" :key="key" :label="key" :value="value" />
      </div>
    </details>
    <div v-else class="data-value-scalar">
      <span>{{ label }}</span>
      <code>{{ displayValue }}</code>
      <small>{{ typeName }}</small>
    </div>
  </div>
</template>
