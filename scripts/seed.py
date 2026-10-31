"""Wipe the demo workspace and insert background memories (run before every demo)."""
import asyncio

import _path  # noqa: F401

from app import config
from app.seed import reset_and_seed


async def main():
    print(f"Seeding workspace '{config.WORKSPACE_ID}' (embeddings: {config.EMBED_PROVIDER}) ...")
    n = await reset_and_seed()
    print(f"Inserted {n} memories. Chat history cleared.")


if __name__ == "__main__":
    asyncio.run(main())
