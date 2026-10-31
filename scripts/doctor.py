"""Pre-flight check: run this after configuring .env and before the demo."""
import asyncio

import _path  # noqa: F401

from app import config

OK, BAD, WARN = "\033[32m✔\033[0m", "\033[31m✘\033[0m", "\033[33m!\033[0m"


async def main():
    from app.db import client, db, memories
    from app.embeddings import embed_one
    from app.llm import llm
    from app.memory import ENGINE, recall

    print(f"LLM provider: {llm.name} (chat={llm.chat_model}, memory={llm.memory_model})")
    print(f"Embeddings:   {config.EMBED_PROVIDER} ({config.VOYAGE_MODEL}, {config.EMBED_DIM} dims)")
    if config.LLM_PROVIDER == "mock":
        print(f" {WARN} LLM is the offline MOCK. Set ANTHROPIC_API_KEY, or LLM_PROVIDER=groq + LLM_API_KEY, for the real demo.")
    if config.EMBED_PROVIDER == "fake":
        print(f" {WARN} Embeddings are FAKE hashing vectors. Set VOYAGE_API_KEY for the real demo.")

    # MongoDB connectivity
    try:
        hello = await db.command("hello")
        info = await client.server_info()
        atlas = "mongodb.net" in config.MONGODB_URI
        print(f" {OK} MongoDB {info['version']} reachable ({'Atlas' if atlas else 'non-Atlas'}), db='{config.DB_NAME}'")
    except Exception as e:  # noqa: BLE001
        print(f" {BAD} Cannot connect to MongoDB: {e}\n   Check MONGODB_URI, DB user/password and Atlas Network Access (IP allowlist).")
        return
    print(f" {OK if hello.get('setName') else BAD} replica set: {hello.get('setName') or 'NO (change streams need one)'}")

    # Change stream
    try:
        async with await memories.watch(max_await_time_ms=200) as s:
            await s.try_next()
        print(f" {OK} change streams work")
    except Exception as e:  # noqa: BLE001
        print(f" {BAD} change stream failed: {e}")

    # Indexes
    names = {i["name"]: i for i in await (await memories.list_indexes()).to_list()}
    ttl = names.get("ttl_expireAt")
    print(f" {OK if ttl else BAD} TTL index {'present' if ttl else 'MISSING, run scripts/setup_db.py'}")
    try:
        idx = await (await memories.list_search_indexes(config.VECTOR_INDEX)).to_list()
        if not idx:
            print(f" {BAD} vector index '{config.VECTOR_INDEX}' missing, run scripts/setup_db.py")
        else:
            dims = idx[0].get("latestDefinition", {}).get("fields", [{}])[0].get("numDimensions")
            ready = idx[0].get("status") == "READY" and idx[0].get("queryable")
            print(f" {OK if ready else WARN} vector index status={idx[0].get('status')} queryable={idx[0].get('queryable')}")
            print(f" {OK if dims == config.EMBED_DIM else BAD} vector index dims={dims}, EMBED_DIM={config.EMBED_DIM}")
    except Exception as e:  # noqa: BLE001
        print(f" {WARN} search indexes unsupported here ({type(e).__name__}); recall will use the exact-scan fallback")

    # Embeddings
    try:
        v = await embed_one("health check", "query")
        print(f" {OK if len(v) == config.EMBED_DIM else BAD} embedding call returned {len(v)} dims")
    except Exception as e:  # noqa: BLE001
        print(f" {BAD} embedding call failed: {e}")
        return

    # Recall end to end
    count = await memories.count_documents({"workspace_id": config.WORKSPACE_ID, "status": "active"})
    if count == 0:
        print(f" {WARN} no memories yet, run scripts/seed.py")
    else:
        res = await recall("alice", await embed_one("how should I store prices?", "query"))
        top = res["items"][0]["memory"]["content"][:70] if res["items"] else "(nothing)"
        good = ENGINE["name"] == "atlas-vector-search" and res["items"]
        print(f" {OK if good else WARN} recall via {ENGINE['name']}: {len(res['items'])} hits; top = {top!r}")
        if ENGINE["name"] == "atlas-vector-search" and not res["items"]:
            print("   $vectorSearch returned nothing: the index may still be building, or dims mismatch.")

    # LLM
    if config.LLM_PROVIDER == "anthropic":
        import anthropic

        c = anthropic.AsyncAnthropic()
        for model in (config.MEMORY_MODEL, config.CHAT_MODEL):
            try:
                r = await c.messages.create(model=model, max_tokens=2000, output_config={"effort": "low"},
                                            messages=[{"role": "user", "content": "Reply with just: OK"}])
                text = "".join(b.text for b in r.content if b.type == "text").strip()
                print(f" {OK} {model} answered {text[:20]!r}")
            except anthropic.APIStatusError as e:
                print(f" {BAD} {model}: HTTP {e.status_code} {e.message}")
            except anthropic.APIConnectionError as e:
                print(f" {BAD} {model}: connection error {e}")
    elif config.LLM_PROVIDER != "mock":
        from app.llm import ProviderError

        if not llm.chat_model:
            print(f" {BAD} no model set: put a model id in LLM_CHAT_MODEL")
            return
        for model in dict.fromkeys((llm.memory_model, llm.chat_model)):
            try:
                text = await llm.complete(model, [{"role": "user", "content": "Reply with just: OK"}])
                print(f" {OK} {model} @ {llm.base_url} answered {text.strip()[:20]!r}")
            except ProviderError as e:
                print(f" {BAD} {model}: {e}")
            except Exception as e:  # noqa: BLE001
                print(f" {BAD} {model}: {type(e).__name__} {e}")


if __name__ == "__main__":
    asyncio.run(main())
