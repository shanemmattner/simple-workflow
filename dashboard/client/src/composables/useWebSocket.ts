import { ref, onMounted, onUnmounted } from 'vue';
import type { RunSummary, WsMessage } from '../types';

export function useWebSocket() {
  const runs = ref<RunSummary[]>([]);
  const isConnected = ref(false);

  let ws: WebSocket | null = null;
  let reconnectTimeout: number | null = null;

  function getWsUrl(): string {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${location.host}/stream`;
  }

  function connect() {
    try {
      ws = new WebSocket(getWsUrl());

      ws.onopen = () => {
        isConnected.value = true;
      };

      ws.onmessage = (event) => {
        try {
          const msg: WsMessage = JSON.parse(event.data);
          if (msg.type === 'runs_update') {
            runs.value = msg.data;
          } else if (msg.type === 'run_update') {
            const idx = runs.value.findIndex((r) => r.id === msg.data.id);
            if (idx >= 0) {
              runs.value[idx] = msg.data;
            } else {
              runs.value.unshift(msg.data);
            }
          }
        } catch {
          // ignore parse errors
        }
      };

      ws.onclose = () => {
        isConnected.value = false;
        reconnectTimeout = window.setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        ws?.close();
      };
    } catch {
      reconnectTimeout = window.setTimeout(connect, 3000);
    }
  }

  function disconnect() {
    if (reconnectTimeout) {
      clearTimeout(reconnectTimeout);
      reconnectTimeout = null;
    }
    ws?.close();
    ws = null;
  }

  onMounted(connect);
  onUnmounted(disconnect);

  return { runs, isConnected };
}
