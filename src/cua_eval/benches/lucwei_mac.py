"""远程 macOS 桌面（lucwei Fleet）的 GuestDesktop。

截图走 Fleet HTTP；键鼠 / guest shell 经 SSH 落到 **Mac 客户机**，不得在评测
宿主机上执行 agent 给出的命令。SSH 只是传输层。

控制面地址、池 id、VM uuid、SSH 登录信息只从环境变量读，不进 git。
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
from cua_eval.benches.osworld_guest import png_size
from cua_eval.errors import ConfigError, InfraError

FLEET_URL_ENV = "CUA_EVAL_MAC_FLEET_URL"
FLEET_POOL_ENV = "CUA_EVAL_MAC_POOL"
FLEET_UUID_ENV = "CUA_EVAL_MAC_VM_UUID"
SSH_HOST_ENV = "CUA_EVAL_MAC_SSH_HOST"
SSH_PORT_ENV = "CUA_EVAL_MAC_SSH_PORT"
SSH_USER_ENV = "CUA_EVAL_MAC_SSH_USER"
SSH_KEY_ENV = "CUA_EVAL_MAC_SSH_KEY"
SSH_PASSWORD_ENV_NAME = "CUA_EVAL_MAC_SSH_PASSWORD_ENV"


def fleet_url(environ: dict[str, str] | None = None) -> str:
    env = environ if environ is not None else dict(os.environ)
    return env.get(FLEET_URL_ENV, "").strip().rstrip("/")


def fleet_pool(environ: dict[str, str] | None = None) -> str:
    env = environ if environ is not None else dict(os.environ)
    return env.get(FLEET_POOL_ENV, "").strip()


def fleet_vm_uuid(environ: dict[str, str] | None = None) -> str:
    env = environ if environ is not None else dict(os.environ)
    return env.get(FLEET_UUID_ENV, "").strip()


def fleet_headers(pool: str) -> dict[str, str]:
    return {"X-Access-Label": pool, "Accept": "image/png, image/jpeg, application/json"}


def http_get(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float = 8.0,
) -> tuple[int, bytes, str]:
    request = Request(url, method="GET", headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            body = response.read()
            content_type = str(response.headers.get("Content-Type") or "")
            return status, body, content_type
    except HTTPError as exc:
        body = exc.read() if exc.fp is not None else b""
        return int(exc.code), body, str(exc.headers.get("Content-Type") if exc.headers else "")
    except (URLError, TimeoutError, OSError) as exc:
        raise InfraError(f"Fleet HTTP 失败 ({url}): {exc}") from exc


def screenshot_url(base: str, vm_uuid: str) -> str:
    return f"{base.rstrip('/')}/api/v2/vms/{vm_uuid}/screenshot"


def fetch_screenshot_png(
    *,
    base_url: str,
    pool: str,
    vm_uuid: str,
    timeout: float = 8.0,
    getter: Any = http_get,
) -> tuple[bytes, int, int]:
    url = screenshot_url(base_url, vm_uuid)
    status, body, content_type = getter(url, headers=fleet_headers(pool), timeout=timeout)
    if status != 200 or not body:
        raise InfraError(
            f"Fleet 截图失败 HTTP {status} ({url})。池 id 不是口令；"
            "确认 CUA_EVAL_MAC_POOL / CUA_EVAL_MAC_VM_UUID。不得记成模型 0 分。"
        )
    if "json" in content_type.lower():
        raise InfraError(f"Fleet 截图返回了 JSON 而不是图像 ({url})。不得记成模型 0 分。")
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = png_size(body)
        return body, width, height
    try:
        image = Image.open(BytesIO(body)).convert("RGB")
    except OSError as exc:
        raise InfraError(f"Fleet 截图不是可解析的图像: {exc}") from exc
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue(), image.width, image.height


@dataclass
class LucweiMacGuest:
    """远程 Mac 桌面。所有副作用经 Fleet 截图 API 或 SSH 进 guest。"""

    base_url: str
    pool: str
    vm_uuid: str
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_user: str = ""
    ssh_key: str = ""
    ssh_password: str = field(default="", repr=False)
    native_width: int = 1920
    native_height: int = 1080
    http_getter: Any = field(default=http_get, repr=False)
    ssh_runner: Any = field(default=None, repr=False)
    shells: list[str] = field(default_factory=list)
    terminated: str | None = None

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> LucweiMacGuest:
        env = environ if environ is not None else dict(os.environ)
        base = fleet_url(env)
        pool = fleet_pool(env)
        vm_uuid = fleet_vm_uuid(env)
        if not base or not pool or not vm_uuid:
            raise ConfigError(
                "lucwei_mac guest 需要 CUA_EVAL_MAC_FLEET_URL、CUA_EVAL_MAC_POOL、"
                "CUA_EVAL_MAC_VM_UUID。不要把控制面口令或池 id 写进 git。"
            )
        port_raw = env.get(SSH_PORT_ENV, "22").strip() or "22"
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise ConfigError(f"{SSH_PORT_ENV} 必须是整数，得到 {port_raw!r}") from exc
        password_env = env.get(SSH_PASSWORD_ENV_NAME, "").strip()
        return cls(
            base_url=base,
            pool=pool,
            vm_uuid=vm_uuid,
            ssh_host=env.get(SSH_HOST_ENV, "").strip(),
            ssh_port=port,
            ssh_user=env.get(SSH_USER_ENV, "").strip(),
            ssh_key=env.get(SSH_KEY_ENV, "").strip(),
            ssh_password=env.get(password_env, "") if password_env else "",
        )

    def identity(self) -> str:
        return self.run_bash("hostname", timeout=10).strip().splitlines()[0].strip()

    def capture_png(self) -> tuple[bytes, int, int]:
        png, width, height = fetch_screenshot_png(
            base_url=self.base_url,
            pool=self.pool,
            vm_uuid=self.vm_uuid,
            getter=self.http_getter,
        )
        self.native_width, self.native_height = width, height
        return png, width, height

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

    def run_bash(self, command: str, timeout: float | None) -> str:
        self.shells.append(command)
        return self._ssh(command, timeout=30.0 if timeout is None else float(timeout))

    def _exec_gui(self, action: object) -> None:
        script = to_pyautogui(action)  # type: ignore[arg-type]
        wrapped = (
            "import pyautogui\npyautogui.FAILSAFE = False\n"
            + "\n".join(
                line
                for line in script.splitlines()
                if line.strip() and line.strip() != "import pyautogui"
            )
        )
        quoted = shlex.quote(wrapped)
        self._ssh(f"sudo /usr/bin/python3 -c {quoted}", timeout=30.0)
        if isinstance(action, WaitAction):
            time.sleep(min(action.seconds, 0.05))

    def _ssh(self, command: str, *, timeout: float) -> str:
        if self.ssh_runner is not None:
            return str(self.ssh_runner(command, timeout))
        if not self.ssh_host or not self.ssh_user:
            raise ConfigError(
                "远程 Mac 键鼠 / guest shell 需要 CUA_EVAL_MAC_SSH_HOST 与 "
                "CUA_EVAL_MAC_SSH_USER。SSH 只是传到 Mac 客户机的通道，"
                "不得在评测宿主机上执行 agent 命令。"
            )
        ssh = shutil.which("ssh")
        if ssh is None:
            raise InfraError("未找到 ssh 客户端，无法把命令送到 Mac guest")
        args = [
            ssh,
            "-o",
            "BatchMode=no" if self.ssh_password and not self.ssh_key else "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-p",
            str(self.ssh_port),
        ]
        child_env = None
        if self.ssh_password and not self.ssh_key:
            sshpass = shutil.which("sshpass")
            if sshpass is None:
                raise InfraError(
                    "配置了 SSH 密码但未找到 sshpass；请改用 CUA_EVAL_MAC_SSH_KEY，"
                    "或在评测执行机安装 sshpass。"
                )
            args.insert(0, sshpass)
            args.insert(1, "-e")
            child_env = os.environ.copy()
            child_env["SSHPASS"] = self.ssh_password
        if self.ssh_key:
            args.extend(["-i", self.ssh_key])
        args.append(f"{self.ssh_user}@{self.ssh_host}")
        args.append(command)
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=child_env,
            )
        except subprocess.TimeoutExpired as exc:
            raise InfraError(f"SSH 到 Mac guest 超时: {exc}") from exc
        except OSError as exc:
            raise InfraError(f"SSH 到 Mac guest 失败: {exc}") from exc
        text = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            text = f"{text.rstrip()}\n[returncode={proc.returncode}]\n"
        return text


__all__ = [
    "FLEET_POOL_ENV",
    "FLEET_URL_ENV",
    "FLEET_UUID_ENV",
    "LucweiMacGuest",
    "SSH_HOST_ENV",
    "SSH_KEY_ENV",
    "SSH_PASSWORD_ENV_NAME",
    "SSH_PORT_ENV",
    "SSH_USER_ENV",
    "fetch_screenshot_png",
    "fleet_pool",
    "fleet_url",
    "fleet_vm_uuid",
    "http_get",
    "screenshot_url",
]
