<script setup lang="ts">
import { ref, watch } from 'vue';
import type { RunSummary, PhaseDetail } from '../types';
import PhaseTimeline from './PhaseTimeline.vue';
import PhaseMessages from './PhaseMessages.vue';

const props = defineProps<{ run: RunSummary }>();

const expandedPhaseId = ref<number | null>(null);
const phaseDetail = ref<PhaseDetail | null>(null);
const loading = ref(false);

function togglePhase(phaseId: number) {
  if (expandedPhaseId.value === phaseId) {
    expandedPhaseId.value = null;
    phaseDetail.value = null;
    return;
  }
  expandedPhaseId.value = phaseId;
  fetchPhaseDetail(phaseId);
}

async function fetchPhaseDetail(phaseId: number) {
  loading.value = true;
  try {
    const res = await fetch(`/api/runs/${props.run.id}/phases/${phaseId}`);
    if (res.ok) {
      phaseDetail.value = await res.json();
    }
  } finally {
    loading.value = false;
  }
}

watch(() => props.run.id, () => {
  expandedPhaseId.value = null;
  phaseDetail.value = null;
});

function totalTokens(run: RunSummary): string {
  const total = run.total_tokens_in + run.total_tokens_out;
  if (total > 1_000_000) return (total / 1_000_000).toFixed(1) + 'M';
  if (total > 1_000) return (total / 1_000).toFixed(0) + 'k';
  return total.toString();
}
</script>

<template>
  <div class="bg-gray-900 rounded-lg border border-gray-800 p-4 mobile:p-2">
    <div class="flex items-center justify-between mb-3 flex-wrap gap-2">
      <div>
        <h2 class="text-base font-medium text-gray-100">
          {{ run.repo }}#{{ run.issue_number }}
        </h2>
        <div class="text-xs text-gray-500 mt-0.5">
          {{ run.id }} &middot; {{ run.model ?? 'unknown' }} &middot; {{ totalTokens(run) }} tokens
        </div>
      </div>
      <div v-if="run.review_verdict" class="text-sm">
        <span
          class="px-2 py-0.5 rounded text-xs font-medium"
          :class="run.review_verdict === 'approve' ? 'bg-green-500/20 text-green-400' : 'bg-yellow-500/20 text-yellow-400'"
        >
          {{ run.review_verdict }}
        </span>
      </div>
    </div>

    <PhaseTimeline :phases="run.phases" :runStarted="run.started_at" />

    <div class="mt-3 space-y-1">
      <div
        v-for="phase in run.phases"
        :key="phase.id"
      >
        <button
          class="w-full text-left text-xs px-2 py-1 rounded hover:bg-gray-800 text-gray-400 flex justify-between"
          @click="togglePhase(phase.id)"
        >
          <span>{{ phase.phase_name }}</span>
          <span>{{ expandedPhaseId === phase.id ? '−' : '+' }}</span>
        </button>
        <div v-if="expandedPhaseId === phase.id" class="ml-2 mt-1">
          <div v-if="loading" class="text-xs text-gray-500 py-2">Loading...</div>
          <PhaseMessages v-else-if="phaseDetail" :detail="phaseDetail" />
        </div>
      </div>
    </div>
  </div>
</template>
