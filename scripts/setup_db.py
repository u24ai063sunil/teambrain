"""Create collections, schema validation, TTL/regular indexes and the Atlas Vector Search index."""
import asyncio

import _path  # noqa: F401

from app import config
from app.setup import setup_all


async def main():
    print(f"Setting up database '{config.DB_NAME}' ...")
    await setup_all()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
