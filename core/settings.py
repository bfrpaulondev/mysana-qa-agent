from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


ROOT_DIR = Path(__file__).resolve().parents[1]


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


def _env_list(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _default_chrome_profile_dir() -> Path:
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data) / "MySANA-QA-Agent" / "browser-profile"
    return ROOT_DIR / "runtime" / "chrome-profile"


@dataclass(slots=True)
class Settings:
    base_url: str = "https://mysana.sanahotels.com"
    allowed_hosts: tuple[str, ...] = ("mysana.sanahotels.com",)
    headless: bool = False
    chromium_binary: str | None = None
    chrome_profile_dir: Path = field(default_factory=_default_chrome_profile_dir)
    evidence_dir: Path = field(default_factory=lambda: ROOT_DIR / "runtime" / "evidence")
    visual_action_delay_ms: int = 500
    visual_typing_delay_ms: int = 55
    approval_timeout_seconds: int = 600
    prefer_openai: bool = True
    use_native_computer: bool = True
    computer_model: str = "gpt-5.6-sol"
    computer_reasoning_effort: str = "low"
    computer_max_turns: int = 20
    vision_model: str = "openai/gpt-5.6-luna"
    llm_models: tuple[str, ...] = (
        "openai/gpt-5.6-luna",
        "groq/openai/gpt-oss-120b",
        "nvidia_nim/z-ai/glm-5.3",
    )
    max_llm_calls_per_task: int = 8
    max_agent_steps: int = 10
    llm_timeout_seconds: int = 60
    llm_max_output_tokens: int = 1200
    enable_paid_fallback: bool = False
    allow_dangerous_actions: bool = False
    action_timeout_seconds: int = 15

    @classmethod
    def from_env(cls) -> "Settings":
        if load_dotenv:
            load_dotenv(ROOT_DIR / ".env")

        default_profile = _default_chrome_profile_dir()
        prefer_openai = _env_bool("QA_PREFER_OPENAI", True)
        use_native_computer = _env_bool("QA_USE_NATIVE_COMPUTER", True)
        configured_vision_model = os.getenv(
            "QA_VISION_MODEL",
            "openai/gpt-5.6-luna",
        ).strip() or "openai/gpt-5.6-luna"
        configured_llm_models = _env_list(
            "QA_LLM_MODELS",
            "openai/gpt-5.6-luna,groq/openai/gpt-oss-120b,nvidia_nim/z-ai/glm-5.3",
        )

        if prefer_openai:
            openai_primary = "openai/gpt-5.6-luna"
            configured_vision_model = openai_primary
            configured_llm_models = (
                openai_primary,
                *tuple(model for model in configured_llm_models if model != openai_primary),
            )

        settings = cls(
            base_url=os.getenv("MYSANA_BASE_URL", "https://mysana.sanahotels.com").rstrip("/"),
            allowed_hosts=_env_list("QA_ALLOWED_HOSTS", "mysana.sanahotels.com"),
            headless=_env_bool("QA_HEADLESS", False),
            chromium_binary=os.getenv("QA_CHROMIUM_BINARY", "").strip() or None,
            chrome_profile_dir=Path(
                os.getenv("QA_CHROME_PROFILE_DIR", "").strip() or str(default_profile)
            ).expanduser().resolve(),
            evidence_dir=Path(
                os.getenv("QA_EVIDENCE_DIR", str(ROOT_DIR / "runtime" / "evidence"))
            ).expanduser().resolve(),
            visual_action_delay_ms=_env_int("QA_VISUAL_ACTION_DELAY_MS", 500),
            visual_typing_delay_ms=_env_int("QA_VISUAL_TYPING_DELAY_MS", 55),
            approval_timeout_seconds=_env_int("QA_APPROVAL_TIMEOUT_SECONDS", 600),
            prefer_openai=prefer_openai,
            use_native_computer=use_native_computer,
            computer_model=os.getenv("QA_COMPUTER_MODEL", "gpt-5.6-sol").strip() or "gpt-5.6-sol",
            computer_reasoning_effort=os.getenv(
                "QA_COMPUTER_REASONING_EFFORT",
                "low",
            ).strip().lower() or "low",
            computer_max_turns=_env_int("QA_COMPUTER_MAX_TURNS", 20),
            vision_model=configured_vision_model,
            llm_models=configured_llm_models,
            max_llm_calls_per_task=_env_int("QA_MAX_LLM_CALLS_PER_TASK", 8),
            max_agent_steps=_env_int("QA_MAX_AGENT_STEPS", 10),
            llm_timeout_seconds=_env_int("QA_LLM_TIMEOUT_SECONDS", 60),
            llm_max_output_tokens=_env_int("QA_LLM_MAX_OUTPUT_TOKENS", 1200),
            enable_paid_fallback=_env_bool("QA_ENABLE_PAID_FALLBACK", False),
            allow_dangerous_actions=_env_bool("QA_ALLOW_DANGEROUS_ACTIONS", False),
            action_timeout_seconds=_env_int("QA_ACTION_TIMEOUT_SECONDS", 15),
        )
        settings.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
        settings.evidence_dir.mkdir(parents=True, exist_ok=True)
        return settings
