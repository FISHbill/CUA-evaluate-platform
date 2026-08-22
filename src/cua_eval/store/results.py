"""把一次 run 写成本地目录。不引入数据库。"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cua_eval.schema import Experiment, RunRecord, TrialResult

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def new_run_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(2)}"


def sanitize_id(value: str) -> str:
    cleaned = _SAFE_ID.sub("_", value).strip("._") or "unnamed"
    return cleaned[:120]


class TaskWriter:
    def __init__(self, task_dir: Path) -> None:
        self.task_dir = task_dir
        (task_dir / "screenshots").mkdir(parents=True, exist_ok=True)
        (task_dir / "raw").mkdir(parents=True, exist_ok=True)

    def save_screenshot(self, step: int, png: bytes) -> Path:
        path = self.task_dir / "screenshots" / f"step-{step:03d}.png"
        path.write_bytes(png)
        return path

    def append_trace(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False)
        with (self.task_dir / "trace.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def write_result(self, trial: TrialResult, extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = trial.model_dump(mode="json")
        if extra:
            payload.update(extra)
        (self.task_dir / "result.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


@dataclass
class StartedRun:
    run_id: str
    run_dir: Path
    experiment: Experiment

    def task_writer(self, bench_id: str, model_id: str, task_id: str) -> TaskWriter:
        task_dir = (
            self.run_dir / sanitize_id(bench_id) / sanitize_id(model_id) / sanitize_id(task_id)
        )
        task_dir.mkdir(parents=True, exist_ok=True)
        return TaskWriter(task_dir)

    def write_run_record(self, record: RunRecord) -> Path:
        path = self.run_dir / "run.json"
        path.write_text(
            json.dumps(record.to_json_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path


class ResultStore:
    def __init__(self, results_dir: Path) -> None:
        self.results_dir = results_dir

    def start_run(self, experiment: Experiment) -> StartedRun:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        for _ in range(8):
            run_id = new_run_id()
            run_dir = self.results_dir / run_id
            try:
                run_dir.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue
            return StartedRun(run_id=run_id, run_dir=run_dir, experiment=experiment)
        raise RuntimeError("无法分配未占用的 run_id")


__all__ = [
    "ResultStore",
    "StartedRun",
    "TaskWriter",
    "new_run_id",
    "sanitize_id",
]
