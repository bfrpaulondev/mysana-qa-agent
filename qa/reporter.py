from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class StepResult:
    index: int
    action: str
    status: str
    message: str
    screenshot: str | None = None
    provider: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class RunReport:
    def __init__(self, name: str, output_dir: Path):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.name = name
        self.run_id = f"{name}-{stamp}"
        self.output_dir = output_dir / self.run_id
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.steps: list[StepResult] = []
        self.started_at = datetime.now().isoformat(timespec="seconds")

    def add(self, result: StepResult) -> None:
        self.steps.append(result)

    def screenshot_path(self, index: int) -> Path:
        return self.output_dir / f"step-{index:03d}.png"

    def save(self) -> tuple[Path, Path]:
        finished_at = datetime.now().isoformat(timespec="seconds")
        payload = {
            "name": self.name,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "steps": [asdict(step) for step in self.steps],
        }

        json_path = self.output_dir / "report.json"
        md_path = self.output_dir / "report.md"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        lines = [
            f"# QA Report — {self.name}",
            "",
            f"Run: `{self.run_id}`",
            "",
            "| # | Action | Status | Result |",
            "|---:|---|---|---|",
        ]
        for step in self.steps:
            message = step.message.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {step.index} | {step.action} | **{step.status}** | {message} |")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return json_path, md_path
