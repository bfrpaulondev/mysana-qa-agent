from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from browser.session import BrowserSession
from core.provider_router import ProviderRouter
from core.settings import Settings
from qa.agent_runner import AgentRunner
from qa.reporter import RunReport, StepResult


STATIC_DIR = Path(__file__).resolve().parent / "static"
DASHBOARD_VERSION = "0.9.0-openai-primary"


class LoginPayload(BaseModel):
    username: str = Field(default="", max_length=512)
    password: str = Field(min_length=1, max_length=2048)


class AgentCommandPayload(BaseModel):
    command: str = Field(min_length=3, max_length=4000)


class AgentSteerPayload(BaseModel):
    message: str = Field(min_length=2, max_length=2000)
    reject_pending: bool = True


class DashboardRuntime:
    """Local runtime for the visual Computer Use style dashboard."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = ProviderRouter(settings)
        self.browser: BrowserSession | None = None
        self.lock = threading.RLock()
        self.approval_condition = threading.Condition(self.lock)
        self.pending_approval: dict[str, Any] | None = None
        self.approval_results: dict[str, bool] = {}
        self.auto_approve_rules: set[str] = set()
        self.last_provider_tests: list[dict[str, Any]] = []
        self.last_test: dict[str, Any] | None = None
        self.activity: list[dict[str, Any]] = []
        self.test_running = False
        self.login_required = False
        self.current_goal: str | None = None
        self.current_plan: dict[str, Any] | None = None
        self.steering_messages: list[str] = []
        self.stop_requested = False
        self.agent_state: dict[str, Any] = {
            "phase": "idle",
            "step": None,
            "detail": "Agente em espera.",
            "provider": None,
            "phase_started_at": time.time(),
            "last_decision": None,
        }

    def _on_browser_event(self, event: dict[str, Any]) -> None:
        with self.lock:
            self.activity.append(event)
            self.activity = self.activity[-80:]

    def _add_activity(self, action: str, message: str) -> None:
        self._on_browser_event(
            {
                "id": int(time.time() * 1000),
                "action": action,
                "message": message,
                "timestamp": time.time(),
            }
        )

    def _update_agent_state(self, update: dict[str, Any]) -> None:
        with self.lock:
            previous_phase = self.agent_state.get("phase")
            previous_step = self.agent_state.get("step")
            next_phase = update.get("phase", previous_phase)
            next_step = update.get("step", previous_step)

            if next_phase != previous_phase or next_step != previous_step:
                self.agent_state["phase_started_at"] = time.time()

            for key in ("phase", "step", "detail", "provider"):
                if key in update:
                    self.agent_state[key] = update[key]

            if update.get("last_decision") is not None:
                self.agent_state["last_decision"] = update["last_decision"]

    def _consume_agent_control(self) -> dict[str, Any]:
        with self.lock:
            steering = list(self.steering_messages)
            self.steering_messages.clear()
            return {
                "stop_requested": self.stop_requested,
                "steering": steering,
            }

    def steer_agent(self, message: str, reject_pending: bool = True) -> dict[str, Any]:
        text = message.strip()
        if not text:
            raise RuntimeError("Escreve uma correcção para o agente.")

        with self.lock:
            if not self.test_running:
                raise RuntimeError("O agente não está em execução.")
            self.steering_messages.append(text)
            has_pending = self.pending_approval is not None

        self._add_activity("steer", f"Correcção do utilizador: {text[:260]}")

        if has_pending and reject_pending:
            self._reject_pending_approval()
            self._add_activity(
                "steer",
                "Acção pendente rejeitada automaticamente para o agente poder reavaliar.",
            )

        self._update_agent_state(
            {
                "detail": "Correcção recebida; será aplicada no próximo ciclo de decisão.",
            }
        )

        return {
            "queued": True,
            "message": text,
            "pending_action_rejected": bool(has_pending and reject_pending),
        }

    def stop_agent(self) -> dict[str, Any]:
        with self.lock:
            if not self.test_running:
                return {
                    "stop_requested": False,
                    "message": "O agente já está parado.",
                }
            self.stop_requested = True
            has_pending = self.pending_approval is not None

        if has_pending:
            self._reject_pending_approval()

        self._add_activity(
            "stop",
            "Pedido de paragem recebido. Nenhuma nova acção será executada.",
        )
        self._update_agent_state(
            {
                "detail": (
                    "Paragem pedida. Se existir uma chamada ao modelo em curso, "
                    "o agente pára assim que essa chamada terminar."
                ),
            }
        )
        return {
            "stop_requested": True,
            "message": (
                "Paragem pedida. O agente não executará novas acções; "
                "uma chamada ao modelo já iniciada pode precisar de terminar primeiro."
            ),
        }

    def request_approval(self, request: dict[str, Any]) -> bool:
        action_type = str(request.get("action") or "action").strip().lower()
        secret = bool(request.get("secret"))

        with self.approval_condition:
            auto_allowed = action_type in self.auto_approve_rules and not secret

        if auto_allowed:
            self._add_activity(
                "approval",
                f"Auto-aprovado pela regra activa: {action_type.upper()}",
            )
            return True

        approval_id = uuid.uuid4().hex
        created_at = time.time()
        pending = {
            "id": approval_id,
            "action": action_type,
            "label": str(request.get("label") or "Acção pendente"),
            "target": str(request.get("target") or ""),
            "secret": bool(request.get("secret")),
            "value_preview": None if request.get("secret") else request.get("value_preview"),
            "value_length": request.get("value_length"),
            "url": str(request.get("url") or ""),
            "title": str(request.get("title") or ""),
            "created_at": created_at,
            "timeout_seconds": self.settings.approval_timeout_seconds,
        }

        with self.approval_condition:
            if self.pending_approval is not None:
                raise RuntimeError("Já existe uma acção à espera de aprovação.")

            self.pending_approval = pending
            self._add_activity(
                "approval",
                f"A aguardar aprovação: {pending['label']}",
            )

            deadline = time.monotonic() + self.settings.approval_timeout_seconds
            while approval_id not in self.approval_results:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if self.pending_approval and self.pending_approval["id"] == approval_id:
                        self.pending_approval = None
                    self._add_activity("approval", "Tempo de aprovação expirou — acção rejeitada")
                    return False
                self.approval_condition.wait(timeout=remaining)

            approved = self.approval_results.pop(approval_id)
            if self.pending_approval and self.pending_approval["id"] == approval_id:
                self.pending_approval = None

            self._add_activity(
                "approval",
                "Acção aprovada pelo utilizador" if approved else "Acção rejeitada pelo utilizador",
            )
            return approved

    def resolve_approval(
        self,
        approval_id: str,
        approved: bool,
        remember_type: bool = False,
    ) -> dict[str, Any]:
        with self.approval_condition:
            pending = self.pending_approval
            if pending is None or pending.get("id") != approval_id:
                raise RuntimeError("Esta aprovação já não está pendente.")

            action_type = str(pending.get("action") or "").lower()
            if remember_type:
                if action_type not in {"click", "write", "select"}:
                    raise RuntimeError(
                        f"O tipo de acção '{action_type}' não suporta aprovação automática."
                    )
                self.auto_approve_rules.add(action_type)

            self.approval_results[approval_id] = approved
            self.pending_approval = None
            self.approval_condition.notify_all()

        if remember_type and approved:
            self._add_activity(
                "approval-rule",
                f"Regra activa nesta sessão: sempre aprovar {action_type.upper()}",
            )

        return {
            "id": approval_id,
            "approved": approved,
            "remembered_type": action_type if remember_type and approved else None,
        }

    def disable_auto_approval(self, action_type: str) -> dict[str, Any]:
        normalized = action_type.strip().lower()
        if normalized not in {"click", "write", "select"}:
            raise RuntimeError(f"Regra de aprovação desconhecida: {action_type}")

        with self.approval_condition:
            existed = normalized in self.auto_approve_rules
            self.auto_approve_rules.discard(normalized)

        if existed:
            self._add_activity(
                "approval-rule",
                f"Regra desactivada: {normalized.upper()} volta a pedir aprovação",
            )

        return {
            "action": normalized,
            "active": False,
            "changed": existed,
        }

    def approval_rules_payload(self) -> list[dict[str, Any]]:
        labels = {
            "click": "CLICAR",
            "write": "ESCREVER",
            "select": "SELECCIONAR",
        }
        with self.approval_condition:
            active = sorted(self.auto_approve_rules)

        return [
            {
                "action": action,
                "label": labels[action],
                "scope": "session",
                "secret_exception": action == "write",
            }
            for action in active
        ]

    def _reject_pending_approval(self) -> None:
        with self.approval_condition:
            pending = self.pending_approval
            if pending is None:
                return
            approval_id = str(pending["id"])
            self.approval_results[approval_id] = False
            self.pending_approval = None
            self.approval_condition.notify_all()

    def _agent_runner(self) -> AgentRunner:
        with self.lock:
            browser = self.browser
        if browser is None or browser.driver is None:
            raise RuntimeError("Abre primeiro a sessão Chromium/MySANA.")

        return AgentRunner(
            browser=browser,
            provider=self.provider,
            settings=self.settings,
            event_callback=self._add_activity,
            state_callback=self._update_agent_state,
            control_callback=self._consume_agent_control,
        )

    def plan_agent_command(self, command: str) -> dict[str, Any]:
        command = command.strip()
        if not command:
            raise RuntimeError("Escreve um comando para o agente.")

        with self.lock:
            if self.test_running:
                raise RuntimeError("Já existe uma execução do agente em curso.")

        self._add_activity("chat", f"Comando recebido: {command[:240]}")
        runner = self._agent_runner()
        plan = runner.plan(command)

        with self.lock:
            self.current_goal = command
            self.current_plan = plan
            self.agent_state["last_decision"] = None

        self._add_activity("plan", plan["summary"])
        for index, step in enumerate(plan["steps"], start=1):
            self._add_activity("plan", f"{index}. {step}")

        return {
            "goal": command,
            "plan": plan,
            "message": "Plano gerado. Revê as acções e carrega Executar plano.",
        }

    def execute_agent_plan(self) -> dict[str, Any]:
        with self.lock:
            if self.test_running:
                raise RuntimeError("Já existe uma execução do agente em curso.")
            if not self.current_goal or not self.current_plan:
                raise RuntimeError("Gera primeiro um plano através do chat.")
            if self.browser is None or self.browser.driver is None:
                raise RuntimeError("Abre primeiro a sessão Chromium/MySANA.")

            goal = self.current_goal
            self.test_running = True
            self.stop_requested = False
            self.steering_messages.clear()
            self.last_test = {
                "ok": None,
                "status": "RUNNING",
                "message": f"Agente a executar: {goal}",
            }

        thread = threading.Thread(
            target=self._run_agent_worker,
            args=(goal,),
            daemon=True,
        )
        thread.start()
        return {
            "started": True,
            "goal": goal,
            "message": "Execução iniciada. O agente irá reavaliar o ecrã a cada passo.",
        }

    def _run_agent_worker(self, goal: str) -> None:
        self._add_activity("agent", f"A iniciar execução autónoma: {goal[:240]}")
        try:
            runner = self._agent_runner()
            report = runner.run(goal, report_name="dashboard-agent")
            final_status = report.steps[-1].status if report.steps else "PASS"
            result = {
                "ok": final_status == "PASS",
                "status": final_status,
                "title": self.browser.page_title() if self.browser else None,
                "url": self.browser.current_url() if self.browser else None,
                "interactive_elements": None,
                "visually_inspected": None,
                "report_dir": str(report.output_dir),
                "message": (
                    "Execução do agente terminada. Consulta o feed e o relatório."
                    if final_status == "PASS"
                    else "Execução interrompida/bloqueada. Consulta o trace e o relatório."
                ),
            }
        except Exception as exc:
            result = {
                "ok": False,
                "status": "FAIL",
                "title": None,
                "url": None,
                "interactive_elements": None,
                "visually_inspected": None,
                "report_dir": None,
                "message": f"{type(exc).__name__}: {exc}",
            }
            self._add_activity("agent", f"Execução falhou: {type(exc).__name__}: {exc}")
        finally:
            with self.lock:
                self.last_test = result
                self.test_running = False

    def _start_login_recovery_agent(self) -> None:
        with self.lock:
            if self.test_running:
                return
            if self.browser is None or self.browser.driver is None:
                return
            self.test_running = True
            self.stop_requested = False
            self.steering_messages.clear()
            self.last_test = {
                "ok": None,
                "status": "RUNNING",
                "message": "Agente visual a concluir o login.",
            }

        goal = (
            "Complete the current MySANA login using the credentials that are already filled in. "
            "Observe the screenshot and current DOM, click the correct login/entrar/sign-in control, "
            "and stop as soon as the login form disappears or the authenticated MySANA page is visible. "
            "Do not change any business data."
        )
        self._add_activity("recover", "A analisar visualmente o ecrã para concluir o login")
        thread = threading.Thread(
            target=self._run_login_recovery_worker,
            args=(goal,),
            daemon=True,
        )
        thread.start()

    def _run_login_recovery_worker(self, goal: str) -> None:
        try:
            runner = self._agent_runner()
            report = runner.run(goal, report_name="login-recovery")
            with self.lock:
                browser = self.browser

            login_required = True
            if browser is not None:
                try:
                    login_required = bool(browser.detect_login_form().get("required"))
                except Exception:
                    pass

            with self.lock:
                self.login_required = login_required
                self.last_test = {
                    "ok": not login_required,
                    "status": "PASS" if not login_required else "BLOCKED",
                    "title": browser.page_title() if browser else None,
                    "url": browser.current_url() if browser else None,
                    "interactive_elements": None,
                    "visually_inspected": None,
                    "report_dir": str(report.output_dir),
                    "message": (
                        "Login concluído pelo agente visual."
                        if not login_required
                        else "O login continua pendente; revê as acções propostas."
                    ),
                }
        except Exception as exc:
            with self.lock:
                self.last_test = {
                    "ok": False,
                    "status": "FAIL",
                    "title": None,
                    "url": None,
                    "interactive_elements": None,
                    "visually_inspected": None,
                    "report_dir": None,
                    "message": f"{type(exc).__name__}: {exc}",
                }
            self._add_activity("recover", f"Recuperação de login falhou: {type(exc).__name__}")
        finally:
            with self.lock:
                self.test_running = False

    def provider_status(self) -> list[dict[str, Any]]:
        configured = self.provider.provider_status()
        rows: list[dict[str, Any]] = []

        for index, model in enumerate(self.settings.llm_models):
            is_primary = index == 0
            if model.startswith("groq/"):
                name = "Groq"
                is_configured = configured["Groq"]
                enabled = is_configured
            elif model.startswith("nvidia_nim/"):
                name = "NVIDIA NIM"
                is_configured = configured["NVIDIA NIM"]
                enabled = is_configured
            elif model.startswith("openai/"):
                name = "OpenAI"
                is_configured = configured["OpenAI"]
                enabled = is_configured and (
                    is_primary or self.settings.enable_paid_fallback
                )
            else:
                name = model.split("/", 1)[0]
                is_configured = True
                enabled = True

            last = next(
                (item for item in self.last_provider_tests if item["model"] == model),
                None,
            )
            rows.append(
                {
                    "name": name,
                    "model": model,
                    "configured": is_configured,
                    "enabled": enabled,
                    "paid": model.startswith("openai/"),
                    "role": "primary" if is_primary else "fallback",
                    "vision": model == self.settings.vision_model,
                    "latency_ms": last.get("latency_ms") if last else None,
                    "last_ok": last.get("ok") if last else None,
                }
            )

        return rows

    def session_status(self) -> dict[str, Any]:
        with self.lock:
            browser = self.browser
            running = self.test_running

        if browser is None or browser.driver is None:
            return {
                "state": "closed",
                "url": None,
                "title": None,
                "browser": "Chromium",
                "login_required": False,
                "message": "Chromium QA ainda não foi aberto.",
            }

        try:
            url = browser.current_url()
            title = browser.page_title()

            if not running:
                try:
                    login = browser.detect_login_form()
                    with self.lock:
                        self.login_required = bool(login.get("required"))
                except Exception:
                    pass

            with self.lock:
                login_required = self.login_required

            return {
                "state": "open",
                "url": url,
                "title": title,
                "browser": browser.browser_label,
                "login_required": login_required,
                "message": (
                    "Login necessário — introduz as credenciais no dashboard."
                    if login_required
                    else "Chromium QA aberto e controlado pelo agente."
                ),
            }
        except Exception as exc:
            return {
                "state": "error",
                "url": None,
                "title": None,
                "browser": browser.browser_label,
                "login_required": False,
                "message": f"{type(exc).__name__}: {exc}",
            }

    def open_session(self) -> dict[str, Any]:
        with self.lock:
            current = self.session_status()
            if current["state"] == "open":
                return current

            if self.browser is not None:
                try:
                    self.browser.close()
                except Exception:
                    pass

            self.activity = []
            self.login_required = False
            self.auto_approve_rules.clear()
            self.steering_messages.clear()
            self.stop_requested = False
            self.agent_state = {
                "phase": "idle",
                "step": None,
                "detail": "Agente em espera.",
                "provider": None,
                "phase_started_at": time.time(),
                "last_decision": None,
            }
            self.browser = BrowserSession(
                self.settings,
                event_callback=self._on_browser_event,
                approval_callback=self.request_approval,
            )
            browser = self.browser

        browser.start()
        browser.navigate(self.settings.base_url)

        login = browser.detect_login_form()
        with self.lock:
            self.login_required = bool(login.get("required"))

        if self.login_required:
            self._add_activity("login", "Formulário de login detectado — aguardar credenciais do utilizador")
        else:
            self._add_activity("session", "MySANA aberto sem formulário de login visível")

        return self.session_status()

    def submit_login(self, username: str, password: str) -> dict[str, Any]:
        with self.lock:
            browser = self.browser

        if browser is None or browser.driver is None:
            raise RuntimeError("Abre primeiro a sessão Chromium/MySANA.")

        self._add_activity("login", "Credenciais recebidas localmente — a simular teclado no Chromium")
        result = browser.submit_login(username, password)

        with self.lock:
            self.login_required = bool(result.get("login_required"))

        if result.get("needs_agent_recovery"):
            self._add_activity(
                "login",
                "Credenciais preenchidas; o agente visual vai decidir o próximo passo do login",
            )
            self._start_login_recovery_agent()
        elif self.login_required:
            self._add_activity("login", "O formulário de login continua visível")
        else:
            self._add_activity("login", "Login submetido e formulário de password deixou de estar visível")

        # username/password are intentionally not stored on runtime/state/reports.
        return {
            "submitted": bool(result.get("submitted")),
            "agent_recovery_started": bool(result.get("needs_agent_recovery")),
            "login_required": self.login_required,
            "url": result.get("url"),
            "title": result.get("title"),
        }

    def close_session(self) -> dict[str, Any]:
        self._reject_pending_approval()
        with self.lock:
            browser = self.browser
            self.browser = None
            self.login_required = False
            self.auto_approve_rules.clear()
            self.steering_messages.clear()
            self.stop_requested = False
            self.agent_state = {
                "phase": "idle",
                "step": None,
                "detail": "Agente em espera.",
                "provider": None,
                "phase_started_at": time.time(),
                "last_decision": None,
            }

        if browser is not None:
            browser.close()

        self._add_activity("session", "Sessão Chromium fechada")
        return self.session_status()

    def test_enabled_providers(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for model in self.settings.llm_models:
            self._add_activity("provider", f"A testar {model}")
            probe = self.provider.probe_model(model)
            results.append(
                {
                    "model": probe.model,
                    "ok": probe.ok,
                    "latency_ms": probe.latency_ms,
                    "response": probe.response,
                    "error": probe.error,
                }
            )
            self._add_activity(
                "provider",
                f"{model}: {'PASS' if probe.ok else 'FAIL'} ({probe.latency_ms} ms)",
            )

        self.last_provider_tests = results
        return results

    def start_visual_test(self) -> dict[str, Any]:
        with self.lock:
            if self.browser is None or self.browser.driver is None:
                raise RuntimeError("Abre primeiro a sessão MySANA no dashboard.")
            if self.login_required:
                raise RuntimeError("Conclui primeiro o login no MySANA.")
            if self.test_running:
                raise RuntimeError("Já existe um teste visual em execução.")

            self.test_running = True
            self.last_test = {
                "ok": None,
                "status": "RUNNING",
                "message": "Inspecção visual em execução no Chromium.",
            }

        thread = threading.Thread(target=self._run_visual_test_worker, daemon=True)
        thread.start()
        return {"started": True, "message": "Teste visual iniciado no Chromium."}

    def _run_visual_test_worker(self) -> None:
        with self.lock:
            browser = self.browser

        if browser is None:
            return

        report = RunReport("dashboard-visual-readonly", self.settings.evidence_dir)
        self._add_activity("test", "A iniciar smoke test visual read-only")

        try:
            url = browser.current_url()
            title = browser.page_title()
            elements = browser.snapshot_interactive(max_elements=150)
            inspected = browser.visual_inspect(limit=min(10, len(elements)))
            screenshot = browser.screenshot(report.screenshot_path(1))

            report.add(
                StepResult(
                    1,
                    "visual-read-only-inspection",
                    "PASS",
                    (
                        f"Página inspeccionada visualmente sem alterações: "
                        f"{len(elements)} elementos interactivos, {len(inspected)} destacados."
                    ),
                    screenshot=screenshot,
                    data={
                        "url": url,
                        "title": title,
                        "interactive_elements": len(elements),
                        "visually_inspected": len(inspected),
                    },
                )
            )
            report.save()

            result = {
                "ok": True,
                "status": "PASS",
                "url": url,
                "title": title,
                "interactive_elements": len(elements),
                "visually_inspected": len(inspected),
                "report_dir": str(report.output_dir),
                "message": "Teste visual concluído sem cliques, preenchimentos ou gravações.",
            }
            self._add_activity("test", "Smoke test visual concluído: PASS")
        except Exception as exc:
            report.add(
                StepResult(
                    1,
                    "visual-read-only-inspection",
                    "FAIL",
                    f"{type(exc).__name__}: {exc}",
                )
            )
            report.save()
            result = {
                "ok": False,
                "status": "FAIL",
                "url": None,
                "title": None,
                "interactive_elements": None,
                "visually_inspected": None,
                "report_dir": str(report.output_dir),
                "message": f"{type(exc).__name__}: {exc}",
            }
            self._add_activity("test", f"Smoke test visual falhou: {type(exc).__name__}")
        finally:
            with self.lock:
                self.last_test = result
                self.test_running = False

    def status_payload(self) -> dict[str, Any]:
        return {
            "version": DASHBOARD_VERSION,
            "providers": self.provider_status(),
            "session": self.session_status(),
            "last_test": self.last_test,
            "test_running": self.test_running,
            "activity": list(self.activity[-40:]),
            "pending_approval": dict(self.pending_approval) if self.pending_approval else None,
            "auto_approve_rules": self.approval_rules_payload(),
            "current_goal": self.current_goal,
            "current_plan": dict(self.current_plan) if self.current_plan else None,
            "agent_state": dict(self.agent_state),
            "steering_queue_size": len(self.steering_messages),
            "paid_fallback_enabled": self.settings.enable_paid_fallback,
            "safe_mode": not self.settings.allow_dangerous_actions,
        }


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    runtime = DashboardRuntime(resolved_settings)

    app = FastAPI(
        title="MySANA QA Agent",
        version=DASHBOARD_VERSION,
        docs_url=None,
        redoc_url=None,
    )

    app.state.runtime = runtime

    @app.middleware("http")
    async def disable_dashboard_cache(request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/") or request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status")
    def api_status() -> dict[str, Any]:
        return runtime.status_payload()

    @app.post("/api/providers/test")
    def api_test_providers() -> dict[str, Any]:
        return {"results": runtime.test_enabled_providers()}

    @app.post("/api/session/open")
    def api_open_session() -> dict[str, Any]:
        try:
            return runtime.open_session()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/session/login")
    def api_session_login(payload: LoginPayload) -> dict[str, Any]:
        try:
            return runtime.submit_login(payload.username, payload.password)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/approvals/{approval_id}/approve")
    def api_approve_action(approval_id: str) -> dict[str, Any]:
        try:
            return runtime.resolve_approval(approval_id, True)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/approvals/{approval_id}/approve-always")
    def api_approve_action_always(approval_id: str) -> dict[str, Any]:
        try:
            return runtime.resolve_approval(
                approval_id,
                True,
                remember_type=True,
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.delete("/api/approval-rules/{action_type}")
    def api_disable_approval_rule(action_type: str) -> dict[str, Any]:
        try:
            return runtime.disable_auto_approval(action_type)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/approvals/{approval_id}/reject")
    def api_reject_action(approval_id: str) -> dict[str, Any]:
        try:
            return runtime.resolve_approval(approval_id, False)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/agent/plan")
    def api_agent_plan(payload: AgentCommandPayload) -> dict[str, Any]:
        try:
            return runtime.plan_agent_command(payload.command)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/agent/run")
    def api_agent_run() -> dict[str, Any]:
        try:
            return runtime.execute_agent_plan()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/agent/steer")
    def api_agent_steer(payload: AgentSteerPayload) -> dict[str, Any]:
        try:
            return runtime.steer_agent(
                payload.message,
                reject_pending=payload.reject_pending,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/agent/stop")
    def api_agent_stop() -> dict[str, Any]:
        try:
            return runtime.stop_agent()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/session/close")
    def api_close_session() -> dict[str, Any]:
        try:
            return runtime.close_session()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/tests/start")
    def api_start_test() -> dict[str, Any]:
        try:
            return runtime.start_visual_test()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}") from exc

    return app
