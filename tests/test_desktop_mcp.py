"""Desktop MCP：工具集随 protocol 变化；shell 落在 guest 而不是宿主机。"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from cua_eval.harness.desktop_mcp import (
    MODEL_SCREEN_SIZE,
    DesktopMcpServer,
    protocol_from_env,
    to_model_jpeg,
)
from cua_eval.harness.guest import FakeGuest
from cua_eval.schema import GuestActions, Observation, Protocol

REPO = Path(__file__).resolve().parents[1]


def _server(
    protocol: Protocol, guest: FakeGuest | None = None
) -> tuple[DesktopMcpServer, FakeGuest]:
    fake = guest or FakeGuest()
    return DesktopMcpServer(protocol, fake, max_screenshot_history=4), fake


@pytest.mark.parametrize(
    ("protocol", "expect_present", "expect_absent"),
    [
        (
            Protocol(
                observation=Observation.SCREENSHOT,
                guest_actions=GuestActions.MOUSE_KEYBOARD,
                guest_shell=True,
            ),
            {"screenshot", "click", "shell", "terminate"},
            set(),
        ),
        (
            Protocol(
                observation=Observation.SCREENSHOT,
                guest_actions=GuestActions.MOUSE_KEYBOARD,
                guest_shell=False,
            ),
            {"screenshot", "click", "terminate"},
            {"shell"},
        ),
        (
            Protocol(
                observation=Observation.TEXT,
                guest_actions=GuestActions.MOUSE_KEYBOARD,
                guest_shell=True,
            ),
            {"click", "shell", "terminate"},
            {"screenshot"},
        ),
        (
            Protocol(
                observation=Observation.TEXT,
                guest_actions=GuestActions.NONE,
                guest_shell=False,
            ),
            {"terminate"},
            {"screenshot", "click", "shell"},
        ),
    ],
)
def test_toolset_follows_protocol(
    protocol: Protocol,
    expect_present: set[str],
    expect_absent: set[str],
) -> None:
    server, _guest = _server(protocol)
    names = set(server.tool_names())
    assert expect_present <= names
    assert names.isdisjoint(expect_absent)


def test_shell_not_registered_when_guest_shell_false() -> None:
    server, _guest = _server(
        Protocol(observation=Observation.SCREENSHOT, guest_shell=False)
    )
    assert "shell" not in server.tool_names()
    result = server.call_tool("shell", {"command": "hostname"})
    assert result["isError"] is True


def test_screenshot_is_jpeg_1920x1080() -> None:
    server, _guest = _server(
        Protocol(observation=Observation.SCREENSHOT, guest_shell=False)
    )
    result = server.call_tool("screenshot", {})
    assert result["isError"] is False
    image_part = next(part for part in result["content"] if part["type"] == "image")
    assert image_part["mimeType"] == "image/jpeg"
    jpeg = base64.standard_b64decode(image_part["data"])
    image = Image.open(BytesIO(jpeg))
    assert image.format == "JPEG"
    assert image.size == MODEL_SCREEN_SIZE


def test_click_coordinates_map_back_to_native_pixels() -> None:
    guest = FakeGuest(native_width=64, native_height=64)
    server, _ = _server(Protocol(observation=Observation.SCREENSHOT), guest)
    server.call_tool("screenshot", {})
    server.call_tool("click", {"x": 960, "y": 540, "button": "left"})
    assert guest.clicks == [(32, 32, "left")]


def test_shell_runs_on_guest_not_host() -> None:
    guest = FakeGuest(hostname="osworld-guest")
    server, _ = _server(
        Protocol(observation=Observation.TEXT, guest_shell=True),
        guest,
    )
    result = server.call_tool("shell", {"command": "hostname"})
    text = result["content"][0]["text"]
    host = socket.gethostname()
    assert "osworld-guest" in text
    assert host not in text.strip().splitlines()[0]
    assert guest.shells == ["hostname"]


def test_history_clips_extra_screenshots() -> None:
    server, _guest = _server(Protocol(observation=Observation.SCREENSHOT))
    attached = 0
    for _ in range(6):
        result = server.call_tool("screenshot", {})
        attached += sum(1 for part in result["content"] if part["type"] == "image")
    assert attached == 4
    assert "history full" in server.call_tool("screenshot", {})["content"][0]["text"]


def test_screenshot_archive_is_written_when_configured(tmp_path: Path) -> None:
    server = DesktopMcpServer(
        Protocol(observation=Observation.SCREENSHOT),
        FakeGuest(),
        max_screenshot_history=1,
        screenshot_dir=tmp_path,
    )
    server.call_tool("screenshot", {})
    server.call_tool("screenshot", {})
    files = sorted(tmp_path.glob("step-*.jpg"))
    assert [path.name for path in files] == ["step-001.jpg", "step-002.jpg"]
    assert all(Image.open(path).size == MODEL_SCREEN_SIZE for path in files)


def test_to_model_jpeg_reports_native_size() -> None:
    png, native_w, native_h = FakeGuest().capture_png()
    jpeg, native, model = to_model_jpeg(png)
    assert native == (native_w, native_h)
    assert model == MODEL_SCREEN_SIZE
    assert jpeg[:2] == b"\xff\xd8"


def test_protocol_from_env_reads_switches() -> None:
    protocol = protocol_from_env(
        {
            "CUA_EVAL_MCP_OBSERVATION": "text",
            "CUA_EVAL_MCP_GUEST_ACTIONS": "none",
            "CUA_EVAL_MCP_GUEST_SHELL": "true",
        }
    )
    assert protocol.observation is Observation.TEXT
    assert protocol.guest_actions is GuestActions.NONE
    assert protocol.guest_shell is True


def test_stdio_lists_tools_and_shell_stays_on_guest(tmp_path: Path) -> None:
    log = tmp_path / "actions.jsonl"
    proc = subprocess.Popen(
        [sys.executable, "-m", "cua_eval.harness.desktop_mcp"],
        cwd=str(REPO),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "CUA_EVAL_MCP_OBSERVATION": "text",
            "CUA_EVAL_MCP_GUEST_ACTIONS": "none",
            "CUA_EVAL_MCP_GUEST_SHELL": "true",
            "CUA_EVAL_MCP_BACKEND": "fake",
            "CUA_EVAL_MCP_GUEST_HOSTNAME": "osworld-guest",
            "CUA_EVAL_MCP_ACTION_LOG": str(log),
        },
    )
    assert proc.stdin is not None
    assert proc.stdout is not None

    def rpc(method: str, params: dict[str, object], msg_id: int) -> dict[str, object]:
        payload = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        assert line, proc.stderr.read() if proc.stderr else "no stdout"
        return json.loads(line)

    try:
        init = rpc(
            "initialize",
            {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test"}},
            1,
        )
        assert init["result"]["serverInfo"]["name"] == "cua-eval-desktop"
        notice = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        proc.stdin.write(json.dumps(notice) + "\n")
        proc.stdin.flush()
        listed = rpc("tools/list", {}, 2)
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert names == {"terminate", "shell"}
        called = rpc("tools/call", {"name": "shell", "arguments": {"command": "hostname"}}, 3)
        text = called["result"]["content"][0]["text"]
        assert "osworld-guest" in text
        assert socket.gethostname() not in text.strip().splitlines()[0]
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=5)
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
    assert events[-1]["type"] == "shell"
    assert events[-1]["hostname"] == "osworld-guest"
