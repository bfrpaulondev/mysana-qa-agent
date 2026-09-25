from __future__ import annotations

import json
import re
from typing import Any

from browser.session import BrowserSession
from core.provider_router import ProviderRouter
from core.settings import Settings
from qa.reporter import RunReport, StepResult


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


class AgentRunner:
    """Constrained browser agent for exploratory QA.

    The LLM can only choose from a small action vocabulary. It cannot execute
    arbitrary JavaScript, shell commands or Python in the browser workflow.
    """

    def __init__(self, browser: BrowserSession, provider: ProviderRouter, settings: Settings):
        self.browser = browser
        self.provider = provider
        self.settings = settings

    def run(self, goal: str) -> RunReport:
        self.provider.reset_budget()
        report = RunReport("agent-qa", self.settings.evidence_dir)
        feedback = "No previous action."

        for index in range(1, self.settings.max_agent_steps + 1):
            snapshot = self.browser.snapshot_interactive(max_elements=90)
            messages = self._messages(goal, snapshot, feedback)
            completion = self.provider.completion(messages)
            action = self._parse_action(completion.content)
            action_type = str(action.get("action", "unknown"))

            if action_type == "done":
                summary = str(action.get("summary", "Goal completed"))
                report.add(
                    StepResult(index, "done", "PASS", summary, provider=completion.model, data=action)
                )
                break

            try:
                message = self.browser.execute_action(action)
                shot = self.browser.screenshot(report.screenshot_path(index))
                report.add(
                    StepResult(
                        index,
                        action_type,
                        "PASS",
                        message,
                        screenshot=shot,
                        provider=completion.model,
                        data=action,
                    )
                )
                feedback = f"Previous action succeeded: {message}"
            except Exception as exc:
                shot = None
                try:
                    shot = self.browser.screenshot(report.screenshot_path(index))
                except Exception:
                    pass
                message = f"{type(exc).__name__}: {exc}"
                report.add(
                    StepResult(
                        index,
                        action_type,
                        "FAIL",
                        message,
                        screenshot=shot,
                        provider=completion.model,
                        data=action,
                    )
                )
                feedback = f"Previous action failed: {message}. Choose a safe recovery action."
        else:
            report.add(
                StepResult(
                    self.settings.max_agent_steps + 1,
                    "budget",
                    "BLOCKED",
                    "Maximum agent steps reached before the goal was completed.",
                )
            )

        report.save()
        return report

    def _messages(self, goal: str, snapshot: list[dict[str, Any]], feedback: str) -> list[dict[str, str]]:
        current_url = self.browser.current_url()
        page_title = self.browser.page_title()
        compact_snapshot = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        system = """
You are a constrained QA browser agent inspired by AgenticSeek's specialized BrowserAgent.
Your job is to test a web workflow, not to perform business decisions.

Return exactly one JSON object and nothing else.
Allowed actions:
- {"action":"click","selector":"CSS_OR_xpath=...","reason":"..."}
- {"action":"fill","selector":"...","value":"...","reason":"..."}
- {"action":"select","selector":"...","value":"...","by":"visible_text|value","reason":"..."}
- {"action":"wait","seconds":1,"reason":"..."}
- {"action":"assert_text","text":"...","reason":"..."}
- {"action":"done","summary":"..."}

Rules:
1. Use only selectors present in the supplied interactive element snapshot.
2. Never output JavaScript, Python, shell commands or credentials.
3. Never click delete, remove, approve, payment, transfer or rejection actions.
4. Prefer deterministic actions and short values suitable for QA.
5. If the goal is already complete, return done.
6. Do not invent page elements.
""".strip()
        user = f"""
Goal: {goal}
Current URL: {current_url}
Page title: {page_title}
Feedback: {feedback}
Interactive elements JSON: {compact_snapshot}
""".strip()
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _parse_action(self, content: str) -> dict[str, Any]:
        text = content.strip()
        match = _JSON_BLOCK.search(text)
        if match:
            text = match.group(1)
        try:
            action = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM returned invalid action JSON: {content[:500]!r}") from exc
        if not isinstance(action, dict):
            raise ValueError("LLM action must be a JSON object")
        return action
