from __future__ import annotations

import base64
import json
import os
import shutil
import struct
import time
from pathlib import Path
from typing import Any, Callable

from browser.policy import BrowserPolicy
from core.settings import Settings


class ActionRejected(RuntimeError):
    pass


_VISUAL_OVERLAY_JS = r"""
(() => {
  const ROOT_ID = "__mysana_qa_visual_root";
  const CURSOR_ID = "__mysana_qa_cursor";
  const LABEL_ID = "__mysana_qa_label";

  const ensure = () => {
    if (document.getElementById(ROOT_ID)) return;

    const root = document.createElement("div");
    root.id = ROOT_ID;
    root.style.cssText = [
      "position:fixed",
      "inset:0",
      "pointer-events:none",
      "z-index:2147483647",
      "font-family:Segoe UI,Arial,sans-serif"
    ].join(";");

    const cursor = document.createElement("div");
    cursor.id = CURSOR_ID;
    cursor.style.cssText = [
      "position:fixed",
      "left:24px",
      "top:80px",
      "width:22px",
      "height:22px",
      "margin-left:-11px",
      "margin-top:-11px",
      "border:3px solid #7CFFB2",
      "border-radius:50%",
      "background:rgba(124,255,178,.18)",
      "box-shadow:0 0 0 7px rgba(124,255,178,.12),0 0 24px rgba(124,255,178,.8)",
      "transition:left .42s cubic-bezier(.22,.8,.25,1),top .42s cubic-bezier(.22,.8,.25,1),transform .16s ease",
      "pointer-events:none"
    ].join(";");

    const label = document.createElement("div");
    label.id = LABEL_ID;
    label.style.cssText = [
      "position:fixed",
      "top:18px",
      "left:50%",
      "transform:translateX(-50%)",
      "max-width:min(760px,calc(100vw - 40px))",
      "padding:10px 16px",
      "border:1px solid rgba(124,255,178,.72)",
      "border-radius:999px",
      "background:rgba(9,15,18,.94)",
      "color:#EFFFF6",
      "font-size:13px",
      "font-weight:800",
      "letter-spacing:.04em",
      "box-shadow:0 10px 34px rgba(0,0,0,.35),0 0 0 3px rgba(124,255,178,.08)",
      "opacity:0",
      "transition:opacity .15s ease",
      "white-space:nowrap",
      "overflow:hidden",
      "text-overflow:ellipsis"
    ].join(";");

    root.appendChild(cursor);
    root.appendChild(label);
    (document.body || document.documentElement).appendChild(root);

    window.__mysanaQAVisual = {
      current: null,
      ensure,
      clear() {
        const current = this.current;
        if (current) {
          current.style.outline = current.dataset.qaOldOutline || "";
          current.style.boxShadow = current.dataset.qaOldBoxShadow || "";
          current.style.outlineOffset = current.dataset.qaOldOutlineOffset || "";
          delete current.dataset.qaOldOutline;
          delete current.dataset.qaOldBoxShadow;
          delete current.dataset.qaOldOutlineOffset;
        }
        this.current = null;
      },
      status(text, color = "#7CFFB2") {
        ensure();
        const labelNode = document.getElementById(LABEL_ID);
        labelNode.textContent = text;
        labelNode.style.borderColor = color;
        labelNode.style.opacity = "1";
      },
      focus(element, text, color = "#7CFFB2") {
        ensure();
        this.clear();
        if (!element) {
          this.status(text, color);
          return;
        }

        element.scrollIntoView({behavior:"smooth", block:"center", inline:"center"});
        const rect = element.getBoundingClientRect();
        const x = Math.max(14, Math.min(window.innerWidth - 14, rect.left + rect.width / 2));
        const y = Math.max(50, Math.min(window.innerHeight - 14, rect.top + rect.height / 2));

        const cursor = document.getElementById(CURSOR_ID);
        cursor.style.left = x + "px";
        cursor.style.top = y + "px";
        cursor.style.borderColor = color;
        cursor.style.boxShadow = "0 0 0 7px " + color + "22,0 0 26px " + color;

        element.dataset.qaOldOutline = element.style.outline || "";
        element.dataset.qaOldBoxShadow = element.style.boxShadow || "";
        element.dataset.qaOldOutlineOffset = element.style.outlineOffset || "";
        element.style.outline = "4px solid " + color;
        element.style.outlineOffset = "4px";
        element.style.boxShadow = "0 0 0 9px " + color + "22,0 0 30px " + color + "77";
        this.current = element;
        this.status(text, color);
      },
      point(x, y, text, color = "#7CFFB2") {
        ensure();
        this.clear();
        const cursor = document.getElementById(CURSOR_ID);
        const safeX = Math.max(14, Math.min(window.innerWidth - 14, x));
        const safeY = Math.max(50, Math.min(window.innerHeight - 14, y));
        cursor.style.left = safeX + "px";
        cursor.style.top = safeY + "px";
        cursor.style.borderColor = color;
        cursor.style.boxShadow = "0 0 0 7px " + color + "22,0 0 26px " + color;
        const element = document.elementFromPoint(safeX, safeY);
        if (element && element.id !== ROOT_ID && !element.closest("#" + ROOT_ID)) {
          element.dataset.qaOldOutline = element.style.outline || "";
          element.dataset.qaOldBoxShadow = element.style.boxShadow || "";
          element.dataset.qaOldOutlineOffset = element.style.outlineOffset || "";
          element.style.outline = "4px solid " + color;
          element.style.outlineOffset = "4px";
          element.style.boxShadow = "0 0 0 9px " + color + "22,0 0 30px " + color + "77";
          this.current = element;
        }
        this.status(text, color);
      },
      pulse() {
        ensure();
        const cursor = document.getElementById(CURSOR_ID);
        cursor.style.transform = "scale(.66)";
        setTimeout(() => { cursor.style.transform = "scale(1)"; }, 170);
      }
    };
  };

  ensure();
  return true;
})();
"""


class BrowserSession:
    def __init__(
        self,
        settings: Settings,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        approval_callback: Callable[[dict[str, Any]], bool] | None = None,
    ):
        self.settings = settings
        self.policy = BrowserPolicy(
            allowed_hosts=settings.allowed_hosts,
            allow_dangerous_actions=settings.allow_dangerous_actions,
        )
        self.driver = None
        self.event_callback = event_callback
        self.approval_callback = approval_callback
        self.browser_label = "Chromium"
        self._event_id = 0

    def start(self) -> None:
        try:
            from selenium import webdriver
            from selenium.common.exceptions import SessionNotCreatedException, WebDriverException
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Selenium is not installed. Run: pip install -r requirements.txt") from exc

        binary, label = self._resolve_chromium_binary()
        self.browser_label = label if binary else "Chrome (Chromium)"

        errors: list[str] = []
        for profile_dir in self._profile_candidates():
            profile_dir.mkdir(parents=True, exist_ok=True)
            options = self._build_chrome_options(webdriver, profile_dir, binary)

            self._emit(
                "browser",
                f"A abrir {self.browser_label} com perfil QA: {profile_dir}",
            )
            try:
                self.driver = webdriver.Chrome(options=options)
                self.driver.set_page_load_timeout(60)
                self._remember_working_profile(profile_dir)
                self._emit(
                    "browser",
                    f"{self.browser_label} aberto e controlado pelo agente",
                )
                return
            except (SessionNotCreatedException, WebDriverException) as exc:
                message = f"{type(exc).__name__}: {exc}"
                errors.append(message)
                self.driver = None
                self._emit(
                    "browser",
                    f"Falha ao abrir perfil {profile_dir.name}; a tentar perfil de recuperação",
                )

                text = str(exc).lower()
                recoverable = (
                    "devtoolsactiveport" in text
                    or "chrome failed to start" in text
                    or "session not created" in text
                    or "user data directory is already in use" in text
                )
                if not recoverable:
                    break

        detail = " | ".join(errors[-3:])
        raise RuntimeError(
            "Não foi possível iniciar o Chromium controlado. "
            "O agente tentou o perfil principal e perfis de recuperação. "
            "Fecha apenas janelas antigas do MySANA QA Agent, reinicia o dashboard e tenta novamente. "
            f"Detalhe: {detail}"
        )

    def close(self) -> None:
        if self.driver is not None:
            self._emit("browser", "A fechar o Chromium controlado")
            self.driver.quit()
            self.driver = None

    def navigate(self, url: str) -> None:
        self._require_driver()
        self.policy.ensure_url_allowed(url)
        self._emit("navigate", f"A navegar para {url}")
        self.driver.get(url)
        self._ensure_current_url_allowed()
        self._install_visual_overlay()
        self._visual_status("NAVEGAÇÃO CONCLUÍDA", "#7CFFB2")
        self._emit("navigate", f"Página carregada: {self.page_title() or url}")

    def current_url(self) -> str:
        self._require_driver()
        return self.driver.current_url

    def page_title(self) -> str:
        self._require_driver()
        return self.driver.title

    def screenshot(self, path: Path) -> str:
        self._require_driver()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.driver.save_screenshot(str(path))
        return str(path)

    def screenshot_base64(self) -> str:
        self._require_driver()
        return self.driver.get_screenshot_as_base64()

    def computer_observation(self) -> dict[str, Any]:
        """Capture the exact viewport image used by native Computer Use."""
        self._require_driver()
        image_base64 = self.driver.get_screenshot_as_base64()
        raw = base64.b64decode(image_base64)
        if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("Unexpected screenshot format; PNG was expected.")

        image_width, image_height = struct.unpack(">II", raw[16:24])
        viewport = self.driver.execute_script(
            "return {width: window.innerWidth, height: window.innerHeight, "
            "devicePixelRatio: window.devicePixelRatio || 1};"
        ) or {}

        return {
            "base64": image_base64,
            "image_width": int(image_width),
            "image_height": int(image_height),
            "viewport_width": int(viewport.get("width") or image_width),
            "viewport_height": int(viewport.get("height") or image_height),
            "device_pixel_ratio": float(viewport.get("devicePixelRatio") or 1),
        }

    @staticmethod
    def scale_computer_point(
        x: float,
        y: float,
        observation: dict[str, Any],
    ) -> tuple[float, float]:
        image_width = max(float(observation.get("image_width") or 1), 1)
        image_height = max(float(observation.get("image_height") or 1), 1)
        viewport_width = max(float(observation.get("viewport_width") or image_width), 1)
        viewport_height = max(float(observation.get("viewport_height") or image_height), 1)

        return (
            max(0.0, min(viewport_width - 1, float(x) * viewport_width / image_width)),
            max(0.0, min(viewport_height - 1, float(y) * viewport_height / image_height)),
        )

    def execute_computer_action(
        self,
        action: dict[str, Any],
        observation: dict[str, Any],
    ) -> str:
        """Execute one native OpenAI computer action against the visible Chromium."""
        self._require_driver()
        action_type = str(action.get("type") or "").strip().lower()

        if action_type == "screenshot":
            self._emit("computer", "Computer Use pediu uma nova screenshot")
            return "Screenshot requested"

        if action_type == "wait":
            self._visual_status("COMPUTER USE — AGUARDAR", "#FFD66B")
            self._emit("computer", "Computer Use está a aguardar a interface")
            time.sleep(2)
            self._ensure_current_url_allowed()
            return "Waited 2 seconds"

        if action_type in {"click", "double_click", "move", "scroll"}:
            x, y = self.scale_computer_point(
                float(action.get("x") or 0),
                float(action.get("y") or 0),
                observation,
            )
            target = self._describe_point(x, y)
            label = f"{action_type.upper()} — {target[:90]}"
            self._visual_point(x, y, label, "#7CFFB2")

            if action_type in {"click", "double_click"}:
                self.policy.ensure_click_allowed(target)
                self._require_approval(
                    action="click",
                    label=label,
                    target=target,
                    value_preview=None,
                    secret=False,
                )
                self._computer_click(
                    x,
                    y,
                    button=str(action.get("button") or "left"),
                    double=action_type == "double_click",
                )
                self._visual_pulse()
                self._emit("computer", label)
                time.sleep(0.35)
                self._ensure_current_url_allowed()
                self._install_visual_overlay()
                return f"{action_type} at ({x:.0f}, {y:.0f}) on {target}"

            if action_type == "move":
                self._dispatch_mouse("mouseMoved", x, y, button="none")
                self._emit("computer", label)
                return f"Moved pointer to ({x:.0f}, {y:.0f})"

            scroll_x = float(action.get("scroll_x") or 0)
            scroll_y = float(action.get("scroll_y") or 0)
            self.driver.execute_cdp_cmd(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseWheel",
                    "x": x,
                    "y": y,
                    "deltaX": scroll_x,
                    "deltaY": scroll_y,
                },
            )
            self._emit("computer", f"SCROLL — dx={scroll_x:.0f}, dy={scroll_y:.0f}")
            time.sleep(0.25)
            return f"Scrolled ({scroll_x:.0f}, {scroll_y:.0f})"

        if action_type == "drag":
            path = action.get("path") or []
            if len(path) < 2:
                raise ValueError("Computer drag requires at least two path points.")

            scaled: list[tuple[float, float]] = []
            for point in path:
                if isinstance(point, dict):
                    px, py = point.get("x"), point.get("y")
                elif isinstance(point, (list, tuple)) and len(point) >= 2:
                    px, py = point[0], point[1]
                else:
                    raise ValueError("Invalid Computer Use drag path point.")
                scaled.append(self.scale_computer_point(float(px), float(py), observation))

            start_x, start_y = scaled[0]
            target = self._describe_point(start_x, start_y)
            self._visual_point(start_x, start_y, f"DRAG — {target[:90]}", "#7CFFB2")
            self.policy.ensure_click_allowed(target)
            self._require_approval(
                action="click",
                label=f"ARRASTAR — {target[:90]}",
                target=target,
                value_preview=None,
                secret=False,
            )
            self._dispatch_mouse("mouseMoved", start_x, start_y, button="none")
            self._dispatch_mouse("mousePressed", start_x, start_y, button="left", click_count=1)
            for px, py in scaled[1:]:
                self._dispatch_mouse("mouseMoved", px, py, button="left")
            end_x, end_y = scaled[-1]
            self._dispatch_mouse("mouseReleased", end_x, end_y, button="left", click_count=1)
            self._emit("computer", f"DRAG — {target[:90]}")
            return f"Dragged from ({start_x:.0f}, {start_y:.0f}) to ({end_x:.0f}, {end_y:.0f})"

        if action_type == "type":
            text = str(action.get("text") or "")
            active = self._describe_active_element()
            secret = bool(active.get("secret"))
            target = str(active.get("label") or "elemento focado")
            self._visual_status(f"COMPUTER USE — ESCREVER EM {target[:60]}", "#77B9FF")
            self._require_approval(
                action="write",
                label=f"ESCREVER — {target[:80]}",
                target=target,
                value_preview=None if secret else text[:120],
                secret=secret,
                value_length=len(text),
            )

            element = self.driver.switch_to.active_element
            for character in text:
                element.send_keys(character)
                time.sleep(min(self.settings.visual_typing_delay_ms, 25) / 1000)
            self._visual_pulse()
            self._emit("computer", "TYPE — valor secreto" if secret else f"TYPE — {text[:80]}")
            return f"Typed {len(text)} characters"

        if action_type == "keypress":
            keys = [str(key) for key in (action.get("keys") or [])]
            if not keys:
                raise ValueError("Computer keypress requires keys.")
            self._visual_status(
                "COMPUTER USE — TECLAS " + "+".join(keys)[:70],
                "#77B9FF",
            )
            self._require_approval(
                action="keypress",
                label="TECLAS — " + "+".join(keys)[:80],
                target=str(self._describe_active_element().get("label") or "elemento focado"),
                value_preview="+".join(keys)[:120],
                secret=False,
            )
            self._computer_keypress(keys)
            self._emit("computer", "KEYPRESS — " + "+".join(keys)[:80])
            time.sleep(0.2)
            return "Pressed " + "+".join(keys)

        raise ValueError(f"Unsupported Computer Use action: {action_type}")

    def _describe_point(self, x: float, y: float) -> str:
        result = self.driver.execute_script(
            """
            const el = document.elementFromPoint(arguments[0], arguments[1]);
            if (!el) return {label:"sem elemento"};
            const text = (el.innerText || el.value || el.getAttribute("aria-label") ||
              el.getAttribute("title") || el.getAttribute("placeholder") || "").trim();
            return {
              tag: (el.tagName || "").toLowerCase(),
              role: el.getAttribute("role") || "",
              type: el.getAttribute("type") || "",
              label: text.slice(0, 160)
            };
            """,
            x,
            y,
        ) or {}
        parts = [str(result.get("tag") or "elemento")]
        if result.get("role"):
            parts.append(f"role={result['role']}")
        if result.get("label"):
            parts.append(str(result["label"]))
        return " | ".join(parts)[:220]

    def _describe_active_element(self) -> dict[str, Any]:
        return self.driver.execute_script(
            """
            const el = document.activeElement;
            if (!el) return {label:"elemento focado", secret:false};
            const type = (el.getAttribute("type") || "").toLowerCase();
            const label = (
              el.getAttribute("aria-label") ||
              el.getAttribute("placeholder") ||
              el.getAttribute("name") ||
              el.id ||
              el.tagName ||
              "elemento focado"
            );
            return {label:String(label).slice(0,160), secret:type === "password"};
            """
        ) or {"label": "elemento focado", "secret": False}

    def _visual_point(self, x: float, y: float, label: str, color: str) -> None:
        try:
            self._install_visual_overlay()
            self.driver.execute_script(
                "window.__mysanaQAVisual.point(arguments[0], arguments[1], arguments[2], arguments[3]);",
                x,
                y,
                label,
                color,
            )
            time.sleep(self.settings.visual_action_delay_ms / 1000)
        except Exception:
            pass

    def _dispatch_mouse(
        self,
        event_type: str,
        x: float,
        y: float,
        *,
        button: str,
        click_count: int = 0,
    ) -> None:
        button_map = {"left": "left", "right": "right", "wheel": "middle", "middle": "middle", "none": "none"}
        normalized = button_map.get(button.lower())
        if normalized is None:
            raise ValueError(f"Unsupported mouse button: {button}")
        payload = {
            "type": event_type,
            "x": x,
            "y": y,
            "button": normalized,
        }
        if click_count:
            payload["clickCount"] = click_count
        self.driver.execute_cdp_cmd("Input.dispatchMouseEvent", payload)

    def _computer_click(
        self,
        x: float,
        y: float,
        *,
        button: str = "left",
        double: bool = False,
    ) -> None:
        count = 2 if double else 1
        self._dispatch_mouse("mouseMoved", x, y, button="none")
        self._dispatch_mouse("mousePressed", x, y, button=button, click_count=count)
        self._dispatch_mouse("mouseReleased", x, y, button=button, click_count=count)

    def _computer_keypress(self, keys: list[str]) -> None:
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys

        key_map = {
            "ENTER": Keys.ENTER,
            "RETURN": Keys.ENTER,
            "ESC": Keys.ESCAPE,
            "ESCAPE": Keys.ESCAPE,
            "TAB": Keys.TAB,
            "SPACE": Keys.SPACE,
            "BACKSPACE": Keys.BACKSPACE,
            "DELETE": Keys.DELETE,
            "DEL": Keys.DELETE,
            "HOME": Keys.HOME,
            "END": Keys.END,
            "PAGEUP": Keys.PAGE_UP,
            "PAGEDOWN": Keys.PAGE_DOWN,
            "UP": Keys.ARROW_UP,
            "DOWN": Keys.ARROW_DOWN,
            "LEFT": Keys.ARROW_LEFT,
            "RIGHT": Keys.ARROW_RIGHT,
            "ARROWUP": Keys.ARROW_UP,
            "ARROWDOWN": Keys.ARROW_DOWN,
            "ARROWLEFT": Keys.ARROW_LEFT,
            "ARROWRIGHT": Keys.ARROW_RIGHT,
            "CTRL": Keys.CONTROL,
            "CONTROL": Keys.CONTROL,
            "SHIFT": Keys.SHIFT,
            "ALT": Keys.ALT,
            "OPTION": Keys.ALT,
            "META": Keys.META,
            "CMD": Keys.META,
            "COMMAND": Keys.META,
        }
        modifiers = {"CTRL", "CONTROL", "SHIFT", "ALT", "OPTION", "META", "CMD", "COMMAND"}
        chain = ActionChains(self.driver)
        pressed = []

        for key in keys:
            upper = key.upper()
            normalized = key_map.get(upper, key)
            if upper in modifiers:
                chain.key_down(normalized)
                pressed.append(normalized)
            else:
                chain.send_keys(normalized)

        for modifier in reversed(pressed):
            chain.key_up(modifier)
        chain.perform()

    def detect_login_form(self) -> dict[str, Any]:
        self._require_driver()
        script = r"""
        const visible = (el) => {
          if (!el) return false;
          const r = el.getBoundingClientRect();
          const s = getComputedStyle(el);
          return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
        };

        const cssPath = (el) => {
          if (!el) return null;
          if (el.id) return "#" + CSS.escape(el.id);
          const parts = [];
          let node = el;
          while (node && node.nodeType === Node.ELEMENT_NODE && parts.length < 7) {
            let selector = node.nodeName.toLowerCase();
            const name = node.getAttribute("name");
            if (name) {
              selector += '[name="' + CSS.escape(name) + '"]';
              parts.unshift(selector);
              break;
            }
            const parent = node.parentElement;
            if (parent) {
              const siblings = Array.from(parent.children).filter(x => x.nodeName === node.nodeName);
              if (siblings.length > 1) {
                selector += ":nth-of-type(" + (siblings.indexOf(node) + 1) + ")";
              }
            }
            parts.unshift(selector);
            node = parent;
          }
          return parts.join(" > ");
        };

        const passwords = Array.from(document.querySelectorAll('input[type="password"]')).filter(visible);
        if (!passwords.length) {
          return {required:false, username_selector:null, password_selector:null, submit_selector:null};
        }

        const password = passwords[0];
        const form = password.closest("form") || document;
        const usernameCandidates = Array.from(
          form.querySelectorAll('input[type="email"],input[type="text"],input:not([type])')
        ).filter(visible).filter(el => el !== password && !el.disabled);

        let username = null;
        for (const candidate of usernameCandidates) {
          const relation = candidate.compareDocumentPosition(password);
          if (relation & Node.DOCUMENT_POSITION_FOLLOWING) username = candidate;
        }
        if (!username && usernameCandidates.length) username = usernameCandidates[0];

        const buttons = Array.from(
          form.querySelectorAll('button,input[type="submit"],input[type="button"],[role="button"]')
        ).filter(visible).filter(el => !el.disabled);

        const loginWords = ["entrar","login","iniciar sessão","iniciar sessao","sign in","log in","continuar","continue"];
        let submit = buttons.find(el => {
          const text = (el.innerText || el.value || el.getAttribute("aria-label") || "").trim().toLowerCase();
          return loginWords.some(word => text.includes(word));
        });
        if (!submit) submit = buttons.find(el => (el.getAttribute("type") || "").toLowerCase() === "submit");
        if (!submit && buttons.length) submit = buttons[0];

        return {
          required:true,
          username_selector:cssPath(username),
          password_selector:cssPath(password),
          submit_selector:cssPath(submit)
        };
        """
        result = self.driver.execute_script(script)
        return result or {
            "required": False,
            "username_selector": None,
            "password_selector": None,
            "submit_selector": None,
        }

    def submit_login(self, username: str, password: str) -> dict[str, Any]:
        self._require_driver()
        login = self.detect_login_form()
        if not login.get("required"):
            raise RuntimeError("Não foi detectado um formulário de login visível no Chromium.")

        username_selector = login.get("username_selector")
        password_selector = login.get("password_selector")
        submit_selector = login.get("submit_selector")

        if username_selector:
            username_element = self._find(username_selector)
            self._type_visible(
                username_element,
                username,
                "ESCREVER UTILIZADOR",
                secret=False,
                approval_target=username_selector,
            )

        if not password_selector:
            raise RuntimeError("Campo de password não encontrado.")

        password_element = self._find(password_selector)
        self._type_visible(
            password_element,
            password,
            "ESCREVER PASSWORD",
            secret=True,
            approval_target=password_selector,
        )

        needs_agent_recovery = False
        if submit_selector:
            try:
                submit = self._find(submit_selector)
                self._click_visible(
                    submit,
                    "CLICAR EM ENTRAR",
                    enforce_policy=False,
                    approval_target=submit_selector,
                )
            except ActionRejected:
                raise
            except Exception as exc:
                needs_agent_recovery = True
                self._emit(
                    "login",
                    f"Botão de login não foi accionado ({type(exc).__name__}); "
                    "o agente visual vai reanalisar o ecrã.",
                )
        else:
            needs_agent_recovery = True
            self._emit(
                "login",
                "Botão de login não identificado pelo DOM; "
                "o agente visual vai analisar a captura de ecrã.",
            )

        time.sleep(1.2)
        try:
            self._ensure_current_url_allowed()
        except Exception:
            # SSO may temporarily navigate through a host that must be explicitly
            # added to QA_ALLOWED_HOSTS by the user.
            raise

        self._install_visual_overlay()
        self._visual_status("LOGIN SUBMETIDO — A AGUARDAR RESPOSTA", "#77B9FF")
        state = self.detect_login_form()
        return {
            "submitted": not needs_agent_recovery,
            "needs_agent_recovery": needs_agent_recovery,
            "login_required": bool(state.get("required")),
            "url": self.current_url(),
            "title": self.page_title(),
        }

    def visual_inspect(self, limit: int = 8) -> list[dict[str, Any]]:
        self._require_driver()
        elements = self.snapshot_interactive(max_elements=max(limit, 1))
        inspected: list[dict[str, Any]] = []

        for item in elements[:limit]:
            selector = item.get("selector")
            if not selector:
                continue
            try:
                element = self._find(selector)
                label = item.get("text") or item.get("placeholder") or item.get("name") or item.get("tag")
                self._visual_focus(element, f"INSPECCIONAR — {str(label)[:60]}", "#FFD66B")
                self._emit("inspect", f"A inspeccionar elemento: {str(label)[:80]}")
                time.sleep(self.settings.visual_action_delay_ms / 1000)
                inspected.append(item)
            except Exception:
                continue

        self.driver.execute_script("window.scrollTo({top:0, behavior:'smooth'});")
        self._visual_status("INSPECÇÃO VISUAL CONCLUÍDA", "#7CFFB2")
        return inspected

    def snapshot_interactive(self, max_elements: int = 100) -> list[dict[str, Any]]:
        self._require_driver()
        script = r"""
        const maxElements = arguments[0];
        const cssPath = (el) => {
          if (el.id) return '#' + CSS.escape(el.id);
          const parts = [];
          let node = el;
          while (node && node.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
            let selector = node.nodeName.toLowerCase();
            if (node.getAttribute('name')) {
              selector += '[name="' + CSS.escape(node.getAttribute('name')) + '"]';
              parts.unshift(selector);
              break;
            }
            const parent = node.parentElement;
            if (parent) {
              const siblings = Array.from(parent.children).filter(x => x.nodeName === node.nodeName);
              if (siblings.length > 1) selector += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
            }
            parts.unshift(selector);
            node = parent;
          }
          return parts.join(' > ');
        };

        const candidates = Array.from(document.querySelectorAll(
          'input, select, textarea, button, a[href], [role="button"], [contenteditable="true"]'
        ));

        return candidates
          .filter(el => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          })
          .slice(0, maxElements)
          .map((el, index) => ({
            index,
            tag: el.tagName.toLowerCase(),
            type: el.getAttribute('type') || '',
            id: el.id || '',
            name: el.getAttribute('name') || '',
            text: (el.innerText || el.value || '').trim().slice(0, 160),
            placeholder: el.getAttribute('placeholder') || '',
            ariaLabel: el.getAttribute('aria-label') || '',
            selector: cssPath(el),
            disabled: !!el.disabled,
            required: !!el.required,
          }))
          .map(item => item.type === 'password' ? {...item, text: ''} : item);
        """
        return self.driver.execute_script(script, max_elements)

    def execute_action(self, action: dict[str, Any]) -> str:
        self._require_driver()
        action_type = str(action.get("action", "")).strip().lower()

        if action_type == "navigate":
            url = str(action.get("url", "")).strip()
            self.navigate(url)
            return f"Navigated to {url}"

        if action_type == "wait":
            seconds = min(max(float(action.get("seconds", 1)), 0), 5)
            self._visual_status(f"AGUARDAR {seconds:.1f}s", "#FFD66B")
            self._emit("wait", f"A aguardar {seconds:.1f}s")
            time.sleep(seconds)
            self._ensure_current_url_allowed()
            return f"Waited {seconds:.1f}s"

        if action_type == "screenshot":
            path = Path(str(action["path"]))
            return self.screenshot(path)

        if action_type == "assert_text":
            expected = str(action.get("text", ""))
            self._visual_status(f"VALIDAR TEXTO — {expected[:60]}", "#FFD66B")
            if expected not in self.driver.page_source:
                raise AssertionError(f"Expected text not found: {expected!r}")
            self._emit("assert", f"Texto encontrado: {expected[:80]}")
            return f"Text found: {expected!r}"

        if action_type == "done":
            self._visual_status("TAREFA CONCLUÍDA", "#7CFFB2")
            return str(action.get("summary", "Done"))

        selector = str(action.get("selector", "")).strip()
        if not selector:
            raise ValueError(f"Action '{action_type}' requires a selector")

        element = self._find(selector)

        if action_type == "click":
            target_text = (
                element.text
                or element.get_attribute("value")
                or element.get_attribute("aria-label")
                or ""
            )
            self.policy.ensure_click_allowed(target_text)
            self._click_visible(
                element,
                f"CLICAR — {target_text[:60] or selector}",
                approval_target=selector,
            )
            return f"Clicked {selector} ({target_text[:80]!r})"

        if action_type == "fill":
            value = str(action.get("value", ""))
            self._type_visible(
                element,
                value,
                f"ESCREVER — {selector}",
                secret=False,
                approval_target=selector,
            )
            return f"Filled {selector}"

        if action_type == "select":
            from selenium.webdriver.support.ui import Select

            value = str(action.get("value", ""))
            self._visual_focus(element, f"SELECCIONAR — {value[:60]}", "#77B9FF")
            self._require_approval(
                action="select",
                label=f"Seleccionar {value[:80]}",
                target=selector,
                value_preview=value[:120],
                secret=False,
            )
            select = Select(element)
            by = str(action.get("by", "visible_text")).lower()
            if by == "value":
                select.select_by_value(value)
            else:
                select.select_by_visible_text(value)
            self._visual_pulse()
            self._emit("select", f"Opção seleccionada: {value[:80]}")
            return f"Selected {value!r} on {selector}"

        raise ValueError(f"Unsupported action: {action_type}")

    def snapshot_json(self, max_elements: int = 100) -> str:
        return json.dumps(self.snapshot_interactive(max_elements), ensure_ascii=False, indent=2)

    def _click_visible(
        self,
        element,
        label: str,
        enforce_policy: bool = True,
        approval_target: str | None = None,
    ) -> None:
        from selenium.webdriver.common.action_chains import ActionChains

        if enforce_policy:
            target_text = (
                element.text
                or element.get_attribute("value")
                or element.get_attribute("aria-label")
                or ""
            )
            self.policy.ensure_click_allowed(target_text)

        self._visual_focus(element, label, "#7CFFB2")
        target_text = (
            element.text
            or element.get_attribute("value")
            or element.get_attribute("aria-label")
            or approval_target
            or "element"
        )
        self._require_approval(
            action="click",
            label=label,
            target=approval_target or str(target_text)[:160],
            value_preview=None,
            secret=False,
        )
        ActionChains(self.driver).move_to_element(element).pause(
            self.settings.visual_action_delay_ms / 1000
        ).click().perform()
        self._visual_pulse()
        self._emit("mouse", label)
        time.sleep(0.45)

        try:
            self._ensure_current_url_allowed()
            self._install_visual_overlay()
        except Exception:
            raise

    def _type_visible(
        self,
        element,
        value: str,
        label: str,
        secret: bool,
        approval_target: str | None = None,
    ) -> None:
        from selenium.webdriver.common.keys import Keys

        self._visual_focus(element, label, "#77B9FF")
        self._require_approval(
            action="write",
            label=label,
            target=approval_target or "input",
            value_preview=None if secret else value[:120],
            secret=secret,
            value_length=len(value),
        )
        element.click()
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.BACKSPACE)

        for character in value:
            element.send_keys(character)
            time.sleep(self.settings.visual_typing_delay_ms / 1000)

        self._visual_pulse()
        self._emit(
            "keyboard",
            "Password introduzida" if secret else "Texto introduzido",
        )

    def _require_approval(
        self,
        action: str,
        label: str,
        target: str,
        value_preview: str | None,
        secret: bool,
        value_length: int | None = None,
    ) -> None:
        if self.approval_callback is None:
            return

        self._visual_status("AGUARDAR APROVAÇÃO NO DASHBOARD", "#FFD66B")
        request = {
            "action": action,
            "label": label,
            "target": target,
            "secret": secret,
            "value_preview": None if secret else value_preview,
            "value_length": value_length,
            "url": self.current_url(),
            "title": self.page_title(),
        }
        self._emit("approval", f"A aguardar aprovação: {label}")

        approved = self.approval_callback(request)
        if not approved:
            self._visual_status("ACÇÃO REJEITADA PELO UTILIZADOR", "#FF8D8D")
            self._emit("approval", f"Acção rejeitada: {label}")
            raise ActionRejected(f"Acção rejeitada pelo utilizador: {label}")

        self._visual_status("APROVADO — A EXECUTAR", "#7CFFB2")
        self._emit("approval", f"Acção aprovada: {label}")

    def _visual_focus(self, element, label: str, color: str) -> None:
        self._install_visual_overlay()
        self.driver.execute_script(
            "window.__mysanaQAVisual.focus(arguments[0], arguments[1], arguments[2]);",
            element,
            label,
            color,
        )
        time.sleep(self.settings.visual_action_delay_ms / 1000)

    def _visual_status(self, label: str, color: str) -> None:
        try:
            self._install_visual_overlay()
            self.driver.execute_script(
                "window.__mysanaQAVisual.status(arguments[0], arguments[1]);",
                label,
                color,
            )
        except Exception:
            pass

    def _visual_pulse(self) -> None:
        try:
            self.driver.execute_script("window.__mysanaQAVisual.pulse();")
        except Exception:
            pass

    def _install_visual_overlay(self) -> None:
        self._require_driver()
        self.driver.execute_script(_VISUAL_OVERLAY_JS)

    def _find(self, selector: str):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        if selector.startswith("xpath="):
            by = By.XPATH
            value = selector[len("xpath="):]
        else:
            by = By.CSS_SELECTOR
            value = selector

        return WebDriverWait(self.driver, self.settings.action_timeout_seconds).until(
            EC.presence_of_element_located((by, value))
        )

    def _build_chrome_options(self, webdriver, profile_dir: Path, binary: str | None):
        options = webdriver.ChromeOptions()
        if binary:
            options.binary_location = binary

        options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument("--profile-directory=Default")
        options.add_argument("--remote-debugging-port=0")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-search-engine-choice-screen")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-background-mode")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-component-update")
        options.add_experimental_option(
            "prefs",
            {
                "credentials_enable_service": False,
                "profile.password_manager_enabled": False,
            },
        )
        if self.settings.headless:
            options.add_argument("--headless=new")
        return options

    def _profile_candidates(self) -> list[Path]:
        base = self.settings.chrome_profile_dir
        pointer = base.parent / "active-profile.txt"
        candidates: list[Path] = []

        try:
            if pointer.exists():
                saved = Path(pointer.read_text(encoding="utf-8").strip())
                if saved:
                    candidates.append(saved)
        except Exception:
            pass

        candidates.extend(
            [
                base,
                base.parent / f"{base.name}-recovery-1",
                base.parent / f"{base.name}-recovery-2",
            ]
        )

        unique: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate).lower()
            if key not in seen:
                seen.add(key)
                unique.append(candidate)
        return unique

    def _remember_working_profile(self, profile_dir: Path) -> None:
        try:
            pointer = self.settings.chrome_profile_dir.parent / "active-profile.txt"
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.write_text(str(profile_dir), encoding="utf-8")
        except Exception:
            pass

    def _resolve_chromium_binary(self) -> tuple[str | None, str]:
        if self.settings.chromium_binary:
            forced = Path(self.settings.chromium_binary).expanduser()
            if not forced.exists():
                raise RuntimeError(f"QA_CHROMIUM_BINARY não existe: {forced}")
            return str(forced), "Chromium"

        for command in ("chromium", "chromium-browser"):
            found = shutil.which(command)
            if found:
                return found, "Chromium"

        if os.name == "nt":
            env = os.environ
            candidates = [
                Path(env.get("LOCALAPPDATA", "")) / "Chromium/Application/chrome.exe",
                Path(env.get("PROGRAMFILES", "")) / "Chromium/Application/chrome.exe",
                Path(env.get("PROGRAMFILES(X86)", "")) / "Chromium/Application/chrome.exe",
            ]
            for candidate in candidates:
                if str(candidate) and candidate.exists():
                    return str(candidate), "Chromium"

        return None, "Chrome (Chromium)"

    def _ensure_current_url_allowed(self) -> None:
        current = self.driver.current_url
        if current and current != "data:,":
            self.policy.ensure_url_allowed(current)

    def _emit(self, action: str, message: str) -> None:
        self._event_id += 1
        if self.event_callback:
            self.event_callback(
                {
                    "id": self._event_id,
                    "action": action,
                    "message": message,
                    "timestamp": time.time(),
                }
            )

    def _require_driver(self) -> None:
        if self.driver is None:
            raise RuntimeError("Browser session has not been started")
