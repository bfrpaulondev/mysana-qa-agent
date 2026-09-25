import os
import unittest
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
            },
            clear=True,
        ):
            router = ProviderRouter(self.settings)
            status = router.provider_status()
            self.assertTrue(status["Groq"])
            self.assertTrue(status["NVIDIA NIM"])
            self.assertFalse(status["OpenAI"])

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
            kwargs = router._provider_kwargs("nvidia_nim/openai/gpt-oss-120b")
            self.assertEqual(kwargs["api_base"], "https://example.invalid/v1")
            self.assertEqual(kwargs["api_key"], "test-nvidia-secret")


if __name__ == "__main__":
    unittest.main()
