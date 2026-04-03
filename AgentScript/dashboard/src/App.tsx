import { useEffect, useState } from "react";

import type { DashboardPayload, RunSummary } from "./types";

const apiBase = import.meta.env.VITE_API_BASE ?? "";

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBase}${path}`);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export function App() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [payload, setPayload] = useState<DashboardPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function loadRuns() {
      try {
        const data = await fetchJson<{ runs: RunSummary[] }>("/api/runs");
        if (cancelled) return;
        setError(null);
        setRuns(data.runs);
        setSelectedRunId(data.runs[0]?.run_id ?? null);
      } catch (loadError) {
        if (cancelled) return;
        setError(loadError instanceof Error ? loadError.message : "Failed to load runs.");
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    void loadRuns();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedRunId) {
      setPayload(null);
      return;
    }
    let cancelled = false;
    async function loadRun() {
      try {
        const nextPayload = await fetchJson<DashboardPayload>(`/api/runs/${selectedRunId}/dashboard`);
        if (!cancelled) {
          setError(null);
          setPayload(nextPayload);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Failed to load run.");
        }
      }
    }
    void loadRun();
    return () => {
      cancelled = true;
    };
  }, [selectedRunId]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div>
          <p className="eyebrow">AgentScript</p>
          <h1>Observability Console</h1>
          <p className="lede">
            Tool timelines, memory drift, and replay data from the AgentScript runtime.
          </p>
        </div>
        <div className="run-list">
          {loading && <p className="empty">Loading runs...</p>}
          {!loading && runs.length === 0 && <p className="empty">No trace runs yet.</p>}
          {runs.map((run) => (
            <button
              key={run.run_id}
              className={run.run_id === selectedRunId ? "run-pill is-active" : "run-pill"}
              onClick={() => setSelectedRunId(run.run_id)}
            >
              <strong>{run.workflow_name}</strong>
              <span>{run.status}</span>
            </button>
          ))}
        </div>
      </aside>

      <main className="content">
        {error && <div className="banner error">{error}</div>}
        {!payload && !error && <div className="banner">Select a run to inspect its timeline.</div>}
        {payload && (
          <>
            <section className="metrics-grid">
              <MetricCard label="Status" value={payload.run.summary.status} />
              <MetricCard label="Events" value={String(payload.run.event_count)} />
              <MetricCard label="Tool Results" value={String(payload.run.tool_result_count)} />
              <MetricCard
                label="Duration"
                value={
                  payload.run.summary.duration_ms === null
                    ? "running"
                    : `${payload.run.summary.duration_ms.toFixed(1)} ms`
                }
              />
            </section>

            <section className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Timeline</p>
                  <h2>Tool Calls</h2>
                </div>
                <p>{payload.timeline.length} tool attempts</p>
              </div>
              <div className="timeline">
                {payload.timeline.map((entry) => (
                  <div className="timeline-row" key={`${entry.step_id}-${entry.attempt}`}>
                    <div>
                      <strong>{entry.tool_name}</strong>
                      <p>{entry.step_id}</p>
                    </div>
                    <div className="timeline-bar">
                      <span
                        className={entry.ok ? "timeline-fill is-success" : "timeline-fill is-failed"}
                        style={{ width: `${Math.min(100, Math.max(12, entry.latency_ms / 6))}%` }}
                      />
                    </div>
                    <div className="timeline-meta">
                      <span>{entry.latency_ms.toFixed(1)} ms</span>
                      <span>{entry.source}</span>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section className="panel split">
              <div>
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">Memory</p>
                    <h2>Evolution</h2>
                  </div>
                </div>
                <div className="memory-list">
                  {payload.memory.map((point) => (
                    <div className="memory-row" key={`${point.seq}-${point.key}`}>
                      <strong>{point.key}</strong>
                      <span>{point.source}</span>
                      <code>{JSON.stringify(point.value)}</code>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">Replay</p>
                    <h2>Formatted Trace</h2>
                  </div>
                </div>
                <pre className="replay-view">{payload.replay.formatted}</pre>
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}
