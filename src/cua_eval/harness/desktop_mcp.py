"""Desktop MCP server：把中立键鼠 / 截图 / guest shell 暴露给 dsh。

stdio JSON-RPC（NDJSON；也接受 LSP 风格的 Content-Length 帧）。工具集由
`protocol` 四个开关决定：`guest_shell=false` 时 **完全不注册** `shell`。

截图送给模型的永远是 JPEG 1920×1080（见 AGENTS.md 6.4）。若 guest 原图尺寸
不同，点击坐标按缩放比映射回原图像素再执行。
"""

from __future__ import annotations

import base64
import json
import os
import sys
from io import BytesIO
from typing import Any, TextIO

from PIL import Image

from cua_eval.actions import (
    ClickAction,
    DoubleClickAction,
    KeyAction,
    MoveAction,
    ScrollAction,
    ShellAction,
    TerminateAction,
    TypeAction,
    WaitAction,
    parse_action,
    scale_action,
    validate_against_protocol,
)
from cua_eval.errors import ConfigError
from cua_eval.harness.guest import GuestDesktop, build_guest
from cua_eval.schema import GuestActions, Observation, Protocol

MODEL_SCREEN_SIZE: tuple[int, int] = (1920, 1080)
JPEG_QUALITY = 85
SERVER_NAME = "cua-eval-desktop"
PROTOCOL_VERSION = "2024-11-05"

_PIXEL_ACTIONS = {"click", "double_click", "move", "scroll"}


def protocol_from_env(environ: dict[str, str] | None = None) -> Protocol:
    env = environ if environ is not None else os.environ
    shell_raw = env.get("CUA_EVAL_MCP_GUEST_SHELL", "false").strip().lower()
    return Protocol(
        observation=Observation(env.get("CUA_EVAL_MCP_OBSERVATION", "screenshot")),
        guest_actions=GuestActions(env.get("CUA_EVAL_MCP_GUEST_ACTIONS", "mouse_keyboard")),
        guest_shell=shell_raw in {"1", "true", "yes", "on"},
        harness_shell=False,
    )


def to_model_jpeg(png_bytes: bytes) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
    """PNG → JPEG 1920×1080。返回 (jpeg, native_size, model_size)。"""
    image = Image.open(BytesIO(png_bytes)).convert("RGB")
    native = (image.width, image.height)
    if native != MODEL_SCREEN_SIZE:
        image = image.resize(MODEL_SCREEN_SIZE, Image.Resampling.BILINEAR)
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue(), native, MODEL_SCREEN_SIZE


def _tool_schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def tool_specs(protocol: Protocol) -> list[dict[str, Any]]:
    """当前协议下注册的工具。`shell` 只在 guest_shell=true 时出现。"""
    tools: list[dict[str, Any]] = []
    if protocol.observation is Observation.SCREENSHOT:
        tools.append(
            _tool_schema(
                "screenshot",
                "Capture the desktop VM screen as a JPEG (1920x1080). "
                "Coordinates of later mouse actions are pixels on this image.",
                {},
                [],
            )
        )
    if protocol.guest_actions is GuestActions.MOUSE_KEYBOARD:
        xy = {
            "x": {
                "type": "integer",
                "minimum": 0,
                "description": "Pixel x on the 1920x1080 screenshot.",
            },
            "y": {
                "type": "integer",
                "minimum": 0,
                "description": "Pixel y on the 1920x1080 screenshot.",
            },
        }
        tools.extend(
            [
                _tool_schema(
                    "click",
                    "Click at pixel coordinates on the screenshot.",
                    {**xy, "button": {"type": "string", "enum": ["left", "right", "middle"]}},
                    ["x", "y"],
                ),
                _tool_schema("double_click", "Double-click at pixel coordinates.", xy, ["x", "y"]),
                _tool_schema("move", "Move the pointer to pixel coordinates.", xy, ["x", "y"]),
                _tool_schema(
                    "scroll",
                    "Scroll at pixel coordinates.",
                    {**xy, "dx": {"type": "integer"}, "dy": {"type": "integer"}},
                    ["x", "y"],
                ),
                _tool_schema(
                    "type",
                    "Type text into the focused guest widget.",
                    {"text": {"type": "string"}},
                    ["text"],
                ),
                _tool_schema(
                    "key",
                    "Press a key or key chord inside the guest, e.g. [\"ctrl\", \"s\"].",
                    {
                        "keys": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        }
                    },
                    ["keys"],
                ),
                _tool_schema(
                    "wait",
                    "Wait before the next action.",
                    {"seconds": {"type": "number", "minimum": 0}},
                    ["seconds"],
                ),
            ]
        )
    tools.append(
        _tool_schema(
            "terminate",
            "End the task. status=success if the instruction is done, else fail.",
            {"status": {"type": "string", "enum": ["success", "fail"]}},
            [],
        )
    )
    if protocol.guest_shell:
        tools.append(
            _tool_schema(
                "shell",
                "Run a bash command INSIDE the desktop VM (guest). "
                "This is not the eval host. Do not expect host paths or host env vars.",
                {
                    "command": {"type": "string", "minLength": 1},
                    "timeout": {"type": "number", "exclusiveMinimum": 0},
                },
                ["command"],
            )
        )
    return tools


class DesktopMcpServer:
    def __init__(
        self,
        protocol: Protocol,
        guest: GuestDesktop,
        *,
        max_screenshot_history: int = 20,
    ) -> None:
        if max_screenshot_history <= 0:
            raise ConfigError("max_screenshot_history 必须为正")
        self.protocol = protocol
        self.guest = guest
        self.max_screenshot_history = max_screenshot_history
        self._tools = {spec["name"]: spec for spec in tool_specs(protocol)}
        self._native_size = MODEL_SCREEN_SIZE
        self._model_size = MODEL_SCREEN_SIZE
        self._screenshots_taken = 0
        self._screenshots_sent = 0

    def tool_names(self) -> list[str]:
        return list(self._tools)

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        msg_id = message.get("id")
        if not isinstance(method, str):
            if msg_id is None:
                return None
            return _rpc_error(msg_id, -32600, "invalid request")
        if msg_id is None:
            return None
        raw_params = message.get("params")
        params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
        try:
            if method == "initialize":
                return _rpc_result(msg_id, self._initialize(params))
            if method == "ping":
                return _rpc_result(msg_id, {})
            if method == "tools/list":
                return _rpc_result(msg_id, {"tools": list(self._tools.values())})
            if method == "tools/call":
                name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                return _rpc_result(msg_id, self.call_tool(name, arguments))
            return _rpc_error(msg_id, -32601, f"method not found: {method}")
        except Exception as exc:
            return _rpc_error(msg_id, -32000, str(exc))

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if isinstance(requested, str) and requested else PROTOCOL_VERSION
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": "0.1.0"},
        }

    def call_tool(self, name: str, arguments: object) -> dict[str, Any]:
        if name not in self._tools:
            return {
                "content": [
                    {"type": "text", "text": f"tool {name!r} is not registered for this protocol"}
                ],
                "isError": True,
            }
        args = arguments if isinstance(arguments, dict) else {}
        if name == "screenshot":
            return self._screenshot()
        payload = {"type": name, **args}
        action = parse_action(payload)
        validate_against_protocol(action, self.protocol)
        if name in _PIXEL_ACTIONS:
            action = scale_action(action, from_size=self._model_size, to_size=self._native_size)
        text = self._dispatch(action)
        return {"content": [{"type": "text", "text": text}], "isError": False}

    def _screenshot(self) -> dict[str, Any]:
        png, native_w, native_h = self.guest.capture_png()
        jpeg, native, model = to_model_jpeg(png)
        self._native_size = native
        self._model_size = model
        self._screenshots_taken += 1
        if self._screenshots_sent >= self.max_screenshot_history:
            note = (
                f"screenshot history full ({self.max_screenshot_history}); "
                "new frame not attached. native="
                f"{native_w}x{native_h} model={model[0]}x{model[1]} "
                f"screenshots_sent={self._screenshots_sent}"
            )
            return {"content": [{"type": "text", "text": note}], "isError": False}
        self._screenshots_sent += 1
        note = (
            f"screenshot jpeg {model[0]}x{model[1]} native={native_w}x{native_h} "
            f"screenshots_sent={self._screenshots_sent}"
        )
        return {
            "content": [
                {"type": "text", "text": note},
                {
                    "type": "image",
                    "data": base64.standard_b64encode(jpeg).decode("ascii"),
                    "mimeType": "image/jpeg",
                },
            ],
            "isError": False,
        }

    def _dispatch(self, action: object) -> str:
        match action:
            case ClickAction(x=x, y=y, button=button):
                self.guest.click(x, y, button)
                return f"clicked {button} at native ({x},{y})"
            case DoubleClickAction(x=x, y=y):
                self.guest.double_click(x, y)
                return f"double-clicked native ({x},{y})"
            case MoveAction(x=x, y=y):
                self.guest.move(x, y)
                return f"moved to native ({x},{y})"
            case ScrollAction(x=x, y=y, dx=dx, dy=dy):
                self.guest.scroll(x, y, dx, dy)
                return f"scrolled native ({x},{y}) dx={dx} dy={dy}"
            case TypeAction(text=text):
                self.guest.type_text(text)
                return f"typed {len(text)} chars"
            case KeyAction(keys=keys):
                self.guest.key(keys)
                return f"pressed {keys}"
            case WaitAction(seconds=seconds):
                self.guest.wait(seconds)
                return f"waited {seconds}s"
            case TerminateAction(status=status):
                self.guest.terminate(status)
                return f"terminated status={status}"
            case ShellAction(command=command, timeout=timeout):
                output = self.guest.run_bash(command, timeout)
                return output
            case _:
                raise ConfigError(f"unsupported action {type(action)!r}")


def _rpc_result(msg_id: object, result: object) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _read_message(stream: TextIO) -> tuple[dict[str, Any] | None, str]:
    """Return (message, framing) where framing is lsp or ndjson."""
    line = stream.readline()
    if line == "":
        return None, "ndjson"
    if line.lower().startswith("content-length:"):
        length = int(line.split(":", 1)[1].strip())
        while True:
            header = stream.readline()
            if header in ("", "\n", "\r\n"):
                break
        body = stream.read(length)
        parsed = json.loads(body)
        msg = parsed if isinstance(parsed, dict) else None
        return msg, "lsp"
    stripped = line.strip()
    if not stripped:
        return _read_message(stream)
    parsed = json.loads(stripped)
    msg = parsed if isinstance(parsed, dict) else None
    return msg, "ndjson"


def _write_message(stream: TextIO, obj: dict[str, Any], framing: str) -> None:
    payload = json.dumps(obj, ensure_ascii=False)
    if framing == "lsp":
        data = payload.encode("utf-8")
        header = ("Content-Length: %d" % len(data) + chr(13) + chr(10) + chr(13) + chr(10)).encode("ascii")
        buf = getattr(stream, "buffer", None)
        if buf is not None:
            buf.write(header + data)
            buf.flush()
        else:
            stream.write(header.decode("ascii") + payload)
            stream.flush()
        return
    stream.write(payload + chr(10))
    stream.flush()


def serve_stdio(
    server: DesktopMcpServer,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> None:
    incoming = stdin if stdin is not None else sys.stdin
    outgoing = stdout if stdout is not None else sys.stdout
    while True:
        framing = "ndjson"
        try:
            message, framing = _read_message(incoming)
        except json.JSONDecodeError as exc:
            _write_message(outgoing, _rpc_error(None, -32700, f"parse error: {exc}"), framing)
            continue
        if message is None:
            return
        reply = server.handle(message)
        if reply is None:
            continue
        _write_message(outgoing, reply, framing)

def main() -> None:
    protocol = protocol_from_env()
    guest = build_guest()
    max_hist = int(os.environ.get("CUA_EVAL_MCP_MAX_SCREENSHOT_HISTORY", "20"))
    server = DesktopMcpServer(protocol, guest, max_screenshot_history=max_hist)
    serve_stdio(server)


if __name__ == "__main__":
    main()
