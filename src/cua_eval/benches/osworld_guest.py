"""把 OSWorld `PythonController` 收成 `GuestDesktop`。

键鼠走官方 `execute_python_command`（pyautogui 在 guest 内执行）。
`shell` 只走 `run_bash_script`，命令在桌面 VM 内，不得 `subprocess`。
"""

from __future__ import annotations

import os
import struct
import sys
import time
from pathlib import Path
from typing import Any, Literal

from cua_eval.actions import (
    ClickAction,
    DoubleClickAction,
    KeyAction,
    MoveAction,
    ScrollAction,
    TypeAction,
    WaitAction,
    to_pyautogui,
)
from cua_eval.errors import ConfigError, InfraError


def png_size(data: bytes) -> tuple[int, int]:
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    return 1920, 1080


def format_bash_result(result: object) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        output = str(result.get("output") or "")
        error = str(result.get("error") or "")
        text = output if not error else f"{output}{error}"
        code = result.get("returncode")
        if code not in (0, None, "0"):
            text = f"{text.rstrip()}\n[returncode={code}]\n"
        return text
    return str(result)


def ensure_osworld_on_path(root: Path) -> None:
    text = str(root)
    if text not in sys.path:
        sys.path.insert(0, text)


class OSWorldGuest:
    """真实桌面 VM。所有副作用都经 OSWorld controller，不碰宿主机 shell。"""

    def __init__(
        self,
        controller: Any,
        *,
        native_width: int = 1920,
        native_height: int = 1080,
    ) -> None:
        self._controller = controller
        self.native_width = native_width
        self.native_height = native_height
        self.terminated: str | None = None

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> OSWorldGuest:
        env = environ if environ is not None else os.environ
        vm_ip = env.get("CUA_EVAL_OSWORLD_VM_IP", "").strip()
        if not vm_ip:
            raise ConfigError("osworld guest 需要 CUA_EVAL_OSWORLD_VM_IP（桌面 VM 的地址）")
        port = int(env.get("CUA_EVAL_OSWORLD_SERVER_PORT", "5000"))
        root = env.get("CUA_EVAL_OSWORLD_ROOT")
        if root:
            ensure_osworld_on_path(Path(root))
        try:
            from desktop_env.controllers.python import PythonController
        except ImportError as exc:
            raise ConfigError(
                "导入不了 OSWorld PythonController。请把官方仓库 checkout 到 "
                "third_party/OSWorld 并把该路径加入 PYTHONPATH。"
            ) from exc
        controller = PythonController(vm_ip=vm_ip, server_port=port)
        return cls(controller)

    def identity(self) -> str:
        return self.run_bash("hostname", timeout=10).strip().splitlines()[0].strip()

    def capture_png(self) -> tuple[bytes, int, int]:
        raw = self._controller.get_screenshot()
        if not raw:
            raise InfraError("OSWorld controller.get_screenshot() 返回空；桌面 VM 可能没起来")
        data = bytes(raw)
        width, height = png_size(data)
        self.native_width, self.native_height = width, height
        return data, width, height

    def click(self, x: int, y: int, button: str) -> None:
        mapping: dict[str, Literal["left", "right", "middle"]] = {
            "left": "left",
            "right": "right",
            "middle": "middle",
        }
        self._exec_gui(ClickAction(x=x, y=y, button=mapping.get(button, "left")))

    def double_click(self, x: int, y: int) -> None:
        self._exec_gui(DoubleClickAction(x=x, y=y))

    def move(self, x: int, y: int) -> None:
        self._exec_gui(MoveAction(x=x, y=y))

    def scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self._exec_gui(ScrollAction(x=x, y=y, dx=dx, dy=dy))

    def type_text(self, text: str) -> None:
        self._exec_gui(TypeAction(text=text))

    def key(self, keys: list[str]) -> None:
        self._exec_gui(KeyAction(keys=keys))

    def wait(self, seconds: float) -> None:
        self._exec_gui(WaitAction(seconds=seconds))

    def terminate(self, status: str) -> None:
        self.terminated = status

    def run_bash(self, command: str, timeout: float | None) -> str:
        seconds = 30 if timeout is None else max(1, int(timeout))
        runner = getattr(self._controller, "run_bash_script", None)
        if runner is None:
            raise InfraError(
                "OSWorld controller 没有 run_bash_script；拒绝改走宿主机 subprocess"
            )
        result = runner(command, timeout=seconds)
        return format_bash_result(result)

    def _exec_gui(self, action: object) -> None:
        script = to_pyautogui(action)  # type: ignore[arg-type]
        self._controller.execute_python_command(script)
        if isinstance(action, WaitAction):
            # guest 侧 sleep 已发出；本地再让出一点点，避免立刻截到旧帧。
            time.sleep(min(action.seconds, 0.05))


__all__ = ["OSWorldGuest", "ensure_osworld_on_path", "format_bash_result", "png_size"]
