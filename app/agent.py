"""One chat turn: redact -> recall -> decide memory ops -> apply -> recall again -> respond -> log."""
import re

from . import config, guard
from .db import messages, now, retrievals, to_json
from .embeddings import embed_one
from .llm import llm
from .memory import apply_operations, recall, touch

HISTORY_TURNS = 10
# Models sometimes cite as (M1), 【M1】, [M1, M2] or (M1–M4); normalise every form to [M1][M2] before counting.
CITE_GROUP = re.compile(r"[\[(【]\s*(M\d+(?:\s*(?:[,;]|[-–—]|to)\s*M\d+)*)\s*[\])】]")


def _expand_refs(group: str) -> str:
    refs: list[int] = []
    for a, b in re.findall(r"M(\d+)(?:\s*(?:[-–—]|to)\s*M(\d+))?", group):
        lo, hi = int(a), int(b or a)
        refs += range(lo, hi + 1) if hi - lo < 10 else [lo, hi]
    return "".join(f"[M{n}]" for n in dict.fromkeys(refs))


def speaker_info(user_id: str) -> dict:
    u = config.USERS[user_id]
    return {"id": user_id, "name": u["name"], "role": u["role"]}


async def _history(session_id: str) -> list[dict]:
    docs = await messages.find({"session_id": session_id}).sort("created_at", -1).limit(HISTORY_TURNS).to_list()
    docs.reverse()
    out: list[dict] = []
    for d in docs:
        if out and out[-1]["role"] == d["role"]:
            out[-1]["content"] += "\n\n" + d["content"]
        else:
            out.append({"role": d["role"], "content": d["content"]})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _history_text(history: list[dict], speaker: dict) -> str:
    lines = []
    for m in history[-4:]:
        who = speaker["name"] if m["role"] == "user" else "Assistant"
        lines.append(f"{who}: {m['content'][:500]}")
    return "\n".join(lines)


async def chat_turn(user_id: str, session_id: str, text: str, use_memory: bool = True) -> dict:
    speaker = speaker_info(user_id)
    clean, secret_kinds = guard.redact(text)
    history = await _history(session_id)

    user_msg = {"session_id": session_id, "user_id": user_id, "role": "user", "content": clean,
                "redacted": secret_kinds, "created_at": now()}
    user_msg["_id"] = (await messages.insert_one(user_msg)).inserted_id

    # 1. Recall neighbours of the message so the memory manager can dedupe / supersede / detect conflicts.
    qvec = await embed_one(clean, "query")
    neighbours = await recall(user_id, qvec, k=8)

    # 2. Memory manager proposes operations; the engine validates and applies them.
    ops = await llm.manage_memory(speaker, clean, _history_text(history, speaker), neighbours["items"])
    source = {"kind": "chat", "session_id": session_id, "message_id": user_msg["_id"], "quote": clean[:400]}
    actions: list[dict] = []
    if secret_kinds:
        actions.append({"kind": "redacted", "kinds": secret_kinds})
    actions += await apply_operations(user_id, ops, neighbours["items"], source)

    # 3. Recall again (memory may have just changed) and answer.
    recalled = await recall(user_id, qvec) if use_memory else {"items": [], "engine": neighbours["engine"], "filter": None}
    reply = await llm.respond(speaker, history, clean, recalled["items"], actions)

    reply = CITE_GROUP.sub(lambda m: _expand_refs(m.group(1)), reply)
    cited_refs = set(re.findall(r"\[(M\d+)\]", reply))
    results = [{
        "ref": it["ref"], "memory_id": it["memory"]["_id"], "content": it["memory"]["content"],
        "type": it["memory"]["type"], "scope": it["memory"]["scope"], "owner_id": it["memory"]["owner_id"],
        "sim": it["sim"], "recency": it["recency"], "importance": it["importance"], "final": it["final"],
        "cited": it["ref"] in cited_refs,
    } for it in recalled["items"]]

    assistant_msg = {"session_id": session_id, "user_id": user_id, "role": "assistant", "content": reply,
                     "actions": actions, "created_at": now()}
    assistant_msg["_id"] = (await messages.insert_one(assistant_msg)).inserted_id

    retrieval = {
        "message_id": assistant_msg["_id"], "session_id": session_id, "user_id": user_id,
        "query": clean, "use_memory": use_memory, "engine": recalled["engine"], "filter": recalled["filter"],
        "weights": {"sim": config.W_SIM, "recency": config.W_RECENCY, "importance": config.W_IMPORTANCE},
        "results": results, "created_at": now(),
    }
    retrieval_id = (await retrievals.insert_one(retrieval)).inserted_id
    await messages.update_one({"_id": assistant_msg["_id"]}, {"$set": {"retrieval_id": retrieval_id}})
    await touch([r["memory_id"] for r in results if r["cited"]])

    return to_json({
        "user_message": user_msg, "reply": reply, "message_id": assistant_msg["_id"],
        "retrieval_id": retrieval_id, "memories": results, "actions": actions,
        "engine": recalled["engine"], "use_memory": use_memory,
    })
