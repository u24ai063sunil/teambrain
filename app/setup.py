"""Idempotent database setup: collections, schema validation, regular/TTL indexes and the vector index."""
import asyncio
import logging

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import CollectionInvalid, OperationFailure
from pymongo.operations import SearchIndexModel

from . import config
from .db import db, events, memories, messages, retrievals

log = logging.getLogger("teambrain.setup")

MEMORY_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["workspace_id", "owner_id", "scope", "type", "content", "status", "created_at", "updated_at"],
        "properties": {
            "workspace_id": {"bsonType": "string"},
            "owner_id": {"bsonType": "string"},
            "scope": {"enum": ["workspace", "private"]},
            "type": {"enum": config.MEMORY_TYPES},
            "content": {"bsonType": "string", "minLength": 1},
            "status": {"enum": ["active", "superseded", "deleted"]},
            "importance": {"bsonType": ["double", "int"], "minimum": 0, "maximum": 1},
            "embedding": {"bsonType": "array"},
            "version": {"bsonType": "int", "minimum": 1},
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
            "expireAt": {"bsonType": ["date", "null"]},
        },
    }
}


def vector_index_definition() -> dict:
    return {
        "fields": [
            {"type": "vector", "path": "embedding", "numDimensions": config.EMBED_DIM, "similarity": "cosine"},
            {"type": "filter", "path": "workspace_id"},
            {"type": "filter", "path": "owner_id"},
            {"type": "filter", "path": "scope"},
            {"type": "filter", "path": "status"},
            {"type": "filter", "path": "type"},
        ]
    }


async def ensure_collections() -> None:
    try:
        await db.create_collection("memories", validator=MEMORY_VALIDATOR, validationLevel="moderate")
    except CollectionInvalid:
        try:
            await db.command("collMod", "memories", validator=MEMORY_VALIDATOR, validationLevel="moderate")
        except OperationFailure as e:  # e.g. the DB user only has readWrite (collMod needs dbAdmin)
            log.warning("could not update the schema validator (%s); keeping the existing one", e.code)
    for name in ("memory_events", "messages", "retrievals"):
        try:
            await db.create_collection(name)
        except CollectionInvalid:
            pass


async def ensure_indexes() -> None:
    # TTL: per-document expiry. Docs without expireAt never expire.
    await memories.create_index("expireAt", expireAfterSeconds=0, name="ttl_expireAt")
    # Memory panel listing and admin queries.
    await memories.create_index([("workspace_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)],
                                name="workspace_status_updated")
    await memories.create_index([("workspace_id", ASCENDING), ("owner_id", ASCENDING), ("type", ASCENDING)],
                                name="workspace_owner_type")
    await events.create_index([("memory_id", ASCENDING), ("ts", ASCENDING)], name="memory_ts")
    await messages.create_index([("session_id", ASCENDING), ("created_at", ASCENDING)], name="session_created")
    await retrievals.create_index("message_id", name="message")
    await retrievals.create_index("results.memory_id", name="results_memory")


async def ensure_vector_index(wait: bool = True, timeout: int = 300) -> str:
    """Create (or update) the Atlas Vector Search index. Returns a status string."""
    definition = vector_index_definition()
    try:
        existing = await (await memories.list_search_indexes(config.VECTOR_INDEX)).to_list()
    except OperationFailure as e:
        return f"unsupported ({e.details.get('errmsg', e) if e.details else e}). Not an Atlas cluster? Recall will use the exact-scan fallback."

    if not existing:
        await memories.create_search_index(
            SearchIndexModel(definition=definition, name=config.VECTOR_INDEX, type="vectorSearch")
        )
        print(f"  created vector index '{config.VECTOR_INDEX}' ({config.EMBED_DIM} dims)")
    else:
        current = existing[0].get("latestDefinition", {})
        if current != definition:
            await memories.update_search_index(config.VECTOR_INDEX, definition)
            print(f"  updated vector index '{config.VECTOR_INDEX}' definition")

    if not wait:
        return "building"
    for _ in range(timeout // 5):
        idx = (await (await memories.list_search_indexes(config.VECTOR_INDEX)).to_list())[0]
        if idx.get("queryable") and idx.get("status") == "READY":
            return "READY"
        print(f"  waiting for vector index... status={idx.get('status')}")
        await asyncio.sleep(5)
    return "still building (check Atlas UI)"


async def setup_all() -> None:
    await ensure_collections()
    await ensure_indexes()
    print("  collections, validator and indexes OK")
    print("  vector index:", await ensure_vector_index())
