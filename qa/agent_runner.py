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
    """Constrained visual browser agent with observable operational trace.

    This exposes concise observations/decisions, not the model's hidden chain of
    thought. The user can steer or stop the agent between actions.
    """

    def __init__(
        self,
        browser: BrowserSession,
        provider: ProviderRouter,
        settings: Settings,
        event_callback: Callable[[str, str], None] | None = None,
        state_callback: Callable[[dict[str, Any]], None] | None = None,
        control_callback: Callable[[], dict[str, Any]] | None = None,
    ):
        self.browser = browser
        self.provider = provider
        self.settings = settings
        self.event_callback = event_callback
        self.state_callback = state_callback
        self.control_callback = control_callback

    def plan(self, goal: str) -> dict[str, Any]:
        self.provider.reset_budget()
        snapshot = self.browser.snapshot_interactive(max_elements=90)
        current_url = self.browser.current_url()
        page_title = self.browser.page_title()
        compact_snapshot = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))

        self._state(
            phase="planning",
            step=0,
            detail="A analisar screenshot + elementos da página para gerar o plano.",
            provider=self.settings.vision_model,
        )

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
  "steps": ["high-level step 1", "high-level step 2"]
}}

Planning rules:
- This is a preview only. Do not claim an action already happened.
- Keep 2 to 6 concise, concrete steps.
- Base the plan on the current screenshot and DOM.
- Mention when a step will require user approval.
- Never include passwords or secrets.
- Never plan destructive actions such as deleting records, approving business workflows, payments, transfers or business rejections.
- Keep the response concise.
""".strip()

        try:
            completion = self.provider.vision_completion(
                prompt,
                self.browser.screenshot_base64(),
                max_tokens=500,
            )
        except AllProvidersFailed as exc:
            self._emit("vision", f"Vision indisponível no planeamento; fallback texto: {exc}")
            self._state(
                phase="planning",
                step=0,
                detail="Vision indisponível; a gerar o plano pelo DOM/texto.",
                provider="text fallback",
            )
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
                ],
                max_tokens=500,
            )

        data = self._parse_json_object(completion.content)
        steps = data.get("steps")
        if not isinstance(steps, list):
            raise ValueError("Planner response must contain a steps array")

        clean_steps = [str(step).strip() for step in steps if str(step).strip()]
        if not clean_steps:
            raise ValueError("Planner returned an empty plan")

        self._state(
            phase="plan-ready",
            step=0,
            detail="Plano pronto para revisão antes da execução.",
            provider=completion.model,
        )

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
            control = self._control()
            if control.get("stop_requested"):
                report.add(
                    StepResult(
                        index,
                        "stop",
                        "BLOCKED",
                        "Execution stopped by user.",
                    )
                )
                self._emit("stopped", "Execução parada pelo utilizador.")
                self._state(
                    phase="stopped",
                    step=index,
                    detail="Execução interrompida pelo utilizador.",
                    provider=None,
                )
                break

            steering = [
                str(item).strip()
                for item in control.get("steering", [])
                if str(item).strip()
            ]

            self._state(
                phase="observing",
                step=index,
                detail="A recolher screenshot e elementos interactivos da página actual.",
                provider=None,
            )
            snapshot = self.browser.snapshot_interactive(max_elements=90)
            self._emit("observe", f"Passo {index}: screenshot + {len(snapshot)} elementos")

            if steering:
                steer_text = " | ".join(steering)
                self._emit("steer", f"Instrução do utilizador aplicada: {steer_text[:260]}")

            self._state(
                phase="thinking",
                step=index,
                detail="A escolher a próxima acção segura com base no estado actual.",
                provider=self.settings.vision_model,
            )
            completion = self._plan_next_action(
                goal,
                snapshot,
                feedback,
                steering,
            )

            control_after_think = self._control()
            if control_after_think.get("stop_requested"):
                report.add(
                    StepResult(index, "stop", "BLOCKED", "Execution stopped by user.")
                )
                self._emit("stopped", "Paragem pedida durante a análise; nenhuma nova acção executada.")
                self._state(
                    phase="stopped",
                    step=index,
                    detail="Paragem aplicada após terminar a chamada ao modelo.",
                    provider=completion.model,
                )
                break

            extra_steering = [
                str(item).strip()
                for item in control_after_think.get("steering", [])
                if str(item).strip()
            ]
            if extra_steering:
                feedback = (
                    "The user corrected the plan after the last model call. "
                    "Do not execute the previous proposal. Re-observe and follow: "
                    + " | ".join(extra_steering)
                )
                self._emit(
                    "steer",
                    "Correcção chegou durante a análise; proposta descartada e será recalculada.",
                )
                continue

            action = self._parse_action(completion.content)
            action = self._resolve_element_index(action, snapshot)
            action_type = str(action.get("action", "unknown"))

            observation = str(action.get("observation", "")).strip()
            reason = str(action.get("reason", "")).strip()
            confidence = str(action.get("confidence", "")).strip().lower()

            trace = {
                "observation": observation[:240],
                "decision": self._decision_label(action),
                "reason": reason[:240],
                "confidence": confidence or None,
            }
            self._state(
                phase="decision",
                step=index,
                detail="Decisão proposta; a preparar o alvo para execução.",
                provider=completion.model,
                last_decision=trace,
            )

            if observation:
                self._emit("observe-summary", observation[:240])
            if reason:
                confidence_text = f" [{confidence}]" if confidence else ""
                self._emit("decision", f"{reason[:220]}{confidence_text}")
            else:
                self._emit("decision", f"{completion.model}: propôs {action_type}")

            if action_type == "done":
                summary = str(action.get("summary", "Goal completed"))
                report.add(
                    StepResult(index, "done", "PASS", summary, provider=completion.model, data=action)
                )
                self._emit("done", summary)
                self._state(
                    phase="done",
                    step=index,
                    detail=summary[:260],
                    provider=completion.model,
                    last_decision=trace,
                )
                break

            try:
                self._state(
                    phase="acting",
                    step=index,
                    detail=f"A preparar/executar: {self._decision_label(action)}",
                    provider=completion.model,
                    last_decision=trace,
                )
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
                self._state(
                    phase="verifying",
                    step=index,
                    detail="Acção executada; no próximo ciclo a página será observada novamente.",
                    provider=completion.model,
                    last_decision=trace,
                )
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
                self._state(
                    phase="recovering",
                    step=index,
                    detail=f"A acção falhou; vou reavaliar o ecrã. {message[:180]}",
                    provider=completion.model,
                    last_decision=trace,
                )
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
            self._state(
                phase="blocked",
                step=self.settings.max_agent_steps,
                detail="Máximo de passos atingido.",
                provider=None,
            )

        report.save()
        return report

    def _plan_next_action(
        self,
        goal: str,
        snapshot: list[dict[str, Any]],
        feedback: str,
        steering: list[str],
    ):
        prompt = self._visual_prompt(goal, snapshot, feedback, steering)
        try:
            image_base64 = self.browser.screenshot_base64()
            return self.provider.vision_completion(
                prompt,
                image_base64,
                max_tokens=320,
            )
        except AllProvidersFailed as exc:
            self._emit("vision", f"Vision indisponível; fallback DOM/texto: {exc}")
            self._state(
                phase="thinking",
                step=None,
                detail="Vision indisponível; a decidir pelo DOM/texto.",
                provider="text fallback",
            )
            return self.provider.completion(
                self._text_messages(goal, snapshot, feedback, steering),
                max_tokens=320,
            )

    def _visual_prompt(
        self,
        goal: str,
        snapshot: list[dict[str, Any]],
        feedback: str,
        steering: list[str],
    ) -> str:
        current_url = self.browser.current_url()
        page_title = self.browser.page_title()
        compact_snapshot = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        steering_text = "\n".join(f"- {item}" for item in steering) or "(none)"

        return f"""
You are controlling a visible Chromium browser for a QA task.

GOAL:
{goal}

LATEST USER CORRECTIONS:
{steering_text}

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

Return exactly one concise JSON object, no markdown:
{{"action":"click","element_index":12,"observation":"brief factual observation","reason":"brief operational rationale","confidence":"high"}}
{{"action":"fill","element_index":5,"value":"...","observation":"...","reason":"...","confidence":"medium"}}
{{"action":"select","element_index":8,"value":"...","by":"visible_text","observation":"...","reason":"...","confidence":"high"}}
{{"action":"wait","seconds":1,"observation":"...","reason":"...","confidence":"low"}}
{{"action":"assert_text","text":"...","observation":"...","reason":"...","confidence":"high"}}
{{"action":"done","summary":"...","observation":"...","reason":"...","confidence":"high"}}

The observation/reason fields are a SHORT operational trace for the user, not hidden chain-of-thought.
Rules:
- For click/fill/select, use ONLY an element_index present in the DOM list.
- Prioritize LATEST USER CORRECTIONS over the earlier plan.
- Use the screenshot for visual context and the DOM for the exact actionable element.
- Never invent a selector or element.
- If login fields are already filled and a visible Login/Entrar/Sign in button exists, clicking it is normally the next step.
- Re-evaluate after every action; do not follow the original plan blindly.
- Never delete/remove records, approve business workflows, make payments, transfer money, or reject business records.
- Keep observation/reason under 25 words each.
- Do not execute JavaScript, shell or Python.
- If the goal is complete, return done.
""".strip()

    def _text_messages(
        self,
        goal: str,
        snapshot: list[dict[str, Any]],
        feedback: str,
        steering: list[str],
    ) -> list[dict[str, str]]:
        current_url = self.browser.current_url()
        page_title = self.browser.page_title()
        compact_snapshot = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        steering_text = " | ".join(steering) or "none"
        system = """
You are a constrained QA browser agent. Return exactly one concise JSON object.
For click/fill/select use element_index from the supplied DOM list.
Include brief observation, reason and confidence fields for the user-visible operational trace.
Never invent elements. Never perform delete, approval, payment, transfer or rejection actions.
""".strip()
        user = f"""
Goal: {goal}
Latest user corrections: {steering_text}
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

    def _decision_label(self, action: dict[str, Any]) -> str:
        action_type = str(action.get("action", "unknown")).upper()
        element = action.get("element") or {}
        target = (
            element.get("text")
            or element.get("placeholder")
            or element.get("name")
            or action.get("selector")
            or action.get("text")
            or action.get("value")
            or ""
        )
        return f"{action_type}: {str(target)[:120]}" if target else action_type

    def _control(self) -> dict[str, Any]:
        if self.control_callback:
            return self.control_callback() or {}
        return {"stop_requested": False, "steering": []}

    def _state(
        self,
        *,
        phase: str,
        step: int | None,
        detail: str,
        provider: str | None,
        last_decision: dict[str, Any] | None = None,
    ) -> None:
        if self.state_callback:
            self.state_callback(
                {
                    "phase": phase,
                    "step": step,
                    "detail": detail,
                    "provider": provider,
                    "last_decision": last_decision,
                }
            )

    def _emit(self, action: str, message: str) -> None:
        if self.event_callback:
            self.event_callback(action, message)
