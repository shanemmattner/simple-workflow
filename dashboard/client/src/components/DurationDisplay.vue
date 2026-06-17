<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue';

const props = defineProps<{
  started: string;
  finished: string | null;
  live?: boolean;
}>();

const now = ref(Date.now());
let timer: number | null = null;

onMounted(() => {
  if (props.live) {
    timer = window.setInterval(() => { now.value = Date.now(); }, 1000);
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });

const duration = computed(() => {
  const start = new Date(props.started).getTime();
  const end = props.finished ? new Date(props.finished).getTime() : now.value;
  const secs = Math.max(0, Math.floor((end - start) / 1000));
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}m ${s.toString().padStart(2, '0')}s`;
});
</script>

<template>
  <span class="tabular-nums" :class="live ? 'text-green-400' : 'text-gray-300'">
    {{ duration }}
  </span>
</template>
