<script setup lang="ts">
import { computed, ref } from "vue";
import Button from "primevue/button";

type LogEntry = { time: string; level: "info" | "success" | "warning" | "error"; message: string };

const props = defineProps<{
  geometryCount: number;
  running: boolean;
  runtimeStatus: string;
  downloadStatus: string;
  errors: string[];
  logs: LogEntry[];
}>();
const expanded = ref(false);
const headline = computed(() => props.downloadStatus || (props.running
  ? "Evaluating graph"
  : props.errors.length
    ? "Evaluation errors"
    : props.geometryCount
      ? props.runtimeStatus || "Ready"
      : "No visible geometry"));
const state = computed(() => props.downloadStatus
  ? "working"
  : props.errors.length
    ? "error"
    : props.running
      ? "working"
      : props.geometryCount
        ? "success"
        : "idle");
</script>

<template>
  <div class="viewport-status" aria-live="polite">
    <div class="viewport-status-bar">
      <span class="viewport-status-dot" :class="'is-' + state"></span>
      <span class="viewport-status-label">{{ headline }}</span>
      <span class="viewport-status-count">{{ geometryCount }} visible</span>
      <Button v-if="logs.length || errors.length" icon="pi pi-list" text rounded size="small"
        class="viewport-log-toggle" :aria-label="expanded ? 'Hide activity log' : 'Show activity log'"
        :title="expanded ? 'Hide activity log' : 'Show activity log'"
        @click="expanded = !expanded" />
    </div>
    <div v-if="expanded" class="viewport-log-panel">
      <header><strong>Activity</strong><Button icon="pi pi-times" text rounded size="small"
        aria-label="Close activity log" @click="expanded = false" /></header>
      <div v-if="!logs.length && !errors.length" class="viewport-log-empty">No recent activity</div>
      <div v-for="(entry, index) in logs" :key="index" class="viewport-log-entry" :class="'log-' + entry.level">
        <time>{{ entry.time }}</time><span>{{ entry.message }}</span>
      </div>
      <div v-for="(error, index) in errors" :key="'error-' + index" class="viewport-log-entry log-error">
        <time>ERROR</time><span>{{ error }}</span>
      </div>
    </div>
  </div>
  <div v-if="geometryCount === 0 && !running && !downloadStatus" class="viewport-empty-state">
    <i class="pi pi-box" aria-hidden="true"></i>
    <strong>No visible geometry</strong>
    <small>Enable preview on a geometry node</small>
  </div>
</template>
