<script setup lang="ts">
import { ref, computed } from 'vue';
import { useWebSocket } from './composables/useWebSocket';
import type { RunSummary } from './types';
import RunList from './components/RunList.vue';
import RunDetail from './components/RunDetail.vue';

const { runs, isConnected } = useWebSocket();
const selectedRunId = ref<string | null>(null);

const selectedRun = computed<RunSummary | null>(() => {
  if (!selectedRunId.value) return null;
  return runs.value.find((r) => r.id === selectedRunId.value) ?? null;
});

function selectRun(id: string) {
  selectedRunId.value = selectedRunId.value === id ? null : id;
}
</script>

<template>
  <div class="max-w-7xl mx-auto px-4 py-4 mobile:px-2 mobile:py-2">
    <header class="flex items-center justify-between mb-4">
      <h1 class="text-lg font-semibold text-gray-100">Pipeline Dashboard</h1>
      <div class="flex items-center gap-2 text-sm">
        <span
          class="inline-block w-2 h-2 rounded-full"
          :class="isConnected ? 'bg-green-400 animate-pulse-dot' : 'bg-red-500'"
        />
        <span class="text-gray-400">{{ isConnected ? 'Live' : 'Disconnected' }}</span>
      </div>
    </header>

    <RunList :runs="runs" :selectedId="selectedRunId" @select="selectRun" />

    <RunDetail v-if="selectedRun" :run="selectedRun" class="mt-4" />

    <div
      v-if="runs.length === 0"
      class="text-center text-gray-500 py-16"
    >
      No pipeline runs found. Start a run with <code class="text-gray-400">./scripts/run.sh</code>
    </div>
  </div>
</template>
