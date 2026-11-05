# 10. Engineering Decisions, Trade-Offs, and Architecture Comparisons

---

## 1. Overview

Every software architecture represents a series of intentional trade-offs. This document provides a rigorous architectural evaluation of the 9 core technical decisions in TeamBrain, contrasting the chosen approaches against industry alternatives.

---

## 2. Deep Architectural Comparisons

### Decision 1: MongoDB vs. Relational Databases (PostgreSQL)

| Aspect | MongoDB (Chosen) | Relational DB (PostgreSQL) |
|---|---|---|
| **Problem** | Storing polymorphic memory entities with co-located vectors, arbitrary source metadata, and lifecycle pointers. |
| **Approach** | Single BSON document per memory in the `memories` collection. | Normalized tables: `memories`, `sources`, `versions`, `vectors`. |
| **Advantages** | Zero joins; vector embedding, metadata, and lifecycle reside in one disk block; dynamic schema validation via `$jsonSchema`. | Strict relational foreign keys; ACID transactions across arbitrary tables. |
| **Disadvantages** | No multi-document cross-collection relational integrity constraints out of the box. | High join complexity to reconstruct memory provenance; vector indexing requires extensions (`pgvector`). |
| **When Alternative is Better** | When the core domain requires heavy multi-table analytical reporting or financial ledger consistency. |

---

### Decision 2: Atlas Vector Search vs. Standalone Vector Databases (Pinecone / Milvus / Qdrant)

| Aspect | MongoDB Atlas Vector Search (Chosen) | External Standalone Vector DB |
|---|---|---|
| **Problem** | Executing semantic similarity search with strict metadata pre-filtering (tenant, user, status). |
| **Approach** | Native Lucene HNSW index operating directly on the `embedding` field in MongoDB. | Dual-database stack: Relational/Doc DB for metadata + External Vector DB for vectors. |
| **Advantages** | **Zero Dual-Write Distributed Systems Risk**: Document metadata and vectors are written in one atomic operation. Pre-filters execute inside the query. Single operational vendor. | Highly specialized vector search optimizations (e.g. PQ, GPU quantization) on multi-billion scale datasets. |
| **Disadvantages** | Search index definitions on M0 free tier take 1–2 minutes to build; limited to 3 indexes on free tier. | Network hop between metadata DB and vector DB; two systems to monitor, back up, and secure; eventual consistency mismatch. |
| **When Alternative is Better** | When vector collections exceed 100 million embeddings and require specialized hardware-accelerated quantization. |

---

### Decision 3: Semantic Vector Retrieval vs. Keyword Search (BM25)

| Aspect | Semantic Vectors + Hybrid Re-Ranking (Chosen) | Pure Keyword Search (BM25 / Full-Text) |
|---|---|---|
| **Problem** | Matching user questions against stored constraints written in different phrasing or vocabulary. |
| **Approach** | Voyage AI 1024d embeddings with cosine distance + in-app keyword fallback. | Traditional term-frequency inverse-document-frequency text matching. |
| **Advantages** | Captures conceptual synonymy: *"how should I store prices?"* correctly retrieves *"Money amounts must be stored as integer cents"*. | Zero embedding latency; zero external API cost; exact identifier collision (e.g., error codes, UUIDs). |
| **Disadvantages** | Vulnerable to false positives on exact alphanumeric IDs unless pre-filtered; requires external API call. | Completely blind to synonyms, Hinglish, phrasing variations, or intent. |
| **When Alternative is Better** | For exact log searching, code-symbol lookups, or strict keyword filtering. |

---

### Decision 4: MongoDB Change Streams vs. Database Polling

| Aspect | Change Streams on `memories` (Chosen) | Periodic Client Polling (`GET /memories?since=t`) |
|---|---|---|
| **Problem** | Pushing database mutations to multiple connected agent interfaces in real time. |
| **Approach** | Asynchronous coroutine tailing the MongoDB replica set oplog via `memories.watch()`. | Browser intervals sending HTTP requests every 1–2 seconds. |
| **Advantages** | True event-driven architecture; sub-100ms notification latency; zero idle database query load. | Works on standalone single-instance databases without replica set oplogs. |
| **Disadvantages** | Requires a replica set (Atlas cluster or local `--replSet`). | Heavy query overhead on database; latency equal to polling window; wasted bandwidth. |
| **When Alternative is Better** | On legacy standalone SQL databases lacking change data capture (CDC) infrastructure. |

---

### Decision 5: Server-Sent Events (SSE) vs. WebSockets

| Aspect | Server-Sent Events (Chosen) | WebSockets |
|---|---|---|
| **Problem** | Delivering real-time memory feed updates from the backend to browser clients. |
| **Approach** | Unidirectional HTTP stream via `GET /api/stream` (`text/event-stream`). | Bidirectional persistent TCP socket connection. |
| **Advantages** | Native browser `EventSource` automatic reconnects; HTTP/2 multiplexing; proxy/firewall friendly; simpler server state. | Supports client-to-server upstream messages over the same socket. |
| **Disadvantages** | Unidirectional only (client-to-server requests use standard REST HTTP). | Fragile through enterprise proxies; requires custom heartbeat/reconnection logic. |
| **When Alternative is Better** | For high-frequency bidirectional interactions like multiplayer gaming or collaborative whiteboards. |

---

### Decision 6: Long-Term Memory Layer vs. Raw Conversation History (Context Stuffing)

| Aspect | Distilled Memory Documents (Chosen) | Raw Conversation History Stuffing |
|---|---|---|
| **Problem** | Managing context across multiple long-running developer sessions and agent instances. |
| **Approach** | Extracting declarative assertions into discrete documents; retrieving top $k=6$ candidates. | Appending every historical user and assistant message to the prompt. |
| **Advantages** | Predictable token consumption; eliminates attention dilution ("lost in the middle"); spans across independent users. | Zero extraction or embedding logic required. |
| **Disadvantages** | Requires an extra LLM call for memory extraction and reconciliation. | Rapidly hits token limits; high inference cost; loses all context when starting a new session. |
| **When Alternative is Better** | For short, single-session transactional tasks (e.g. translating a single document). |

---

### Decision 7: Hardware-Level TTL Indexes vs. Cron-Based Application Purging

| Aspect | MongoDB TTL Index + Query-Time Guard (Chosen) | Scheduled Cron Job (`DELETE WHERE expireAt < NOW()`) |
|---|---|---|
| **Problem** | Expiring transient environmental notices (*"staging down for 2 minutes"*). |
| **Approach** | MongoDB index `{ expireAt: 1 }` (`expireAfterSeconds: 0`) + query filter `expireAt > now()`. | Background Python cron task waking every minute to execute bulk deletes. |
| **Advantages** | Managed directly by the database engine; zero application worker threads; guaranteed physical reclamation. | Exact deterministic timing of physical deletion if cron runs frequently. |
| **Disadvantages** | Background TTL monitor thread runs on ~60-second intervals (solved by TeamBrain's query filter). | Worker process can crash; locks database collections during large bulk deletes; requires external scheduler. |
| **When Alternative is Better** | When complex multi-table cascading business logic must precede physical record deletion. |

---

### Decision 8: LLM Extraction + Deterministic Backend Validation vs. Pure LLM Autonomy

| Aspect | Hybrid: LLM Proposes, Python Enforces (Chosen) | Autonomous Agent (Raw Function Calling / ReAct) |
|---|---|---|
| **Problem** | Deciding when to mutate memory while guaranteeing security and data integrity. |
| **Approach** | LLM outputs structured operations; Python deterministic rules validate and execute mutations. | Agent directly executes raw database query tools (`insert`, `update`, `delete`). |
| **Advantages** | **Zero Unconstrained LLM Write Access**: Secrets and slander blocked 100% deterministically; unconfirmed teammate overrides prevented. | Maximum flexibility; agent can formulate arbitrary database queries. |
| **Disadvantages** | Requires maintaining validation logic in Python code. | Extremely vulnerable to prompt injection, accidental deletions, hallucinations, and silent overrides. |
| **When Alternative is Better** | For exploratory, read-only analytics where users want an agent to discover ad-hoc data insights. |

---

### Decision 9: Multi-Provider LLM Abstraction vs. Single-Provider Lock-In

| Aspect | Dual Provider + Automatic Fallback (Chosen) | Single-Provider Binding (e.g. OpenAI Only) |
|---|---|---|
| **Problem** | Preventing demo interruptions and operational downtime from provider rate limits or outages. |
| **Approach** | Provider abstraction supporting Anthropic, Groq, Gemini, Ollama with automatic retry-to-fallback switching. | Direct calls to a single proprietary SDK. |
| **Advantages** | Resilience against HTTP 429 rate limits; enables free-tier operation (Groq + Gemini); model specialization (Haiku for memory, Opus for chat). | Simpler codebase; leverages vendor-specific proprietary beta features. |
| **Disadvantages** | Requires maintaining compatibility across JSON schema formatting and prompt variances. | System goes down whenever the chosen provider experiences an outage or hits a quota ceiling. |
| **When Alternative is Better** | When an enterprise has an exclusive corporate licensing contract with a single cloud provider. |

---

## 3. Document Navigation

- Previous Document: [09. Security & Reliability](09-security-and-reliability.md)
- Next Document: [11. Evaluation & Testing](11-evaluation-and-testing.md)
- Complete Index: [Table of Contents](../README.md#documentation-index)

