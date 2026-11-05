# 🧠 TeamBrain: Shared, Self-Correcting Memory for a Team's AI Agents

**MongoDB Student Hackathon · Track 1: Agentic Memory**

Every AI agent on a software team currently starts from zero knowledge. If Alice's agent learns a critical rate limit, chooses a package manager, or sets a temporary staging workaround, Bob’s agent remains completely oblivious, hallucinating outdated defaults and repeating mistakes.

**TeamBrain** gives them a shared, authoritative long-term memory layer stored in **MongoDB Atlas** that:
- **Remembers** decisions, constraints, facts, preferences, outcomes, feedback, and strategies from natural conversation.
- **Changes Behavior**: Bob's agent in a brand-new session adheres to a constraint Alice told *her* agent an hour earlier.
- **Explains Itself**: Every answer cites memories (`[M1]`, `[M2]`), and the Memory Inspector shows why each memory ranked, its provenance quote, and its audit history.
- **Handles the Hard Cases**: Versioned superseding (`v1 -> v2`), conflict detection (refuses silent overwrites of teammate choices), automated expiry via **MongoDB TTL indexes**, explicit forgetting (soft + hard delete), and deterministic pre-LLM secret redaction.
- **Syncs Reactively**: Uses **MongoDB Change Streams** and Server-Sent Events (SSE) to push live memory updates to all open browsers with zero polling, and connects to external IDE agents via the **Model Context Protocol (MCP)**.

---

## 📚 Complete Documentation Index

For technical interviewers, developers, and code reviewers, comprehensive technical specifications and interview preparation guides are provided below:

### Technical Documentation (`docs/`)
1. [01. Project Overview](docs/01-project-overview.md) — Problem statement, context window limitations, target use cases, and capability comparison.
2. [02. System Architecture](docs/02-system-architecture.md) — End-to-end component architecture, interaction sequence diagrams, and startup lifecycle.
3. [03. Memory Data Model](docs/03-memory-data-model.md) — MongoDB collections (`memories`, `memory_events`, `messages`, `retrievals`), schema validator, and indexing strategy.
4. [04. Memory Creation & Retrieval Pipeline](docs/04-memory-pipeline.md) — Two-phase recall cycle, hybrid re-ranking formula ($0.8\text{ sim} + 0.1\text{ recency} + 0.1\text{ importance}$), and citation engine.
5. [05. Memory Lifecycle & Advanced Cases](docs/05-memory-lifecycle.md) — Versioned superseding, conflict resolution, physical TTL vs. logical query expiry, and GDPR erasure.
6. [06. Real-Time Synchronization](docs/06-realtime-system.md) — MongoDB Change Streams, resume tokens, quiet field filtering, and SSE transport.
7. [07. API & MCP Reference](docs/07-api-and-mcp.md) — Full REST API documentation and Model Context Protocol stdio tool specification.
8. [08. Setup & Deployment Guide](docs/08-setup-and-deployment.md) — Step-by-step Atlas setup, environment variables, local execution, and Render deployment.
9. [09. Security & Reliability](docs/09-security-and-reliability.md) — Deterministic secret redaction, Luhn validation, prompt injection defense, and circuit breaker.
10. [10. Engineering Decisions & Trade-Offs](docs/10-design-decisions.md) — Deep architectural comparisons (MongoDB vs. PostgreSQL, Atlas Vector Search vs. Pinecone, SSE vs. WebSockets).
11. [11. Evaluation & Testing](docs/11-evaluation-and-testing.md) — Benchmark evaluation dataset, Hit@1, Recall@6, MRR metrics, and 15 adversarial robustness scenarios.



---

## 🛠 Tech Stack

- **Database**: MongoDB Atlas (M0 Free Tier or higher)
  - **Atlas Vector Search**: 1024-dimensional HNSW index with metadata pre-filtering.
  - **Change Streams**: Tailable oplog stream on `memories` with `full_document="updateLookup"`.
  - **TTL Indexes**: Automated hardware-level physical purge on `expireAt`.
  - **Document Validator**: Collection-level `$jsonSchema` schema enforcement.
- **Embeddings**: Voyage AI (`voyage-3.5`, 1024 dimensions) with asymmetric query/document projections.
- **LLM Reasoning**: Multi-provider architecture with automatic rate-limit failover:
  - *Paid Tier*: Anthropic Claude 3.5 Haiku (Memory Manager) & Claude 3.5 Opus (Responder).
  - *Free Tier*: Groq (`openai/gpt-oss-120b` + `20b`) with automatic fallback to Google Gemini (`gemini-flash-latest`).
- **Backend Framework**: Python 3.11+ / FastAPI / Uvicorn (ASGI) / PyMongo Async.
- **Protocols & Standards**: Server-Sent Events (SSE) / Model Context Protocol (MCP stdio).
- **Frontend**: Vanilla HTML5, CSS3, ES6 JavaScript, and Tailwind CSS.

---

## 🚀 Quick Start

### 1. Installation
```bash
git clone https://github.com/your-username/teambrain.git
cd teambrain

uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt
# (or: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt)
```

### 2. Configure Environment (`.env`)
```bash
cp .env.example .env
```
Supply your MongoDB connection string and API keys (see [Setup Guide](docs/08-setup-and-deployment.md)):
```ini
MONGODB_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
DB_NAME=teambrain
VOYAGE_API_KEY=pa-...
# For paid Claude:
ANTHROPIC_API_KEY=sk-ant-...
# Or for free Groq:
LLM_PROVIDER=groq
LLM_API_KEY=gsk_...
```

### 3. Initialize Database & Run Diagnostics
```bash
python scripts/setup_db.py    # Provisions collections, schema validator, TTL index, & Atlas Vector Search
python scripts/seed.py        # Populates standard demo background memories
python scripts/doctor.py      # Pre-flight diagnostic check: verifies all subsystems show ✔
```

### 4. Start the Application
```bash
uvicorn app.main:app --port 8000
# Open http://localhost:8000 in your browser
```

---

## 🎭 3-Minute Live Stage Demo Script

1. **Create Team Memories (Alice's Pane)**:
   - Type in Alice:
     ```
     Staging DB is down for the next 2 minutes. Also, we decided to use pnpm, not npm. The payments API is rate limited to 100 requests per second, and there's a freeze on new dependencies starting Wednesday.
     ```
   - *Point out*: Green "saved" chips appear under the response; new cards slide **live** into Bob's panel via **Change Streams**; the staging card displays a **TTL countdown timer**.
2. **Memory Changes Behavior in a New Session (Bob's Pane)**:
   - Click **＋ New session** on Bob. Type:
     ```
     Write a quick script that installs a charting library and bulk-syncs 10,000 orders to the payments API.
     ```
   - *Point out*: Bob's agent uses `pnpm add`, throttles to 100 req/s, and warns about the dependency freeze, citing `[M1]`.
   - *Toggle Memory OFF*: Send the prompt again; observe generic `npm install` with no throttling. Flip it back ON.
3. **Inspect Provenance ("Why these memories?")**:
   - Click **🔍 Why these memories?** under Bob's answer to view the hybrid scoring breakdown:
     $$\text{Score} = 0.8 \times \text{sim} + 0.1 \times \text{recency} + 0.1 \times \text{importance}$$
   - Click any citation chip to open the **Memory Inspector** (source quote, version lineage, audit log).
4. **Rapid-Fire Edge Cases**:
   - **Correction**: Alice says *"Update: payments rate limit raised to 500 req/s"*. Old card is marked `superseded`, new card is `v2`. Bob asks again and receives 500 req/s.
   - **Conflict**: Bob says *"We use npm on this project"*. The agent refuses to silently overwrite Alice's choice, warning of a `⚠️ conflict` and demanding explicit confirmation.
   - **Private & Secrets**: Alice shares a private preference (visible only to her agent) and pastes an AWS key (`AKIA...`), which is redacted *before* reaching the LLM or database.
   - **Expiry**: After 2 minutes, the staging memory disappears automatically via the **MongoDB TTL index**.

---

## 💻 External Agent Integration via MCP

Connect terminal AI coding agents (such as **Claude Code** or **Cursor**) to the same shared memory:

```bash
# Register TeamBrain with Claude Code
claude mcp add teambrain -e TEAMBRAIN_USER=bob -- "$PWD/.venv/bin/python" "$PWD/scripts/mcp_server.py"

# Launch Claude Code
claude
# Prompt: "Add a script that bulk-syncs orders to the payments API"
```
The agent calls `recall_team_memory`, adopts the team's `pnpm` decision and 500 req/s limit, and any new decisions it `remember`s immediately appear live in the web UI!

---

## 📊 Evaluation & Verification

TeamBrain includes automated benchmark and robustness suites:

```bash
# 1. Retrieval Accuracy Benchmark (Hit@1, Recall@6, MRR)
python scripts/eval.py
# Results: Hit@1 = 1.00 | Recall@6 = 1.00 | MRR = 1.00 | Violations = 0

# 2. Adversarial Robustness Check (15 messy/injection prompts)
python scripts/robustness.py
# Tests Hinglish, Hindi, typos, prompt injections, defamation guards, and secret redactions.
```

---

## 📄 License

MIT License. Developed for the MongoDB Student Hackathon (Track 1: Agentic Memory).
