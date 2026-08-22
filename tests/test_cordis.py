"""自备 osworld.cordis.yml 的护栏：不许把宿主 bash 交给模型。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cua_eval.errors import ConfigError
from cua_eval.harness.cordis import (
    FORBIDDEN_LLM_PLUGINS,
    FORBIDDEN_PLUGINS,
    MAX_IMAGE_DIMENSION,
    MAX_REQUEST_IMAGE_BYTES,
    REQUIRED_PLUGINS,
    assert_osworld_cordis_guards,
    load_cordis_yaml,
    plugin_names,
)

REPO = Path(__file__).resolve().parents[1]
CORDIS = REPO / "configs" / "dsh" / "osworld.cordis.yml"
LOCK = REPO / "uv.lock"


def test_committed_cordis_passes_guards() -> None:
    raw = load_cordis_yaml(CORDIS, environ={})
    assert_osworld_cordis_guards(raw)
    names = plugin_names(raw)
    for required in REQUIRED_PLUGINS:
        assert required in names
    for forbidden in (*FORBIDDEN_PLUGINS, *FORBIDDEN_LLM_PLUGINS):
        assert forbidden not in names


def test_image_limits_are_explicit() -> None:
    text = CORDIS.read_text(encoding="utf-8")
    assert f"maxImageDimension: {MAX_IMAGE_DIMENSION}" in text
    assert f"maxRequestImageBytes: {MAX_REQUEST_IMAGE_BYTES}" in text
    assert "defaultInput: [text, image]" in text


def test_guard_rejects_host_bash(tmp_path: Path) -> None:
    src = CORDIS.read_text(encoding="utf-8")
    poisoned = src + "\n- id: bash\n  name: '@deepseek-ai/dsh-bash-local'\n"
    path = tmp_path / "bad.cordis.yml"
    path.write_text(poisoned, encoding="utf-8")
    raw = load_cordis_yaml(path, environ={})
    with pytest.raises(ConfigError, match="dsh-bash-local"):
        assert_osworld_cordis_guards(raw)


def test_pydantic_lock_is_stable() -> None:
    """T4.1：不得用 --prerelease=allow 把 pydantic 拉成 beta。"""
    text = LOCK.read_text(encoding="utf-8")
    match = re.search(r'^name = "pydantic"\nversion = "([^"]+)"', text, re.MULTILINE)
    assert match is not None
    version = match.group(1)
    assert re.search(r"(a|b|rc)\d*$", version) is None, version
    sdk = re.search(r'^name = "deepseek-harness-sdk"\nversion = "([^"]+)"', text, re.MULTILINE)
    assert sdk is not None
    assert sdk.group(1) == "0.1.0rc7"
