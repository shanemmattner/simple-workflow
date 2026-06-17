<script setup lang="ts">
import type { RunSummary } from '../types';
import StatusBadge from './StatusBadge.vue';
import CostDisplay from './CostDisplay.vue';
import DurationDisplay from './DurationDisplay.vue';

defineProps<{
  runs: RunSummary[];
  selectedId: string | null;
}>();

const emit = defineEmits<{
  select: [id: string];
}>();

function shortRepo(repo: string): string {
  return repo.split('/').pop() ?? repo;
}
</script>

<template>
  <div class="overflow-x-auto rounded-lg border border-gray-800">
    <table class="w-full text-sm">
      <thead class="bg-gray-900 text-gray-400 text-left">
        <tr>
          <th class="px-3 py-2 mobile:px-2">Repo</th>
          <th class="px-3 py-2 mobile:hidden">Issue</th>
          <th class="px-3 py-2">Status</th>
          <th class="px-3 py-2 mobile:hidden">Model</th>
          <th class="px-3 py-2">Cost</th>
          <th class="px-3 py-2 mobile:hidden">Duration</th>
          <th class="px-3 py-2 mobile:hidden">Phases</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="run in runs"
          :key="run.id"
          class="border-t border-gray-800 cursor-pointer transition-colors"
          :class="selectedId === run.id ? 'bg-gray-800/60' : 'hover:bg-gray-900/50'"
          @click="emit('select', run.id)"
        >
          <td class="px-3 py-2 mobile:px-2 font-medium text-gray-200">
            {{ shortRepo(run.repo) }}
            <span class="mobile:inline hidden text-gray-500 ml-1">#{{ run.issue_number }}</span>
          </td>
          <td class="px-3 py-2 mobile:hidden text-gray-400">#{{ run.issue_number }}</td>
          <td class="px-3 py-2">
            <StatusBadge :status="run.status" />
          </td>
          <td class="px-3 py-2 mobile:hidden text-gray-400">{{ run.model ?? '-' }}</td>
          <td class="px-3 py-2">
            <CostDisplay :cost="run.total_cost" :live="run.status === 'running'" />
          </td>
          <td class="px-3 py-2 mobile:hidden">
            <DurationDisplay :started="run.started_at" :finished="run.finished_at" :live="run.status === 'running'" />
          </td>
          <td class="px-3 py-2 mobile:hidden text-gray-400">
            {{ run.phases.length }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
