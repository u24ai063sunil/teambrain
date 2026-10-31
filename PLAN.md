# PS-1 (Track 1: Agentic Memory): 7-Hour Winning Plan

## 1. What the judges will actually score (read from the PS)

The brief is open-ended, but it lists very specific checkpoints. **The winning strategy is to hit every one of them visibly in the demo** and be able to defend each design choice.

| PS requirement | How we prove it on stage |
|---|---|
| Persistent memory across sessions | Close session, open a new one (even a different user) and memory is still there |
| *Decide* what to remember, retrieve, update, correct, forget | An LLM "memory manager" decides ADD / UPDATE / SUPERSEDE / IGNORE / CONFLICT for each candidate |
| Memory types: facts, preferences, decisions, outcomes, constraints, feedback, strategies | `type` field, color-coded chips in UI |
| Memory **visibly changes** the next response or action | Side-by-side: same question with memory OFF vs ON (toggle) |
| Inspect memory + explain **why it was selected** | "Why this memory?" panel: similarity, recency, importance, final score |
| One difficult case (correction / expiry / deletion / conflict / private) | **Do all five.** Most teams will do one. This is the differentiator. |
| Memory = document with owner, type, content, source, timestamps, lifecycle | Exact schema below |
| Vector Search with filters (user, workspace, type, recency) | `$vectorSearch` with pre-filter + recency re-rank |
| TTL index for expiry; app logic for other rules | `expireAt` TTL index + soft-delete/supersede in app |
| Change streams for multi-user/agent live updates | Second user's memory panel updates live while first user chats |
| "Explain architecture, data model, choices" | 1 architecture slide + prepared Q&A (section 9) |
| "Keep input small, setup repeatable, one workflow end to end" | `seed.py` / `make setup` creates indexes + demo data in one command |

## 2. The idea (recommended): **TeamBrain, shared memory for a team's AI agents**

Based on the PS example "Shared Context for Teammates' Agents". It uses **every** MongoDB feature listed (change streams only make sense with multiple users/agents), and developer judges will recognize the problem straight away.

**Pitch line:** *"Every AI agent on your team starts from zero. TeamBrain gives them a shared, self-correcting memory of your team's decisions, constraints and preferences. Private things stay private, stale things expire, and wrong things get corrected."*

**Setup:** one workspace ("Project Phoenix"), two users (Alice: backend, Bob: frontend), each with their own agent chat. A shared memory panel updates live.

### Demo script (write this FIRST, build ONLY what it needs)
1. **Create memory.** Alice chats: *"We decided to use pnpm, not npm. The payments API is rate-limited at 100 req/s. Code freeze on new dependencies starts Wednesday."*
   Memory panel (in Bob's window too, via change stream) shows 3 memories appear: `decision`, `constraint`, `constraint`.
2. **Memory changes behavior (new session, different user).** Bob opens a fresh session: *"Write a script to install a charting lib and bulk-sync 10k orders to payments."*
   Bob's agent uses `pnpm add`, throttles to 100 req/s, and warns about the dependency freeze, citing `[decision by Alice, 3 min ago]`.
   **Toggle "memory OFF"** and the same prompt gives generic npm code with no throttling. That contrast is the money shot.
3. **Inspect + explain.** Click a citation. The "Why selected" panel shows similarity 0.82, recency 0.97, importance 0.9, final score 0.86, the source message it came from, and the audit history.
4. **Hard cases (rapid fire, about 20 s each):**
   - **Correction:** Alice: *"Update: payments limit raised to 500 req/s."* The old memory is struck through as `superseded` and linked to the new version. Bob re-asks and gets 500.
   - **Conflict:** Bob: *"We use npm here."* The agent does not silently overwrite. It says: *"This conflicts with Alice's decision (pnpm, 10 min ago). Which is correct?"*
   - **Expiry (TTL):** Alice: *"Staging DB is down for the next 2 minutes."* The memory shows a countdown, then disappears. Explain the TTL index and the query-time `expireAt` guard.
   - **Private info:** Alice: *"I prefer very terse answers"* is stored as `scope: private`, so it affects Alice's agent only. *"My AWS key is AKIA…"* is **refused/redacted** and never stored (show the toast).
   - **Forget:** *"Forget everything about the staging DB"* soft-deletes the memory, and the audit log shows who deleted it and when.
5. **(Stretch wow)** Same memory exposed as an **MCP server**, so a real Claude Code session in a terminal recalls "use pnpm" by itself.

> If your team strongly prefers another domain (e.g. Incident Response Agent, Decision Assistant), keep the **exact same engine and demo structure**. Only the prompts and seed data change.

## 3. Architecture

```
 ┌──────────── React UI (2 chat panes + live memory panel + inspector) ───────────┐
 │        REST: /chat /memories /forget        SSE: /stream (change stream)       │
 └───────────────────────────────┬────────────────────────────────────────────────┘
                     FastAPI (or Next.js API routes)
   ┌───────────────┬─────────────┴──────────────┬──────────────────────────┐
   │ 1. RECALL     │ 2. RESPOND                 │ 3. REMEMBER (background)  │
   │ embed query → │ LLM + top-k memories       │ LLM extracts candidates → │
   │ $vectorSearch │ injected; must cite [M#]   │ PII guard → nearest-match │
   │ + filters →   │                            │ → ADD/UPDATE/SUPERSEDE/   │
   │ re-rank → log │                            │   CONFLICT/IGNORE → write │
   └───────┬───────┴────────────────────────────┴────────────┬─────────────┘
           └──────────────── MongoDB Atlas ───────────────────┘
     memories (vector idx + TTL idx) | memory_events (audit) | messages | retrievals
                     change stream on memories ──► SSE ──► UI
```

**Key design choice to call out:** the REMEMBER step runs *after* the response is sent (background task). The user never waits on memory writes, and the UI still updates live through the change stream.

### Stack (default; swap for whatever your team is fastest in)
- **DB:** MongoDB Atlas M0 (free; supports Vector Search + change streams). Alternative: `atlas deployments setup --type local`.
- **Embeddings:** **Voyage AI** `voyage-3.5-lite` or `voyage-3.5` (1024 dims). It's MongoDB's own embedding company, so judges notice. MongoDB automated embedding is an option if your cluster supports it, but don't bet the demo on a preview feature.
- **LLM:** any model you have a key for (e.g. Claude Haiku for fast extraction, Claude Sonnet for responses). Use JSON/structured output for the extraction step.
- **Backend:** Python FastAPI + `pymongo` (async) + SSE. **Frontend:** React (Vite) + Tailwind.

## 4. Data model

### `memories`
```js
{
  _id, workspace_id: "phoenix", owner_id: "alice", agent_id: "alice-agent",
  scope: "workspace" | "private",
  type: "fact" | "preference" | "decision" | "constraint" | "outcome" | "feedback" | "strategy",
  content: "Use pnpm, not npm, for all package installs",
  embedding: [/* 1024 floats */],
  source: { kind: "chat" | "manual" | "feedback", session_id, message_id, quote: "We decided to use pnpm…" },
  importance: 0.9,            // LLM-assigned 0..1
  confidence: 0.95,
  sensitivity: "normal" | "personal",   // secrets are never stored
  status: "active" | "superseded" | "deleted",
  version: 2, supersedes: ObjectId, superseded_by: null,
  tags: ["tooling"],
  created_at, updated_at, last_accessed_at, access_count,
  expireAt: ISODate | absent   // only for time-bound memories → TTL
}
```

### `memory_events` (audit log, which powers "history" and "who deleted this")
`{ memory_id, workspace_id, actor, action: "created|retrieved|superseded|conflict_flagged|deleted|expired", reason, ts, diff }`

### `retrievals` (powers "why was this selected")
`{ message_id, query, results: [{ memory_id, sim, recency, importance, final }], ts }`

### `messages`, `sessions`: plain chat history.

### Indexes (all created by `setup.py`)
```js
// Vector Search index "memory_vec" on memories
{ "fields": [
  { "type": "vector", "path": "embedding", "numDimensions": 1024, "similarity": "cosine" },
  { "type": "filter", "path": "workspace_id" },
  { "type": "filter", "path": "owner_id" },
  { "type": "filter", "path": "scope" },
  { "type": "filter", "path": "status" },
  { "type": "filter", "path": "type" }
]}
db.memories.createIndex({ expireAt: 1 }, { expireAfterSeconds: 0 })          // TTL (per-doc expiry)
db.memories.createIndex({ workspace_id: 1, status: 1, updated_at: -1 })      // memory panel listing
db.memory_events.createIndex({ memory_id: 1, ts: -1 })
```
Bonus rigor (10 min): a `$jsonSchema` validator on `memories` requiring `owner_id, type, content, status, created_at`.

### Recall query
```js
[
  { $vectorSearch: {
      index: "memory_vec", path: "embedding", queryVector: q,
      numCandidates: 100, limit: 20,
      filter: { workspace_id: W, status: "active",
                $or: [ { scope: "workspace" }, { owner_id: U } ] } } },
  { $match: { $or: [ { expireAt: { $exists: false } }, { expireAt: { $gt: new Date() } } ] } },
  { $project: { embedding: 0, sim: { $meta: "vectorSearchScore" } } }
]
```
Then re-rank in app: `final = 0.8·sim + 0.1·recency + 0.1·importance` (tuned with the eval: hit@1 0.70 → 1.00), with
`recency = exp(-ln2 · age / half_life[type])` (preferences/decisions decay slowly, situational facts decay fast). Keep top 5, log to `retrievals`, and bump `last_accessed_at`.

## 5. Memory manager logic (the "brain", where to spend prompt effort)

For each user turn, after the response is sent:
1. **Extract:** LLM returns `[{type, content, importance, ttl_seconds|null, sensitivity, is_secret}]`. Instruct it to be selective: only durable, useful facts, no chit-chat.
2. **Guard:** if `is_secret` or a regex matches (API keys, passwords, card numbers), **drop it** and emit a `redacted` event, which shows a toast. If `sensitivity: personal`, force `scope: private`.
3. **Reconcile:** vector-search the top 3 *active* memories of the same workspace (and type). Give the LLM the candidate and the neighbors. It answers one of:
   - `ADD`: new memory.
   - `UPDATE`: same meaning, refine wording or bump confidence.
   - `SUPERSEDE`: new info replaces old. Old doc gets `status: superseded, superseded_by`; new doc gets `supersedes, version+1`.
   - `CONFLICT`: contradicts a memory **owned by someone else**, or one with high confidence. Don't write. Return a clarification question to the user.
   - `IGNORE`: duplicate.
4. **Expiry:** if `ttl_seconds`, set `expireAt = now + ttl`.
5. **Forget intent:** detect "forget …" in the chat (or a 🗑 button), vector-match the targets, soft-delete with `status: deleted`, and log the event. Optional hard-delete button for the "right to be forgotten" story.
6. **Feedback loop (cheap, strong):** a 👍/👎 on an agent reply creates a `feedback` memory ("Bob dislikes long explanations"). This covers "learned strategies".

## 6. Hour-by-hour (7 h, assuming a team of 3; compress roles if fewer)

| Time | **P1: Backend / Memory engine** | **P2: Frontend** | **P3: LLM prompts, data, demo, pitch** |
|---|---|---|---|
| **0:00–0:30** | Atlas cluster, DB user, IP allowlist, `.env`. **Create vector index NOW** (it takes minutes to build) | Vite + Tailwind skeleton, layout: 2 chat panes + memory panel | Finalize demo script (section 2). Get Voyage + LLM keys and test both with curl |
| **0:30–2:00** | `setup.py` (collections, indexes, validator). `recall()` with `$vectorSearch` + re-rank + logging. `/chat` endpoint | Chat pane component, user switcher (Alice/Bob), memory list with type chips | Extraction prompt + reconcile prompt with JSON output. Test on 10 sample utterances |
| **2:00–3:00** | `remember()` background task: extract → guard → reconcile → write + events | SSE client: memory panel live-updates from the change stream | Seed data (`seed.py`) for a realistic workspace with about 15 memories |
| **3:00–4:00** | Change stream → `/stream` SSE. TTL + `expireAt` guard. Forget endpoint | "Why selected" inspector drawer (scores, source quote, history). Citation chips in replies | Response prompt: must cite `[M1]` and obey constraints. **Memory ON/OFF toggle** |
| **4:00–5:00** | Conflict + supersede paths, PII guard, private scope | Superseded strikethrough + version chain, TTL countdown, redaction toast, conflict prompt UI | Run the full demo script end to end and log every bug |
| **5:00–5:45** | Bug fixes from run-through. **Feature freeze at 5:45** | Polish: empty states, loading spinners, colors | Small eval: 8 queries → expected memory IDs, report recall@5 (one slide) |
| **5:45–6:30** | *(stretch, only if everything works)* MCP server: `remember` / `recall` tools | Final UI polish | Architecture slide, README, **record a backup demo video** |
| **6:30–7:00** | Reset the DB with `seed.py`, rehearse twice, submit | | |

**Rules for the day:**
- Get one complete loop working (chat → memory stored → new session recalls it) **by hour 2:30**, even if it's ugly. Everything after that is improvement.
- Each hard case is about 30–45 min of work. Cut the stretch goal first, never the hard cases.
- Commit after every working milestone. Never demo uncommitted code.

## 7. Gotchas that will waste your time (pre-solved)
1. **Vector index build time / status.** Create it first. Queries return `[]` (not an error) until it is `READY`. M0 allows only 3 search indexes.
2. **Filter fields must be declared** as `"type": "filter"` in the vector index, or `$vectorSearch` filter fails.
3. **Dimension mismatch.** `numDimensions` must equal your embedding model's output (Voyage 3.5 = 1024). Use the same model for docs and queries. With Voyage, use `input_type="document"` vs `"query"`.
4. **TTL is not instant.** The TTL monitor runs about every 60 s. That's why the recall query also filters `expireAt > now`, so expired memories are never served even before deletion. **Say this to the judges; it shows you understand the feature.** For the demo, use 60–90 s TTLs.
5. **Change streams need a replica set.** Atlas has one; a plain local `mongod` does not.
6. **`$exists` isn't supported inside the `$vectorSearch` filter.** Do the expiry check in a following `$match` (as above).
7. **LLM latency on stage.** Use a fast model for extraction and run it in the background. Pre-warm by sending one request before demoing.
8. **Wi-Fi dies.** Have the recorded backup video ready.

## 8. Pitch (3 minutes)
1. **Problem (20 s):** "Your teammate's AI agent doesn't know what your agent learned an hour ago. Every session starts from zero, and naive 'save everything' memory goes stale, conflicts, and leaks secrets."
2. **Live demo (2 min):** follow the script in section 2.
3. **Under the hood (30 s):** one architecture slide: memory as a document with lifecycle, `$vectorSearch` + filters + recency re-rank, TTL, change streams, audit log.
4. **Close (10 s):** recall@5 number from your eval and what you'd build next (memory consolidation, per-team policies, MCP for any agent).

## 9. Judge Q&A, prepared answers
- **Why MongoDB / the document model?** A memory is one self-contained doc: content, embedding, lifecycle, provenance, and type-specific fields all together. No joins, and the vector plus metadata live in the same doc, so a pre-filtered vector search runs in one query.
- **Why supersede instead of overwrite?** Auditability and explainability. You can show what the agent believed then and why it changed. Also safe rollback.
- **Why soft delete + hard delete?** Soft delete for undo/audit; hard delete when privacy requires actual erasure.
- **How do you decide what to remember?** An LLM extracts with an importance score, then reconciles against nearest neighbors (dedupe/supersede/conflict). Secrets are filtered deterministically (regex) *before* any LLM decision.
- **Why the re-rank on top of vector score?** Semantic similarity alone ignores freshness and importance. A 6-month-old decision shouldn't outrank yesterday's correction.
- **How does it scale?** Shard on `workspace_id`. The vector pre-filter keeps searches tenant-scoped, and change streams can be filtered per workspace.
- **Privacy?** `scope: private` is enforced in the vector-search *pre-filter* (not just the UI), so other users' agents can't retrieve it.

## 10. Definition of done (checklist before demo)
- [ ] `python setup.py && python seed.py` sets up from scratch in < 1 min
- [ ] Memory created in session 1 changes the answer in a new session for another user
- [ ] Memory ON/OFF toggle shows a clear difference
- [ ] "Why selected" shows score breakdown + source quote + history
- [ ] Correction → superseded chain visible
- [ ] Conflict → clarification question, no silent overwrite
- [ ] TTL memory disappears on its own
- [ ] Private preference affects only its owner; secret never stored
- [ ] Forget → soft-deleted + audit event
- [ ] Live memory panel updates across two browser windows
- [ ] Backup video recorded, architecture slide ready, README done
