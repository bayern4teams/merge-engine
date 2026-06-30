"""
LLM client setup using LangChain.

Provides a unified async interface for calling LLMs (OpenAI, Azure, Anthropic)
with structured JSON output support and optional Langfuse tracing.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

logger = logging.getLogger("merge-engine.llm.client")


# ── Langfuse Settings ────────────────────────────────────────────────────
# Read from environment variables with sensible defaults.
# Also load from .env file if present.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

USE_LANGFUSE = os.getenv("LANGFUSE_ENABLED", "false").lower() in ("true", "1", "yes")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3000")


class _LangfuseContext:
    """Lazy-loaded Langfuse CallbackHandler."""

    _handler: Any = None
    _initialized: bool = False

    @classmethod
    def init_langfuse(cls):
        """Initialize Langfuse client (called once)."""
        if cls._initialized:
            return
        cls._initialized = True
        if not USE_LANGFUSE:
            logger.debug("Langfuse tracing disabled (LANGFUSE_ENABLED=false)")
            return
        try:
            from langfuse import Langfuse

            Langfuse(
                public_key=LANGFUSE_PUBLIC_KEY,
                secret_key=LANGFUSE_SECRET_KEY,
                host=LANGFUSE_HOST,
            )
            logger.info("Langfuse client initialized (host=%s)", LANGFUSE_HOST)
            logger.debug("Langfuse public_key=%s", LANGFUSE_PUBLIC_KEY[:8] + "..." if LANGFUSE_PUBLIC_KEY else "None")
        except Exception as exc:
            logger.warning("Failed to initialize Langfuse: %s", exc, exc_info=True)

    @classmethod
    def get_handler(cls):
        """Get or create the Langfuse CallbackHandler instance."""
        cls.init_langfuse()
        if cls._handler is None and USE_LANGFUSE:
            try:
                from langfuse.langchain import CallbackHandler

                cls._handler = CallbackHandler()
                logger.debug("Langfuse CallbackHandler created")
            except ImportError:
                logger.warning(
                    "langfuse.langchain module not found; tracing disabled. "
                    "Ensure langfuse>=3.0.0,<4.0.0 is installed."
                )
            except Exception as exc:
                logger.warning("Failed to create Langfuse CallbackHandler: %s", exc)
        if not USE_LANGFUSE:
            return None
        return cls._handler


def _get_langfuse_config(session_name: str = "merge-engine") -> dict[str, Any]:
    """Build Langfuse tracing config dict for with_config()."""
    handler = _LangfuseContext.get_handler()
    if handler is None:
        return {}
    return {
        "callbacks": [handler],
        "metadata": {
            "user_id": "merge-engine",
            "langfuse_session_id": session_name,
            "langfuse_tags": ["merge-pipeline"],
        },
        "tags": ["merge-pipeline", "langgraph"],
        "run_name": "llm_call",
    }


class LLMClient:
    """Unified LLM client wrapping LangChain chat models."""

    def __init__(self, config: dict[str, Any], session_name: str = "merge-engine") -> None:
        """Initialize the LLM client.

        Parameters
        ----------
        config : dict
            LLM configuration with keys: provider, model, api_key, base_url, temperature.
        session_name : str
            Langfuse session name for tracing grouping.
        """
        self.provider = config.get("provider", "openai")
        self.model_name = config.get("model", "gpt-4o")
        self.api_key = config.get("api_key", "")
        self.base_url = config.get("base_url")
        self.temperature = config.get("temperature", 0.0)
        self.session_name = session_name
        self._model: Any = None
        logger.debug("Initialized LLMClient (provider=%s, model=%s, base_url=%s, temp=%.1f)",
                     self.provider, self.model_name, self.base_url, self.temperature)

    def _get_model(self) -> Any:
        """Lazy-initialize the LangChain chat model with optional Langfuse tracing."""
        if self._model is not None:
            return self._model

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "api_key": self.api_key,
            "temperature": self.temperature,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url

        logger.debug("Creating %s model: %s", self.provider, self.model_name)

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

        # Add Langfuse tracing if enabled
        config = _get_langfuse_config(self.session_name)
        if config:
            self._model = self._model.with_config(config)
            logger.debug("Langfuse tracing enabled for model")
        else:
            logger.debug("Langfuse tracing disabled for model")

        return self._model

    async def call(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: dict[str, Any] | None = None,
    ) -> Any:
        """Call the LLM with system and user prompts.

        Parameters
        ----------
        system_prompt : str
            System message describing the task.
        user_prompt : str
            User message with the actual query/data.
        response_format : dict, optional
            JSON schema for structured output (OpenAI only).

        Returns
        -------
        Any
            Parsed response content.
        """
        logger.debug("ENTER LLMClient.call (provider=%s, model=%s)", self.provider, self.model_name)
        logger.debug("system_prompt (truncated): %.200s", system_prompt)
        logger.debug("user_prompt (truncated): %.500s", user_prompt)

        model = self._get_model()
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        if response_format:
            model = model.with_structured_output(response_format)
            logger.debug("Structured output mode enabled")

        logger.debug("Calling model.ainvoke() with %d messages", len(messages))
        response = await model.ainvoke(messages)
        logger.debug("LLM response received: type=%s, len=%d chars", type(response.content).__name__, len(str(response.content)))
        logger.debug("LLM response (first 500 chars): %s", str(response.content)[:500])

        logger.debug("EXIT LLMClient.call")
        return response.content

    async def call_json(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """Call the LLM and parse the response as JSON.

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
            If the response is not valid JSON.
        """
        logger.debug("ENTER LLMClient.call_json")
        content = await self.call(system_prompt, user_prompt)

        # Try direct JSON parse
        try:
            result = json.loads(content)
            logger.debug("Direct JSON parse successful: type=%s, keys=%s", type(result).__name__, list(result.keys()) if isinstance(result, dict) else "N/A")
            logger.debug("EXIT LLMClient.call_json (direct parse)")
            return result
        except json.JSONDecodeError as exc:
            # Try to extract JSON from markdown code blocks
            logger.debug("Direct JSON parse failed, trying markdown extraction: %s", exc)
            import re
            match = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
            if match:
                extracted = match.group(1).strip()
                logger.debug("Found markdown JSON block, extracting and parsing")
                try:
                    result = json.loads(extracted)
                    logger.debug("EXIT LLMClient.call_json (markdown parse)")
                    return result
                except json.JSONDecodeError as exc2:
                    logger.error("Markdown JSON parse failed: %s", exc2)
                    raise
            logger.error("Response is not valid JSON and no markdown block found")
            raise json.JSONDecodeError(
                f"Response is not valid JSON: {content[:200]}",
                content,
                0,
            )

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

        for i, prompt in enumerate(prompts):
            msg = HumanMessage(content=prompt)
            all_messages.append([messages[0], msg])

        # Process sequentially (parallel requires careful concurrency management)
        results = []
        for idx, msgs in enumerate(all_messages):
            logger.debug("  Processing prompt %d/%d", idx + 1, len(prompts))
            response = await model.ainvoke(msgs)
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