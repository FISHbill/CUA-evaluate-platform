"""桌面客户机接口。

MCP 的 `shell` / 键鼠 / 截图都只通过这个协议落到 guest。实现里不得
`subprocess` 调宿主机 bash，也不得读宿主机文件系统给模型。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from io import BytesIO
from typing import Protocol, runtime_checkable

from PIL import Image


@runtime_checkable
class GuestDesktop(Protocol):
    """桌面 VM（或它的测试替身）对外暴露的最小能力。"""

    def identity(self) -> str:
        """guest 侧主机名。测试用它证明 shell 没落到评测宿主机。"""

    def capture_png(self) -> tuple[bytes, int, int]:
        """返回 (PNG 字节, native_width, native_height)。"""

    def click(self, x: int, y: int, button: str) -> None: ...

    def double_click(self, x: int, y: int) -> None: ...

    def move(self, x: int, y: int) -> None: ...

    def scroll(self, x: int, y: int, dx: int, dy: int) -> None: ...

    def type_text(self, text: str) -> None: ...

    def key(self, keys: list[str]) -> None: ...

    def wait(self, seconds: float) -> None: ...

    def terminate(self, status: str) -> None: ...

    def run_bash(self, command: str, timeout: float | None) -> str:
        """在 guest 内执行。返回 stdout+stderr 文本。"""


def _solid_png(width: int, height: int, rgb: tuple[int, int, int] = (32, 96, 160)) -> bytes:
    image = Image.new("RGB", (width, height), rgb)
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


@dataclass
class FakeGuest:
    """内存里的假桌面。hostname 故意与宿主机不同，用来钉死 shell 落点。"""

    hostname: str = "osworld-guest"
    native_width: int = 64
    native_height: int = 64
    clicks: list[tuple[int, int, str]] = field(default_factory=list)
    double_clicks: list[tuple[int, int]] = field(default_factory=list)
    moves: list[tuple[int, int]] = field(default_factory=list)
    scrolls: list[tuple[int, int, int, int]] = field(default_factory=list)
    typed: list[str] = field(default_factory=list)
    keys_pressed: list[list[str]] = field(default_factory=list)
    waits: list[float] = field(default_factory=list)
    shells: list[str] = field(default_factory=list)
    terminated: str | None = None
    action_log: str | None = None

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> FakeGuest:
        env = environ if environ is not None else os.environ
        return cls(
            hostname=env.get("CUA_EVAL_MCP_GUEST_HOSTNAME", "osworld-guest"),
            native_width=int(env.get("CUA_EVAL_MCP_NATIVE_WIDTH", "64")),
            native_height=int(env.get("CUA_EVAL_MCP_NATIVE_HEIGHT", "64")),
            action_log=env.get("CUA_EVAL_MCP_ACTION_LOG"),
        )

    def identity(self) -> str:
        return self.hostname

    def capture_png(self) -> tuple[bytes, int, int]:
        png = _solid_png(self.native_width, self.native_height)
        return png, self.native_width, self.native_height

    def click(self, x: int, y: int, button: str) -> None:
        self.clicks.append((x, y, button))
        self._log({"type": "click", "x": x, "y": y, "button": button})

    def double_click(self, x: int, y: int) -> None:
        self.double_clicks.append((x, y))
        self._log({"type": "double_click", "x": x, "y": y})

    def move(self, x: int, y: int) -> None:
        self.moves.append((x, y))
        self._log({"type": "move", "x": x, "y": y})

    def scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self.scrolls.append((x, y, dx, dy))
        self._log({"type": "scroll", "x": x, "y": y, "dx": dx, "dy": dy})

    def type_text(self, text: str) -> None:
        self.typed.append(text)
        self._log({"type": "type", "text": text})

    def key(self, keys: list[str]) -> None:
        self.keys_pressed.append(list(keys))
        self._log({"type": "key", "keys": list(keys)})

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)
        self._log({"type": "wait", "seconds": seconds})

    def terminate(self, status: str) -> None:
        self.terminated = status
        self._log({"type": "terminate", "status": status})

    def run_bash(self, command: str, timeout: float | None) -> str:
        del timeout
        self.shells.append(command)
        self._log({"type": "shell", "command": command, "hostname": self.hostname})
        stripped = command.strip()
        if stripped in {"hostname", "hostname -s", "uname -n"}:
            return f"{self.hostname}\n"
        return f"[fake-guest {self.hostname}] ran: {command}\n"

    def _log(self, event: dict[str, object]) -> None:
        if not self.action_log:
            return
        with open(self.action_log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def build_guest(environ: dict[str, str] | None = None) -> GuestDesktop:
    env = environ if environ is not None else dict(os.environ)
    backend = env.get("CUA_EVAL_MCP_BACKEND", "fake")
    if backend == "fake":
        return FakeGuest.from_env(env)
    if backend == "osworld":
        from cua_eval.benches.osworld_guest import OSWorldGuest

        return OSWorldGuest.from_env(env)
    if backend in {"lucwei_mac", "mac_agent_bench"}:
        from cua_eval.benches.lucwei_mac import LucweiMacGuest

        return LucweiMacGuest.from_env(env)
    if backend == "scienceboard":
        from cua_eval.benches.scienceboard_guest import ScienceBoardGuest

        return ScienceBoardGuest.from_env(env)
    from cua_eval.errors import ConfigError

    raise ConfigError(
        f"CUA_EVAL_MCP_BACKEND={backend} 不是 fake / osworld / lucwei_mac / scienceboard。"
        "不要退化成宿主机 shell。"
    )


__all__ = ["FakeGuest", "GuestDesktop", "build_guest"]
