<script setup lang="ts">
defineProps<{
  cost: number;
  live?: boolean;
}>();

function formatCost(c: number): string {
  if (c < 0.01) return '<$0.01';
  return '$' + c.toFixed(2);
}
</script>

<template>
  <span class="tabular-nums" :class="live ? 'text-green-400' : 'text-gray-300'">
    {{ formatCost(cost) }}
  </span>
</template>
