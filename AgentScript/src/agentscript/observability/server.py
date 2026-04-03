"""FastAPI server for AgentScript observability views."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from agentscript import __version__
from agentscript.observability.store import TraceStore


def create_app(trace_path: str | Path) -> FastAPI:
    """Create the FastAPI app that serves trace-backed observability views."""

    cors_origins = _cors_origins()
    app = FastAPI(
        title="AgentScript Observability",
        version=__version__,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    trace_file = Path(trace_path)
    dashboard_dist = _dashboard_dist_dir()
    dashboard_index = dashboard_dist / "index.html"
    assets_dir = dashboard_dist / "assets"
    dashboard_ready = dashboard_index.exists() and assets_dir.exists()
    if dashboard_ready:
        app.mount(
            "/assets",
            StaticFiles(directory=assets_dir),
            name="dashboard-assets",
        )
        app.mount(
            "/dashboard/assets",
            StaticFiles(directory=assets_dir),
            name="dashboard-assets-legacy",
        )

    def store() -> TraceStore:
        return TraceStore(trace_file)

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"ok": True, "trace_path": str(trace_file), "version": __version__}

    @app.get("/api/meta")
    def meta() -> dict[str, object]:
        return {
            "trace_path": str(trace_file),
            "version": __version__,
            "dashboard_built": dashboard_ready,
        }

    @app.get("/api/runs")
    def list_runs(limit: int = 20) -> dict[str, object]:
        trace_store = store()
        try:
            runs = [run.to_dict() for run in trace_store.list_runs(limit=limit)]
            return {"runs": runs}
        finally:
            trace_store.close()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        trace_store = store()
        try:
            return trace_store.get_run(run_id).to_dict()
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        finally:
            trace_store.close()

    @app.get("/api/runs/{run_id}/timeline")
    def get_timeline(run_id: str) -> dict[str, object]:
        trace_store = store()
        try:
            return {"timeline": [entry.to_dict() for entry in trace_store.timeline(run_id)]}
        finally:
            trace_store.close()

    @app.get("/api/runs/{run_id}/memory")
    def get_memory(run_id: str) -> dict[str, object]:
        trace_store = store()
        try:
            return {"memory": [point.to_dict() for point in trace_store.memory_evolution(run_id)]}
        finally:
            trace_store.close()

    @app.get("/api/runs/{run_id}/replay")
    def get_replay(run_id: str) -> dict[str, object]:
        trace_store = store()
        try:
            return trace_store.replay_view(run_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        finally:
            trace_store.close()

    @app.get("/api/runs/{run_id}/dashboard")
    def get_dashboard_payload(run_id: str) -> dict[str, object]:
        trace_store = store()
        try:
            return trace_store.dashboard_payload(run_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        finally:
            trace_store.close()

    @app.get("/", response_class=HTMLResponse, response_model=None)
    def dashboard_root():
        if dashboard_ready:
            return FileResponse(dashboard_index)
        return HTMLResponse(_development_html(str(trace_file)))

    return app


def _dashboard_dist_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "dashboard" / "dist"


def _cors_origins() -> list[str]:
    raw = os.getenv("AGENTSCRIPT_CORS_ORIGINS", "").strip()
    if raw:
        origins = [item.strip() for item in raw.split(",") if item.strip()]
        if origins:
            return origins
    return [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]


def _development_html(trace_path: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>AgentScript Observability</title>
    <style>
      :root {{
        --bg: #0d1b1e;
        --panel: #13272b;
        --line: rgba(244, 237, 225, 0.14);
        --text: #f4ede1;
        --muted: #b7b0a3;
        --accent: #ff9f1c;
        --accent-2: #3dd6d0;
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        background:
          radial-gradient(circle at top right, rgba(61, 214, 208, 0.18), transparent 28rem),
          linear-gradient(180deg, #091315, var(--bg));
        color: var(--text);
        font-family: "Space Grotesk", "Aptos", sans-serif;
      }}
      main {{
        max-width: 72rem;
        margin: 0 auto;
        padding: 3rem 1.5rem 4rem;
      }}
      h1 {{
        margin: 0 0 0.75rem;
        font-size: clamp(2.4rem, 8vw, 4.5rem);
        line-height: 0.96;
      }}
      p {{
        color: var(--muted);
        max-width: 42rem;
      }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
        gap: 1rem;
        margin-top: 2rem;
      }}
      .card {{
        background: rgba(19, 39, 43, 0.82);
        border: 1px solid var(--line);
        border-radius: 1.25rem;
        padding: 1.1rem 1.2rem;
        backdrop-filter: blur(10px);
      }}
      code {{
        font-family: "IBM Plex Mono", monospace;
        color: var(--accent-2);
      }}
      a {{
        color: var(--accent);
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>AgentScript<br />Observability</h1>
      <p>
        The FastAPI backend is live and pointed at <code>{trace_path}</code>. Build the React
        dashboard in <code>dashboard/</code> to replace this development shell, or hit the JSON
        endpoints directly:
        <a href="/api/docs">/api/docs</a>.
      </p>
      <div class="grid">
        <section class="card">
          <strong>Runs</strong>
          <p>List recent executions at <code>/api/runs</code>.</p>
        </section>
        <section class="card">
          <strong>Timeline</strong>
          <p>Inspect tool attempts and retries at <code>/api/runs/&lt;id&gt;/timeline</code>.</p>
        </section>
        <section class="card">
          <strong>Memory</strong>
          <p>Watch memory growth at <code>/api/runs/&lt;id&gt;/memory</code>.</p>
        </section>
        <section class="card">
          <strong>Replay</strong>
          <p>Load deterministic replay data at <code>/api/runs/&lt;id&gt;/replay</code>.</p>
        </section>
      </div>
    </main>
  </body>
</html>"""
