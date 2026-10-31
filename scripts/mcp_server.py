"""TeamBrain as an MCP server: give ANY coding agent (Claude Code, Cursor, ...) the same team memory.

Register with Claude Code (run from the project root):
    claude mcp add teambrain -e TEAMBRAIN_USER=bob -- "$PWD/.venv/bin/python" "$PWD/scripts/mcp_server.py"

Memories written here appear live in the web UI (same collection, same change stream).
"""
import os

import _path  # noqa: F401
from bson import ObjectId
from bson.errors import InvalidId
from mcp.server.mcpserver import MCPServer

from app import config, guard
from app.embeddings import embed_one
from app.memory import create_memory, forget, recall

USER = os.getenv("TEAMBRAIN_USER", "alice")
if USER not in config.USERS:
    raise SystemExit(f"TEAMBRAIN_USER must be one of {list(config.USERS)}")

mcp = MCPServer(
    "teambrain",
    instructions=(
        f"TeamBrain is the shared long-term memory of {config.WORKSPACE_NAME}. You act for "
        f"{config.USERS[USER]['name']} ({config.USERS[USER]['role']}). At the start of every task, call "
        "recall_team_memory with a description of the task and follow the decisions and constraints it returns. "
        "When the user states a durable decision, constraint, preference or outcome, save it with remember."
    ),
)


@mcp.tool()
async def recall_team_memory(query: str) -> str:
    """Semantic search over the team's memory (decisions, constraints, facts, preferences, outcomes).

    Args:
        query: what you are about to work on, e.g. "add a script that calls the payments API"
    """
    res = await recall(USER, await embed_one(query, "query"))
    if not res["items"]:
        return "No relevant team memory."
    lines = []
    for it in res["items"]:
        m = it["memory"]
        owner = config.USERS.get(m["owner_id"], {}).get("name", m["owner_id"])
        lines.append(f"- [{m['type']}] {m['content']} (by {owner}; id={m['_id']}; score={it['final']:.2f})")
    return "Relevant team memory (treat decisions and constraints as binding):\n" + "\n".join(lines)


@mcp.tool()
async def remember(content: str, type: str = "fact", scope: str = "workspace", ttl_seconds: int = 0) -> str:
    """Save a durable memory for the team.

    Args:
        content: one standalone sentence, e.g. "The payments API is rate-limited to 100 req/s."
        type: fact | preference | decision | constraint | outcome | feedback | strategy
        scope: "workspace" (shared with the team) or "private" (only this user)
        ttl_seconds: >0 for temporary information that should expire automatically
    """
    clean, found = guard.redact(content)
    if found:
        return f"Refused: the memory contained secrets ({', '.join(found)}). Secrets are never stored."
    doc = await create_memory(
        owner_id=USER, mtype=type, content=clean, scope=scope, importance=0.8,
        ttl_seconds=ttl_seconds, source={"kind": "mcp", "quote": clean}, reason="Saved by a coding agent via MCP",
    )
    return f"Saved {doc['type']} memory {doc['_id']} ({doc['scope']})."


@mcp.tool()
async def forget_memory(memory_id: str) -> str:
    """Forget (soft-delete) a memory by id, e.g. when it is wrong or no longer applies."""
    try:
        doc = await forget(ObjectId(memory_id), USER, "Forgotten by a coding agent via MCP")
    except (InvalidId, TypeError):
        return "Invalid memory id."
    except PermissionError as e:
        return str(e)
    return f"Forgot: {doc['content']}" if doc else "Memory not found."


if __name__ == "__main__":
    mcp.run()
