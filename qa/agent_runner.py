from __future__ import annotations

import json
import re
from typing import Any, Callable

from browser.session import BrowserSession
from core.provider_router import AllProvidersFailed, ProviderRouter
from core.settings import Settings
from qa.reporter import RunReport, StepResult


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


class AgentRunner:
    """Constrained visual browser agent.

    Each cycle observes the screenshot + compact DOM, proposes one safe action,
    executes it through BrowserSession (which enforces approval), then observes
    again. If vision is unavailable it falls back to the text DOM planner.
    """

    def __init__(
        self,
        browser: BrowserSession,
        provider: ProviderRouter,
        settings: Settings,
        event_callback: Callable[[str, str], None] | None = None,
    ):
        self.browser = browser
        self.provider = provider
        self.settings = settings
        self.event_callback = event_callback

    def plan(self, goal: str) -> dict[str, Any]:
        self.provider.reset_budget()
        snapshot = self.browser.snapshot_interactive(max_elements=110)
        current_url = self.browser.current_url()
        page_title = self.browser.page_title()
        compact_snapshot = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))

        prompt = f"""
You are planning a visible browser QA task before execution.

USER COMMAND:
{goal}

CURRENT PAGE:
URL: {current_url}
Title: {page_title}

You have the current screenshot attached and this compact DOM:
{compact_snapshot}

Return exactly one JSON object:
{{
  "summary": "short explanation of what you intend to do",
  "steps": [
    "high-level step 1",
    "high-level step 2"
  ]
}}

Planning rules:
- This is a preview only. Do not claim an action already happened.
- Keep 2 to 6 concise, concrete steps.
- Base the plan on the current screenshot and DOM.
- Mention when a step will require user approval.
- Never include passwords or secrets in the plan.
- Never plan destructive actions such as deleting records, approving business workflows, payments, transfers or business rejections.
- The execution agent will re-observe after every action, so mark the plan as provisional if the page may change.
""".strip()

        try:
            completion = self.provider.vision_completion(
                prompt,
                self.browser.screenshot_base64(),
            )
        except AllProvidersFailed as exc:
            self._emit("vision", f"Vision indisponível no planeamento; fallback texto: {exc}")
            completion = self.provider.completion(
                [
                    {
                        "role": "system",
                        "content": (
                            "Create a short safe browser QA plan. Return JSON only with "
                            "summary and steps. Do not include secrets or destructive actions."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Command: {goal}\nURL: {current_url}\nTitle: {page_title}\n"
                            f"DOM: {compact_snapshot}"
                        ),
                    },
                ]
            )

        data = self._parse_json_object(completion.content)
        steps = data.get("steps")
        if not isinstance(steps, list):
            raise ValueError("Planner response must contain a steps array")

        clean_steps = [str(step).strip() for step in steps if str(step).strip()]
        if not clean_steps:
            raise ValueError("Planner returned an empty plan")

        return {
            "summary": str(data.get("summary") or "Plano proposto pelo agente."),
            "steps": clean_steps[:6],
            "model": completion.model,
            "url": current_url,
            "title": page_title,
        }

    def run(self, goal: str, report_name: str = "agent-qa") -> RunReport:
        self.provider.reset_budget()
        report = RunReport(report_name, self.settings.evidence_dir)
        feedback = "No previous action."

        for index in range(1, self.settings.max_agent_steps + 1):
            snapshot = self.browser.snapshot_interactive(max_elements=110)
            self._emit("observe", f"Passo {index}: screenshot + {len(snapshot)} elementos")

            completion = self._plan_next_action(goal, snapshot, feedback)
            action = self._parse_action(completion.content)
            action = self._resolve_element_index(action, snapshot)
            action_type = str(action.get("action", "unknown"))

            reason = str(action.get("reason", "")).strip()
            if reason:
                self._emit("think", f"{completion.model}: {reason[:220]}")
            else:
                self._emit("think", f"{completion.model}: propôs {action_type}")

            if action_type == "done":
                summary = str(action.get("summary", "Goal completed"))
                report.add(
                    StepResult(index, "done", "PASS", summary, provider=completion.model, data=action)
                )
                self._emit("done", summary)
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
                self._emit("result", feedback)
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
                feedback = (
                    f"Previous action failed: {message}. Observe the NEW screenshot and DOM, "
                    "then choose a safe recovery action. Do not repeat a selector blindly."
                )
                self._emit("recover", feedback[:260])
        else:
            report.add(
                StepResult(
                    self.settings.max_agent_steps + 1,
                    "budget",
                    "BLOCKED",
                    "Maximum agent steps reached before the goal was completed.",
                )
            )
            self._emit("blocked", "Máximo de passos atingido.")

        report.save()
        return report

    def _plan_next_action(
        self,
        goal: str,
        snapshot: list[dict[str, Any]],
        feedback: str,
    ):
        prompt = self._visual_prompt(goal, snapshot, feedback)
        try:
            image_base64 = self.browser.screenshot_base64()
            return self.provider.vision_completion(prompt, image_base64)
        except AllProvidersFailed as exc:
            self._emit("vision", f"Vision indisponível; fallback DOM/texto: {exc}")
            return self.provider.completion(self._text_messages(goal, snapshot, feedback))

    def _visual_prompt(
        self,
        goal: str,
        snapshot: list[dict[str, Any]],
        feedback: str,
    ) -> str:
        current_url = self.browser.current_url()
        page_title = self.browser.page_title()
        compact_snapshot = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))

        return f"""
You are controlling a visible Chromium browser for a QA task.

GOAL:
{goal}

CURRENT PAGE:
URL: {current_url}
Title: {page_title}

PREVIOUS RESULT:
{feedback}

You have TWO observations:
1. The screenshot attached to this request.
2. The exact interactive DOM elements below.

INTERACTIVE ELEMENTS JSON:
{compact_snapshot}

Choose exactly ONE next action.

Return exactly one JSON object, no markdown:
{{"action":"click","element_index":12,"reason":"..."}}
{{"action":"fill","element_index":5,"value":"...","reason":"..."}}
{{"action":"select","element_index":8,"value":"...","by":"visible_text","reason":"..."}}
{{"action":"wait","seconds":1,"reason":"..."}}
{{"action":"assert_text","text":"...","reason":"..."}}
{{"action":"done","summary":"...","reason":"..."}}

Rules:
- For click/fill/select, use ONLY an element_index present in the DOM list.
- Use the screenshot to understand visual context, labels, dialogs and the logical next step.
- Use the DOM list to identify the exact actionable element.
- Never invent a selector or element.
- If login fields are already filled and a visible Login/Entrar/Sign in button exists, clicking it is normally the next step.
- After every action you will receive a fresh screenshot, so re-evaluate rather than repeating blindly.
- Never delete/remove records, approve business workflows, make payments, transfer money, or reject business records.
- Do not execute JavaScript, shell or Python.
- If the goal is complete, return done.
""".strip()

    def _text_messages(
        self,
        goal: str,
        snapshot: list[dict[str, Any]],
        feedback: str,
    ) -> list[dict[str, str]]:
        current_url = self.browser.current_url()
        page_title = self.browser.page_title()
        compact_snapshot = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        system = """
You are a constrained QA browser agent. Return exactly one JSON object.
For click/fill/select use element_index from the supplied DOM list.
Never invent elements. Never perform delete, approval, payment, transfer or rejection actions.
""".strip()
        user = f"""
Goal: {goal}
Current URL: {current_url}
Page title: {page_title}
Feedback: {feedback}
Interactive elements JSON: {compact_snapshot}
""".strip()
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _resolve_element_index(
        self,
        action: dict[str, Any],
        snapshot: list[dict[str, Any]],
    ) -> dict[str, Any]:
        action_type = str(action.get("action", "")).lower()
        if action_type not in {"click", "fill", "select"}:
            return action

        if action.get("selector"):
            return action

        if "element_index" not in action:
            raise ValueError(f"Action '{action_type}' requires element_index")

        try:
            wanted = int(action["element_index"])
        except (TypeError, ValueError) as exc:
            raise ValueError("element_index must be an integer") from exc

        element = next((item for item in snapshot if int(item.get("index", -1)) == wanted), None)
        if element is None:
            raise ValueError(f"element_index {wanted} is not present in current DOM snapshot")

        selector = str(element.get("selector") or "").strip()
        if not selector:
            raise ValueError(f"element_index {wanted} has no usable selector")

        resolved = dict(action)
        resolved["selector"] = selector
        resolved["element"] = {
            "index": wanted,
            "tag": element.get("tag"),
            "text": element.get("text"),
            "name": element.get("name"),
            "placeholder": element.get("placeholder"),
        }
        return resolved

    def _parse_json_object(self, content: str) -> dict[str, Any]:
        text = content.strip()
        match = _JSON_BLOCK.search(text)
        if match:
            text = match.group(1)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM returned invalid JSON: {content[:500]!r}") from exc
        if not isinstance(data, dict):
            raise ValueError("LLM response must be a JSON object")
        return data

    def _parse_action(self, content: str) -> dict[str, Any]:
        return self._parse_json_object(content)

    def _emit(self, action: str, message: str) -> None:
        if self.event_callback:
            self.event_callback(action, message)
