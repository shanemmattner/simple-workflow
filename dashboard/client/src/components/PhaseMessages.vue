<script setup lang="ts">
import type { PhaseDetail } from '../types';

defineProps<{ detail: PhaseDetail }>();

function truncate(s: string | null, max: number): string {
  if (!s) return '';
  return s.length > max ? s.slice(0, max) + '...' : s;
}

function roleColor(role: string): string {
  if (role === 'assistant') return 'text-blue-400';
  if (role === 'user') return 'text-green-400';
  return 'text-gray-400';
}
</script>

<template>
  <div class="space-y-2 text-xs max-h-96 overflow-y-auto pr-1">
    <div
      v-for="msg in detail.messages"
      :key="msg.id"
      class="bg-gray-800/50 rounded p-2"
    >
      <div class="flex items-center gap-2 mb-1">
        <span :class="roleColor(msg.role)" class="font-medium">{{ msg.role }}</span>
        <span class="text-gray-600">turn {{ msg.turn_number }}</span>
        <span v-if="msg.cost > 0" class="text-gray-600 ml-auto">${{ msg.cost.toFixed(3) }}</span>
      </div>
      <pre class="text-gray-300 whitespace-pre-wrap break-words font-sans text-xs leading-relaxed">{{ truncate(msg.content, 2000) }}</pre>

      <!-- Tool calls for this message -->
      <div
        v-for="tc in detail.tool_calls.filter(t => t.message_id === msg.id)"
        :key="tc.id"
        class="mt-1 ml-3 border-l-2 border-gray-700 pl-2"
      >
        <div class="text-purple-400 font-medium">
          {{ tc.tool_name }}
          <span v-if="tc.duration_ms" class="text-gray-600 font-normal">{{ tc.duration_ms }}ms</span>
        </div>
        <div v-if="tc.tool_input" class="text-gray-500 truncate">{{ truncate(tc.tool_input, 200) }}</div>
      </div>
    </div>

    <div v-if="detail.messages.length === 0" class="text-gray-600 py-2">
      No messages recorded.
    </div>
  </div>
</template>
