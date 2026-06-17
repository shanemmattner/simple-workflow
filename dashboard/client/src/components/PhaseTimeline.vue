<script setup lang="ts">
import { computed } from 'vue';
import type { PhaseSummary } from '../types';
import { usePhaseColors } from '../composables/usePhaseColors';
import StatusBadge from './StatusBadge.vue';

const props = defineProps<{
  phases: PhaseSummary[];
  runStarted: string;
}>();

const { getPhaseColor } = usePhaseColors();

const runStart = computed(() => new Date(props.runStarted).getTime());

const totalSpan = computed(() => {
  const now = Date.now();
  let latest = runStart.value;
  for (const p of props.phases) {
    const end = p.finished_at ? new Date(p.finished_at).getTime() : now;
    if (end > latest) latest = end;
  }
  return Math.max(latest - runStart.value, 1000);
});

function barStyle(phase: PhaseSummary) {
  if (!phase.started_at) return { left: '0%', width: '0%' };
  const start = new Date(phase.started_at).getTime();
  const end = phase.finished_at ? new Date(phase.finished_at).getTime() : Date.now();
  const left = ((start - runStart.value) / totalSpan.value) * 100;
  const width = Math.max(((end - start) / totalSpan.value) * 100, 1);
  return { left: `${left}%`, width: `${width}%` };
}
</script>

<template>
  <div class="space-y-1">
    <div
      v-for="phase in phases"
      :key="phase.id"
      class="flex items-center gap-2 text-xs"
    >
      <span class="w-32 mobile:w-24 truncate text-gray-400 text-right shrink-0">
        {{ phase.phase_name }}
      </span>
      <div class="flex-1 h-5 relative bg-gray-800/50 rounded overflow-hidden">
        <div
          class="absolute top-0 h-full rounded transition-all duration-500"
          :class="phase.status === 'running' ? 'animate-pulse-dot' : ''"
          :style="{
            ...barStyle(phase),
            backgroundColor: getPhaseColor(phase.phase_name),
            opacity: phase.status === 'pending' ? 0.3 : 0.8,
          }"
        />
      </div>
      <span class="w-16 shrink-0">
        <StatusBadge :status="phase.status" />
      </span>
      <span class="w-14 text-right text-gray-500 tabular-nums shrink-0 mobile:hidden">
        ${{ phase.cost.toFixed(2) }}
      </span>
    </div>
  </div>
</template>
