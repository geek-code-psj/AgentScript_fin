# FastAPI Dashboard API Reference

This document describes the REST API endpoints provided by the AgentScript FastAPI dashboard server.

**Base URL:** `http://localhost:8000/api`

**OpenAPI/Swagger:** `http://localhost:8000/docs`

---

## Authentication

Currently, no authentication is required. In production, add authentication via:
- Bearer tokens (API keys)
- OAuth2
- mTLS

See [SECURITY.md](./SECURITY.md) for hardening guidelines.

---

## Runs Endpoints

### List All Runs

```
GET /api/runs
```

Returns all workflow executions ordered by creation time (newest first).

**Parameters:**
- `limit` (optional, default=50): Max results to return
- `offset` (optional, default=0): Pagination offset
- `workflow` (optional): Filter by workflow name
- `status` (optional): Filter by status (`success`, `error`, `running`)

**Response:**
```json
{
  "runs": [
    {
      "run_id": "legal_brief_20260405_120000",
      "workflow_name": "legal_brief",
      "status": "success",
      "created_at": "2026-04-05T12:00:00.000Z",
      "updated_at": "2026-04-05T12:00:05.000Z",
      "duration_ms": 5400,
      "error": null
    }
  ],
  "total": 42,
  "limit": 50,
  "offset": 0
}
```

**Example:**
```bash
curl "http://localhost:8000/api/runs?limit=10&status=success"
```

---

### Get Run Details

```
GET /api/runs/{run_id}
```

Returns full run details including timeline of events.

**Path Parameters:**
- `run_id` (required): Unique run identifier

**Response:**
```json
{
  "run_id": "legal_brief_20260405_120000",
  "workflow_name": "legal_brief",
  "status": "success",
  "created_at": "2026-04-05T12:00:00.000Z",
  "inputs": {
    "query": "BNS theft appeal"
  },
  "outputs": {
    "brief": {
      "confidence": 0.95,
      "text": "Section 103 of the Indian Penal Code provides..."
    }
  },
  "events": [
    {
      "step_id": 1,
      "event_type": "TOOL_CALL",
      "tool_name": "search_indian_kanoon",
      "arguments": {
        "query": "BNS theft appeal"
      },
      "timestamp": "2026-04-05T12:00:01.234Z"
    },
    {
      "step_id": 1,
      "event_type": "TOOL_RESULT",
      "tool_name": "search_indian_kanoon",
      "ok": true,
      "status_code": 200,
      "response_payload": [
        {
          "source": "Indian Kanoon",
          "text": "Section 103 of the Indian Penal Code...",
          "url": "https://indiankanoon.org/..."
        }
      ],
      "latency_ms": 1523,
      "retry_count": 0,
      "timestamp": "2026-04-05T12:00:02.757Z"
    },
    {
      "step_id": 2,
      "event_type": "TOOL_CALL",
      "tool_name": "filter_relevance",
      "arguments": {
        "citations": [...],
        "query": "BNS theft appeal"
      },
      "timestamp": "2026-04-05T12:00:02.758Z"
    }
  ],
  "circuit_breaker_state": "CLOSED",
  "memory_snapshot": {
    "session": {
      "query_cache": "BNS theft appeal"
    }
  }
}
```

**Example:**
```bash
curl "http://localhost:8000/api/runs/legal_brief_20260405_120000"
```

---

### Get Run Timeline

```
GET /api/runs/{run_id}/timeline
```

Returns a condensed timeline with latency breakdown and state transitions.

**Response:**
```json
{
  "run_id": "legal_brief_20260405_120000",
  "timeline": [
    {
      "step_id": 1,
      "operation": "tool_call",
      "name": "search_indian_kanoon",
      "state_before": "AGENT_RUNNING",
      "state_after": "AGENT_WAITING_FOR_RESULT",
      "latency_ms": 1523,
      "retries": 0,
      "timestamp": "2026-04-05T12:00:01.234Z"
    },
    {
      "step_id": 2,
      "operation": "circuit_breaker_transition",
      "from_state": "CLOSED",
      "to_state": "HALF_OPEN",
      "reason": "Failure rate spike detected (3 failures in last 10 calls)",
      "timestamp": "2026-04-05T12:00:03.500Z"
    },
    {
      "step_id": 3,
      "operation": "fallback_activated",
      "original_tool": "search_indian_kanoon",
      "fallback_tool": "recall_cached",
      "reason": "Circuit breaker open",
      "timestamp": "2026-04-05T12:00:04.000Z"
    }
  ],
  "total_duration_ms": 5400,
  "circuit_breaker_transitions": 2
}
```

**Example:**
```bash
curl "http://localhost:8000/api/runs/legal_brief_20260405_120000/timeline"
```

---

### Delete Run

```
DELETE /api/runs/{run_id}
```

Deletes run from trace store (soft delete; archive to blob storage).

**Response:**
```json
{
  "status": "deleted",
  "run_id": "legal_brief_20260405_120000"
}
```

**Example:**
```bash
curl -X DELETE "http://localhost:8000/api/runs/legal_brief_20260405_120000"
```

---

## Memory Endpoints

### Get Run Memory State

```
GET /api/memory/{run_id}
```

Returns session memory + semantic memory state at end of run.

**Response:**
```json
{
  "run_id": "legal_brief_20260405_120000",
  "session_memory": {
    "query_cache": "BNS theft appeal",
    "last_result_key": "legal_brief_cached_00123"
  },
  "semantic_memory": [
    {
      "key": "precedent_1",
      "value": "Indian Supreme Court ruling on theft in 1999...",
      "similarity_score": 0.89,
      "embedding_dim": 1536,
      "stored_at": "2026-04-05T11:55:00.000Z"
    },
    {
      "key": "precedent_2",
      "value": "High Court decision on occupancy and liability...",
      "similarity_score": 0.76,
      "embedding_dim": 1536,
      "stored_at": "2026-04-05T11:50:00.000Z"
    }
  ]
}
```

**Parameters:**
- `include_embeddings` (optional, default=false): Include raw embedding vectors (large payload)

**Example:**
```bash
curl "http://localhost:8000/api/memory/legal_brief_20260405_120000"
```

---

### Search Semantic Memory

```
POST /api/memory/search
```

Perform semantic similarity search across all stored memories.

**Request Body:**
```json
{
  "query": "theft and occupancy requirements",
  "limit": 5,
  "threshold": 0.7
}
```

**Response:**
```json
{
  "query": "theft and occupancy requirements",
  "results": [
    {
      "key": "precedent_1",
      "value": "Indian Supreme Court ruling...",
      "similarity_score": 0.89,
      "stored_at": "2026-04-05T11:55:00.000Z",
      "run_id": "legal_brief_20260405_120000"
    },
    {
      "key": "precedent_2",
      "value": "High Court decision...",
      "similarity_score": 0.76,
      "stored_at": "2026-04-05T11:50:00.000Z",
      "run_id": "legal_brief_20260405_110000"
    }
  ],
  "retrieval_time_ms": 42
}
```

**Example:**
```bash
curl -X POST "http://localhost:8000/api/memory/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "theft and occupancy",
    "limit": 5,
    "threshold": 0.7
  }'
```

---

## Replay Endpoints

### Trigger Deterministic Replay

```
POST /api/replay/{run_id}
```

Start a deterministic replay of a historical run.

**Request Body** (optional):
```json
{
  "async": false,
  "compare_with_live": true
}
```

**Parameters:**
- `async` (optional, default=false): Run replay asynchronously
- `compare_with_live` (optional, default=true): Compare replayed output against original

**Response** (sync mode):
```json
{
  "status": "success",
  "original_run_id": "legal_brief_20260405_120000",
  "replay_run_id": "legal_brief_20260405_120000_replay_001",
  "fidelity": 1.0,
  "output_match": "byte_identical",
  "divergence_points": [],
  "duration_ms": 2150
}
```

**Response** (async mode):
```json
{
  "status": "started",
  "replay_task_id": "replay_task_uuid_here",
  "check_status_at": "/api/replay-tasks/replay_task_uuid_here"
}
```

**Example:**
```bash
# Synchronous replay with comparison
curl -X POST "http://localhost:8000/api/replay/legal_brief_20260405_120000" \
  -H "Content-Type: application/json" \
  -d '{
    "async": false,
    "compare_with_live": true
  }'
```

---

### Get Replay Status

```
GET /api/replay-tasks/{task_id}
```

Check status of asynchronous replay.

**Response:**
```json
{
  "task_id": "replay_task_uuid_here",
  "status": "completed",
  "original_run_id": "legal_brief_20260405_120000",
  "replay_run_id": "legal_brief_20260405_120000_replay_001",
  "fidelity": 1.0,
  "output_match": "byte_identical",
  "divergence_points": [],
  "completion_time": "2026-04-05T12:05:00.000Z"
}
```

**Example:**
```bash
curl "http://localhost:8000/api/replay-tasks/replay_task_uuid_here"
```

---

## Evaluation Endpoints

### Run Regression Tests

```
POST /api/evals/run-regressions
```

Trigger the DeepEval regression suite.

**Request Body** (optional):
```json
{
  "filter": "happy_path",
  "async": true
}
```

**Parameters:**
- `filter` (optional): Run specific test case (e.g., "happy_path", "retry_recovery")
- `async` (optional, default=false): Run asynchronously

**Response:**
```json
{
  "status": "started",
  "eval_task_id": "eval_task_uuid_here",
  "cases": [
    "happy_path",
    "retry_recovery",
    "outage_degradation",
    "bad_model_divergence_replay"
  ],
  "check_status_at": "/api/eval-tasks/eval_task_uuid_here"
}
```

**Example:**
```bash
curl -X POST "http://localhost:8000/api/evals/run-regressions" \
  -H "Content-Type: application/json" \
  -d '{"async": true}'
```

---

### Get Evaluation Results

```
GET /api/eval-tasks/{task_id}
```

Retrieve evaluation results.

**Response:**
```json
{
  "task_id": "eval_task_uuid_here",
  "status": "completed",
  "cases": [
    {
      "name": "happy_path",
      "passed": true,
      "confidence": 0.99,
      "details": {
        "duration_ms": 3200,
        "assertions_passed": 12,
        "assertions_failed": 0
      }
    },
    {
      "name": "retry_recovery",
      "passed": true,
      "confidence": 0.97,
      "details": {
        "simulated_failures": 2,
        "successful_retries": 2
      }
    }
  ],
  "overall_pass_rate": 0.98,
  "completion_time": "2026-04-05T12:10:00.000Z"
}
```

**Example:**
```bash
curl "http://localhost:8000/api/eval-tasks/eval_task_uuid_here"
```

---

## Statistics Endpoints

### Get Execution Statistics

```
GET /api/stats
```

Returns aggregated statistics across all runs.

**Response:**
```json
{
  "total_runs": 1247,
  "average_duration_ms": 3450,
  "p50_latency_ms": 2800,
  "p95_latency_ms": 8200,
  "p99_latency_ms": 12100,
  "success_rate": 0.985,
  "retry_rate": 0.23,
  "circuit_breaker_activations": 89,
  "fallback_executions": 56,
  "average_tool_calls_per_run": 3.2,
  "date_range": {
    "from": "2026-04-01T00:00:00.000Z",
    "to": "2026-04-05T23:59:59.999Z"
  }
}
```

**Parameters:**
- `from` (optional): Start date (ISO 8601)
- `to` (optional): End date (ISO 8601)
- `workflow` (optional): Filter by workflow name

**Example:**
```bash
curl "http://localhost:8000/api/stats?workflow=legal_brief&from=2026-04-01T00:00:00Z"
```

---

## Circuit Breaker Status Endpoint

### Get Circuit Breaker State

```
GET /api/circuit-breakers
```

Returns current state of all circuit breakers.

**Response:**
```json
{
  "circuit_breakers": [
    {
      "tool_name": "search_indian_kanoon",
      "state": "CLOSED",
      "failure_count": 1,
      "window_size": 10,
      "failure_rate": 0.1,
      "last_transition": "2026-04-05T12:00:00.000Z",
      "cooldown_until": null
    },
    {
      "tool_name": "filter_relevance",
      "state": "HALF_OPEN",
      "failure_count": 7,
      "window_size": 10,
      "failure_rate": 0.7,
      "last_transition": "2026-04-05T11:50:00.000Z",
      "cooldown_until": "2026-04-05T11:51:30.000Z"
    }
  ]
}
```

**Example:**
```bash
curl "http://localhost:8000/api/circuit-breakers"
```

---

## OpenAPI / Swagger

**Interactive API documentation:**

```
http://localhost:8000/docs
```

Or ReDoc (alternative UI):

```
http://localhost:8000/redoc
```

Both include:
- Full endpoint descriptions
- Request/response schemas
- "Try it out" buttons for testing
- Authentication headers (if configured)

---

## Error Responses

All endpoints return standard error responses:

### 400 Bad Request

```json
{
  "status": 400,
  "error": "bad_request",
  "message": "Invalid limit parameter: expected int, got str",
  "details": {
    "parameter": "limit",
    "value": "abc"
  }
}
```

### 404 Not Found

```json
{
  "status": 404,
  "error": "not_found",
  "message": "Run 'unknown_run_id' not found in trace store",
  "details": {
    "run_id": "unknown_run_id"
  }
}
```

### 500 Internal Server Error

```json
{
  "status": 500,
  "error": "internal_server_error",
  "message": "Unexpected error during replay",
  "details": {
    "exception_type": "RuntimeError",
    "traceback": "..."
  }
}
```

---

## Rate Limiting

Currently, no rate limits are enforced. In production:
- Implement per-IP rate limiting (10 req/sec for reads, 5 req/sec for writes)
- Add API key quotas (enterprise tier)
- See [OPERATIONS.md](./OPERATIONS.md) for guidance

---

## Pagination

List endpoints support cursor-based pagination:

```
?limit=50&offset=0
```

Returns:
```json
{
  "results": [...],
  "total": 1247,
  "limit": 50,
  "offset": 0,
  "has_more": true
}
```

---

## Versioning

API version: **v1**

All endpoints are prefixed with `/api/v1` (currently `/api` for backward compatibility).

Future breaking changes will use `/api/v2`.

---

## Examples

### Complete Workflow

```bash
# 1. List runs
curl "http://localhost:8000/api/runs?limit=5"

# 2. Get details of first run
RUN_ID=$(curl -s "http://localhost:8000/api/runs?limit=1" | jq -r '.runs[0].run_id')
curl "http://localhost:8000/api/runs/$RUN_ID"

# 3. Inspect memory state
curl "http://localhost:8000/api/memory/$RUN_ID"

# 4. Trigger deterministic replay
curl -X POST "http://localhost:8000/api/replay/$RUN_ID" \
  -H "Content-Type: application/json" \
  -d '{"async": false, "compare_with_live": true}'

# 5. Check execution statistics
curl "http://localhost:8000/api/stats"

# 6. View circuit breaker state
curl "http://localhost:8000/api/circuit-breakers"
```

---

## SDKs / Client Libraries

**Python:**
```python
import httpx

client = httpx.AsyncClient(base_url="http://localhost:8000/api")
runs = await client.get("/runs?limit=10")
print(runs.json())
```

**JavaScript/TypeScript:**
```typescript
const response = await fetch("http://localhost:8000/api/runs?limit=10");
const data = await response.json();
console.log(data);
```

**cURL:**
See examples above.

---

## Support

- **API Issues:** GitHub Issues
- **Documentation:** See [docs/](./docs/)
- **Community:** Discussion forum
