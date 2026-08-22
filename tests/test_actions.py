"""中立动作的序列化、协议校验、以及到 pyautogui 的映射。"""

from __future__ import annotations

import pytest

from cua_eval.actions import (
    Action,
    ClickAction,
    DoubleClickAction,
    GUI_ACTION_TYPES,
    KeyAction,
    MoveAction,
    ScrollAction,
    ShellAction,
    TerminateAction,
    TypeAction,
    WaitAction,
    parse_action,
    scale_action,
    scale_pixels,
    to_pyautogui,
    validate_against_protocol,
)
from cua_eval.errors import ModelError
from cua_eval.schema import GuestActions, Observation, Protocol


class TestRoundTrip:
    @pytest.mark.parametrize(
        "action",
        [
            ClickAction(x=10, y=20, button="right"),
            DoubleClickAction(x=1, y=2),
            MoveAction(x=3, y=4),
            ScrollAction(x=5, y=6, dx=-1, dy=3),
            TypeAction(text="hello"),
            KeyAction(keys=["ctrl", "s"]),
            WaitAction(seconds=0.5),
            TerminateAction(status="fail"),
            ShellAction(command="ls", timeout=2.0),
        ],
    )
    def test_json_round_trip(self, action: Action) -> None:
        dumped = action.model_dump(mode="json")
        restored = parse_action(dumped)
        assert restored == action


class TestPyautoguiMapping:
    def test_click_uses_pixel_coordinates(self) -> None:
        snippet = to_pyautogui(ClickAction(x=100, y=200))
        assert "pyautogui.click(100, 200" in snippet
        assert "import pyautogui" in snippet

    def test_hotkey(self) -> None:
        snippet = to_pyautogui(KeyAction(keys=["ctrl", "s"]))
        assert "pyautogui.hotkey('ctrl', 's')" in snippet

    def test_single_key_uses_press(self) -> None:
        snippet = to_pyautogui(KeyAction(keys=["enter"]))
        assert "pyautogui.press('enter')" in snippet

    def test_wait_is_sleep_not_shell(self) -> None:
        snippet = to_pyautogui(WaitAction(seconds=1.5))
        assert "time.sleep(1.5)" in snippet
        assert "bash" not in snippet
        assert "os.system" not in snippet

    def test_terminate_is_not_pyautogui(self) -> None:
        with pytest.raises(ValueError, match="pyautogui"):
            to_pyautogui(TerminateAction())

    def test_shell_is_not_mapped_to_host_bash(self) -> None:
        with pytest.raises(ValueError, match="run_bash_script"):
            to_pyautogui(ShellAction(command="rm -rf /"))


class TestGuiActionsHaveNoShellFields:
    def test_gui_models_do_not_carry_command_or_script(self) -> None:
        forbidden = {"command", "script", "bash", "shell"}
        for cls in GUI_ACTION_TYPES:
            overlap = forbidden & set(cls.model_fields)
            assert not overlap, f"{cls.__name__} 含 shell 类字段: {overlap}"


class TestCoordinateScaling:
    def test_identity_when_sizes_match(self) -> None:
        action = ClickAction(x=10, y=20)
        assert scale_action(action, from_size=(64, 64), to_size=(64, 64)) == action

    def test_maps_back_to_original_pixels(self) -> None:
        # 模型看到 1280×720，执行面是 1920×1080。
        x, y = scale_pixels(640, 360, from_size=(1280, 720), to_size=(1920, 1080))
        assert (x, y) == (960, 540)

    def test_scale_action_updates_click(self) -> None:
        scaled = scale_action(
            ClickAction(x=32, y=32),
            from_size=(64, 64),
            to_size=(1920, 1080),
        )
        assert isinstance(scaled, ClickAction)
        assert scaled.x == 960
        assert scaled.y == 540


class TestProtocolGate:
    def test_shell_rejected_when_guest_shell_off(self) -> None:
        protocol = Protocol(observation=Observation.SCREENSHOT, guest_shell=False)
        with pytest.raises(ModelError, match="guest_shell"):
            validate_against_protocol(ShellAction(command="echo x"), protocol)

    def test_shell_allowed_when_guest_shell_on(self) -> None:
        protocol = Protocol(observation=Observation.SCREENSHOT, guest_shell=True)
        validate_against_protocol(ShellAction(command="echo x"), protocol)

    def test_click_rejected_when_guest_actions_none(self) -> None:
        protocol = Protocol(
            observation=Observation.TEXT,
            guest_actions=GuestActions.NONE,
        )
        with pytest.raises(ModelError, match="guest_actions"):
            validate_against_protocol(ClickAction(x=0, y=0), protocol)
