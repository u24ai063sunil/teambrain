"""Retrieval eval: fixed queries -> expected seed memories. Reports hit@1, recall@k and MRR.

Run after scripts/seed.py. Use the numbers on your slide ("recall@5 = ...") and re-run whenever you
change weights, MIN_SIM, the embedding model or the index.
"""
import asyncio

import _path  # noqa: F401

from app import config
from app.db import memories
from app.embeddings import embed_one
from app.memory import ENGINE, recall

# (asking user, query, expected seed keys, keys that must NOT be returned)
CASES = [
    ("alice", "How should I represent prices in the orders table?", ["money_cents"], []),
    ("bob", "Write a new endpoint to list refunds", ["auth", "stack_backend"], []),
    ("bob", "Which component library should I use for the settings page?", ["design_system"], []),
    ("alice", "The sandbox keeps returning too many requests errors, what do we do?", ["retry_strategy"], []),
    ("bob", "How do we ship a change to production?", ["deploy"], []),
    ("alice", "Why were some customers charged twice last week?", ["incident_webhooks"], []),
    ("bob", "What should I use for global client state?", ["state_new"], ["state_old"]),
    ("alice", "Is the orders endpoint fast enough?", ["load_test"], []),
    ("bob", "When is the launch date?", ["product"], []),
    # privacy: Bob's private preference must never be recalled for Alice
    ("alice", "Show me a TypeScript code example style guide", [], ["bob_ts_pref"]),
    ("bob", "Show me a TypeScript code example", ["bob_ts_pref"], []),
]


async def main():
    keys = {d["_id"]: d.get("seed_key") async for d in memories.find({"workspace_id": config.WORKSPACE_ID}, {"seed_key": 1})}
    k = config.RECALL_K
    hits1 = recall_sum = rr_sum = 0.0
    scored_cases = violations = 0
    print(f"{'user':6} {'query':62} {'got (top-3)':40} result")
    for user, query, expected, forbidden in CASES:
        res = await recall(user, await embed_one(query, "query"), k=k)
        got = [keys.get(it["memory"]["_id"]) for it in res["items"]]
        bad = [f for f in forbidden if f in got]
        violations += len(bad)
        status = "FORBIDDEN " + ",".join(bad) if bad else ""
        if expected:
            scored_cases += 1
            found = [e for e in expected if e in got]
            recall_sum += len(found) / len(expected)
            hits1 += 1 if got and got[0] in expected else 0
            ranks = [got.index(e) + 1 for e in expected if e in got]
            rr_sum += 1 / min(ranks) if ranks else 0
            status = status or ("OK" if len(found) == len(expected) else f"missing {set(expected) - set(found)}")
        else:
            status = status or "OK"
        print(f"{user:6} {query[:60]:62} {str(got[:3])[:38]:40} {status}")
    n = scored_cases or 1
    print(f"\nengine={ENGINE['name']}  embeddings={config.EMBED_PROVIDER}  k={k}")
    print(f"hit@1={hits1 / n:.2f}  recall@{k}={recall_sum / n:.2f}  MRR={rr_sum / n:.2f}  privacy/superseded violations={violations}")


if __name__ == "__main__":
    asyncio.run(main())
