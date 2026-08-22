"""中立键鼠动作，以及到 OSWorld pyautogui 片段的映射。

坐标统一用**像素**，并与截图尺寸一起存。模型若看到的是下采样图，必须先
`scale_action` 映射回原图再执行，否则点击会系统性偏移。

`shell` 是独立动作类型，只有 `protocol.guest_shell=true` 时才允许出现；它**不是**
pyautogui 动作，落点是桌面 VM 内的 `PythonController.run_bash_script()`。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from cua_eval.errors import ModelError
from cua_eval.schema import GuestActions, Protocol


class ActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClickAction(ActionBase):
    type: Literal["click"] = "click"
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    button: Literal["left", "right", "middle"] = "left"


class DoubleClickAction(ActionBase):
    type: Literal["double_click"] = "double_click"
    x: int = Field(ge=0)
    y: int = Field(ge=0)


class MoveAction(ActionBase):
    type: Literal["move"] = "move"
    x: int = Field(ge=0)
    y: int = Field(ge=0)


class ScrollAction(ActionBase):
    type: Literal["scroll"] = "scroll"
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    dx: int = 0
    dy: int = 0


class TypeAction(ActionBase):
    type: Literal["type"] = "type"
    text: str


class KeyAction(ActionBase):
    type: Literal["key"] = "key"
    keys: list[str] = Field(min_length=1)


class WaitAction(ActionBase):
    type: Literal["wait"] = "wait"
    seconds: float = Field(ge=0)


class TerminateAction(ActionBase):
    type: Literal["terminate"] = "terminate"
    status: Literal["success", "fail"] = "success"


class ShellAction(ActionBase):
    """仅当 `guest_shell=true` 时合法。命令在桌面 VM 内执行，不在评测宿主机上。"""

    type: Literal["shell"] = "shell"
    command: str = Field(min_length=1)
    timeout: float | None = Field(default=None, gt=0)


Action: TypeAlias = Annotated[
    ClickAction
    | DoubleClickAction
    | MoveAction
    | ScrollAction
    | TypeAction
    | KeyAction
    | WaitAction
    | TerminateAction
    | ShellAction,
    Field(discriminator="type"),
]

_action_adapter: TypeAdapter[Action] = TypeAdapter(Action)

GUI_ACTION_TYPES = (
    ClickAction,
    DoubleClickAction,
    MoveAction,
    ScrollAction,
    TypeAction,
    KeyAction,
)


def parse_action(data: dict[str, Any]) -> Action:
    return _action_adapter.validate_python(data)


def is_gui_action(action: Action) -> bool:
    return isinstance(action, GUI_ACTION_TYPES)


def validate_against_protocol(action: Action, protocol: Protocol) -> None:
    """模型吐出了当前协议不允许的动作时记 `model_error`，不是环境故障。"""
    if isinstance(action, ShellAction) and not protocol.guest_shell:
        raise ModelError(
            "模型产出了 shell 动作，但 protocol.guest_shell=false；"
            "shell 工具在该协议下不得暴露。"
        )
    if is_gui_action(action) and protocol.guest_actions is GuestActions.NONE:
        raise ModelError(
            f"模型产出了键鼠动作 {action.type}，但 protocol.guest_actions=none"
        )


def scale_pixels(
    x: int,
    y: int,
    *,
    from_size: tuple[int, int],
    to_size: tuple[int, int],
) -> tuple[int, int]:
    """把模型看到的图上的像素坐标映射回执行用的原图像素。"""
    from_w, from_h = from_size
    to_w, to_h = to_size
    if from_w <= 0 or from_h <= 0:
        raise ValueError(f"from_size 必须为正，得到 {from_size}")
    if to_w <= 0 or to_h <= 0:
        raise ValueError(f"to_size 必须为正，得到 {to_size}")
    nx = round(x * to_w / from_w)
    ny = round(y * to_h / from_h)
    nx = min(max(nx, 0), to_w - 1)
    ny = min(max(ny, 0), to_h - 1)
    return nx, ny


def scale_action(
    action: Action,
    *,
    from_size: tuple[int, int],
    to_size: tuple[int, int],
) -> Action:
    """按截图缩放比改写带坐标的动作；无坐标的动作原样返回。"""
    if from_size == to_size:
        return action
    if isinstance(action, ClickAction):
        x, y = scale_pixels(action.x, action.y, from_size=from_size, to_size=to_size)
        return action.model_copy(update={"x": x, "y": y})
    if isinstance(action, DoubleClickAction | MoveAction | ScrollAction):
        x, y = scale_pixels(action.x, action.y, from_size=from_size, to_size=to_size)
        return action.model_copy(update={"x": x, "y": y})
    return action


def to_pyautogui(action: Action) -> str:
    """生成 OSWorld 可执行的 pyautogui 片段。

    `terminate` 与 `shell` 不是键鼠动作，调用方应走各自的通道，而不是这段映射。
    """
    match action:
        case ClickAction(x=x, y=y, button=button):
            return f"import pyautogui\npyautogui.click({x}, {y}, button={button!r})"
        case DoubleClickAction(x=x, y=y):
            return f"import pyautogui\npyautogui.doubleClick({x}, {y})"
        case MoveAction(x=x, y=y):
            return f"import pyautogui\npyautogui.moveTo({x}, {y})"
        case ScrollAction(x=x, y=y, dx=dx, dy=dy):
            lines = ["import pyautogui", f"pyautogui.moveTo({x}, {y})"]
            if dy:
                lines.append(f"pyautogui.scroll({int(dy)})")
            if dx:
                lines.append(f"pyautogui.hscroll({int(dx)})")
            if not dy and not dx:
                lines.append("pyautogui.scroll(0)")
            return "\n".join(lines)
        case TypeAction(text=text):
            return f"import pyautogui\npyautogui.write({text!r})"
        case KeyAction(keys=keys):
            if len(keys) == 1:
                return f"import pyautogui\npyautogui.press({keys[0]!r})"
            quoted = ", ".join(repr(k) for k in keys)
            return f"import pyautogui\npyautogui.hotkey({quoted})"
        case WaitAction(seconds=seconds):
            return f"import time\ntime.sleep({seconds})"
        case TerminateAction():
            raise ValueError("terminate 不是 pyautogui 动作")
        case ShellAction():
            raise ValueError(
                "shell 不是 pyautogui 动作；应经 guest 的 run_bash_script 执行，"
                "不得映射成宿主机 bash"
            )


__all__ = [
    "GUI_ACTION_TYPES",
    "Action",
    "ClickAction",
    "DoubleClickAction",
    "KeyAction",
    "MoveAction",
    "ScrollAction",
    "ShellAction",
    "TerminateAction",
    "TypeAction",
    "WaitAction",
    "is_gui_action",
    "parse_action",
    "scale_action",
    "scale_pixels",
    "to_pyautogui",
    "validate_against_protocol",
]
