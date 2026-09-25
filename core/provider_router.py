from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from core.settings import Settings

logger = logging.getLogger(__name__)

_DEFAULT_NVIDIA_NIM_API_BASE = "https://integrate.api.nvidia.com/v1"


class LlmBudgetExceeded(RuntimeError):
    pass


class AllProvidersFailed(RuntimeError):
    pass


@dataclass(slots=True)
class CompletionResult:
    content: str
    model: str
    attempts: list[str]


@dataclass(slots=True)
class ProviderProbe:
    model: str
    ok: bool
    latency_ms: int
    response: str = ""
    error: str = ""


class ProviderRouter:
    """Small provider router inspired by AgenticSeek's provider abstraction.

    The configured model order is authoritative. OpenAI may be used as the
    primary provider; QA_ENABLE_PAID_FALLBACK only gates OpenAI when it is
    configured later in the chain as a fallback.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.calls_used = 0

    def reset_budget(self) -> None:
        self.calls_used = 0

    @staticmethod
    def _env_secret(name: str) -> str | None:
        value = os.getenv(name, "").strip()
        if not value:
            return None

        # .env.example uses explicit placeholders. They must never be treated as
        # configured credentials if the user copied the template unchanged.
        upper = value.upper()
        if upper.startswith("YOUR_") or upper in {"CHANGE_ME", "REPLACE_ME"}:
            return None
        if value.startswith("<") and value.endswith(">"):
            return None
        return value

    def provider_status(self) -> dict[str, bool]:
        return {
            "Groq": self._env_secret("GROQ_API_KEY") is not None,
            "NVIDIA NIM": self._env_secret("NVIDIA_NIM_API_KEY") is not None,
            "OpenAI": self._env_secret("OPENAI_API_KEY") is not None,
        }

    def _has_credentials(self, model: str) -> bool:
        if model.startswith("groq/"):
            return self._env_secret("GROQ_API_KEY") is not None
        if model.startswith("nvidia_nim/"):
            return self._env_secret("NVIDIA_NIM_API_KEY") is not None
        if model.startswith("openai/"):
            return self._env_secret("OPENAI_API_KEY") is not None
        return True

    def _provider_kwargs(self, model: str) -> dict[str, Any]:
        if model.startswith("groq/"):
            return {"api_key": self._env_secret("GROQ_API_KEY")}

        if model.startswith("nvidia_nim/"):
            api_base = os.getenv("NVIDIA_NIM_API_BASE", _DEFAULT_NVIDIA_NIM_API_BASE).strip()
            return {
                "api_key": self._env_secret("NVIDIA_NIM_API_KEY"),
                "api_base": api_base or _DEFAULT_NVIDIA_NIM_API_BASE,
            }

        if model.startswith("openai/"):
            return {"api_key": self._env_secret("OPENAI_API_KEY")}

        return {}

    def _openai_is_primary(self, model: str) -> bool:
        if not model.startswith("openai/"):
            return False
        text_primary = bool(self.settings.llm_models) and self.settings.llm_models[0] == model
        vision_primary = self.settings.vision_model == model
        return text_primary or vision_primary

    def _is_paid_fallback(self, model: str) -> bool:
        return model.startswith("openai/") and not self._openai_is_primary(model)

    def _litellm_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
    ) -> Any:
        try:
            import litellm
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("LiteLLM is not installed. Run: pip install -r requirements.txt") from exc

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "drop_params": True,
            "timeout": self.settings.llm_timeout_seconds,
            "max_tokens": max_tokens,
        }
        kwargs.update(self._provider_kwargs(model))
        return litellm.completion(**kwargs)

    def probe_model(self, model: str) -> ProviderProbe:
        """Make one tiny request to a provider without exposing its credential."""

        if self._is_paid_fallback(model) and not self.settings.enable_paid_fallback:
            return ProviderProbe(
                model=model,
                ok=False,
                latency_ms=0,
                error="paid fallback disabled",
            )

        if not self._has_credentials(model):
            return ProviderProbe(
                model=model,
                ok=False,
                latency_ms=0,
                error="credential not configured",
            )

        started = time.perf_counter()
        try:
            response = self._litellm_completion(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": "Provider connectivity check. Reply with exactly: OK",
                    }
                ],
                max_tokens=64,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            content = (response.choices[0].message.content or "").strip()
            return ProviderProbe(
                model=model,
                ok=True,
                latency_ms=latency_ms,
                response=content[:120],
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return ProviderProbe(
                model=model,
                ok=False,
                latency_ms=latency_ms,
                error=f"{type(exc).__name__}: {exc}",
            )

    def vision_completion(
        self,
        prompt: str,
        image_base64: str,
        mime_type: str = "image/png",
        max_tokens: int | None = None,
    ) -> CompletionResult:
        model = self.settings.vision_model
        if not self._has_credentials(model):
            raise AllProvidersFailed(
                f"Vision model credentials are not configured for {model}."
            )

        if self.calls_used >= self.settings.max_llm_calls_per_task:
            raise LlmBudgetExceeded(
                f"LLM call budget exceeded ({self.settings.max_llm_calls_per_task} calls)."
            )

        self.calls_used += 1
        try:
            response = self._litellm_completion(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{image_base64}"
                                },
                            },
                        ],
                    }
                ],
                max_tokens=max_tokens or self.settings.llm_max_output_tokens,
            )
            content = response.choices[0].message.content
            if not content:
                raise RuntimeError("Vision model returned empty content")
            return CompletionResult(
                content=content,
                model=model,
                attempts=[model],
            )
        except Exception as exc:
            raise AllProvidersFailed(
                f"Vision model failed ({model}): {type(exc).__name__}: {exc}"
            ) from exc

    def completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
    ) -> CompletionResult:
        if self.calls_used >= self.settings.max_llm_calls_per_task:
            raise LlmBudgetExceeded(
                f"LLM call budget exceeded ({self.settings.max_llm_calls_per_task} calls)."
            )

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
                response = self._litellm_completion(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens or self.settings.llm_max_output_tokens,
                )
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
                "No configured LLM provider is available. Configure OPENAI_API_KEY, "
                "GROQ_API_KEY or NVIDIA_NIM_API_KEY for at least one model in the chain."
            )

        raise AllProvidersFailed("All configured LLM providers failed: " + " | ".join(errors))
