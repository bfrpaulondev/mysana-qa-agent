from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from core.settings import Settings

logger = logging.getLogger(__name__)


class LlmBudgetExceeded(RuntimeError):
    pass


class AllProvidersFailed(RuntimeError):
    pass


@dataclass(slots=True)
class CompletionResult:
    content: str
    model: str
    attempts: list[str]


class ProviderRouter:
    """Small provider router inspired by AgenticSeek's provider abstraction.

    Free providers are tried first. OpenAI is skipped unless paid fallback is
    explicitly enabled. This avoids accidental API spend during repetitive QA.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.calls_used = 0

    def reset_budget(self) -> None:
        self.calls_used = 0

    def _has_credentials(self, model: str) -> bool:
        if model.startswith("groq/"):
            return bool(os.getenv("GROQ_API_KEY"))
        if model.startswith("nvidia_nim/"):
            return bool(os.getenv("NVIDIA_NIM_API_KEY"))
        if model.startswith("openai/"):
            return bool(os.getenv("OPENAI_API_KEY"))
        return True

    def _is_paid_fallback(self, model: str) -> bool:
        return model.startswith("openai/")

    def completion(self, messages: list[dict[str, str]]) -> CompletionResult:
        if self.calls_used >= self.settings.max_llm_calls_per_task:
            raise LlmBudgetExceeded(
                f"LLM call budget exceeded ({self.settings.max_llm_calls_per_task} calls)."
            )

        try:
            import litellm
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("LiteLLM is not installed. Run: pip install -r requirements.txt") from exc

        self.calls_used += 1
        attempts: list[str] = []
        errors: list[str] = []

        for model in self.settings.llm_models:
            if self._is_paid_fallback(model) and not self.settings.enable_paid_fallback:
                logger.info("Skipping paid fallback model: %s", model)
                continue
            if not self._has_credentials(model):
                logger.info("Skipping model without configured credentials: %s", model)
                continue

            attempts.append(model)
            logger.info("Trying LLM provider: %s", model)
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "drop_params": True,
                    "timeout": self.settings.llm_timeout_seconds,
                    "max_tokens": self.settings.llm_max_output_tokens,
                }
                response = litellm.completion(**kwargs)
                content = response.choices[0].message.content
                if not content:
                    raise RuntimeError("Provider returned empty content")
                logger.info("LLM provider succeeded: %s", model)
                return CompletionResult(content=content, model=model, attempts=attempts)
            except Exception as exc:  # Provider errors must trigger fallback.
                message = f"{model}: {type(exc).__name__}: {exc}"
                logger.warning("LLM provider failed: %s", message)
                errors.append(message)

        if not attempts:
            raise AllProvidersFailed(
                "No configured LLM provider is available. Add GROQ_API_KEY or NVIDIA_NIM_API_KEY. "
                "OpenAI is disabled by default to prevent accidental spend."
            )

        raise AllProvidersFailed("All configured LLM providers failed: " + " | ".join(errors))
