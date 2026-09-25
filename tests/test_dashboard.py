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

    def test_openai_primary_is_enabled_even_when_paid_fallback_is_off(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-openai-secret"}, clear=True):
            runtime = DashboardRuntime(
                Settings(
                    llm_models=(
                        "openai/gpt-5.6-luna",
                        "groq/openai/gpt-oss-120b",
                    ),
                    vision_model="openai/gpt-5.6-luna",
                    enable_paid_fallback=False,
                )
            )
            openai = next(item for item in runtime.provider_status() if item["name"] == "OpenAI")

        self.assertTrue(openai["configured"])
        self.assertTrue(openai["enabled"])
        self.assertEqual(openai["role"], "primary")
        self.assertTrue(openai["vision"])

    def test_openai_fallback_is_disabled_when_paid_fallback_is_off(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-openai-secret"}, clear=True):
            runtime = DashboardRuntime(
                Settings(
                    llm_models=(
                        "groq/openai/gpt-oss-120b",
                        "openai/gpt-5.6-luna",
                    ),
                    vision_model="nvidia_nim/z-ai/glm-5.3-flash",
                    enable_paid_fallback=False,
                )
            )
            openai = next(item for item in runtime.provider_status() if item["name"] == "OpenAI")

        self.assertTrue(openai["configured"])
        self.assertFalse(openai["enabled"])
        self.assertEqual(openai["role"], "fallback")

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

    def test_approve_always_enables_rule_and_skips_future_prompt(self):
        runtime = DashboardRuntime(Settings(approval_timeout_seconds=2))
        results = []

        thread = threading.Thread(
            target=lambda: results.append(
                runtime.request_approval(
                    {
                        "action": "click",
                        "label": "Clicar em Continuar",
                        "target": "#continuar",
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

        approval_id = runtime.pending_approval["id"]
        result = runtime.resolve_approval(
            approval_id,
            True,
            remember_type=True,
        )
        thread.join(timeout=1)

        self.assertEqual(results, [True])
        self.assertEqual(result["remembered_type"], "click")
        self.assertIn("click", runtime.auto_approve_rules)

        second = runtime.request_approval(
            {
                "action": "click",
                "label": "Clicar noutro botão",
                "target": "#outro",
                "secret": False,
                "value_preview": None,
                "url": "https://mysana.sanahotels.com/test",
                "title": "Teste",
            }
        )
        self.assertTrue(second)
        self.assertIsNone(runtime.pending_approval)

    def test_disable_auto_approval_rule(self):
        runtime = DashboardRuntime(Settings())
        runtime.auto_approve_rules.add("select")

        result = runtime.disable_auto_approval("select")

        self.assertTrue(result["changed"])
        self.assertFalse(result["active"])
        self.assertNotIn("select", runtime.auto_approve_rules)

    def test_secret_write_ignores_write_auto_approval_rule(self):
        runtime = DashboardRuntime(Settings(approval_timeout_seconds=2))
        runtime.auto_approve_rules.add("write")
        results = []

        thread = threading.Thread(
            target=lambda: results.append(
                runtime.request_approval(
                    {
                        "action": "write",
                        "label": "Escrever password",
                        "target": "#password",
                        "secret": True,
                        "value_preview": None,
                        "value_length": 12,
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

        self.assertTrue(thread.is_alive())
        self.assertIsNotNone(runtime.pending_approval)

        approval_id = runtime.pending_approval["id"]
        runtime.resolve_approval(approval_id, False)
        thread.join(timeout=1)
        self.assertEqual(results, [False])

    def test_status_payload_exposes_active_rules_without_secrets(self):
        runtime = DashboardRuntime(Settings())
        runtime.auto_approve_rules.update({"click", "write"})

        payload = runtime.status_payload()

        actions = {rule["action"] for rule in payload["auto_approve_rules"]}
        self.assertEqual(actions, {"click", "write"})
        write_rule = next(
            rule for rule in payload["auto_approve_rules"] if rule["action"] == "write"
        )
        self.assertTrue(write_rule["secret_exception"])

    def test_live_steering_queues_correction_and_rejects_pending_action(self):
        runtime = DashboardRuntime(Settings(approval_timeout_seconds=2))
        runtime.test_running = True
        approval_results = []

        thread = threading.Thread(
            target=lambda: approval_results.append(
                runtime.request_approval(
                    {
                        "action": "click",
                        "label": "Clicar no alvo errado",
                        "target": "#wrong-target",
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

        result = runtime.steer_agent(
            "Não cliques aí; abre primeiro Feiras e Eventos.",
            reject_pending=True,
        )
        thread.join(timeout=1)

        self.assertTrue(result["queued"])
        self.assertTrue(result["pending_action_rejected"])
        self.assertEqual(approval_results, [False])

        control = runtime._consume_agent_control()
        self.assertEqual(
            control["steering"],
            ["Não cliques aí; abre primeiro Feiras e Eventos."],
        )
        self.assertFalse(control["stop_requested"])

    def test_stop_agent_sets_stop_flag_and_rejects_pending_action(self):
        runtime = DashboardRuntime(Settings(approval_timeout_seconds=2))
        runtime.test_running = True
        approval_results = []

        thread = threading.Thread(
            target=lambda: approval_results.append(
                runtime.request_approval(
                    {
                        "action": "select",
                        "label": "Seleccionar opção",
                        "target": "#processo",
                        "secret": False,
                        "value_preview": "Teste",
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

        result = runtime.stop_agent()
        thread.join(timeout=1)

        self.assertTrue(result["stop_requested"])
        self.assertEqual(approval_results, [False])
        control = runtime._consume_agent_control()
        self.assertTrue(control["stop_requested"])

    def test_agent_state_exposes_operational_trace(self):
        runtime = DashboardRuntime(Settings())
        runtime._update_agent_state(
            {
                "phase": "thinking",
                "step": 3,
                "detail": "A escolher a próxima acção.",
                "provider": "nvidia_nim/z-ai/glm-5.3-flash",
                "last_decision": {
                    "observation": "Formulário de login visível.",
                    "decision": "CLICK: Entrar",
                    "reason": "Credenciais já estão preenchidas.",
                    "confidence": "high",
                },
            }
        )

        payload = runtime.status_payload()
        state = payload["agent_state"]

        self.assertEqual(state["phase"], "thinking")
        self.assertEqual(state["step"], 3)
        self.assertEqual(state["last_decision"]["decision"], "CLICK: Entrar")
        self.assertIn("phase_started_at", state)

    def test_status_payload_includes_build_version(self):
        runtime = DashboardRuntime(Settings())
        payload = runtime.status_payload()
        self.assertEqual(payload["version"], DASHBOARD_VERSION)
        self.assertIn("openai-primary", payload["version"])

    def test_agent_plan_requires_open_browser(self):
        runtime = DashboardRuntime(Settings())
        with self.assertRaises(RuntimeError):
            runtime.plan_agent_command("Testa o formulário sem gravar")

    def test_status_starts_without_agent_plan(self):
        runtime = DashboardRuntime(Settings())
        payload = runtime.status_payload()
        self.assertIsNone(payload["current_goal"])
        self.assertIsNone(payload["current_plan"])

    def test_dashboard_routes_exist(self):
        app = create_app(Settings())
        paths = {route.path for route in app.routes}
        self.assertIn("/", paths)
        self.assertIn("/api/status", paths)
        self.assertIn("/api/providers/test", paths)
        self.assertIn("/api/session/open", paths)
        self.assertIn("/api/session/login", paths)
        self.assertIn("/api/approvals/{approval_id}/approve", paths)
        self.assertIn("/api/approvals/{approval_id}/approve-always", paths)
        self.assertIn("/api/approval-rules/{action_type}", paths)
        self.assertIn("/api/approvals/{approval_id}/reject", paths)
        self.assertIn("/api/agent/plan", paths)
        self.assertIn("/api/agent/run", paths)
        self.assertIn("/api/agent/steer", paths)
        self.assertIn("/api/agent/stop", paths)
        self.assertIn("/api/session/close", paths)
        self.assertIn("/api/tests/start", paths)


if __name__ == "__main__":
    unittest.main()
