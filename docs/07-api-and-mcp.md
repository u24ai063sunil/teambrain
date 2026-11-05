# 07. API and Model Context Protocol (MCP) Reference

---

## 1. REST API Specification

The FastAPI backend exposes a clean REST API running on port `8000`. All payloads are serialized to JSON. Embeddings (1024-float arrays) are stripped from JSON responses via `app/db.py: to_json()` to minimize network overhead.

---

### 1.1 Chat & Agent Endpoints

#### `POST /api/chat`
Executes an end-to-end multi-agent chat turn. Evaluates security redaction, memory retrieval, memory extraction mutations, grounded response generation, and access logging.

- **Request Headers**: `Content-Type: application/json`
- **Request Body (`ChatIn`)**:
  ```json
  {
    "user_id": "alice",
    "session_id": "8f3b2a1c4e9d",
    "message": "We decided to use pnpm, not npm. The payments API is rate limited to 100 requests per second.",
    "use_memory": true
  }
  ```
  - `user_id` (`string`, required): One of `"alice"` or `"bob"`.
  - `session_id` (`string`, optional): Alphanumeric session token (defaults to 12-char random hex).
  - `message` (`string`, required): Non-empty text, automatically stripped of whitespace, max length 8,000 characters.
  - `use_memory` (`boolean`, optional): When `false`, memory context is skipped for baseline comparison.
- **Response (`HTTP 200 OK`)**:
  ```json
  {
    "user_message": {
      "_id": "6724a1b8c9d0e1f2a3b4c501",
      "session_id": "8f3b2a1c4e9d",
      "user_id": "alice",
      "role": "user",
      "content": "We decided to use pnpm, not npm. The payments API is rate limited to 100 requests per second.",
      "redacted": [],
      "created_at": "2026-11-01T08:15:00.000Z"
    },
    "reply": "Got it! I've recorded the team decision to use pnpm [M1] and the payments API rate limit of 100 req/s [M2].",
    "message_id": "6724a1b8c9d0e1f2a3b4c505",
    "retrieval_id": "6724a1b8c9d0e1f2a3b4c506",
    "memories": [
      {
        "ref": "M1",
        "memory_id": "6724a1b8c9d0e1f2a3b4c502",
        "content": "The team uses pnpm, not npm, for package installs (decided by Alice).",
        "type": "decision",
        "scope": "workspace",
        "owner_id": "alice",
        "sim": 0.8921,
        "recency": 1.0,
        "importance": 0.9,
        "final": 0.9037,
        "cited": true
      }
    ],
    "actions": [
      {
        "kind": "add",
        "memory_id": "6724a1b8c9d0e1f2a3b4c502",
        "type": "decision",
        "scope": "workspace",
        "content": "The team uses pnpm, not npm, for package installs (decided by Alice).",
        "ttl_seconds": 0,
        "reason": "New team decision about tooling."
      }
    ],
    "engine": {
      "name": "atlas-vector-search",
      "detail": "$vectorSearch on index 'memory_vec'"
    },
    "use_memory": true
  }
  ```
- **Error Responses**:
  - `400 Bad Request`: Unknown `user_id` or invalid session format.
  - `422 Unprocessable Entity`: Blank/whitespace-only message or validation failure.

---

### 1.2 Memory Management Endpoints

#### `GET /api/memories`
Lists stored memories for the active workspace, with optional viewer and status filtering.

- **Query Parameters**:
  - `viewer` (`string`, default: `"all"`): `"all"` or specific user (`"alice"` / `"bob"`). Filters private memories.
  - `status` (`string`, default: `"all"`): `"all"`, `"active"`, `"superseded"`, or `"deleted"`.
- **Response (`HTTP 200 OK`)**: Array of memory documents up to 500 records, sorted by `created_at` descending.

#### `GET /api/memories/{memory_id}`
Retrieves deep diagnostic provenance for an individual memory document. Powers the Memory Inspector.

- **URL Parameter**: `memory_id` (`string`, valid 24-char hex ObjectId).
- **Response (`HTTP 200 OK`)**:
  ```json
  {
    "memory": { /* full memory document excluding raw embedding vector */ },
    "versions": [
      { "version": 1, "content": "Payments limit is 100 req/s", "status": "superseded" },
      { "version": 2, "content": "Payments limit was raised to 500 req/s", "status": "active" }
    ],
    "events": [
      { "action": "created", "actor": "alice", "ts": "2026-11-01T08:00:00Z" },
      { "action": "superseded", "actor": "alice", "by": "...", "ts": "2026-11-01T08:15:00Z" }
    ],
    "times_retrieved": 14
  }
  ```
- **Error Responses**: `400 Bad Request` (invalid ObjectId); `404 Not Found`.

#### `POST /api/memories`
Manually inserts a memory document bypassing conversational extraction.

- **Request Body (`MemoryIn`)**:
  ```json
  {
    "user_id": "alice",
    "content": "The primary database host is pg-cluster-01.internal",
    "type": "fact",
    "scope": "workspace",
    "importance": 0.7,
    "ttl_seconds": 0
  }
  ```
- **Error Response**: `422 Unprocessable Entity` if secrets are detected by `guard.redact`.

#### `DELETE /api/memories/{memory_id}`
Deletes or soft-deletes a memory document.

- **Query Parameters**:
  - `user_id` (`string`, required): Actor requesting deletion.
  - `hard` (`boolean`, default: `false`): `false` for soft-delete (retention scheduled); `true` for physical erasure.
- **Error Responses**:
  - `403 Forbidden`: If attempting to delete another user's private memory.
  - `404 Not Found`: If memory ID does not exist.

---

### 1.3 System & Real-Time Endpoints

#### `POST /api/feedback`
Captures thumbs-down user feedback on an answer, persisting a private feedback constraint.

- **Request Body (`FeedbackIn`)**:
  ```json
  {
    "user_id": "bob",
    "message_id": "6724a1b8c9d0e1f2a3b4c505",
    "text": "Do not include comments in Python code examples."
  }
  ```

#### `GET /api/stream` (Alias: `/api/events`)
Opens a long-lived Server-Sent Events (SSE) stream for live memory feed synchronization.

- **Query Parameter**: `viewer` (`string`, default: `"all"`).
- **Stream Format**: `text/event-stream` with keep-alive `: ping` pings.

#### `GET /api/config`
Returns global workspace metadata, configured embedding models, active LLM provider names, scoring weights, and change stream status.

#### `POST /api/reset`
Resets the demo workspace to a pristine state by clearing records and running `app/seed.py`.

---

## 2. Model Context Protocol (MCP) Server Reference

### 2.1 What is MCP and Why Does TeamBrain Use It?
The **Model Context Protocol (MCP)** is an open industry standard (initiated by Anthropic) that allows AI models and agent runtimes (such as Claude Code, Cursor, Windsurf, or custom autonomous agents) to interact with external tools and context providers over standardized JSON-RPC protocols.

By providing an MCP server (`scripts/mcp_server.py`), TeamBrain allows **any coding agent running in any IDE terminal** to tap into the same shared team memory.

```
       Terminal Developer Session
      ┌───────────────────────────┐
      │  Claude Code / Cursor CLI │
      └─────────────┬─────────────┘
                    │ stdio JSON-RPC
                    v
      ┌───────────────────────────┐
      │  scripts/mcp_server.py    │
      └─────────────┬─────────────┘
                    │ Direct Python Calls
                    v
      ┌───────────────────────────┐
      │  app/memory.py Core       │
      └─────────────┬─────────────┘
                    │ MongoDB Wire Protocol
                    v
      ┌───────────────────────────┐
      │   MongoDB Atlas Cluster   │
      └───────────────────────────┘
```

---

### 2.2 Registration with Claude Code

To connect Claude Code to TeamBrain, execute from the repository root:

```bash
claude mcp add teambrain -e TEAMBRAIN_USER=bob -- "$PWD/.venv/bin/python" "$PWD/scripts/mcp_server.py"
```

To connect for Alice instead:
```bash
claude mcp add teambrain -e TEAMBRAIN_USER=alice -- "$PWD/.venv/bin/python" "$PWD/scripts/mcp_server.py"
```

---

### 2.3 Available MCP Tools

#### Tool 1: `recall_team_memory`
Performs semantic vector search over team decisions, constraints, facts, preferences, and outcomes.

- **Input Arguments**:
  - `query` (`string`, required): Description of the task about to be performed (e.g. `"add a script that calls the payments API"`).
- **Execution**: Computes a query embedding with Voyage AI and invokes `app/memory.py: recall()`.
- **Output**: Formatted markdown listing candidate memories with type, content, author, and final score:
  ```markdown
  Relevant team memory (treat decisions and constraints as binding):
  - [decision] The team uses pnpm, not npm, for package installs (by Alice; id=6724a1b8c9d0; score=0.91)
  - [constraint] The payments API rate limit was raised to 500 requests per second (by Alice; id=6724a100c9d0; score=0.88)
  ```

#### Tool 2: `remember`
Saves a new durable memory for the team.

- **Input Arguments**:
  - `content` (`string`, required): Standalone third-person assertion (e.g. `"The search endpoint requires min_chars=3."`).
  - `type` (`string`, default: `"fact"`): `fact`, `preference`, `decision`, `constraint`, `outcome`, `feedback`, `strategy`.
  - `scope` (`string`, default: `"workspace"`): `"workspace"` (shared) or `"private"` (isolated to user).
  - `ttl_seconds` (`integer`, default: `0`): Expiry time in seconds for temporary information.
- **Security Check**: Passes text through `guard.redact()`. If secrets are present, the tool refuses to store the memory and returns a warning.
- **Output**: Confirmation string with created document ID:
  ```
  Saved decision memory 6724a59ec9d0e1f2a3b4c999 (workspace).
  ```

#### Tool 3: `forget_memory`
Soft-deletes an outdated or invalid memory document by ID.

- **Input Arguments**:
  - `memory_id` (`string`, required): Hex ObjectId string of the target memory.
- **Output**: Confirmation string or error if the user lacks authorization.

---

## 3. Document Navigation

- Previous Document: [06. Real-Time Synchronization](06-realtime-system.md)
- Next Document: [08. Setup & Deployment Guide](08-setup-and-deployment.md)
- Complete Index: [Table of Contents](../README.md#documentation-index)
