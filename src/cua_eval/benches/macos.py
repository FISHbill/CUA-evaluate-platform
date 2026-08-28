"""macOS 客户机 bench。本阶段在 Linux 上明确不支持，不得假跑分数。"""

from __future__ import annotations

from cua_eval.benches.base import RawResult
from cua_eval.errors import UnsupportedBenchError
from cua_eval.harness.base import Harness
from cua_eval.schema import TrialResult

_MESSAGE = "macos bench 客户机本阶段不支持；不得在 Linux 上假跑 macOS 分数"


class MacOSBench:
    id = "macos"

    def prepare(self) -> None:
        raise UnsupportedBenchError(_MESSAGE)

    def list_tasks(self) -> list[str]:
        raise UnsupportedBenchError(_MESSAGE)

    def run_trial(self, task_id: str, agent: Harness) -> RawResult:
        del task_id, agent
        raise UnsupportedBenchError(_MESSAGE)

    def normalize(self, raw: RawResult) -> TrialResult:
        del raw
        raise UnsupportedBenchError(_MESSAGE)

    def cleanup(self) -> None:
        raise UnsupportedBenchError(_MESSAGE)
