"""Text embeddings: Voyage AI in production, a deterministic hashing embedder for offline testing."""
import asyncio
import hashlib
import logging
import math
import re
import time

import httpx

from . import config

log = logging.getLogger("teambrain.embeddings")
_http = httpx.AsyncClient(timeout=10)
# Circuit breaker: after Voyage fails, skip it for a while so every turn doesn't wait through the retries.
BREAKER_S = 30
_down_until = 0.0

_STOP = set(
    "a an the is are was were be been to of in on for and or with by at from as it this that we our us i "
    "my me you your they their he she its do does did have has had will would should can could not no "
    "please about into than then so if but just also all any".split()
)


class EmbeddingError(RuntimeError):
    """The embedding service is unreachable or keeps failing."""


async def embed(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """input_type is 'document' when storing memories and 'query' when searching."""
    if not texts:
        return []
    if config.EMBED_PROVIDER == "voyage":
        return await _voyage(texts, input_type)
    return [_fake(t) for t in texts]


async def embed_one(text: str, input_type: str = "document") -> list[float]:
    return (await embed([text], input_type))[0]


async def _voyage(texts: list[str], input_type: str) -> list[list[float]]:
    global _down_until
    if time.monotonic() < _down_until:
        raise EmbeddingError("Voyage embeddings unavailable (failed recently; retrying shortly)")
    # Retry rate limits, transient errors and network failures with backoff instead of failing the turn.
    resp = None
    for delay in (1, 3, None):
        try:
            resp = await _http.post(
                f"{config.VOYAGE_BASE_URL}/embeddings",
                headers={"Authorization": f"Bearer {config.VOYAGE_API_KEY}"},
                json={
                    "input": texts,
                    "model": config.VOYAGE_MODEL,
                    "input_type": input_type,
                    "output_dimension": config.EMBED_DIM,
                },
            )
            problem = None if resp.status_code not in (429, 500, 502, 503, 504) else f"HTTP {resp.status_code}"
        except httpx.TransportError as e:  # timeouts, DNS, refused connections
            problem = f"{type(e).__name__}"
        if problem is None or delay is None:
            break
        log.warning("Voyage %s; retrying in %ss", problem, delay)
        await asyncio.sleep(delay)
    if problem:
        _down_until = time.monotonic() + BREAKER_S
        raise EmbeddingError(f"Voyage embeddings unavailable ({problem})")
    if resp.status_code != 200:
        raise EmbeddingError(f"Voyage embeddings failed ({resp.status_code}): {resp.text[:300]}")
    data = sorted(resp.json()["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def tokenize(text: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP]
    # crude stemming so "decided"/"decision", "limits"/"limit" collide
    words = [re.sub(r"(ing|ed|es|s|ion)$", "", w) if len(w) > 4 else w for w in words]
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]


def _fake(text: str) -> list[float]:
    """Feature-hashing bag of words. Not semantic, but stable and good enough to test the pipeline."""
    vec = [0.0] * config.EMBED_DIM
    for tok in tokenize(text):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % config.EMBED_DIM] += 1.0 if (h >> 64) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)
