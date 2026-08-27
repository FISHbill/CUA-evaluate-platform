"""把 ScienceBoard `VManager` / OSWorld 风格 controller 收成 GuestDesktop。

ScienceBoard 的 VM 侧同样走 `get_screenshot` / `execute_python_command`；
guest shell 走 manager 的 guest 通道（`_execute` / `_run` / `run_bash_script`），
不得 `subprocess` 调宿主机 bash。
"""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

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
from cua_eval.benches.gitpin import ensure_on_path
from cua_eval.benches.osworld_guest import OSWorldGuest, format_bash_result, png_size
from cua_eval.errors import ConfigError, InfraError


def _png_from_screenshot(raw: object) -> tuple[bytes, int, int]:
    if raw is None:
        raise InfraError("ScienceBoard screenshot 返回空；桌面 VM 可能没起来")
    if isinstance(raw, Image.Image):
        buf = BytesIO()
        raw.convert("RGB").save(buf, format="PNG")
        data = buf.getvalue()
        return data, raw.width, raw.height
    if isinstance(raw, bytes | bytearray):
        data = bytes(raw)
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            width, height = png_size(data)
            return data, width, height
        image = Image.open(BytesIO(data)).convert("RGB")
        buf = BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue(), image.width, image.height
    raise InfraError(f"无法解析 ScienceBoard 截图类型: {type(raw).__name__}")


class ScienceBoardGuest:
    """真实科学桌面 VM。副作用经官方 manager / controller，不碰宿主机 shell。"""

    def __init__(
        self,
        manager: Any,
        *,
        native_width: int = 1920,
        native_height: int = 1080,
    ) -> None:
        self._manager = manager
        self.native_width = native_width
        self.native_height = native_height
        self.terminated: str | None = None
        controller = getattr(manager, "controller", None)
        self._osworld = OSWorldGuest(controller) if controller is not None else None

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> ScienceBoardGuest:
        env = environ if environ is not None else dict(os.environ)
        vm_ip = env.get("CUA_EVAL_SCIENCEBOARD_VM_IP", "").strip()
        if not vm_ip:
            # ScienceBoard 的 guest HTTP 与 OSWorld PythonController 同形。
            vm_ip = env.get("CUA_EVAL_OSWORLD_VM_IP", "").strip()
        if not vm_ip:
            raise ConfigError(
                "scienceboard guest 需要 CUA_EVAL_SCIENCEBOARD_VM_IP（桌面 VM 的地址）"
            )
        root = env.get("CUA_EVAL_SCIENCEBOARD_ROOT") or env.get("CUA_EVAL_OSWORLD_ROOT")
        if root:
            ensure_on_path(Path(root))
        osworld_env = dict(env)
        osworld_env["CUA_EVAL_OSWORLD_VM_IP"] = vm_ip
        if env.get("CUA_EVAL_SCIENCEBOARD_SERVER_PORT"):
            osworld_env["CUA_EVAL_OSWORLD_SERVER_PORT"] = env["CUA_EVAL_SCIENCEBOARD_SERVER_PORT"]
        inner = OSWorldGuest.from_env(osworld_env)
        guest = cls(manager=inner._controller)
        guest._osworld = inner
        return guest

    def identity(self) -> str:
        return self.run_bash("hostname", timeout=10).strip().splitlines()[0].strip()

    def capture_png(self) -> tuple[bytes, int, int]:
        if self._osworld is not None:
            png, width, height = self._osworld.capture_png()
            self.native_width, self.native_height = width, height
            return png, width, height
        shot = getattr(self._manager, "screenshot", None)
        if callable(shot):
            png, width, height = _png_from_screenshot(shot())
            self.native_width, self.native_height = width, height
            return png, width, height
        getter = getattr(self._manager, "get_screenshot", None)
        if callable(getter):
            png, width, height = _png_from_screenshot(getter())
            self.native_width, self.native_height = width, height
            return png, width, height
        raise InfraError("ScienceBoard manager 没有 screenshot / get_screenshot")

    def click(self, x: int, y: int, button: str) -> None:
        mapping = {"left": "left", "right": "right", "middle": "middle"}
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
        if self._osworld is not None:
            self._osworld.terminate(status)

    def run_bash(self, command: str, timeout: float | None) -> str:
        seconds = 30 if timeout is None else max(1, int(timeout))
        if self._osworld is not None:
            return self._osworld.run_bash(command, float(seconds))
        execute = getattr(self._manager, "_execute", None)
        if callable(execute):
            result = execute(command=command, shell=True)
            return format_bash_result(result)
        runner = getattr(self._manager, "_run", None)
        if callable(runner):
            ok = runner(command)
            return "" if ok else "[scienceboard guest shell failed]\n"
        raise InfraError(
            "ScienceBoard manager 没有 guest shell 通道；拒绝改走宿主机 subprocess"
        )

    def _exec_gui(self, action: object) -> None:
        if self._osworld is not None:
            script = to_pyautogui(action)  # type: ignore[arg-type]
            self._osworld._controller.execute_python_command(script)
            return
        script = to_pyautogui(action)  # type: ignore[arg-type]
        if callable(self._manager):
            self._manager(script)
            return
        execute = getattr(self._manager, "execute_python_command", None)
        if callable(execute):
            execute(script)
            return
        raise InfraError("ScienceBoard manager 无法执行 pyautogui")


__all__ = ["ScienceBoardGuest"]
