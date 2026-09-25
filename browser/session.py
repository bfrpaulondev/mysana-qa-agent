from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from browser.policy import BrowserPolicy
from core.settings import Settings


class BrowserSession:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.policy = BrowserPolicy(
            allowed_hosts=settings.allowed_hosts,
            allow_dangerous_actions=settings.allow_dangerous_actions,
        )
        self.driver = None

    def start(self) -> None:
        try:
            from selenium import webdriver
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Selenium is not installed. Run: pip install -r requirements.txt") from exc

        options = webdriver.ChromeOptions()
        options.add_argument(f"--user-data-dir={self.settings.chrome_profile_dir}")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-notifications")
        if self.settings.headless:
            options.add_argument("--headless=new")

        self.driver = webdriver.Chrome(options=options)
        self.driver.set_page_load_timeout(60)

    def close(self) -> None:
        if self.driver is not None:
            self.driver.quit()
            self.driver = None

    def navigate(self, url: str) -> None:
        self._require_driver()
        self.policy.ensure_url_allowed(url)
        self.driver.get(url)
        self._ensure_current_url_allowed()

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
            time.sleep(seconds)
            self._ensure_current_url_allowed()
            return f"Waited {seconds:.1f}s"

        if action_type == "screenshot":
            path = Path(str(action["path"]))
            return self.screenshot(path)

        if action_type == "assert_text":
            expected = str(action.get("text", ""))
            if expected not in self.driver.page_source:
                raise AssertionError(f"Expected text not found: {expected!r}")
            return f"Text found: {expected!r}"

        if action_type == "done":
            return str(action.get("summary", "Done"))

        selector = str(action.get("selector", "")).strip()
        if not selector:
            raise ValueError(f"Action '{action_type}' requires a selector")

        element = self._find(selector)

        if action_type == "click":
            target_text = (element.text or element.get_attribute("value") or element.get_attribute("aria-label") or "")
            self.policy.ensure_click_allowed(target_text)
            element.click()
            time.sleep(0.4)
            self._ensure_current_url_allowed()
            return f"Clicked {selector} ({target_text[:80]!r})"

        if action_type == "fill":
            value = str(action.get("value", ""))
            element.clear()
            element.send_keys(value)
            return f"Filled {selector}"

        if action_type == "select":
            from selenium.webdriver.support.ui import Select

            value = str(action.get("value", ""))
            select = Select(element)
            by = str(action.get("by", "visible_text")).lower()
            if by == "value":
                select.select_by_value(value)
            else:
                select.select_by_visible_text(value)
            return f"Selected {value!r} on {selector}"

        raise ValueError(f"Unsupported action: {action_type}")

    def snapshot_json(self, max_elements: int = 100) -> str:
        return json.dumps(self.snapshot_interactive(max_elements), ensure_ascii=False, indent=2)

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

    def _ensure_current_url_allowed(self) -> None:
        current = self.driver.current_url
        if current and current != "data:,":
            self.policy.ensure_url_allowed(current)

    def _require_driver(self) -> None:
        if self.driver is None:
            raise RuntimeError("Browser session has not been started")
