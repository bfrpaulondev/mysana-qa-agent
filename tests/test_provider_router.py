import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.provider_router import ProviderRouter
from core.settings import Settings


class ProviderRouterTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()

    def test_example_placeholders_are_not_credentials(self):
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "YOUR_GROQ_API_KEY_HERE",
                "NVIDIA_NIM_API_KEY": "YOUR_NVIDIA_NIM_API_KEY_HERE",
                "OPENAI_API_KEY": "YOUR_OPENAI_API_KEY_HERE",
            },
            clear=True,
        ):
            router = ProviderRouter(self.settings)
            self.assertEqual(
                router.provider_status(),
                {"Groq": False, "NVIDIA NIM": False, "OpenAI": False},
            )

    def test_real_values_are_detected_without_exposing_them(self):
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "test-groq-secret",
                "NVIDIA_NIM_API_KEY": "test-nvidia-secret",
                "OPENAI_API_KEY": "test-openai-secret",
            },
            clear=True,
        ):
            router = ProviderRouter(self.settings)
            status = router.provider_status()
            self.assertTrue(status["Groq"])
            self.assertTrue(status["NVIDIA NIM"])
            self.assertTrue(status["OpenAI"])

    def test_openai_primary_is_not_blocked_by_paid_fallback_flag(self):
        settings = Settings(
            vision_model="openai/gpt-5.6-luna",
            llm_models=(
                "openai/gpt-5.6-luna",
                "groq/openai/gpt-oss-120b",
                "nvidia_nim/z-ai/glm-5.3",
            ),
            enable_paid_fallback=False,
        )
        router = ProviderRouter(settings)

        self.assertFalse(router._is_paid_fallback("openai/gpt-5.6-luna"))

    def test_openai_later_in_chain_is_paid_fallback(self):
        settings = Settings(
            vision_model="nvidia_nim/z-ai/glm-5.3-flash",
            llm_models=(
                "groq/openai/gpt-oss-120b",
                "openai/gpt-5.6-luna",
            ),
            enable_paid_fallback=False,
        )
        router = ProviderRouter(settings)

        self.assertTrue(router._is_paid_fallback("openai/gpt-5.6-luna"))

    def test_nvidia_uses_configured_api_base(self):
        with patch.dict(
            os.environ,
            {
                "NVIDIA_NIM_API_KEY": "test-nvidia-secret",
                "NVIDIA_NIM_API_BASE": "https://example.invalid/v1",
            },
            clear=True,
        ):
            router = ProviderRouter(self.settings)
            kwargs = router._provider_kwargs("nvidia_nim/z-ai/glm-5.3")
            self.assertEqual(kwargs["api_base"], "https://example.invalid/v1")
            self.assertEqual(kwargs["api_key"], "test-nvidia-secret")

    def test_probe_allows_openai_primary_even_when_paid_fallback_disabled(self):
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
        )
        settings = Settings(
            vision_model="openai/gpt-5.6-luna",
            llm_models=("openai/gpt-5.6-luna", "groq/openai/gpt-oss-120b"),
            enable_paid_fallback=False,
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-openai-secret"}, clear=True):
            router = ProviderRouter(settings)
            with patch.object(router, "_litellm_completion", return_value=fake_response):
                result = router.probe_model("openai/gpt-5.6-luna")

        self.assertTrue(result.ok)
        self.assertEqual(result.response, "OK")

    def test_vision_completion_uses_configured_openai_model(self):
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"action":"done"}'))]
        )
        settings = Settings(vision_model="openai/gpt-5.6-luna")
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "test-openai-secret"},
            clear=True,
        ):
            router = ProviderRouter(settings)
            with patch.object(router, "_litellm_completion", return_value=fake_response) as mocked:
                result = router.vision_completion("inspect", "ZmFrZS1pbWFnZQ==", max_tokens=320)

        self.assertEqual(result.model, "openai/gpt-5.6-luna")
        self.assertEqual(result.content, '{"action":"done"}')
        call = mocked.call_args.kwargs
        self.assertEqual(call["model"], "openai/gpt-5.6-luna")
        self.assertEqual(call["max_tokens"], 320)
        serialized = repr(call["messages"])
        self.assertIn("data:image/png;base64,ZmFrZS1pbWFnZQ==", serialized)
        self.assertNotIn("test-openai-secret", serialized)

    def test_probe_returns_success_without_exposing_key(self):
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
        )
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-groq-secret"}, clear=True):
            router = ProviderRouter(self.settings)
            with patch.object(router, "_litellm_completion", return_value=fake_response):
                result = router.probe_model("groq/openai/gpt-oss-120b")

        self.assertTrue(result.ok)
        self.assertEqual(result.response, "OK")
        self.assertNotIn("test-groq-secret", result.response)


if __name__ == "__main__":
    unittest.main()
