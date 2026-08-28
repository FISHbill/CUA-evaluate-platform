"""内存假 bench：不启动 VM、不出网。供阶段 0 / CI 使用。"""

from __future__ import annotations

import struct
import time
import zlib
from typing import Final

from cua_eval.actions import TerminateAction
from cua_eval.benches.base import RawResult
from cua_eval.errors import InfraError, ModelError
from cua_eval.harness.base import Harness, StepObservation
from cua_eval.schema import Experiment, FailureClass, TrialResult

FAKE_SCREEN_SIZE: Final[tuple[int, int]] = (64, 64)
FAKE_SCREEN_COLOR: Final[tuple[int, int, int]] = (32, 96, 160)


def solid_color_png(
    width: int,
    height: int,
    rgb: tuple[int, int, int] = FAKE_SCREEN_COLOR,
) -> bytes:
    """写一张未压缩的 8-bit RGB PNG，不引入图像库依赖。"""

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


class FakeBench:
    """单题内存环境。evaluator 对跑完的 dummy 给固定分 1.0。"""

    id = "fake"

    def __init__(self, experiment: Experiment) -> None:
        self._experiment = experiment

    def prepare(self) -> None:
        return None

    def list_tasks(self) -> list[str]:
        return list(self._experiment.task_ids)

    def run_trial(self, task_id: str, agent: Harness) -> RawResult:
        limits = self._experiment.agent.limits
        start = time.monotonic()
        deadline = start + limits.task_timeout_seconds
        png = solid_color_png(*FAKE_SCREEN_SIZE)
        width, height = FAKE_SCREEN_SIZE
        instruction = f"fake task {task_id}: wait, then terminate. the screenshot is a solid color."
        steps = 0
        input_tokens = 0
        output_tokens = 0
        terminated = False
        terminate_status: str | None = None

        try:
            for step in range(limits.max_steps):
                if time.monotonic() > deadline:
                    raise InfraError(
                        f"task {task_id} exceeded wall-clock limit "
                        f"({limits.task_timeout_seconds}s)"
                    )
                observation = StepObservation(
                    instruction=instruction,
                    step_index=step,
                    screenshot_png=png,
                    screenshot_width=width,
                    screenshot_height=height,
                )
                completion = agent.act(observation)
                input_tokens += completion.input_tokens
                output_tokens += completion.output_tokens
                steps = step + 1
                if isinstance(completion.action, TerminateAction):
                    terminated = True
                    terminate_status = completion.action.status
                    break
        except (InfraError, ModelError):
            raise
        except Exception as exc:
            raise InfraError(f"fake env failed on {task_id}: {exc}") from exc

        wall = time.monotonic() - start
        if not terminated:
            return RawResult(
                task_id=task_id,
                steps=steps,
                wall_time_seconds=wall,
                terminated=False,
                terminate_status=None,
                evaluator_score=0.0,
                failure_class=FailureClass.TASK_FAIL,
                error_message=f"hit max_steps={limits.max_steps} without terminate",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        success = terminate_status == "success"
        return RawResult(
            task_id=task_id,
            steps=steps,
            wall_time_seconds=wall,
            terminated=True,
            terminate_status=terminate_status,
            evaluator_score=1.0 if success else 0.0,
            failure_class=FailureClass.OK if success else FailureClass.TASK_FAIL,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def normalize(self, raw: RawResult) -> TrialResult:
        return TrialResult(
            task_id=raw.task_id,
            failure_class=raw.failure_class,
            score=raw.evaluator_score,
            steps=raw.steps,
            wall_time_seconds=raw.wall_time_seconds,
            input_tokens=raw.input_tokens,
            output_tokens=raw.output_tokens,
            error_message=raw.error_message,
        )

    def cleanup(self) -> None:
        return None
