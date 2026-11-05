# 01. Project Overview: TeamBrain

---

## 1. Executive Summary

**TeamBrain** is a distributed, collaborative, long-term memory system designed for teams of AI coding agents and human software engineers. Stored and orchestrated using **MongoDB Atlas**, TeamBrain provides a shared, self-correcting context store that bridges the knowledge silos separating individual developer sessions, disparate AI tools, and distributed teammates.

In modern software engineering environments, software developers increasingly rely on AI coding assistants (such as Claude Code, Cursor, Copilot, or bespoke agentic workflows). However, these agents suffer from a fundamental architectural flaw: **they start every session from zero knowledge**. 

If Alice discovers a critical API rate limit, chooses a package manager, or configures a temporary database workaround at 10:00 AM, Bob’s agent at 11:00 AM remains completely oblivious to that fact. Bob’s agent will hallucinate outdated defaults, generate incompatible configuration scripts, or trip production rate limits—repeating mistakes the team had already resolved.

TeamBrain solves this by establishing a **shared, authoritative long-term memory layer**. Every agent reads from and writes to a central MongoDB-backed context engine. The system does not merely "save chat transcripts"; it actively analyzes dialogue, extracts durable assertions, updates existing beliefs through versioned superseding, catches contradictory team decisions before they cause outages, purges secrets before persistence, expires transient infrastructure notices via TTL indexes, and pushes live updates to all connected agents via MongoDB Change Streams and Server-Sent Events (SSE).

---

## 2. The Core Problem: Why Conventional AI Agents Struggle with Memory

To understand why TeamBrain is necessary, we must analyze the memory paradigms currently available in AI systems:

### 2.1 The Context Window Fallacy
A common misconception is that massive context windows (128K, 1M, or 2M tokens) eliminate the need for persistent memory systems. In practice:
1. **Cost & Latency Scalability**: Re-sending 500,000 tokens of project documentation, historical transcripts, and team chat on every user keystroke causes prohibitive API costs and introduces 5–15 second inference latency.
2. **Attention Degradation ("Lost in the Middle")**: As context length expands, transformer attention mechanisms degrade in precision. Critical negative constraints (e.g., *"never use floating-point numbers for currency"*) get diluted across thousands of lines of boilerplate.
3. **Session Fragmentation**: When a developer opens a new terminal tab, restarts their IDE, or switches tasks, their context window drops to zero. Context windows provide *scratchpad working memory*, not *episodic or semantic long-term memory*.

### 2.2 Individual vs. Isolated Memory Silos
Some contemporary agent frameworks introduce local key-value stores or vector caches (such as MemGPT/Letta or desktop-local sqlite vector databases). While this enables an individual user's agent to remember personal preferences across sessions, it creates **isolated knowledge silos**:
- Alice's agent learns in isolation on Alice's laptop.
- Bob's agent learns in isolation on Bob's laptop.
- The two agents cannot collaborate, cross-verify decisions, or alert their human operators to conflicting architectural choices.

---

## 3. Comparison of Agent Memory Architectures

| Dimension | Stateless Agents (Vanilla ChatGPT / Claude) | Individual Memory Agents (Local MemGPT / Desktop RAG) | TeamBrain (Shared Multi-Agent Memory) |
|---|---|---|---|
| **Context Retention Across Sessions** | ❌ None (resets to zero) | ✅ Yes (retained locally per user) | ✅ Yes (retained centrally in MongoDB Atlas) |
| **Cross-User Knowledge Sharing** | ❌ None | ❌ None (Alice cannot see Bob's learnings) | ✅ Instantaneous (Bob's agent adopts Alice's decisions) |
| **Conflict Detection** | ❌ Impossible | ❌ Impossible | ✅ Proactive (warns before overwriting teammate choices) |
| **Self-Correction & Versioning** | ❌ N/A | ⚠️ Overwrite or append-only duplicates | ✅ Explicit version chains (`v1 -> v2`, `supersedes`) |
| **Temporal Expiration** | ❌ N/A | ❌ Stale memories linger indefinitely | ✅ Automated hardware-level MongoDB TTL indexes |
| **Privacy & Access Control** | ❌ N/A | ⚠️ All or nothing local store | ✅ Pre-filtered database-level isolation (`workspace` vs `private`) |
| **Secret & Credential Safety** | ❌ Dependent on LLM guardrails | ⚠️ Stored in plaintext local vector store | ✅ Pre-LLM deterministic redaction & Luhn card verification |
| **External Agent Interoperability** | ❌ Proprietary | ⚠️ Tool-specific | ✅ Model Context Protocol (MCP) standard (Cursor, Claude Code) |

---

## 4. Target Use Cases

TeamBrain is built for real-world software engineering workflows where teams of developers and autonomous agents collaborate:

### 4.1 Engineering Decision & Tooling Synchronization
- **Scenario**: Alice decides during a debugging session: *"We decided to use pnpm, not npm, for package installs."*
- **TeamBrain Outcome**: A `decision` memory is extracted and stored. An hour later, Bob asks his agent: *"Write a script to install a charting library."* Bob's agent immediately generates `pnpm add recharts` and explicitly cites Alice's decision `[M1]`.

### 4.2 API Constraints, Rate Limits & Incident Workarounds
- **Scenario**: The payments team discovers that sandbox API endpoints fail under load: *"Payments API is rate-limited to 100 requests per second."* Later, the infrastructure team upgrades the tier: *"Payments API rate limit raised to 500 req/s."*
- **TeamBrain Outcome**: The original constraint is recorded. The subsequent correction creates version 2 (`v2`), automatically marks version 1 as `superseded`, and links them in an immutable audit chain. Bob's subsequent code generation uses 500 req/s without human intervention.

### 4.3 Transient Environment States (Automated Expiration)
- **Scenario**: A staging database goes offline for planned maintenance: *"Staging DB is down for the next 2 minutes."*
- **TeamBrain Outcome**: Stored with `ttl_seconds: 120`. A MongoDB TTL index physically purges the document after 2 minutes. Simultaneously, query-time filters prevent serving the memory even before the background TTL worker executes.

### 4.4 Collaborative Conflict Mediation
- **Scenario**: Alice records: *"We decided to use PostgreSQL for user profiles."* Days later, Bob (unaware of Alice's choice) tells his agent: *"We are using MongoDB for user profiles."*
- **TeamBrain Outcome**: TeamBrain recognizes that Bob is contradicting a teammate's `decision`. Instead of silently overwriting Alice's rule, it flags a `CONFLICT`, leaves the database unmutated, and prompts Bob: *"This contradicts Alice's decision. Do you explicitly confirm overriding this choice?"*

### 4.5 External Coding Agent Integration (MCP)
- **Scenario**: A developer operates inside an IDE terminal using an autonomous CLI tool like **Claude Code**.
- **TeamBrain Outcome**: Claude Code connects to TeamBrain's `scripts/mcp_server.py` via standard input/output (stdio). It automatically executes `recall_team_memory` before starting tasks and calls `remember` when new architectural insights emerge.

---

## 5. Core Capabilities

1. **Intelligent Memory Extraction**: Converts informal, noisy conversational dialogue (including Hinglish, slang, typos, and shorthand) into structured, third-person declarative statements.
2. **Hybrid Semantic Retrieval**: Blends 1024-dimensional dense vector embeddings (**Voyage AI**) with exponential recency decay (custom per memory type) and assigned importance weights.
3. **Deterministic Safety Shield**: Hardcoded regex and Luhn-algorithm filters intercept API keys, passwords, database URIs, and credit card numbers *before* text reaches an LLM or database.
4. **Defamation & HR Guardrails**: Deterministically blocks malicious or inappropriate personal claims regarding employment, health, or compensation.
5. **Multi-Model Database Layer**: Leverages MongoDB Atlas for vector search, document persistence, schema validation, TTL expiry, and audit trails.
6. **Reactive Change Streams**: Real-time server-side synchronization pushing memory lifecycle events over Server-Sent Events (SSE) to every open client with zero polling overhead.

---

## 6. System Boundaries & Explicit Limitations

To maintain engineering rigor, the following boundaries and current limitations of the implementation are documented:

- **Workspace Multi-Tenancy**: The current codebase defaults to a single active workspace identifier (`WORKSPACE_ID = "phoenix"`). While all database queries, vector pre-filters, and event streams include `workspace_id` for tenant isolation, there is currently no dynamic workspace management or cross-workspace migration API.
- **Synchronous Response Path**: Memory extraction runs *before* the assistant responds, ensuring that newly asserted memories or conflict warnings are immediately incorporated into the reply. This introduces slight latency (~1.5–3.0s) per chat turn when embeddings and LLM extraction run sequentially.
- **Single-Host In-Memory SSE Hub**: The FastAPI application manages SSE connections using an in-process Python `asyncio` set of subscriber queues. In a multi-instance, horizontally scaled deployment, an external pub/sub layer (such as Redis or MongoDB capped collections) would be required to broadcast change stream events across multiple server replicas.
- **Vector Search Index Limits**: In MongoDB Atlas free tier (M0), clusters are limited to 3 search indexes. Production clusters must account for index memory sizing.

---

## 7. Document Navigation

- Next Document: [02. System Architecture](02-system-architecture.md)
- Complete Index: [Table of Contents](../README.md#documentation-index)

