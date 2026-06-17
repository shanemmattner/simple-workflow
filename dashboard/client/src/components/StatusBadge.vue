<script setup lang="ts">
defineProps<{ status: string }>();

const statusConfig: Record<string, { bg: string; text: string; label: string; pulse?: boolean }> = {
  running: { bg: 'bg-green-500/20', text: 'text-green-400', label: 'Running', pulse: true },
  passed: { bg: 'bg-green-500/20', text: 'text-green-400', label: 'Passed' },
  failed: { bg: 'bg-red-500/20', text: 'text-red-400', label: 'Failed' },
  error: { bg: 'bg-red-500/20', text: 'text-red-400', label: 'Error' },
  pending: { bg: 'bg-gray-500/20', text: 'text-gray-400', label: 'Pending' },
};

function getConfig(status: string) {
  return statusConfig[status] ?? { bg: 'bg-gray-500/20', text: 'text-gray-400', label: status };
}
</script>

<template>
  <span
    class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium"
    :class="[getConfig(status).bg, getConfig(status).text]"
  >
    <span
      v-if="getConfig(status).pulse"
      class="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse-dot"
    />
    {{ getConfig(status).label }}
  </span>
</template>
