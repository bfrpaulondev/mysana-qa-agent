from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from browser.session import BrowserSession
from qa.reporter import RunReport, StepResult


_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_RE.sub(lambda match: os.getenv(match.group(1), match.group(0)), value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


class WorkflowRunner:
    def __init__(self, browser: BrowserSession, report_root: Path):
        self.browser = browser
        self.report_root = report_root

    def run_file(self, workflow_path: Path) -> RunReport:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        workflow = _expand_env(workflow)
        name = str(workflow.get("name") or workflow_path.stem)
        steps = workflow.get("steps", [])
        if not isinstance(steps, list) or not steps:
            raise ValueError("Workflow must contain a non-empty 'steps' array")

        report = RunReport(name, self.report_root)
        for index, step in enumerate(steps, start=1):
            action = str(step.get("action", "unknown"))
            try:
                message = self.browser.execute_action(step)
                shot = self.browser.screenshot(report.screenshot_path(index))
                report.add(StepResult(index, action, "PASS", message, screenshot=shot, data=step))
            except Exception as exc:
                shot = None
                try:
                    shot = self.browser.screenshot(report.screenshot_path(index))
                except Exception:
                    pass
                report.add(
                    StepResult(index, action, "FAIL", f"{type(exc).__name__}: {exc}", screenshot=shot, data=step)
                )
                if not bool(step.get("continue_on_error", False)):
                    break

        report.save()
        return report
