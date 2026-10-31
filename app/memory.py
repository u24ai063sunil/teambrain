"""The memory engine: recall (vector search + re-rank) and the lifecycle (add / supersede / forget / ...)."""
import logging
from datetime import timedelta

from bson import ObjectId
from pymongo.errors import OperationFailure

from . import config, guard
from .db import events, memories, now, retrievals, to_json
from .embeddings import cosine, embed_one

log = logging.getLogger("teambrain.memory")

# Which retrieval engine answered the last query. Shown in the UI so we never silently "fake" vector search.
ENGINE = {"name": "unknown", "detail": ""}


# ----------------------------------------------------------------------------------------------
# Recall
# ----------------------------------------------------------------------------------------------
def visibility_filter(user_id: str) -> dict:
    """Pre-filter applied INSIDE $vectorSearch: privacy is enforced by the database query, not the UI."""
    return {
        "workspace_id": config.WORKSPACE_ID,
        "status": "active",
        "$or": [{"scope": "workspace"}, {"owner_id": user_id}],
    }


def _not_expired() -> dict:
    # The TTL monitor only runs every ~60s, so expired-but-not-yet-deleted docs are excluded here too.
    return {"$or": [{"expireAt": {"$exists": False}}, {"expireAt": None}, {"expireAt": {"$gt": now()}}]}


async def _vector_candidates(user_id: str, qvec: list[float], limit: int) -> list[dict]:
    pipeline = [
        {
            "$vectorSearch": {
                "index": config.VECTOR_INDEX,
                "path": "embedding",
                "queryVector": qvec,
                "numCandidates": max(100, limit * 20),
                "limit": limit * 3,
                "filter": visibility_filter(user_id),
            }
        },
        {"$addFields": {"sim": {"$meta": "vectorSearchScore"}}},
        # The search index is eventually consistent; re-check lifecycle + visibility on the live document
        # so a memory superseded/deleted a second ago is never served from a stale index entry.
        {"$match": {"$and": [visibility_filter(user_id), _not_expired()]}},
        {"$project": {"embedding": 0}},
    ]
    try:
        docs = await (await memories.aggregate(pipeline)).to_list()
        ENGINE.update(name="atlas-vector-search", detail=f"$vectorSearch on index '{config.VECTOR_INDEX}'")
    except OperationFailure as e:
        # Not on Atlas (e.g. plain local mongod): exact cosine scan in the app so the demo still works.
        if ENGINE["name"] != "fallback-exact-scan":
            log.warning("$vectorSearch unavailable (%s). Falling back to exact in-app cosine scan.", e.details)
        ENGINE.update(name="fallback-exact-scan", detail="$vectorSearch unavailable; exact cosine scan in app")
        docs = await _exact_scan(user_id, qvec, {})
        return docs[: limit * 3]
    # Read-your-writes: memories written in the last couple of minutes may not be indexed yet.
    seen = {d["_id"] for d in docs}
    fresh = await _exact_scan(user_id, qvec, {"updated_at": {"$gte": now() - timedelta(seconds=FRESH_WINDOW_S)}})
    docs += [d for d in fresh if d["_id"] not in seen]
    docs.sort(key=lambda d: d["sim"], reverse=True)
    return docs


FRESH_WINDOW_S = 120


async def _exact_scan(user_id: str, qvec: list[float], extra: dict) -> list[dict]:
    docs = await memories.find({"$and": [visibility_filter(user_id), _not_expired(), extra]}).to_list()
    for d in docs:
        d["sim"] = (1 + cosine(qvec, d.pop("embedding", []))) / 2  # same scale as vectorSearchScore
    docs.sort(key=lambda d: d["sim"], reverse=True)
    return docs


def _score(doc: dict, sim: float) -> dict:
    age = max(0.0, (now() - doc["updated_at"]).total_seconds())
    half_life = config.HALF_LIFE_DAYS.get(doc["type"], 30) * 86400
    recency = 0.5 ** (age / half_life)
    importance = float(doc.get("importance", 0.5))
    final = config.W_SIM * sim + config.W_RECENCY * recency + config.W_IMPORTANCE * importance
    return {
        "memory": doc, "sim": round(sim, 4), "recency": round(recency, 4),
        "importance": round(importance, 4), "final": round(final, 4), "age_seconds": age,
    }


async def recall(user_id: str, qvec: list[float], k: int = config.RECALL_K) -> dict:
    docs = await _vector_candidates(user_id, qvec, k)
    scored = [_score(d, d.pop("sim")) for d in docs]
    scored = [s for s in scored if s["sim"] >= config.MIN_SIM]
    scored.sort(key=lambda s: s["final"], reverse=True)
    items = scored[:k]
    for i, it in enumerate(items, 1):
        it["ref"] = f"M{i}"
    return {"items": items, "engine": dict(ENGINE), "filter": visibility_filter(user_id)}


async def touch(memory_ids: list[ObjectId]) -> None:
    if memory_ids:
        await memories.update_many(
            {"_id": {"$in": memory_ids}},
            {"$set": {"last_accessed_at": now()}, "$inc": {"access_count": 1}},
        )


# ----------------------------------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------------------------------
async def log_event(memory_id, action: str, actor: str, reason: str = "", **extra) -> None:
    await events.insert_one({
        "memory_id": memory_id, "workspace_id": config.WORKSPACE_ID, "action": action,
        "actor": actor, "reason": reason, "ts": now(), **extra,
    })


async def create_memory(*, owner_id: str, mtype: str, content: str, scope: str, importance: float,
                        source: dict, ttl_seconds: int = 0, supersedes: dict | None = None,
                        reason: str = "") -> dict:
    ts = now()
    doc = {
        "workspace_id": config.WORKSPACE_ID,
        "owner_id": owner_id,
        "scope": scope if scope in ("workspace", "private") else "workspace",
        "type": mtype if mtype in config.MEMORY_TYPES else "fact",
        "content": content.strip(),
        "embedding": await embed_one(content, "document"),
        "source": source,
        "importance": max(0.0, min(1.0, float(importance))),
        "confidence": 0.9,
        "status": "active",
        "version": (supersedes["version"] + 1) if supersedes else 1,
        "supersedes": supersedes["_id"] if supersedes else None,
        "superseded_by": None,
        "access_count": 0,
        "reinforced_count": 0,
        "created_at": ts,
        "updated_at": ts,
        "last_accessed_at": None,
    }
    if ttl_seconds and ttl_seconds > 0:
        doc["expireAt"] = ts + timedelta(seconds=int(ttl_seconds))
    res = await memories.insert_one(doc)
    doc["_id"] = res.inserted_id
    await log_event(doc["_id"], "created", owner_id, reason, content=doc["content"],
                    version=doc["version"], ttl_seconds=ttl_seconds or None)
    return doc


async def supersede(old: dict, *, actor: str, content: str, mtype: str | None, importance: float,
                    source: dict, reason: str) -> dict:
    new = await create_memory(
        owner_id=actor, mtype=mtype or old["type"], content=content, scope=old["scope"],
        importance=importance, source=source, supersedes=old, reason=reason,
    )
    await memories.update_one(
        {"_id": old["_id"]},
        {"$set": {"status": "superseded", "superseded_by": new["_id"], "updated_at": now()},
         "$unset": {"expireAt": ""}},
    )
    await log_event(old["_id"], "superseded", actor, reason, by=new["_id"], new_content=content)
    return new


async def forget(memory_id: ObjectId, actor: str, reason: str = "", hard: bool = False) -> dict | None:
    doc = await memories.find_one({"_id": memory_id})
    if not doc:
        return None
    if doc["scope"] == "private" and doc["owner_id"] != actor:
        raise PermissionError("Only the owner can delete a private memory.")
    if hard:
        # Right to be forgotten: the content is erased; the audit event keeps only who/when.
        await memories.delete_one({"_id": memory_id})
        await log_event(memory_id, "hard_deleted", actor, reason)
    else:
        # Soft delete: invisible to recall immediately, physically purged by the TTL index later.
        await memories.update_one({"_id": memory_id}, {"$set": {
            "status": "deleted", "deleted_by": actor, "deleted_at": now(), "updated_at": now(),
            "expireAt": now() + timedelta(days=config.DELETED_RETENTION_DAYS),
        }})
        await log_event(memory_id, "deleted", actor, reason, content=doc["content"])
    return doc


async def reinforce(doc: dict, actor: str, reason: str) -> None:
    await memories.update_one({"_id": doc["_id"]}, {
        "$set": {"last_confirmed_at": now(), "confidence": min(1.0, doc.get("confidence", 0.9) + 0.05)},
        "$inc": {"reinforced_count": 1},
    })
    await log_event(doc["_id"], "reinforced", actor, reason)


def _owner_name(user_id: str) -> str:
    return config.USERS.get(user_id, {}).get("name", user_id)


async def apply_operations(speaker_id: str, ops: list[dict], candidates: list[dict], source: dict) -> list[dict]:
    """Validate the LLM's proposed operations against deterministic rules, then execute them."""
    by_ref = {c["ref"]: c["memory"] for c in candidates}
    actions: list[dict] = []

    for op in ops:
        action = op.get("action")
        targets = [by_ref[r] for r in op.get("target_ids", []) if r in by_ref]
        content = (op.get("content") or "").strip()
        reason = op.get("reason", "")

        # Rule: secrets (or their placeholders) are never written, whatever the model says.
        if action in ("add", "supersede", "conflict"):
            content, found = guard.redact(content)
            if found or guard.contains_redaction(content) or not content:
                continue
        if action in ("supersede", "conflict", "reinforce") and not targets:
            action = "add"  # model referenced an unknown id; treat as new information
        if action == "conflict" and targets[0]["owner_id"] == speaker_id:
            action = "supersede"  # you can't conflict with yourself; that's a correction
        # Rule: a teammate's decision/constraint is never silently overwritten.
        if (action == "supersede" and targets[0]["owner_id"] != speaker_id
                and targets[0]["type"] in ("decision", "constraint")
                and not op.get("user_confirmed_override")):
            action = "conflict"

        if action == "add":
            doc = await create_memory(
                owner_id=speaker_id, mtype=op.get("type", "fact"), content=content,
                scope=op.get("scope", "workspace"), importance=op.get("importance", 0.5),
                source=source, ttl_seconds=op.get("ttl_seconds", 0), reason=reason,
            )
            actions.append({"kind": "add", "memory_id": doc["_id"], "type": doc["type"], "scope": doc["scope"],
                            "content": doc["content"], "ttl_seconds": op.get("ttl_seconds") or 0, "reason": reason})
        elif action == "supersede":
            old = targets[0]
            new = await supersede(old, actor=speaker_id, content=content, mtype=op.get("type"),
                                  importance=op.get("importance", old.get("importance", 0.5)),
                                  source=source, reason=reason)
            actions.append({"kind": "supersede", "memory_id": new["_id"], "old_memory_id": old["_id"],
                            "old_content": old["content"], "content": new["content"], "reason": reason})
        elif action == "conflict":
            target = targets[0]
            await log_event(target["_id"], "conflict_flagged", speaker_id, reason, proposed_content=content)
            actions.append({"kind": "conflict", "target_memory_id": target["_id"], "content": content,
                            "target_content": target["content"], "target_owner": target["owner_id"],
                            "target_owner_name": _owner_name(target["owner_id"]), "reason": reason})
        elif action == "forget":
            for t in targets:
                try:
                    await forget(t["_id"], speaker_id, reason or "User asked to forget")
                except PermissionError:
                    continue
                actions.append({"kind": "forget", "memory_id": t["_id"], "content": t["content"], "reason": reason})
        elif action == "reinforce":
            await reinforce(targets[0], speaker_id, reason)
            actions.append({"kind": "reinforce", "memory_id": targets[0]["_id"],
                            "content": targets[0]["content"], "reason": reason})
    return actions


# ----------------------------------------------------------------------------------------------
# Inspection
# ----------------------------------------------------------------------------------------------
async def memory_detail(memory_id: ObjectId) -> dict | None:
    doc = await memories.find_one({"_id": memory_id}, {"embedding": 0})
    if not doc:
        return None
    # Walk the version chain in both directions.
    chain = [doc]
    cur = doc
    while cur.get("supersedes"):
        cur = await memories.find_one({"_id": cur["supersedes"]}, {"embedding": 0})
        if not cur:
            break
        chain.insert(0, cur)
    cur = doc
    while cur.get("superseded_by"):
        cur = await memories.find_one({"_id": cur["superseded_by"]}, {"embedding": 0})
        if not cur:
            break
        chain.append(cur)
    ids = [c["_id"] for c in chain]
    history = await events.find({"memory_id": {"$in": ids}}).sort("ts", 1).to_list()
    retrieved = await retrievals.count_documents({"results.memory_id": memory_id})
    return to_json({"memory": doc, "versions": chain, "events": history, "times_retrieved": retrieved})
