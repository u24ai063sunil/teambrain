"""MongoDB handles and JSON serialisation helpers."""
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import AsyncMongoClient

from . import config

client = AsyncMongoClient(config.MONGODB_URI, appname="teambrain", tz_aware=True)
db = client[config.DB_NAME]

memories = db.memories          # one document per memory (+ embedding + lifecycle)
events = db.memory_events       # append-only audit log of every lifecycle change
messages = db.messages          # chat history, per session
retrievals = db.retrievals      # what was recalled for each answer, with score breakdown


def now() -> datetime:
    return datetime.now(timezone.utc)


def oid(value: str) -> ObjectId:
    return ObjectId(value)


def to_json(value):
    """Recursively convert BSON types to JSON-friendly values. Drops embeddings."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, list):
        return [to_json(v) for v in value]
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items() if k != "embedding"}
    return value
