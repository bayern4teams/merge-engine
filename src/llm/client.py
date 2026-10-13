"""
LLM client setup using LangChain.

Provides a unified async interface for calling LLMs (OpenAI, Azure, Anthropic)
with structured JSON output support. Tracing goes to a self-hosted Langfuse
instance: every ``ainvoke`` here carries the shared Langfuse callback handler
(see ``src/llm/tracing.py``), so each call produces a generation span — nested
under the owning graph-node span when invoked inside the pipeline. Tracing is a
no-op when Langfuse isn't configured, so calls work with or without the stack.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.llm.cache_store import PERSISTENT_CACHE
from src.llm.cost import (
    COST_LEDGER,
    SEMANTIC_CACHE,
    cache_key,
    token_usage,
)
from src.llm.tracing import trace_config

logger = logging.getLogger("merge-engine.llm.client")

# Substrings that mark a call as blocked by the provider's account/quota
# state rather than a transient network blip or a bad request — the only
# class of error a same-account retry can never recover from, so it's the
# trigger for moving to the next model in the fallback chain instead of
# just re-raising.
_QUOTA_ERROR_MARKERS = (
    "quota",
    "429",
    "403",
    "rate limit",
    "rate_limit",
    "insufficient_quota",
    "allocationquota",
    "billing",
)


def _is_rejection(exc: BaseException) -> bool:
    """The endpoint answered and refused the request as malformed (bad request /
    unprocessable entity) -- not a transport, timeout or quota failure."""
    import openai

    return isinstance(exc, openai.APIStatusError) and exc.status_code in (400, 422)


def _is_quota_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _QUOTA_ERROR_MARKERS)


def _estimate_tokens(*parts: str) -> int:
    """Rough token count for the *avoided* cost of a cache hit.

    Deliberately only used for the saved-token figure, never for billing:
    ~4 characters per token is a well-known approximation, and labelling an
    estimate as a measurement is how a cost report stops being trustworthy.
    """
    return sum(len(p or "") for p in parts) // 4


class LLMClient:
    """Unified LLM client wrapping LangChain chat models."""

    def __init__(self, config: dict[str, Any], session_name: str = "merge-engine") -> None:
        """Initialize the LLM client.

        Parameters
        ----------
        config : dict
            LLM configuration with keys: provider, model, api_key, base_url, temperature.
        session_name : str
            Session name used for tracing/grouping.
        """
        self.provider = config.get("provider", "openai")
        self.model_name = config.get("model", "gpt-4o")
        self.api_key = config.get("api_key", "")
        self.base_url = config.get("base_url")
        self.temperature = config.get("temperature", 0.0)
        # An LLM call MUST be able to fail. Without an explicit timeout the
        # OpenAI/httpx stack defaults to 600s and then retries on top of it,
        # so an endpoint that accepts no connection and returns no reset just
        # hangs. config's base_url is a private LAN address
        # (http://10.10.9.45:8000/v1): reachable in 0.0s from a developer
        # machine, silently black-holed from GitHub Actions, where it hung one
        # integration test for the entire 30-minute stage budget and produced
        # a log of 2026 progress dots and nothing else.
        self.timeout = float(config.get("timeout", 60))
        self.max_retries = int(config.get("max_retries", 2))
        self.structured_output = config.get("structured_output", True)
        self.session_name = session_name
        # Phase O.5. Caching is on by default because entity resolution asks
        # about the same record pairs over and over; set cache_enabled=false
        # to compare against an uncached run.
        self.cache_enabled = config.get("cache_enabled", True)
        # Phase A5: the in-memory SEMANTIC_CACHE above dies with the process
        # -- a fresh `python -m src.main` starts with zero entries, so a
        # repeat merge over unchanged data paid for every naming/mapping/
        # matching call again even though the exact same questions were
        # already answered. This is the same cache_key, a second, durable
        # layer underneath it. Gated separately from cache_enabled (a user
        # comparing cached-vs-uncached wants the in-memory layer off too;
        # someone who only wants to skip the disk I/O for one run does not
        # need to lose the process-lifetime cache to do it).
        self.persistent_cache_enabled = config.get("persistent_cache_enabled", True)
        if self.persistent_cache_enabled:
            ttl_days = config.get("persistent_cache_ttl_days")
            max_rows = config.get("persistent_cache_max_rows")
            if ttl_days is not None or max_rows is not None:
                PERSISTENT_CACHE.configure(ttl_days=ttl_days, max_rows=max_rows)
        self.run_id = config.get("run_id", "") or session_name
        self._model: Any = None
        self._model_loop: Any = None  # the event loop self._model's HTTP client belongs to
        self._json_mode_unsupported = False
        # Automatic quota fallback (Phase 7): the primary model followed by
        # every model in config's fallback_models, tried in that exact order.
        # A quota/billing error on one account-bound model is never going to
        # resolve itself mid-run, so call() advances through this chain
        # instead of surfacing the failure and waiting for a manual restart.
        self.model_chain: list[str] = [self.model_name, *(config.get("fallback_models") or [])]
        self._model_chain_idx = 0
        logger.debug(
            "Initialized LLMClient (provider=%s, model=%s, base_url=%s, temp=%.1f, timeout=%.0fs)",
            self.provider,
            self.model_name,
            self.base_url,
            self.temperature,
            self.timeout,
        )
        if len(self.model_chain) > 1:
            logger.debug(
                "LLM fallback chain (%d models): %s", len(self.model_chain), self.model_chain
            )

    def _advance_model(self) -> bool:
        """Move to the next model in the fallback chain.

        Returns False once the chain is exhausted, so the caller knows to
        stop retrying and surface the error instead of looping forever.
        """
        if self._model_chain_idx + 1 >= len(self.model_chain):
            return False
        self._model_chain_idx += 1
        self.model_name = self.model_chain[self._model_chain_idx]
        self._model = None  # force _get_model() to rebuild for the new name
        logger.warning(
            "LLM quota/billing error — falling back to next model in the chain (%d/%d): %s",
            self._model_chain_idx + 1,
            len(self.model_chain),
            self.model_name,
        )
        return True

    def _get_model(self) -> Any:
        """Lazy-initialize the LangChain chat model.

        PRE-D3: rebuilt, with its own async HTTP client, when called from another
        event loop than the one it was built in -- langchain_openai otherwise
        shares one pooled client process-wide, and a connection opened under a
        closed loop fails the next loop's first call ("Event loop is closed")."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._model is not None and (loop is None or loop is self._model_loop):
            return self._model
        self._model_loop = loop

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "api_key": self.api_key,
            "temperature": self.temperature,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url

        logger.debug("Creating %s model: %s", self.provider, self.model_name)

        if loop is not None and self.provider in ("openai", "azure"):
            kwargs["http_async_client"] = httpx.AsyncClient(timeout=self.timeout)

        if self.provider == "openai":
            self._model = ChatOpenAI(**kwargs)
            logger.debug("ChatOpenAI model created")
        elif self.provider == "anthropic":
            kwargs["api_key"] = self.api_key
            self._model = ChatAnthropic(**kwargs)
            logger.debug("ChatAnthropic model created")
        elif self.provider == "azure":
            kwargs["azure_endpoint"] = self.base_url
            self._model = ChatOpenAI(**kwargs)  # Azure uses OpenAI interface
            logger.debug("Azure ChatOpenAI model created")
        else:
            raise ValueError(f"Unsupported LLM provider: '{self.provider}'")

        return self._model

    async def call(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: dict[str, Any] | None = None,
        json_mode: bool = False,
        validate_before_cache: bool = False,
    ) -> Any:
        """Call the LLM with system and user prompts.

        Parameters
        ----------
        system_prompt : str
            System message describing the task.
        user_prompt : str
            User message with the actual query/data.
        response_format : dict, optional
            JSON schema for tool-calling-based structured output
            (``model.with_structured_output``).
        json_mode : bool
            Request OpenAI-style ``{"type": "json_object"}`` response
            formatting (looser than ``response_format`` — just constrains
            output to be valid JSON, no fixed schema). Silently skipped if
            an earlier call showed the endpoint doesn't support it.
        validate_before_cache : bool
            When True, the response is only written to ``SEMANTIC_CACHE`` if
            it parses as JSON (``_parse_json_content``). Without this, a
            malformed response used to get cached unconditionally, so a
            later identical call would replay the same unparseable content
            forever instead of ever retrying the real LLM (Phase 0 Sec 0.8).
            ``call_json`` is the caller that needs this; a caller expecting
            plain text has nothing to "successfully parse" and should leave
            this False.

        Returns
        -------
        Any
            Parsed response content.
        """
        logger.debug(
            "ENTER LLMClient.call (provider=%s, model=%s, json_mode=%s)",
            self.provider,
            self.model_name,
            json_mode,
        )
        logger.debug("system_prompt (truncated): %.200s", system_prompt)
        logger.debug("user_prompt (truncated): %.500s", user_prompt)

        key = cache_key(
            model=self.model_name,
            temperature=self.temperature,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format=response_format,
            json_mode=json_mode,
        )
        if self.cache_enabled:
            found, cached = SEMANTIC_CACHE.get(key)
            if found:
                logger.debug("LLM cache hit (in-memory, %s…)", key[:12])
                COST_LEDGER.record(
                    self.run_id,
                    model=self.model_name,
                    cached=True,
                    input_tokens=_estimate_tokens(system_prompt, user_prompt),
                )
                return cached
            if self.persistent_cache_enabled:
                found, cached = PERSISTENT_CACHE.get(key)
                if found:
                    logger.debug("LLM cache hit (persistent, %s…)", key[:12])
                    # Populate the in-memory layer too, so every call after
                    # this one in the same process hits L1 without another
                    # disk round trip.
                    SEMANTIC_CACHE.put(key, cached)
                    COST_LEDGER.record(
                        self.run_id,
                        model=self.model_name,
                        cached=True,
                        input_tokens=_estimate_tokens(system_prompt, user_prompt),
                    )
                    return cached

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        cfg = trace_config(tags=[self.session_name])

        while True:
            base_model = self._get_model()
            if response_format:
                model = base_model.with_structured_output(response_format)
                logger.debug("Structured output mode enabled")
            elif (
                json_mode
                and self.provider in ("openai", "azure")
                and not self._json_mode_unsupported
            ):
                model = base_model.bind(response_format={"type": "json_object"})
                logger.debug("JSON-object response format requested")
            else:
                model = base_model

            logger.debug(
                "Calling model.ainvoke() with %d messages (model=%s)",
                len(messages),
                self.model_name,
            )
            try:
                response = await model.ainvoke(messages, config=cfg)
                break
            except Exception as exc:
                if json_mode and not response_format and not self._json_mode_unsupported:
                    # PRE-D3: only the endpoint refusing the request is a verdict on
                    # json_object; a transport failure (a timeout, a dropped
                    # connection) is retried without it but says nothing about it.
                    if _is_rejection(exc):
                        logger.warning(
                            "json_object response_format rejected by endpoint, "
                            "retrying without it: %s",
                            exc,
                        )
                        self._json_mode_unsupported = True
                    else:
                        logger.warning("LLM call failed (%s); retrying once without JSON mode", exc)
                    try:
                        response = await base_model.ainvoke(messages, config=cfg)
                        break
                    except Exception as exc2:
                        exc = exc2
                if _is_quota_error(exc) and self._advance_model():
                    continue
                raise
        logger.debug(
            "LLM response received: type=%s, len=%d chars",
            type(response.content).__name__,
            len(str(response.content)),
        )
        logger.debug("LLM response (first 500 chars): %s", str(response.content)[:500])

        prompt_tokens, completion_tokens = token_usage(response)
        COST_LEDGER.record(
            self.run_id,
            model=self.model_name,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        )
        if self.cache_enabled:
            should_cache = True
            if validate_before_cache:
                try:
                    self._parse_json_content(response.content)
                except json.JSONDecodeError:
                    should_cache = False
                    logger.debug("Not caching: response did not parse as JSON")
            if should_cache:
                SEMANTIC_CACHE.put(key, response.content)
                if self.persistent_cache_enabled:
                    # Same validated response, same guarantee as the
                    # in-memory write above: never persist something that
                    # didn't pass validate_before_cache's check.
                    PERSISTENT_CACHE.put(key, response.content, model=self.model_name)

        logger.debug("EXIT LLMClient.call")
        return response.content

    @staticmethod
    def _parse_json_content(content: str) -> dict[str, Any]:
        """Parse LLM output as JSON, falling back to markdown code-block extraction."""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            import re

            match = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
            if match:
                return json.loads(match.group(1).strip())
            raise

    async def call_json(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """Call the LLM and parse the response as JSON.

        Requests JSON-object response formatting (subject to
        ``structured_output`` config / endpoint support), and retries once
        with the parse error fed back to the model if the first response
        isn't valid JSON.

        Parameters
        ----------
        system_prompt : str
            System message describing the task.
        user_prompt : str
            User message with data to process.

        Returns
        -------
        dict
            Parsed JSON object.

        Raises
        ------
        json.JSONDecodeError
            If neither attempt produces valid JSON.
        """
        logger.debug("ENTER LLMClient.call_json")
        content = await self.call(
            system_prompt,
            user_prompt,
            json_mode=self.structured_output,
            validate_before_cache=True,
        )

        try:
            result = self._parse_json_content(content)
            logger.debug("Direct/markdown JSON parse successful: type=%s", type(result).__name__)
            logger.debug("EXIT LLMClient.call_json (first attempt)")
            return result
        except json.JSONDecodeError as exc:
            logger.warning(
                "LLM output was not valid JSON, retrying once with error feedback: %s", exc
            )
            retry_prompt = (
                f"{user_prompt}\n\n---\n"
                f"Your previous output was not valid JSON: {exc}. "
                f"Return ONLY the JSON object, with no surrounding text or markdown fences."
            )
            content = await self.call(
                system_prompt,
                retry_prompt,
                json_mode=self.structured_output,
                validate_before_cache=True,
            )
            try:
                result = self._parse_json_content(content)
                logger.debug("EXIT LLMClient.call_json (retry succeeded)")
                return result
            except json.JSONDecodeError as retry_exc:
                logger.error("Retry also failed to produce valid JSON")
                raise json.JSONDecodeError(
                    f"Response is not valid JSON after retry: {content[:200]}",
                    content,
                    0,
                ) from retry_exc

    async def call_batch(
        self,
        system_prompt: str,
        prompts: list[str],
    ) -> list[Any]:
        """Call the LLM with multiple user prompts.

        Parameters
        ----------
        system_prompt : str
            Common system message.
        prompts : list[str]
            List of user messages.

        Returns
        -------
        list[Any]
            List of responses.
        """
        logger.debug("ENTER LLMClient.call_batch (%d prompts)", len(prompts))
        model = self._get_model()
        messages = [SystemMessage(content=system_prompt)]
        all_messages = []

        for prompt in prompts:
            msg = HumanMessage(content=prompt)
            all_messages.append([messages[0], msg])

        # Process sequentially (parallel requires careful concurrency management)
        cfg = trace_config(tags=[self.session_name])
        results = []
        for idx, msgs in enumerate(all_messages):
            logger.debug("  Processing prompt %d/%d", idx + 1, len(prompts))
            response = await model.ainvoke(msgs, config=cfg)
            results.append(response.content)
            logger.debug("  Prompt %d done: %d chars", idx + 1, len(str(response.content)))

        logger.debug("EXIT LLMClient.call_batch (%d results)", len(results))
        return results


def create_llm_client(config: dict[str, Any]) -> LLMClient:
    """Factory function to create an LLM client.

    Parameters
    ----------
    config : dict
        LLM configuration from Config.llm.

    Returns
    -------
    LLMClient
        Initialized LLM client instance.
    """
    logger.debug("create_llm_client called with config keys: %s", list(config.keys()))
    return LLMClient(config)
