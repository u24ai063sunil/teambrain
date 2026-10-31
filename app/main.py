"""FastAPI app: chat + memory APIs, and a live memory feed powered by a MongoDB change stream."""
import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints

from . import config, guard
from .agent import chat_turn
from .db import memories, messages, retrievals, to_json
from .llm import llm
from .memory import ENGINE, create_memory, forget, memory_detail
from .seed import reset_and_seed
from .setup import ensure_collections, ensure_indexes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("teambrain")

STATIC = Path(__file__).resolve().parent.parent / "static"
QUIET_FIELDS = {"last_accessed_at", "access_count", "last_confirmed_at", "confidence", "reinforced_count"}


# ----------------------------------------------------------------------------------------------
# Change stream -> Server-Sent Events hub
# ----------------------------------------------------------------------------------------------
class Hub:
    def __init__(self):
        self.subscribers: set[tuple[asyncio.Queue, str]] = set()
        self.status = "starting"

    @staticmethod
    def _visible(doc: dict, viewer: str) -> bool:
        if doc.get("workspace_id") != config.WORKSPACE_ID:
            return False
        return doc.get("scope") == "workspace" or viewer == "all" or doc.get("owner_id") == viewer

    def dispatch(self, change: dict) -> None:
        op = change["operationType"]
        if op == "delete":
            payload, doc = {"op": "delete", "memory_id": str(change["documentKey"]["_id"])}, None
        elif op in ("insert", "update", "replace"):
            doc = change.get("fullDocument")
            if not doc:
                return
            updated = set((change.get("updateDescription") or {}).get("updatedFields", {}).keys())
            quiet = op == "update" and updated and updated <= QUIET_FIELDS
            payload = {"op": "touch" if quiet else op, "memory": to_json(doc)}
        else:
            return
        for queue, viewer in list(self.subscribers):
            if doc is None or self._visible(doc, viewer):
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    pass


hub = Hub()


async def watch_memories() -> None:
    resume_token = None
    while True:
        try:
            async with await memories.watch(full_document="updateLookup", resume_after=resume_token) as stream:
                hub.status = "live"
                log.info("change stream on 'memories' is live")
                async for change in stream:
                    resume_token = change["_id"]
                    hub.dispatch(change)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - keep the watcher alive whatever happens
            hub.status = f"reconnecting ({type(e).__name__})"
            log.warning("change stream error: %s; retrying in 3s", e)
            resume_token = None
            await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await ensure_collections()
    await ensure_indexes()
    task = asyncio.create_task(watch_memories())
    log.info("TeamBrain up: llm=%s embeddings=%s db=%s", config.LLM_PROVIDER, config.EMBED_PROVIDER, config.DB_NAME)
    yield
    task.cancel()


app = FastAPI(title="TeamBrain", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _user(user_id: str) -> str:
    if user_id not in config.USERS:
        raise HTTPException(400, f"unknown user '{user_id}'")
    return user_id


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(400, "invalid id")


# ----------------------------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
async def get_config():
    return {
        "workspace": {"id": config.WORKSPACE_ID, "name": config.WORKSPACE_NAME},
        "users": config.USERS,
        "memory_types": config.MEMORY_TYPES,
        "llm": {"provider": llm.name, "chat_model": llm.chat_model, "memory_model": llm.memory_model,
                "fallback": getattr(getattr(llm, "fallback", None), "name", None)},
        "embeddings": {"provider": config.EMBED_PROVIDER, "model": config.VOYAGE_MODEL, "dims": config.EMBED_DIM},
        "engine": ENGINE,
        "change_stream": hub.status,
        "weights": {"sim": config.W_SIM, "recency": config.W_RECENCY, "importance": config.W_IMPORTANCE},
    }


def Text(max_length: int):  # noqa: N802  (type alias factory)
    """A string that is stripped first, so whitespace-only input is rejected as empty."""
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)]


class ChatIn(BaseModel):
    user_id: str
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    message: Text(8000)
    use_memory: bool = True


@app.post("/api/chat")
async def chat(body: ChatIn):
    return await chat_turn(_user(body.user_id), body.session_id, body.message, body.use_memory)


@app.get("/api/sessions/{session_id}/messages")
async def session_messages(session_id: str):
    docs = await messages.find({"session_id": session_id}).sort("created_at", 1).to_list()
    return to_json(docs)


@app.get("/api/memories")
async def list_memories(viewer: str = "all", status: str = "all"):
    q: dict = {"workspace_id": config.WORKSPACE_ID}
    if viewer != "all":
        q["$or"] = [{"scope": "workspace"}, {"owner_id": _user(viewer)}]
    if status != "all":
        q["status"] = status
    docs = await memories.find(q, {"embedding": 0}).sort("created_at", -1).limit(500).to_list()
    return to_json(docs)


@app.get("/api/memories/{memory_id}")
async def get_memory(memory_id: str):
    detail = await memory_detail(_oid(memory_id))
    if not detail:
        raise HTTPException(404, "memory not found (it may have expired or been hard-deleted)")
    return detail


class MemoryIn(BaseModel):
    user_id: str
    content: Text(2000)
    type: str = "fact"
    scope: str = "workspace"
    importance: float = 0.6
    ttl_seconds: int = 0


@app.post("/api/memories")
async def add_memory(body: MemoryIn):
    content, found = guard.redact(body.content)
    if found:
        raise HTTPException(422, f"Refusing to store secrets ({', '.join(found)}).")
    doc = await create_memory(
        owner_id=_user(body.user_id), mtype=body.type, content=content, scope=body.scope,
        importance=body.importance, ttl_seconds=body.ttl_seconds,
        source={"kind": "manual", "quote": content}, reason="Added manually",
    )
    return to_json(doc)


@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: str, user_id: str, hard: bool = False):
    try:
        doc = await forget(_oid(memory_id), _user(user_id), "Deleted from the memory inspector", hard=hard)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    if not doc:
        raise HTTPException(404, "memory not found")
    return {"ok": True, "hard": hard}


class FeedbackIn(BaseModel):
    user_id: str
    message_id: str
    text: Text(1000)


@app.post("/api/feedback")
async def feedback(body: FeedbackIn):
    """Thumbs-down + note becomes a private feedback memory that changes this user's future answers."""
    user_id = _user(body.user_id)
    text, found = guard.redact(body.text)
    if found:
        raise HTTPException(422, "Feedback contained secrets; not stored.")
    name = config.USERS[user_id]["name"]
    doc = await create_memory(
        owner_id=user_id, mtype="feedback", content=f"{name}'s feedback on the assistant's answers: {text}",
        scope="private", importance=0.8, reason="Thumbs-down feedback on an answer",
        source={"kind": "feedback", "message_id": _oid(body.message_id), "quote": text},
    )
    return to_json(doc)


@app.get("/api/retrievals/{retrieval_id}")
async def get_retrieval(retrieval_id: str):
    doc = await retrievals.find_one({"_id": _oid(retrieval_id)})
    if not doc:
        raise HTTPException(404, "retrieval not found")
    return to_json(doc)


@app.post("/api/reset")
async def reset():
    count = await reset_and_seed()
    return {"ok": True, "seeded": count}


@app.get("/api/stream")
async def stream(viewer: str = "all"):
    if viewer != "all":
        _user(viewer)
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    entry = (queue, viewer)

    async def gen():
        hub.subscribers.add(entry)
        try:
            yield f"event: hello\ndata: {json.dumps({'status': hub.status})}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            hub.subscribers.discard(entry)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
