export type RunSummary = {
  run_id: string;
  workflow_name: string;
  agent_name: string | null;
  status: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  jsonl_path: string | null;
  error_text: string | null;
};

export type TimelineEntry = {
  step_id: string;
  workflow_name: string;
  tool_name: string;
  attempt: number;
  started_at: number;
  finished_at: number;
  latency_ms: number;
  ok: boolean;
  status_code: number;
  source: string;
  replayed: boolean;
  retries: number;
  args: Record<string, unknown>;
  payload: unknown;
  error: string | null;
};

export type MemoryPoint = {
  seq: number;
  key: string;
  source: string;
  value: unknown;
  semantic_indexed: boolean;
  snapshot: Record<string, unknown>;
};

export type DashboardPayload = {
  run: {
    summary: RunSummary;
    event_count: number;
    tool_call_count: number;
    tool_result_count: number;
    final_output: unknown;
    arguments: Record<string, unknown>;
  };
  timeline: TimelineEntry[];
  memory: MemoryPoint[];
  replay: {
    run_id: string;
    workflow_name: string;
    status: string;
    final_output: unknown;
    formatted: string;
    events: Array<Record<string, unknown>>;
  };
};
