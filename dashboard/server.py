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
from qa.reporter import RunReport, StepResult


STATIC_DIR = Path(__file__).resolve().parent / "static"


class LoginPayload(BaseModel):
    username: str = Field(default="", max_length=512)
    password: str = Field(min_length=1, max_length=2048)


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
        self.last_provider_tests: list[dict[str, Any]] = []
        self.last_test: dict[str, Any] | None = None
        self.activity: list[dict[str, Any]] = []
        self.test_running = False
        self.login_required = False

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

    def request_approval(self, request: dict[str, Any]) -> bool:
        approval_id = uuid.uuid4().hex
        created_at = time.time()
        pending = {
            "id": approval_id,
            "action": str(request.get("action") or "action"),
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

    def resolve_approval(self, approval_id: str, approved: bool) -> dict[str, Any]:
        with self.approval_condition:
            pending = self.pending_approval
            if pending is None or pending.get("id") != approval_id:
                raise RuntimeError("Esta aprovação já não está pendente.")

            self.approval_results[approval_id] = approved
            self.pending_approval = None
            self.approval_condition.notify_all()

        return {
            "id": approval_id,
            "approved": approved,
        }

    def _reject_pending_approval(self) -> None:
        with self.approval_condition:
            pending = self.pending_approval
            if pending is None:
                return
            approval_id = str(pending["id"])
            self.approval_results[approval_id] = False
            self.pending_approval = None
            self.approval_condition.notify_all()

    def provider_status(self) -> list[dict[str, Any]]:
        configured = self.provider.provider_status()
        rows: list[dict[str, Any]] = []

        for model in self.settings.llm_models:
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
                enabled = is_configured and self.settings.enable_paid_fallback
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

        if self.login_required:
            self._add_activity("login", "O formulário de login continua visível")
        else:
            self._add_activity("login", "Login submetido e formulário de password deixou de estar visível")

        # username/password are intentionally not stored on runtime/state/reports.
        return {
            "submitted": True,
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

        if browser is not None:
            browser.close()

        self._add_activity("session", "Sessão Chromium fechada")
        return self.session_status()

    def test_free_providers(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for model in self.settings.llm_models:
            if model.startswith("openai/"):
                continue

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
            "providers": self.provider_status(),
            "session": self.session_status(),
            "last_test": self.last_test,
            "test_running": self.test_running,
            "activity": list(self.activity[-40:]),
            "pending_approval": dict(self.pending_approval) if self.pending_approval else None,
            "paid_fallback_enabled": self.settings.enable_paid_fallback,
            "safe_mode": not self.settings.allow_dangerous_actions,
        }


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    runtime = DashboardRuntime(resolved_settings)

    app = FastAPI(
        title="MySANA QA Agent",
        version="0.4.0",
        docs_url=None,
        redoc_url=None,
    )

    app.state.runtime = runtime
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status")
    def api_status() -> dict[str, Any]:
        return runtime.status_payload()

    @app.post("/api/providers/test")
    def api_test_providers() -> dict[str, Any]:
        return {"results": runtime.test_free_providers()}

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

    @app.post("/api/approvals/{approval_id}/reject")
    def api_reject_action(approval_id: str) -> dict[str, Any]:
        try:
            return runtime.resolve_approval(approval_id, False)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}") from exc

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
