"""Central configuration, read once from the environment / .env file."""
import os

from dotenv import load_dotenv

load_dotenv()

# --- MongoDB ---
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/?directConnection=true")
DB_NAME = os.getenv("DB_NAME", "teambrain")
VECTOR_INDEX = os.getenv("VECTOR_INDEX", "memory_vec")

# --- Embeddings ---
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY", "")
VOYAGE_BASE_URL = os.getenv("VOYAGE_BASE_URL", "https://api.voyageai.com/v1").rstrip("/")
VOYAGE_MODEL = os.getenv("VOYAGE_MODEL", "voyage-3.5")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))
EMBED_PROVIDER = os.getenv("EMBED_PROVIDER") or ("voyage" if VOYAGE_API_KEY else "fake")

# --- LLM ---
# anthropic | groq | gemini | openrouter | ollama | openai | mock
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or (
    "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "openai" if os.getenv("LLM_BASE_URL") else "mock"
)).lower()

# OpenAI-compatible presets: (base URL, default model). Free tiers change often, so check the
# provider's model list and override with LLM_CHAT_MODEL / LLM_MEMORY_MODEL if a default 404s.
OPENAI_COMPAT_PRESETS = {
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-flash-latest"),
    "openrouter": ("https://openrouter.ai/api/v1", ""),
    "ollama": ("http://localhost:11434/v1", "llama3.1:8b"),
    "openai": ("https://api.openai.com/v1", ""),
}
_preset_url, _preset_model = OPENAI_COMPAT_PRESETS.get(LLM_PROVIDER, ("", ""))
LLM_BASE_URL = (os.getenv("LLM_BASE_URL") or _preset_url).rstrip("/")
LLM_CHAT_MODEL = os.getenv("LLM_CHAT_MODEL") or _preset_model
# Groq rate-limits per model, so the memory manager runs on a separate (smaller, faster) model there.
LLM_MEMORY_MODEL = os.getenv("LLM_MEMORY_MODEL") or (
    "openai/gpt-oss-20b" if LLM_PROVIDER == "groq" and not os.getenv("LLM_CHAT_MODEL") else LLM_CHAT_MODEL
)

# Anthropic
CHAT_MODEL = os.getenv("CHAT_MODEL", "claude-opus-5-5")
MEMORY_MODEL = os.getenv("MEMORY_MODEL", "claude-haiku-5-5")
CHAT_EFFORT = os.getenv("CHAT_EFFORT", "low")
# Server-side refusal fallback ("default" routing) is supported on these chat models.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

# --- Demo workspace ---
WORKSPACE_ID = os.getenv("WORKSPACE_ID", "phoenix")
WORKSPACE_NAME = "Project Phoenix"
USERS = {
    "alice": {"name": "Alice", "role": "Backend lead", "color": "#7c5cff"},
    "bob": {"name": "Bob", "role": "Frontend developer", "color": "#14b8a6"},
}

# --- Memory model ---
MEMORY_TYPES = ["fact", "preference", "decision", "constraint", "outcome", "feedback", "strategy"]
# Recency half-life per memory type, in days. Situational facts fade fast, decisions/preferences slowly.
HALF_LIFE_DAYS = {
    "fact": 30, "preference": 120, "decision": 90, "constraint": 45,
    "outcome": 30, "feedback": 120, "strategy": 120,
}
# final = W_SIM * similarity + W_RECENCY * recency + W_IMPORTANCE * importance
W_SIM, W_RECENCY, W_IMPORTANCE = 0.8, 0.1, 0.1  # tuned with scripts/eval.py on Voyage embeddings
RECALL_K = int(os.getenv("RECALL_K", "6"))
# vectorSearchScore for cosine is normalised to [0, 1] (0.5 == orthogonal). Below this, a memory is noise.
MIN_SIM = float(os.getenv("MIN_SIM") or (0.55 if EMBED_PROVIDER == "voyage" else 0.52))
# Soft-deleted memories are physically purged by the TTL index after this many days.
DELETED_RETENTION_DAYS = 7
