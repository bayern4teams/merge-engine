"""DSPy BaseLM adapter wrapping LLMClient.

Preserves LLMClient's quota fallback chain, SEMANTIC_CACHE integration,
COST_LEDGER partitioning by run_id, and trace_config() Langfuse tracing.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import dspy

from src.llm.client import LLMClient


class LLMClientDSPyAdapter(dspy.BaseLM):
    """Subclass of dspy.BaseLM delegating calls to an underlying LLMClient instance."""

    def __init__(self, client: LLMClient, **kwargs: Any) -> None:
        """Initialize adapter with an LLMClient instance."""
        super().__init__(model=client.model_name, **kwargs)
        self.client = client

    def forward(
        self,
        prompt: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Synchronous forward pass delegating to LLMClient.

        Extracts system prompt and user prompt from DSPy inputs, invokes
        LLMClient.call via asyncio event loop, and wraps the text result
        in OpenAI/LiteLLM completion shape expected by DSPy.
        """
        system_prompt = ""
        user_prompt = ""

        if messages:
            sys_parts = []
            user_parts = []
            for msg in messages:
                role = msg.get("role")
                content = msg.get("content", "")
                if role == "system":
                    sys_parts.append(content)
                else:
                    user_parts.append(content)
            system_prompt = "\n\n".join(sys_parts)
            user_prompt = "\n\n".join(user_parts)
        elif prompt:
            user_prompt = prompt

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            import nest_asyncio

            nest_asyncio.apply()
            res_content = loop.run_until_complete(
                self.client.call(system_prompt=system_prompt, user_prompt=user_prompt)
            )
        else:
            res_content = loop.run_until_complete(
                self.client.call(system_prompt=system_prompt, user_prompt=user_prompt)
            )

        # Update model name on self in case model advanced during quota fallback
        self.model = self.client.model_name

        msg = SimpleNamespace(content=res_content, role="assistant")
        choice = SimpleNamespace(message=msg, text=res_content, finish_reason="stop")
        return SimpleNamespace(
            choices=[choice],
            model=self.client.model_name,
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )
