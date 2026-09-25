from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from browser.session import BrowserSession
from core.provider_router import ProviderRouter
from core.settings import Settings
from qa.agent_runner import AgentRunner
from qa.workflow_runner import WorkflowRunner


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def cmd_doctor(settings: Settings) -> int:
    provider = ProviderRouter(settings)

    print("MySANA QA Agent doctor")
    print(f"Base URL: {settings.base_url}")
    print(f"Allowed hosts: {', '.join(settings.allowed_hosts)}")
    print(f"Headless: {settings.headless}")
    print(f"Paid fallback enabled: {settings.enable_paid_fallback}")
    print(f"LLM call budget: {settings.max_llm_calls_per_task}")
    print(f"Model chain: {' -> '.join(settings.llm_models)}")
    print(
        "NVIDIA NIM endpoint: "
        f"{os.getenv('NVIDIA_NIM_API_BASE', 'https://integrate.api.nvidia.com/v1')}"
    )

    for name, configured in provider.provider_status().items():
        print(f"{name}: {'configured' if configured else 'missing'}")

    print("Secrets: hidden (doctor never prints API keys)")
    return 0


def cmd_providers_test(settings: Settings) -> int:
    provider = ProviderRouter(settings)
    free_models = [model for model in settings.llm_models if not model.startswith("openai/")]

    print("Provider connectivity test")
    print("One minimal request is sent to each configured free provider.")

    failed = False
    for model in free_models:
        probe = provider.probe_model(model)
        if probe.ok:
            print(
                f"PASS | {probe.model} | {probe.latency_ms} ms | "
                f"response={probe.response!r}"
            )
        else:
            failed = True
            print(
                f"FAIL | {probe.model} | {probe.latency_ms} ms | "
                f"{probe.error}"
            )

    print("Secrets: hidden")
    return 1 if failed else 0


def cmd_dashboard(settings: Settings, port: int) -> int:
    import threading
    import webbrowser

    import uvicorn

    from dashboard.server import create_app

    url = f"http://127.0.0.1:{port}"
    print(f"MySANA QA Dashboard: {url}")
    print("O dashboard é servido apenas em localhost.")

    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(
        create_app(settings),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    return 0


def cmd_login(settings: Settings) -> int:
    browser = BrowserSession(settings)
    browser.start()
    try:
        browser.navigate(settings.base_url)
        print("Chrome opened with a persistent local QA profile.")
        print("Log in to MySANA manually. Do not paste credentials into the project or .env.")
        input("Press Enter here after the login is complete...")
        print(f"Current URL: {browser.current_url()}")
    finally:
        browser.close()
    return 0


def cmd_inspect(settings: Settings, url: str | None) -> int:
    browser = BrowserSession(settings)
    browser.start()
    try:
        browser.navigate(url or settings.base_url)
        input("Navigate to the target form in Chrome, then press Enter here to inspect it...")
        print(json.dumps(browser.snapshot_interactive(120), ensure_ascii=False, indent=2))
    finally:
        browser.close()
    return 0


def cmd_workflow(settings: Settings, workflow: Path) -> int:
    browser = BrowserSession(settings)
    browser.start()
    try:
        runner = WorkflowRunner(browser, settings.evidence_dir)
        report = runner.run_file(workflow)
        print(f"Report: {report.output_dir / 'report.md'}")
    finally:
        browser.close()
    return 0


def cmd_agent(settings: Settings, goal: str, url: str | None) -> int:
    browser = BrowserSession(settings)
    provider = ProviderRouter(settings)
    browser.start()
    try:
        browser.navigate(url or settings.base_url)
        input("Navigate/login if needed, then press Enter to let the constrained QA agent start...")
        runner = AgentRunner(browser, provider, settings)
        report = runner.run(goal)
        print(f"Report: {report.output_dir / 'report.md'}")
    finally:
        browser.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mysana-qa", description="Constrained QA browser agent for MySANA")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Validate local configuration and provider keys")
    sub.add_parser("providers-test", help="Test Groq and NVIDIA NIM with one minimal call each")

    dashboard = sub.add_parser("dashboard", help="Open the local visual dashboard")
    dashboard.add_argument("--port", type=int, default=8765)

    sub.add_parser("login", help="Open the persistent Chrome profile for manual login")

    inspect = sub.add_parser("inspect", help="Inspect visible interactive elements on the current page")
    inspect.add_argument("--url")

    workflow = sub.add_parser("workflow", help="Run a deterministic JSON QA workflow")
    workflow.add_argument("file", type=Path)

    agent = sub.add_parser("agent", help="Run the constrained agent for an exploratory QA goal")
    agent.add_argument("goal")
    agent.add_argument("--url")
    return parser


def main() -> int:
    configure_logging()
    settings = Settings.from_env()
    args = build_parser().parse_args()

    if args.command == "doctor":
        return cmd_doctor(settings)
    if args.command == "providers-test":
        return cmd_providers_test(settings)
    if args.command == "dashboard":
        return cmd_dashboard(settings, args.port)
    if args.command == "login":
        return cmd_login(settings)
    if args.command == "inspect":
        return cmd_inspect(settings, args.url)
    if args.command == "workflow":
        return cmd_workflow(settings, args.file)
    if args.command == "agent":
        return cmd_agent(settings, args.goal, args.url)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
