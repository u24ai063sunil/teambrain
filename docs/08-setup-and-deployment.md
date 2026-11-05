# 08. Setup and Deployment Guide

---

## 1. Prerequisites

Before setting up TeamBrain, ensure your environment meets the following specifications:
- **Operating System**: macOS, Linux, or Windows WSL2.
- **Python**: Python 3.11, 3.12, 3.13, or 3.14.
- **Package Manager**: `uv` (recommended) or standard `pip` / `venv`.
- **Database**: A MongoDB Atlas M0 (Free Tier) cluster or higher (supports Vector Search, Change Streams, and TTL indexes).
- **API Keys**:
  - **Voyage AI**: `VOYAGE_API_KEY` from [dash.voyageai.com](https://dash.voyageai.com) (or Atlas Model API).
  - **LLM Provider**: Either `ANTHROPIC_API_KEY` (Claude), or a free-tier OpenAI-compatible key (e.g. `LLM_PROVIDER=groq` + `LLM_API_KEY` from [console.groq.com](https://console.groq.com)).

---

## 2. Local Environment Setup

### 2.1 Clone Repository & Create Virtual Environment

```bash
git clone https://github.com/your-username/teambrain.git
cd teambrain

# Option A: Using uv (fastest)
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Option B: Using standard Python venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2.2 Dependencies Overview (`requirements.txt`)
- `fastapi>=0.115`: High-performance asynchronous web framework.
- `uvicorn[standard]>=0.30`: ASGI web server.
- `pymongo>=4.13`: Official async MongoDB driver.
- `anthropic>=2.0`: Official Anthropic Claude SDK.
- `httpx>=0.27`: Async HTTP client for Voyage AI and OpenAI-compatible endpoints.
- `python-dotenv>=1.0`: Automatic `.env` environment loading.
- `pydantic>=2.7`: Data validation and settings management.
- `mcp>=2.0`: Official Model Context Protocol SDK.

---

## 3. Environment Variable Configuration (`.env`)

Copy the template file to `.env`:
```bash
cp .env.example .env
```

Edit `.env` with your synthetic configuration:

```ini
# ==============================================================================
# 1. MONGODB ATLAS CONFIGURATION
# ==============================================================================
# Copy the SRV string from Atlas UI -> Connect -> Drivers -> Python
MONGODB_URI=mongodb+srv://<username>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
DB_NAME=teambrain
VECTOR_INDEX=memory_vec

# ==============================================================================
# 2. EMBEDDINGS (VOYAGE AI)
# ==============================================================================
# Key from https://dash.voyageai.com
VOYAGE_API_KEY=pa-your-voyage-api-key-here
VOYAGE_BASE_URL=https://api.voyageai.com/v1
VOYAGE_MODEL=voyage-3.5
# Must match numDimensions of the Atlas Search Index
EMBED_DIM=1024
# Set to 'voyage' for real embeddings, or 'fake' for offline testing
EMBED_PROVIDER=voyage

# ==============================================================================
# 3. LLM CONFIGURATION (MULTI-PROVIDER)
# ==============================================================================
# Option A: Anthropic Claude (Paid / High Performance)
# ANTHROPIC_API_KEY=sk-ant-api03-...
# LLM_PROVIDER=anthropic
# CHAT_MODEL=claude-opus-5-5
# MEMORY_MODEL=claude-haiku-5-5
# CHAT_EFFORT=low

# Option B: Groq (Free Tier / Low Latency)
LLM_PROVIDER=groq
LLM_API_KEY=gsk_your_groq_api_key_here
LLM_CHAT_MODEL=openai/gpt-oss-120b
LLM_MEMORY_MODEL=openai/gpt-oss-20b

# Optional Automatic Backup Provider (Switches on rate limits)
LLM_FALLBACK_PROVIDER=gemini
LLM_FALLBACK_API_KEY=AIzaSy...
LLM_FALLBACK_MODEL=gemini-flash-latest

# ==============================================================================
# 4. WORKSPACE DEFAULTS
# ==============================================================================
WORKSPACE_ID=phoenix
RECALL_K=6
MIN_SIM=0.55
```

---

## 4. MongoDB Atlas Cluster & Vector Index Setup

### Step 1: Create an Atlas M0 Free Cluster
1. Log in to [MongoDB Atlas](https://cloud.mongodb.com/).
2. Click **Create** $\rightarrow$ select **M0 Free** (AWS, GCP, or Azure).
3. Under **Security** $\rightarrow$ **Database Access**, create a user with readWrite permissions (or Atlas Admin for automated index setup).
4. Under **Security** $\rightarrow$ **Network Access**, add your current IP address (or `0.0.0.0/0` for development/demo portability).
5. Copy the connection string into `MONGODB_URI`.

### Step 2: Run Automated Database Setup
Execute `scripts/setup_db.py`. This script is completely idempotent:

```bash
python scripts/setup_db.py
```

**What this script does:**
1. Creates collections: `memories`, `memory_events`, `messages`, and `retrievals`.
2. Attaches the `$jsonSchema` validator to `memories`.
3. Creates the TTL index `ttl_expireAt` on `{ expireAt: 1 }`.
4. Creates compound query indexes: `{ workspace_id, status, updated_at }`, `{ workspace_id, owner_id, type }`, and `{ memory_id, ts }`.
5. Creates the Atlas Vector Search index `memory_vec` with 1024 dimensions and cosine metric.
6. **Waits automatically until Atlas marks the vector index status as `READY`** (takes ~1–2 minutes).

### Step 3: Seed Demonstration Knowledge
Populate the database with background team memories:

```bash
python scripts/seed.py
```

### Step 4: Pre-Flight Health Check (`doctor.py`)
Verify that all system components (MongoDB replica set, change streams, TTL index, vector search, Voyage embeddings, and LLM responses) are functioning:

```bash
python scripts/doctor.py
```
Output should show green checkmarks (`✔`) for all subsystems.

---

## 5. Local Execution

Launch the FastAPI application using Uvicorn:

```bash
uvicorn app.main:app --port 8000 --reload
```

Open your browser to:
```
http://localhost:8000
```
You will see the dual agent interface:
- **Left Pane**: Alice (Backend Lead).
- **Middle Pane**: Bob (Frontend Developer).
- **Right Pane**: Live Team Memory Feed powered by Change Streams.

---

## 6. Cloud Deployment (Render / Docker)

### 6.1 Render Deployment

TeamBrain is structured for one-click deployment on [Render](https://render.com).

#### Render Blueprint Configuration (`render.yaml`):
```yaml
services:
  - type: web
    name: teambrain
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: PYTHON_VERSION
        value: 3.12.0
      - key: MONGODB_URI
        sync: false
      - key: DB_NAME
        value: teambrain
      - key: VOYAGE_API_KEY
        sync: false
      - key: LLM_PROVIDER
        value: groq
      - key: LLM_API_KEY
        sync: false
      - key: WORKSPACE_ID
        value: phoenix
```

#### Production Deployment Steps:
1. Connect your GitHub repository to Render.
2. Select **New Web Service** and choose **Python 3**.
3. Set the build command to: `pip install -r requirements.txt`.
4. Set the start command to: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
5. Under **Environment Variables**, supply your production `MONGODB_URI`, `VOYAGE_API_KEY`, and LLM keys.
6. Deploy. Render will provide a live HTTPS URL.

---

## 7. Troubleshooting & Common Operational Issues

| Symptom | Root Cause | Solution |
|---|---|---|
| **Top bar says: *"Recall: exact scan (no Atlas index)"*** | Not running on Atlas, or `memory_vec` search index is missing / building. | Run `python scripts/setup_db.py` and wait for status `READY`. |
| **Vector search returns 0 results on Atlas** | Vector index dimensionality mismatch (`numDimensions != 1024`). | Check `doctor.py`. If dimensions changed, drop index in Atlas, update `EMBED_DIM`, and re-run `setup_db.py` + `seed.py`. |
| **Change stream not "live" (shows reconnecting)** | MongoDB cluster is not running as a replica set. | Plain local `mongod` instances do not have an oplog. Use MongoDB Atlas or launch local mongod with `--replSet rs0`. |
| **`ServerSelectionTimeoutError`** | IP is not whitelisted in Atlas Network Access. | Add your public IP or `0.0.0.0/0` in MongoDB Atlas Network Access. |
| **HTTP 429 Rate Limited by LLM** | Free-tier provider request-per-minute limit reached. | Configure `LLM_FALLBACK_PROVIDER=gemini` with a secondary key in `.env`. TeamBrain switches providers automatically. |
| **SSE connection closes after 30 seconds** | Proxy / load balancer timeout. | TeamBrain sends `: ping` keep-alive comments every 15 seconds. Ensure proxy headers include `X-Accel-Buffering: no`. |

---

## 8. Document Navigation

- Previous Document: [07. API & MCP Server Reference](07-api-and-mcp.md)
- Next Document: [09. Security & Reliability](09-security-and-reliability.md)
- Complete Index: [Table of Contents](../README.md#documentation-index)
