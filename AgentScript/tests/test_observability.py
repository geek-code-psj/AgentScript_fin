from __future__ import annotations

from pathlib import Path
import re
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from agentscript.cli.main import _dump_dashboard_payload, _parse_arguments
from agentscript.demo.legal_demo import run_demo
from agentscript.observability.server import create_app
from agentscript.observability.store import TraceStore


@pytest.mark.asyncio
async def test_trace_store_builds_timeline_and_memory_views() -> None:
    trace_path = Path("tests") / f"obs-store-{uuid4().hex}.sqlite"
    result = await run_demo(mode="retry", trace_path=trace_path)

    store = TraceStore(trace_path)
    try:
        runs = store.list_runs()
        detail = store.get_run(result["run_id"])
        timeline = store.timeline(result["run_id"])
        memory = store.memory_evolution(result["run_id"])
        payload = store.dashboard_payload(result["run_id"])
    finally:
        store.close()

    assert runs
    assert runs[0].workflow_name == "legal_brief"
    assert detail.summary.run_id == result["run_id"]
    assert any(entry.tool_name == "search_indian_kanoon" for entry in timeline)
    assert any(point.key == "stored_brief" for point in memory)
    assert payload["run"]["summary"]["run_id"] == result["run_id"]


@pytest.mark.asyncio
async def test_observability_api_serves_run_views() -> None:
    trace_path = Path("tests") / f"obs-api-{uuid4().hex}.sqlite"
    result = await run_demo(mode="happy", trace_path=trace_path)

    app = create_app(trace_path)
    client = TestClient(app)

    health = client.get("/health")
    runs = client.get("/api/runs")
    dashboard = client.get(f"/api/runs/{result['run_id']}/dashboard")
    replay = client.get(f"/api/runs/{result['run_id']}/replay")

    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert runs.status_code == 200
    assert runs.json()["runs"][0]["run_id"] == result["run_id"]
    assert dashboard.status_code == 200
    assert dashboard.json()["run"]["summary"]["workflow_name"] == "legal_brief"
    assert replay.status_code == 200
    assert replay.json()["workflow_name"] == "legal_brief"


@pytest.mark.asyncio
async def test_dashboard_root_serves_built_bundle_assets() -> None:
    trace_path = Path("tests") / f"obs-root-{uuid4().hex}.sqlite"
    await run_demo(mode="happy", trace_path=trace_path)

    app = create_app(trace_path)
    client = TestClient(app)

    root = client.get("/")
    assert root.status_code == 200
    assert "AgentScript Dashboard" in root.text

    match = re.search(r'src="(?P<path>/assets/[^"]+\.js)"', root.text)
    assert match is not None

    script = client.get(match.group("path"))
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]


@pytest.mark.asyncio
async def test_cli_dashboard_helpers_parse_arguments_and_dump_payload() -> None:
    trace_path = Path("tests") / f"obs-cli-{uuid4().hex}.sqlite"
    result = await run_demo(mode="outage", trace_path=trace_path)

    parsed = _parse_arguments(['query="BNS theft appeal"', "limit=3", "flag=true"])
    dumped = _dump_dashboard_payload(trace_path, result["run_id"])

    assert parsed == {"query": "BNS theft appeal", "limit": 3, "flag": True}
    assert dumped["selected_run"]["run"]["summary"]["run_id"] == result["run_id"]
