export interface RunSummary {
  id: string;
  repo: string;
  issue_number: number;
  issue_url: string | null;
  branch: string | null;
  status: string;
  model: string | null;
  started_at: string;
  finished_at: string | null;
  total_cost: number;
  total_tokens_in: number;
  total_tokens_out: number;
  review_verdict: string | null;
  review_summary: string | null;
  phases: PhaseSummary[];
}

export interface PhaseSummary {
  id: number;
  run_id: string;
  phase_name: string;
  status: string;
  model: string | null;
  started_at: string | null;
  finished_at: string | null;
  cost: number;
  tokens_in: number;
  tokens_out: number;
  failure_category: string | null;
}

export interface Message {
  id: number;
  phase_id: number;
  turn_number: number;
  role: string;
  content: string;
  timestamp: string;
  tokens_in: number;
  tokens_out: number;
  cost: number;
}

export interface ToolCall {
  id: number;
  message_id: number;
  phase_id: number;
  tool_name: string;
  tool_input: string | null;
  tool_result: string | null;
  duration_ms: number | null;
}

export interface PhaseDetail {
  phase: PhaseSummary;
  messages: Message[];
  tool_calls: ToolCall[];
}

export type WsMessage =
  | { type: 'runs_update'; data: RunSummary[] }
  | { type: 'run_update'; data: RunSummary };
