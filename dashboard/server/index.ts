import { Database } from 'bun:sqlite';
import { readdirSync, existsSync, statSync } from 'fs';
import { join, resolve } from 'path';

const PORT = parseInt(process.env.PORT || '4080');
const RUNS_DIR = resolve(process.env.RUNS_DIR || join(import.meta.dir, '../../engines/github_claude/runs'));
const CLIENT_DIST = resolve(import.meta.dir, '../client/dist');

const wsClients = new Set<any>();

// --- DB reading ---

function listDbFiles(): string[] {
  if (!existsSync(RUNS_DIR)) return [];
  return readdirSync(RUNS_DIR)
    .filter((f) => f.endsWith('.db'))
    .sort()
    .reverse();
}

function readRun(dbPath: string) {
  const db = new Database(dbPath, { readonly: true });
  try {
    const run = db.prepare('SELECT * FROM run LIMIT 1').get() as any;
    if (!run) return null;
    const phases = db.prepare('SELECT * FROM phase WHERE run_id = ? ORDER BY started_at').all(run.id);
    return { ...run, phases };
  } catch {
    return null;
  } finally {
    db.close();
  }
}

function readPhaseDetail(dbPath: string, phaseId: number) {
  const db = new Database(dbPath, { readonly: true });
  try {
    const phase = db.prepare('SELECT * FROM phase WHERE id = ?').get(phaseId) as any;
    if (!phase) return null;
    const messages = db.prepare('SELECT * FROM message WHERE phase_id = ? ORDER BY turn_number').all(phaseId);
    const tool_calls = db.prepare('SELECT * FROM tool_call WHERE phase_id = ? ORDER BY id').all(phaseId);
    return { phase, messages, tool_calls };
  } catch {
    return null;
  } finally {
    db.close();
  }
}

function getAllRuns() {
  return listDbFiles()
    .map((f) => readRun(join(RUNS_DIR, f)))
    .filter(Boolean);
}

function findDbForRun(runId: string): string | null {
  for (const f of listDbFiles()) {
    const p = join(RUNS_DIR, f);
    try {
      const db = new Database(p, { readonly: true });
      const row = db.prepare('SELECT id FROM run WHERE id = ? LIMIT 1').get(runId) as any;
      db.close();
      if (row) return p;
    } catch { /* skip corrupt */ }
  }
  return null;
}

// --- Polling ---

let cachedRuns: any[] = [];

function pollRuns() {
  const runs = getAllRuns();
  cachedRuns = runs;
  const msg = JSON.stringify({ type: 'runs_update', data: runs });
  for (const ws of wsClients) {
    try { ws.send(msg); } catch { wsClients.delete(ws); }
  }
}

// Poll active runs every 2s, full scan every 30s
let pollCount = 0;
setInterval(() => {
  pollCount++;
  if (pollCount % 15 === 0 || cachedRuns.some((r: any) => r.status === 'running')) {
    pollRuns();
  }
}, 2000);
pollRuns(); // initial load

// --- HTTP + WebSocket server ---

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

function json(data: any, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...CORS, 'Content-Type': 'application/json' },
  });
}

const server = Bun.serve({
  port: PORT,
  hostname: '0.0.0.0',

  async fetch(req) {
    const url = new URL(req.url);

    if (req.method === 'OPTIONS') return new Response(null, { headers: CORS });

    // WebSocket upgrade
    if (url.pathname === '/stream') {
      if (server.upgrade(req)) return undefined;
      return new Response('WebSocket upgrade failed', { status: 400 });
    }

    // API routes
    if (url.pathname === '/api/runs' && req.method === 'GET') {
      return json(cachedRuns);
    }

    const runMatch = url.pathname.match(/^\/api\/runs\/([^/]+)$/);
    if (runMatch && req.method === 'GET') {
      const dbPath = findDbForRun(runMatch[1]);
      if (!dbPath) return json({ error: 'not found' }, 404);
      const run = readRun(dbPath);
      return run ? json(run) : json({ error: 'not found' }, 404);
    }

    const phaseMatch = url.pathname.match(/^\/api\/runs\/([^/]+)\/phases\/(\d+)$/);
    if (phaseMatch && req.method === 'GET') {
      const dbPath = findDbForRun(phaseMatch[1]);
      if (!dbPath) return json({ error: 'not found' }, 404);
      const detail = readPhaseDetail(dbPath, parseInt(phaseMatch[2]));
      return detail ? json(detail) : json({ error: 'not found' }, 404);
    }

    // Static file serving (production)
    if (existsSync(CLIENT_DIST)) {
      const filePath = join(CLIENT_DIST, url.pathname === '/' ? 'index.html' : url.pathname);
      const file = Bun.file(filePath);
      if (await file.exists()) return new Response(file);
      // SPA fallback
      if (!url.pathname.startsWith('/api')) {
        const index = Bun.file(join(CLIENT_DIST, 'index.html'));
        if (await index.exists()) return new Response(index);
      }
    }

    return new Response('Not found', { status: 404, headers: CORS });
  },

  websocket: {
    open(ws) {
      wsClients.add(ws);
      ws.send(JSON.stringify({ type: 'runs_update', data: cachedRuns }));
    },
    message() { /* no client messages expected */ },
    close(ws) { wsClients.delete(ws); },
  },
});

console.log(`Dashboard server running on http://0.0.0.0:${PORT}`);
console.log(`Watching runs in: ${RUNS_DIR}`);
console.log(`Serving client from: ${CLIENT_DIST}`);
