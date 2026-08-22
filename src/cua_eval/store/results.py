"""把一次 run 写成本地目录。不引入数据库。"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cua_eval.errors import ConfigError
from cua_eval.schema import Experiment, RunRecord, TrialResult

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")
# 与 new_run_id 对齐：只 prune 平台自己写下的目录，避免误删用户文件。
RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
SECONDS_PER_DAY = 86400


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

    def load_run(self, run_id: str) -> RunRecord:
        if Path(run_id).name != run_id or not RUN_ID_RE.match(run_id):
            raise ConfigError(f"非法 run_id: {run_id!r}")
        path = (self.results_dir / run_id / "run.json").resolve()
        root = self.results_dir.resolve()
        if not path.is_relative_to(root) or path.parent.parent != root:
            raise ConfigError(f"run_id 越出 results 目录: {run_id!r}")
        if not path.is_file():
            raise ConfigError(f"找不到 {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"读 {path} 失败: {exc}") from exc
        try:
            return RunRecord.model_validate(payload)
        except ValueError as exc:
            raise ConfigError(f"{path} 不是合法的 RunRecord: {exc}") from exc


def prune_results(
    results_dir: Path,
    retention_days: int,
    *,
    now: datetime | None = None,
) -> list[Path]:
    """删除 `results_dir` 下过期的 `run_id` 目录。不会删到该目录之外。"""
    if retention_days <= 0:
        raise ValueError("retention_days 必须为正")
    root = results_dir.resolve()
    if not root.is_dir():
        return []
    cutoff = (now or datetime.now(UTC)).timestamp() - retention_days * SECONDS_PER_DAY
    deleted: list[Path] = []
    for child in root.iterdir():
        if not RUN_ID_RE.match(child.name):
            continue
        try:
            resolved = child.resolve()
        except OSError:
            continue
        if resolved.parent != root or resolved == root:
            continue
        if not resolved.is_dir():
            continue
        try:
            mtime = resolved.stat().st_mtime
        except OSError:
            continue
        if mtime > cutoff:
            continue
        shutil.rmtree(resolved)
        deleted.append(resolved)
    return deleted


__all__ = [
    "RUN_ID_RE",
    "ResultStore",
    "StartedRun",
    "TaskWriter",
    "new_run_id",
    "prune_results",
    "sanitize_id",
]
