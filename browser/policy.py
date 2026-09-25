from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


class PolicyViolation(RuntimeError):
    pass


@dataclass(slots=True)
class BrowserPolicy:
    allowed_hosts: tuple[str, ...]
    allow_dangerous_actions: bool = False

    blocked_terms: tuple[str, ...] = (
        "delete",
        "remove permanently",
        "approve",
        "approval",
        "payment",
        "pay now",
        "transfer",
        "reject",
        "apagar",
        "eliminar",
        "aprovar",
        "aprovação",
        "pagamento",
        "pagar",
        "transferir",
        "rejeitar",
    )

    def ensure_url_allowed(self, url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        if not host:
            raise PolicyViolation(f"Invalid URL without host: {url}")

        allowed = any(host == item.lower() or host.endswith("." + item.lower()) for item in self.allowed_hosts)
        if not allowed:
            raise PolicyViolation(
                f"Navigation blocked. Host '{host}' is not in QA_ALLOWED_HOSTS={self.allowed_hosts}."
            )

    def ensure_click_allowed(self, target_text: str) -> None:
        if self.allow_dangerous_actions:
            return

        normalized = (target_text or "").strip().lower()
        for term in self.blocked_terms:
            if term in normalized:
                raise PolicyViolation(
                    f"Click blocked by safety policy because target contains '{term}': {target_text!r}"
                )
