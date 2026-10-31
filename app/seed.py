"""Background memories for the demo workspace. The headline demo memories are created LIVE, not seeded."""
from datetime import timedelta

from . import config
from .db import events, memories, messages, now, retrievals
from .embeddings import embed

# key -> used by scripts/eval.py as the expected answer for retrieval tests
SEED = [
    {"key": "product", "owner": "alice", "type": "fact", "scope": "workspace", "importance": 0.7, "age_days": 20,
     "content": "Project Phoenix is a payments dashboard for small online merchants; public launch is planned for November 15."},
    {"key": "stack_backend", "owner": "alice", "type": "decision", "scope": "workspace", "importance": 0.85, "age_days": 18,
     "content": "The backend is built with FastAPI on Python 3.12 and MongoDB Atlas (decided by Alice)."},
    {"key": "stack_frontend", "owner": "bob", "type": "decision", "scope": "workspace", "importance": 0.85, "age_days": 17,
     "content": "The frontend uses React 19 with Vite and TypeScript in strict mode (decided by Bob)."},
    {"key": "money_cents", "owner": "alice", "type": "constraint", "scope": "workspace", "importance": 0.95, "age_days": 15,
     "content": "Money amounts must always be stored and transmitted as integer cents, never as floats (rule set by Alice)."},
    {"key": "auth", "owner": "alice", "type": "constraint", "scope": "workspace", "importance": 0.9, "age_days": 14,
     "content": "Every API endpoint must require JWT authentication through the shared auth middleware in app/auth.py."},
    {"key": "load_test", "owner": "alice", "type": "outcome", "scope": "workspace", "importance": 0.7, "age_days": 6,
     "content": "Load test outcome: GET /orders had p95 latency of 420 ms at 200 rps; adding a Redis cache brought it down to 90 ms."},
    {"key": "retry_strategy", "owner": "alice", "type": "strategy", "scope": "workspace", "importance": 0.75, "age_days": 5,
     "content": "When the payments sandbox returns HTTP 429, retry with exponential backoff starting at 1 second, max 5 attempts."},
    {"key": "incident_webhooks", "owner": "bob", "type": "outcome", "scope": "workspace", "importance": 0.65, "age_days": 4,
     "content": "Incident on Oct 27: duplicate webhook deliveries double-charged 3 orders; fixed by making webhook handlers idempotent on event_id."},
    {"key": "bob_ts_pref", "owner": "bob", "type": "preference", "scope": "private", "importance": 0.5, "age_days": 10,
     "content": "Bob prefers code examples in TypeScript with explicit types and no default exports."},
    {"key": "design_system", "owner": "bob", "type": "decision", "scope": "workspace", "importance": 0.6, "age_days": 9,
     "content": "UI components come from the shadcn/ui design system with Tailwind CSS; no other component libraries (decided by Bob)."},
    {"key": "deploy", "owner": "alice", "type": "fact", "scope": "workspace", "importance": 0.6, "age_days": 8,
     "content": "Deployments go through GitHub Actions to Render; merging to main deploys to staging, tagging a release deploys to production."},
]

# A pre-existing correction so the version chain can be shown even before the live demo.
SEED_CHAIN = {
    "old": {"key": "state_old", "owner": "bob", "type": "decision", "scope": "workspace", "importance": 0.7, "age_days": 12,
            "content": "Client-side state is managed with Redux Toolkit (decided by Bob)."},
    "new": {"key": "state_new", "owner": "bob", "type": "decision", "scope": "workspace", "importance": 0.75, "age_days": 3,
            "content": "Client-side state moved from Redux Toolkit to Zustand to reduce boilerplate (decided by Bob)."},
}


def _doc(spec: dict, embedding: list[float]) -> dict:
    ts = now() - timedelta(days=spec["age_days"])
    return {
        "workspace_id": config.WORKSPACE_ID, "owner_id": spec["owner"], "scope": spec["scope"],
        "type": spec["type"], "content": spec["content"], "embedding": embedding,
        "source": {"kind": "seed", "quote": spec["content"]}, "importance": spec["importance"],
        "confidence": 0.9, "status": "active", "version": 1, "supersedes": None, "superseded_by": None,
        "access_count": 0, "reinforced_count": 0, "created_at": ts, "updated_at": ts,
        "last_accessed_at": None, "seed_key": spec["key"],
    }


async def reset_and_seed() -> int:
    scope = {"workspace_id": config.WORKSPACE_ID}
    await memories.delete_many(scope)
    await events.delete_many(scope)
    await messages.delete_many({})
    await retrievals.delete_many({})

    specs = SEED + [SEED_CHAIN["old"], SEED_CHAIN["new"]]
    vectors = await embed([s["content"] for s in specs], "document")
    docs = [_doc(s, v) for s, v in zip(specs, vectors)]
    old, new = docs[-2], docs[-1]
    new["version"] = 2
    await memories.insert_many(docs)
    await memories.update_one({"_id": old["_id"]}, {"$set": {"status": "superseded", "superseded_by": new["_id"]}})
    await memories.update_one({"_id": new["_id"]}, {"$set": {"supersedes": old["_id"]}})

    await events.insert_many([
        {"memory_id": d["_id"], "workspace_id": config.WORKSPACE_ID, "action": "created",
         "actor": d["owner_id"], "reason": "seed data", "ts": d["created_at"], "content": d["content"]}
        for d in docs
    ] + [{"memory_id": old["_id"], "workspace_id": config.WORKSPACE_ID, "action": "superseded",
          "actor": "bob", "reason": "Team migrated state management", "ts": new["created_at"], "by": new["_id"]}])
    return len(docs)
