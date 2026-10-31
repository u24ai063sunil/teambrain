# 🧠 TeamBrain: shared, self-correcting memory for a team's AI agents

**MongoDB Student Hackathon · Track 1: Agentic Memory**

Every AI agent on a team starts from zero. TeamBrain gives all of them one long-term memory, stored in MongoDB, that:
- **remembers** decisions, constraints, facts, preferences, outcomes, feedback and strategies from normal conversation
- **changes behaviour**: Bob's agent follows a rule Alice told *her* agent an hour ago, in a brand-new session
- **explains itself**: every answer shows which memories were used, why they ranked, and where they came from
- **handles the hard cases**: corrections (versioned supersede), conflicts (asks before overwriting a teammate), expiry (TTL), forgetting (soft + hard delete with audit), privacy (private scope + secret redaction)
- **syncs live** across users and agents through **change streams**, and plugs into any coding agent via **MCP**

See `DEMO.md` for the stage script and `PLAN.md` for the strategy and judge Q&A.

---

## Quick start

```bash
cd Mongodb-Uncharted
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
# (or: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)
source .venv/bin/activate

cp .env.example .env          # then fill it in (see "Atlas setup" below)
python scripts/setup_db.py    # collections, validator, TTL + regular indexes, Atlas Vector Search index
python scripts/seed.py        # demo workspace with background memories (re-run to reset)
python scripts/doctor.py      # pre-flight check: everything should be ✔
uvicorn app.main:app --port 8000
# open http://localhost:8000
```

Without API keys the app still runs (an offline mock LLM and fake embeddings, flagged in yellow in the top bar), but the real demo needs `VOYAGE_API_KEY` plus an LLM: `ANTHROPIC_API_KEY`, **or a free provider** (below).

### Free LLM option (Groq / Gemini / Ollama)
Any OpenAI-compatible `/chat/completions` endpoint works. In `.env`:
```
LLM_PROVIDER=groq            # or gemini, ollama, openrouter, openai
LLM_API_KEY=gsk_...          # Groq: console.groq.com/keys · Gemini: aistudio.google.com/apikey · Ollama: leave empty
# LLM_CHAT_MODEL=...         # optional; defaults: groq openai/gpt-oss-120b, gemini gemini-flash-latest, ollama llama3.1:8b
```
Leave `ANTHROPIC_API_KEY` empty, or set `LLM_PROVIDER` explicitly. `doctor.py` checks the model answers. Free tiers have low rate limits (requests per minute), and every chat message costs 2 calls, so don't spam during the demo.

**Backup provider (recommended for the demo):** if the primary errors or stays rate limited for more than ~5 s, each call switches to a second free provider:
```bash
LLM_FALLBACK_PROVIDER=gemini
LLM_FALLBACK_API_KEY=...     # aistudio.google.com/apikey
# LLM_FALLBACK_MODEL=...     # optional; defaults to the provider preset
```

## Atlas setup (about 10 minutes)

1. **Create a cluster:** https://cloud.mongodb.com, then *Create* → **M0 Free** (any region). M0 supports Vector Search, TTL and change streams.
2. **Database user:** *Security → Database Access → Add new user*, with password auth and the **Atlas admin** role (simplest; it also lets `setup_db.py` manage the schema validator and search index).
3. **Network access:** *Security → Network Access → Add IP*. Add your current IP (or `0.0.0.0/0` for the hackathon Wi-Fi).
4. **Connection string:** *Connect → Drivers → Python*. Copy the `mongodb+srv://...` string, put the password in, and paste it into `.env` as `MONGODB_URI`.
5. **Voyage AI key:** https://dash.voyageai.com → API keys → `VOYAGE_API_KEY`. (If you're using an Atlas *Model API key* instead, also set `VOYAGE_BASE_URL=https://ai.mongodb.com/v1`.)
6. **Anthropic key:** https://console.anthropic.com → `ANTHROPIC_API_KEY`.
7. Run `python scripts/setup_db.py`. It creates the vector index and **waits until it is READY** (about 1–2 minutes the first time).
8. Run `python scripts/seed.py`, then `python scripts/doctor.py`.

> If you change `EMBED_DIM` or the embedding model, re-run `setup_db.py` (it updates the index) **and** `seed.py` (it re-embeds everything).

## Architecture

```
 Browser (2 agent chats + live memory panel + inspectors)
     │ REST /api/chat …                ▲ SSE /api/stream
     ▼                                 │
 FastAPI ──────────────────────────────┴── change stream on `memories` (fullDocument: updateLookup)
   chat turn:
   1. guard.redact()      secrets removed BEFORE the LLM or DB ever sees them
   2. recall()            Voyage query embedding → $vectorSearch (pre-filter: workspace, status, scope/owner)
                          → expireAt > now → re-rank: 0.8·sim + 0.1·recency(half-life per type) + 0.1·importance
   3. memory manager      Claude Haiku 5.5, structured JSON output: add / supersede / conflict / forget / reinforce
   4. apply_operations()  deterministic rules on top of the LLM:
                            · never store secrets   · never silently overwrite a teammate's decision/constraint
                            · conflict with yourself = correction   · unknown ids → treated as new
   5. recall() again      memory may have just changed
   6. responder           Claude Opus 5.5: answers, applies constraints, cites [M1]
   7. log                 messages, retrievals (score breakdown), memory_events (audit)

 MCP server (scripts/mcp_server.py): recall_team_memory / remember / forget_memory on the same collection
```

### Collections

| Collection | Purpose |
|---|---|
| `memories` | One document per memory: `owner_id, workspace_id, scope, type, content, embedding, source{kind, session_id, message_id, quote}, importance, confidence, status (active/superseded/deleted), version, supersedes, superseded_by, access_count, created_at, updated_at, last_accessed_at, expireAt` |
| `memory_events` | Append-only audit log: created / superseded / conflict_flagged / reinforced / deleted / hard_deleted |
| `retrievals` | For each answer: query, engine, pre-filter, weights, and per-memory sim / recency / importance / final / cited |
| `messages` | Chat history per session |

### Indexes

| Index | Why |
|---|---|
| Vector Search `memory_vec`: `embedding` (cosine, 1024d) + filter fields `workspace_id, owner_id, scope, status, type` | Semantic recall with privacy and lifecycle enforced inside the query |
| `expireAt` TTL (`expireAfterSeconds: 0`) | Per-document expiry for temporary memories, and later purging of soft-deleted ones |
| `{workspace_id, status, updated_at}`, `{workspace_id, owner_id, type}` | Memory panel and admin queries |
| `memory_events {memory_id, ts}`, `messages {session_id, created_at}`, `retrievals {message_id}`, `{results.memory_id}` | Inspector lookups |
| `$jsonSchema` validator on `memories` | Required fields and enums for type, scope and status, with flexible extra fields |

## Configuration (`.env`)

| Var | Default | Notes |
|---|---|---|
| `MONGODB_URI` / `DB_NAME` | | `teambrain` |
| `VOYAGE_API_KEY`, `VOYAGE_MODEL`, `EMBED_DIM` | `voyage-3.5`, 1024 | Same model for documents and queries (`input_type` differs) |
| `ANTHROPIC_API_KEY` | | Paid option |
| `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_CHAT_MODEL`, `LLM_MEMORY_MODEL`, `LLM_BASE_URL` | auto | Free option: `groq` / `gemini` / `ollama` / `openrouter` / `openai` |
| `LLM_FALLBACK_PROVIDER`, `LLM_FALLBACK_API_KEY`, `LLM_FALLBACK_MODEL`, `LLM_FALLBACK_BASE_URL` | off | Backup OpenAI-compatible provider used when the primary fails |
| `CHAT_MODEL` | `claude-opus-5-5` | Answers. Server-side refusal fallback (`fallbacks: "default"`) is enabled for it |
| `MEMORY_MODEL` | `claude-haiku-5-5` | Fast structured-output memory manager |
| `CHAT_EFFORT` | `low` | Raise to `medium` for richer answers (slower) |
| `MIN_SIM`, `RECALL_K` | 0.55, 6 | Similarity floor (normalised score) and number of recalled memories |
| `LLM_PROVIDER` / `EMBED_PROVIDER` | auto | Force `mock` / `fake` for offline testing |

## Scripts

| Command | What it does |
|---|---|
| `python scripts/setup_db.py` | Idempotent DB setup, including creating the vector index and waiting for it |
| `python scripts/seed.py` | Reset the demo workspace (also the **↺ Reset demo** button) |
| `python scripts/doctor.py` | Checks connection, replica set, change streams, TTL, vector index status and dims, embeddings, recall and both Claude models |
| `python scripts/eval.py` | Retrieval eval: hit@1, recall@k, MRR, and privacy/superseded violations. Put the numbers on a slide |
| `scripts/mcp_server.py` | MCP server for Claude Code and other agents (below) |

## Use it from Claude Code (MCP)

```bash
claude mcp add teambrain -e TEAMBRAIN_USER=bob -- "$PWD/.venv/bin/python" "$PWD/scripts/mcp_server.py"
claude   # then: "add a script that bulk-syncs orders to the payments API"
```
The agent calls `recall_team_memory`, picks up pnpm and the rate limit, and anything it `remember`s appears live in the web UI.

## Troubleshooting

- **Top bar says "Recall: exact scan (no Atlas index)":** you're not on Atlas, or `$vectorSearch` errored. Run `doctor.py`.
- **Recall returns nothing on Atlas:** the vector index is still building, or `numDimensions` ≠ `EMBED_DIM`. Run `setup_db.py`, then `seed.py`.
- **TTL memory shows "expired, awaiting TTL monitor":** that's normal. MongoDB's TTL monitor runs about every 60 s, and recall already ignores the memory.
- **Change stream not "live":** the connection must be to a replica set (Atlas always is).
- **`ServerSelectionTimeoutError`:** your IP isn't in Atlas Network Access, or the URI or password is wrong.
