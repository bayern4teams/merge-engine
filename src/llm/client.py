"""
LLM client setup using LangChain.

Provides a unified async interface for calling LLMs (OpenAI, Azure, Anthropic)
with structured JSON output support.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

logger = logging.getLogger("merge-engine.llm.client")


class LLMClient:
    """Unified LLM client wrapping LangChain chat models."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize the LLM client.

        Parameters
        ----------
        config : dict
            LLM configuration with keys: provider, model, api_key, base_url, temperature.
        """
        self.provider = config.get("provider", "openai")
        self.model_name = config.get("model", "gpt-4o")
        self.api_key = config.get("api_key", "")
        self.base_url = config.get("base_url")
        self.temperature = config.get("temperature", 0.0)
        self._model: Any = None

    def _get_model(self) -> Any:
        """Lazy-initialize the LangChain chat model."""
        if self._model is not None:
            return self._model

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "api_key": self.api_key,
            "temperature": self.temperature,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url

        if self.provider == "openai":
            self._model = ChatOpenAI(**kwargs)
        elif self.provider == "anthropic":
            kwargs["api_key"] = self.api_key
            self._model = ChatAnthropic(**kwargs)
        elif self.provider == "azure":
            kwargs["azure_endpoint"] = self.base_url
            self._model = ChatOpenAI(**kwargs)  # Azure uses OpenAI interface
        else:
            raise ValueError(f"Unsupported LLM provider: '{self.provider}'")

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
        model = self._get_model()
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        if response_format and self.provider == "openai":
            model = model.with_structured_output(response_format)

        response = await model.ainvoke(messages)
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
        content = await self.call(system_prompt, user_prompt)

        # Try direct JSON parse
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            import re
            match = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
            if match:
                return json.loads(match.group(1).strip())
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
        model = self._get_model()
        messages = [SystemMessage(content=system_prompt)]
        all_messages = []

        for i, prompt in enumerate(prompts):
            msg = HumanMessage(content=prompt)
            all_messages.append([messages[0], msg])

        # Process sequentially (parallel requires careful concurrency management)
        results = []
        for msgs in all_messages:
            response = await model.ainvoke(msgs)
            results.append(response.content)

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
    return LLMClient(config)