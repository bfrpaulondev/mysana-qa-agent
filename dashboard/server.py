from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from browser.session import BrowserSession
from core.provider_router import ProviderRouter
from core.settings import Settings
from qa.reporter import RunReport, StepResult


STATIC_DIR = Path(__file__).resolve().parent / "static"


class DashboardRuntime:
    """In-process state shared by the local dashboard API.

    The dashboard binds to localhost only. API keys remain in the local .env
    and are never included in API responses.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = ProviderRouter(settings)
        self.browser: BrowserSession | None = None
        self.lock = threading.RLock()
        self.last_provider_tests: list[dict[str, Any]] = []
        self.last_test: dict[str, Any] | None = None

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
            if self.browser is None or self.browser.driver is None:
                return {
                    "state": "closed",
                    "url": None,
                    "title": None,
                    "message": "Chrome QA ainda não foi aberto.",
                }

            try:
                return {
                    "state": "open",
                    "url": self.browser.current_url(),
                    "title": self.browser.page_title(),
                    "message": "Sessão Chrome QA aberta.",
                }
            except Exception as exc:
                return {
                    "state": "error",
                    "url": None,
                    "title": None,
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

            self.browser = BrowserSession(self.settings)
            self.browser.start()
            self.browser.navigate(self.settings.base_url)
            return self.session_status()

    def close_session(self) -> dict[str, Any]:
        with self.lock:
            if self.browser is not None:
                try:
                    self.browser.close()
                finally:
                    self.browser = None
            return self.session_status()

    def test_free_providers(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for model in self.settings.llm_models:
            if model.startswith("openai/"):
                continue

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

        self.last_provider_tests = results
        return results

    def run_read_only_test(self) -> dict[str, Any]:
        with self.lock:
            if self.browser is None or self.browser.driver is None:
                raise RuntimeError("Abre primeiro a sessão MySANA no dashboard.")

            report = RunReport("dashboard-readonly", self.settings.evidence_dir)
            try:
                url = self.browser.current_url()
                title = self.browser.page_title()
                elements = self.browser.snapshot_interactive(max_elements=150)
                screenshot = self.browser.screenshot(report.screenshot_path(1))

                report.add(
                    StepResult(
                        1,
                        "read-only-inspection",
                        "PASS",
                        f"Página lida sem alterações: {len(elements)} elementos interactivos.",
                        screenshot=screenshot,
                        data={
                            "url": url,
                            "title": title,
                            "interactive_elements": len(elements),
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
                    "report_dir": str(report.output_dir),
                    "message": "Smoke test read-only concluído sem clicar ou preencher campos.",
                }
                self.last_test = result
                return result
            except Exception as exc:
                report.add(
                    StepResult(
                        1,
                        "read-only-inspection",
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
                    "report_dir": str(report.output_dir),
                    "message": f"{type(exc).__name__}: {exc}",
                }
                self.last_test = result
                return result


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    runtime = DashboardRuntime(resolved_settings)

    app = FastAPI(
        title="MySANA QA Agent",
        version="0.2.0",
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
        return {
            "providers": runtime.provider_status(),
            "session": runtime.session_status(),
            "last_test": runtime.last_test,
            "paid_fallback_enabled": resolved_settings.enable_paid_fallback,
            "safe_mode": not resolved_settings.allow_dangerous_actions,
        }

    @app.post("/api/providers/test")
    def api_test_providers() -> dict[str, Any]:
        return {"results": runtime.test_free_providers()}

    @app.post("/api/session/open")
    def api_open_session() -> dict[str, Any]:
        try:
            return runtime.open_session()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/session/close")
    def api_close_session() -> dict[str, Any]:
        try:
            return runtime.close_session()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/tests/start")
    def api_start_test() -> dict[str, Any]:
        try:
            return runtime.run_read_only_test()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}") from exc

    return app
