import os
import threading
import time
import unittest
from unittest.mock import patch

from core.settings import Settings
from dashboard.server import DASHBOARD_VERSION, DashboardRuntime, create_app


class DashboardRuntimeTests(unittest.TestCase):
    def test_session_starts_closed(self):
        runtime = DashboardRuntime(Settings())
        status = runtime.session_status()
        self.assertEqual(status["state"], "closed")
        self.assertEqual(status["browser"], "Chromium")

    def test_visual_test_requires_open_browser(self):
        runtime = DashboardRuntime(Settings())
        with self.assertRaises(RuntimeError):
            runtime.start_visual_test()

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

    def test_status_payload_does_not_contain_credentials(self):
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "secret-groq-value",
                "NVIDIA_NIM_API_KEY": "secret-nvidia-value",
            },
            clear=True,
        ):
            runtime = DashboardRuntime(Settings())
            payload = runtime.status_payload()

        serialized = repr(payload)
        self.assertNotIn("secret-groq-value", serialized)
        self.assertNotIn("secret-nvidia-value", serialized)
        self.assertNotIn("password", serialized.lower())

    def test_approval_blocks_until_approved(self):
        runtime = DashboardRuntime(Settings(approval_timeout_seconds=2))
        results = []

        thread = threading.Thread(
            target=lambda: results.append(
                runtime.request_approval(
                    {
                        "action": "click",
                        "label": "Clicar em Guardar",
                        "target": "#guardar",
                        "secret": False,
                        "value_preview": None,
                        "url": "https://mysana.sanahotels.com/test",
                        "title": "Teste",
                    }
                )
            )
        )
        thread.start()

        for _ in range(50):
            if runtime.pending_approval:
                break
            time.sleep(0.01)

        self.assertTrue(thread.is_alive())
        self.assertIsNotNone(runtime.pending_approval)
        approval_id = runtime.pending_approval["id"]

        runtime.resolve_approval(approval_id, True)
        thread.join(timeout=1)

        self.assertEqual(results, [True])
        self.assertIsNone(runtime.pending_approval)

    def test_approval_reject_stops_action(self):
        runtime = DashboardRuntime(Settings(approval_timeout_seconds=2))
        results = []

        thread = threading.Thread(
            target=lambda: results.append(
                runtime.request_approval(
                    {
                        "action": "write",
                        "label": "Escrever valor",
                        "target": "#valor",
                        "secret": False,
                        "value_preview": "12,30",
                        "url": "https://mysana.sanahotels.com/test",
                        "title": "Teste",
                    }
                )
            )
        )
        thread.start()

        for _ in range(50):
            if runtime.pending_approval:
                break
            time.sleep(0.01)

        approval_id = runtime.pending_approval["id"]
        runtime.resolve_approval(approval_id, False)
        thread.join(timeout=1)

        self.assertEqual(results, [False])

    def test_secret_approval_never_exposes_value(self):
        runtime = DashboardRuntime(Settings(approval_timeout_seconds=2))
        results = []

        thread = threading.Thread(
            target=lambda: results.append(
                runtime.request_approval(
                    {
                        "action": "write",
                        "label": "Escrever password",
                        "target": "#password",
                        "secret": True,
                        "value_preview": "super-secret-password",
                        "value_length": 21,
                        "url": "https://mysana.sanahotels.com/login",
                        "title": "Login",
                    }
                )
            )
        )
        thread.start()

        for _ in range(50):
            if runtime.pending_approval:
                break
            time.sleep(0.01)

        payload = runtime.status_payload()
        serialized = repr(payload)
        self.assertNotIn("super-secret-password", serialized)
        self.assertIsNone(payload["pending_approval"]["value_preview"])
        self.assertEqual(payload["pending_approval"]["value_length"], 21)

        approval_id = payload["pending_approval"]["id"]
        runtime.resolve_approval(approval_id, False)
        thread.join(timeout=1)
        self.assertEqual(results, [False])

    def test_status_payload_includes_build_version(self):
        runtime = DashboardRuntime(Settings())
        payload = runtime.status_payload()
        self.assertEqual(payload["version"], DASHBOARD_VERSION)
        self.assertIn("computer-use", payload["version"])

    def test_dashboard_routes_exist(self):
        app = create_app(Settings())
        paths = {route.path for route in app.routes}
        self.assertIn("/", paths)
        self.assertIn("/api/status", paths)
        self.assertIn("/api/providers/test", paths)
        self.assertIn("/api/session/open", paths)
        self.assertIn("/api/session/login", paths)
        self.assertIn("/api/approvals/{approval_id}/approve", paths)
        self.assertIn("/api/approvals/{approval_id}/reject", paths)
        self.assertIn("/api/session/close", paths)
        self.assertIn("/api/tests/start", paths)


if __name__ == "__main__":
    unittest.main()
