"""Typed clients for the embedding and rerank endpoints (report §2.22, P22).

Two thin, synchronous httpx clients over the OpenAI-compatible vLLM
endpoints configured in config.yaml (``embedding:`` / ``reranker:``
sections, ``EmbeddingConfig`` / ``RerankerConfig`` in
src/utils/config_loader.py). Nothing in the pipeline calls these yet —
this module is the integration surface P22's retrieve-then-rerank tier
wires against; the bake-off gate (D-53) is what decides whether the tier
ever replaces anything.

Deliberate choices, each with a reason:

* **httpx, direct** — matches the shape LLMClient ends up at anyway (the
  openai SDK's own httpx underneath), without dragging LangChain into a
  non-chat call path. httpx is already installed (pulled in by
  langchain-openai / openai); if that ever stops being true, add it to
  the manifest here, not silently.
* **fail fast** — the config's base_url is a private LAN address,
  black-holed from CI; LLMClient's docstring records a 30-minute test
  hang from exactly that. The timeout here is therefore mandatory, with
  the same never-unset rule as llm.timeout.
* **rerank scores rank; thresholds are D-53's job** — the endpoint was
  mis-deployed until 2026-11-25 (missing Qwen3 yes/no scoring; ranking was
  wrong on name pairs — measured and reported to ai-stack ops, who fixed
  and we re-verified: name pairs 0.92-0.93 top-1, order-invariant within
  0.0014, control spread ~0.9). The score's absolute scale still earns no
  threshold before the D-53 bake-off against ground truth.
* **deterministic, no cache** — embeddings of the same text are the
  same vectors; rerank reads both sides per call. Caching rerank results
  is P22's call (it changes what a "run" replays), not this layer's.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import httpx

logger = logging.getLogger("merge-engine.embedding")


def _auth(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _as_dict(config: Any) -> dict[str, Any]:
    """Accept an EmbeddingConfig/RerankerConfig model or a plain dict."""
    if isinstance(config, dict):
        return config
    if hasattr(config, "model_dump"):
        dumped: dict[str, Any] = config.model_dump()
        return dumped
    raise TypeError(f"expected a config model or dict, got {type(config).__name__}")


class EmbeddingClient:
    """``POST {base_url}/embeddings`` (OpenAI-compatible).

    Parameters
    ----------
    config :
        An ``EmbeddingConfig``, or a plain dict with the same keys
        (model / api_key / base_url / timeout / max_retries / dimensions)
        — mirrors how LLMClient accepts a dict, so callers that already
        hold ``config.model_dump()`` need no conversion step.
    """

    def __init__(self, config: Any) -> None:
        cfg = _as_dict(config)
        self.model_name: str = str(cfg.get("model") or "")
        self.api_key: str = str(cfg.get("api_key") or "")
        self.base_url: str = str(cfg.get("base_url") or "").rstrip("/")
        self.timeout: float = float(cfg.get("timeout") or 30)
        self.max_retries: int = int(cfg.get("max_retries") or 2)
        self.dimensions: int = int(cfg.get("dimensions") or 2560)
        if not self.model_name or not self.base_url:
            raise ValueError(
                "EmbeddingClient needs model and base_url (config section 'embedding')"
            )
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=_auth(self.api_key),
            timeout=httpx.Timeout(self.timeout),
        )
        # httpx.Client is thread-safe for concurrent requests; the lock only
        # guards the dimension-drift check against a mid-flight first call.
        self._lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed ``texts`` in one request; vectors come back in request order.

        Raises ``httpx.HTTPStatusError`` on a non-2xx after ``max_retries``
        retries on transport/5xx errors (4xx never retries — a 404 model id
        is a config bug, retrying it just wastes the run's time budget).
        """
        if not texts:
            return []
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.post(
                    "/embeddings",
                    json={"model": self.model_name, "input": texts},
                )
                resp.raise_for_status()
                data = resp.json()["data"]
                # OpenAI-compatible endpoints echo an index per item; sort on
                # it rather than trusting array order (vLLM does return it in
                # order — the sort costs nothing and the contract is the index).
                data = sorted(data, key=lambda d: d.get("index", 0))
                vectors = [item["embedding"] for item in data]
                if len(vectors) != len(texts):
                    raise ValueError(f"embeddings: asked for {len(texts)}, got {len(vectors)}")
                with self._lock:
                    for v in vectors:
                        if len(v) != self.dimensions:
                            raise ValueError(
                                f"embeddings: model {self.model_name!r} returned "
                                f"{len(v)} dims, config says {self.dimensions} — "
                                "stored vectors would be incomparable; fix the "
                                "config and re-embed, do not mix."
                            )
                return vectors
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                retryable = (
                    not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code >= 500
                )
                if not retryable or attempt >= self.max_retries:
                    raise
                logger.warning(
                    "embed attempt %d/%d failed (%s), retrying",
                    attempt + 1,
                    self.max_retries,
                    exc,
                )
        raise RuntimeError("embed: unreachable retry-loop exit") from last_exc


class RerankerClient:
    """``POST {base_url}/rerank`` (vLLM's Cohere-style body).

    ``rerank(query, documents)`` returns ``(original_index, score)`` pairs
    sorted by score, highest first. Deployment history: mis-scored until
    2026-11-25 (see module docstring); scores now rank correctly on name
    and paraphrase probes — still treat any threshold as pending D-53
    calibration, not a number from spot probes.
    """

    def __init__(self, config: Any) -> None:
        cfg = _as_dict(config)
        self.model_name: str = str(cfg.get("model") or "")
        self.api_key: str = str(cfg.get("api_key") or "")
        self.base_url: str = str(cfg.get("base_url") or "").rstrip("/")
        self.timeout: float = float(cfg.get("timeout") or 30)
        self.max_retries: int = int(cfg.get("max_retries") or 2)
        if not self.model_name or not self.base_url:
            raise ValueError("RerankerClient needs model and base_url (config section 'reranker')")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=_auth(self.api_key),
            timeout=httpx.Timeout(self.timeout),
        )

    def close(self) -> None:
        self._client.close()

    def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        payload: dict[str, Any] = {
            "model": self.model_name,
            "query": query,
            "documents": documents,
            "return_documents": False,
        }
        if top_n is not None:
            payload["top_n"] = min(top_n, len(documents))
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.post("/rerank", json=payload)
                resp.raise_for_status()
                results = resp.json()["results"]
                ranked = [(int(r["index"]), float(r["relevance_score"])) for r in results]
                ranked.sort(key=lambda t: -t[1])
                return ranked
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                retryable = (
                    not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code >= 500
                )
                if not retryable or attempt >= self.max_retries:
                    raise
                logger.warning(
                    "rerank attempt %d/%d failed (%s), retrying",
                    attempt + 1,
                    self.max_retries,
                    exc,
                )
        raise RuntimeError("rerank: unreachable retry-loop exit") from last_exc
