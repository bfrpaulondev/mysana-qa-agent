import os
import unittest
from unittest.mock import patch

from core.settings import Settings
from dashboard.server import DashboardRuntime, create_app


class DashboardRuntimeTests(unittest.TestCase):
    def test_session_starts_closed(self):
        runtime = DashboardRuntime(Settings())
        status = runtime.session_status()
        self.assertEqual(status["state"], "closed")

    def test_read_only_test_requires_open_browser(self):
        runtime = DashboardRuntime(Settings())
        with self.assertRaises(RuntimeError):
            runtime.run_read_only_test()

    def test_provider_payload_never_contains_api_key(self):
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "test-groq-secret",
                "NVIDIA_NIM_API_KEY": "test-nvidia-secret",
            },
            clear=True,
        ):
            runtime = DashboardRuntime(Settings())
            payload = runtime.provider_status()

        serialized = repr(payload)
        self.assertNotIn("test-groq-secret", serialized)
        self.assertNotIn("test-nvidia-secret", serialized)
        self.assertTrue(any(item["name"] == "Groq" and item["configured"] for item in payload))
        self.assertTrue(any(item["name"] == "NVIDIA NIM" and item["configured"] for item in payload))

    def test_openai_is_disabled_when_paid_fallback_is_off(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-openai-secret"}, clear=True):
            runtime = DashboardRuntime(Settings(enable_paid_fallback=False))
            openai = next(item for item in runtime.provider_status() if item["name"] == "OpenAI")
        self.assertTrue(openai["configured"])
        self.assertFalse(openai["enabled"])

    def test_dashboard_routes_exist(self):
        app = create_app(Settings())
        paths = {route.path for route in app.routes}
        self.assertIn("/", paths)
        self.assertIn("/api/status", paths)
        self.assertIn("/api/providers/test", paths)
        self.assertIn("/api/session/open", paths)
        self.assertIn("/api/tests/start", paths)


if __name__ == "__main__":
    unittest.main()
