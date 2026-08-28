"""Harness：给观测，收动作。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from cua_eval.actions import Action


@dataclass(frozen=True)
class StepObservation:
    """一步的观测。

    `screenshot_png` 为 None 表示当前协议不给截图（`observation=text`）。
    坐标以这张图的像素为准；若后续下采样，必须按缩放比映射回原图。
    """

    instruction: str
    step_index: int
    screenshot_png: bytes | None
    screenshot_width: int
    screenshot_height: int
    text: str | None = None


@dataclass(frozen=True)
class Completion:
    action: Action
    input_tokens: int = 0
    output_tokens: int = 0


@runtime_checkable
class Harness(Protocol):
    def act(self, observation: StepObservation) -> Completion: ...
