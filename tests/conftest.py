"""pytest 钩子：OSWorld 真跑默认 skip。"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    if os.environ.get("CUA_EVAL_OSWORLD") == "1":
        return
    skip = pytest.mark.skip(reason="OSWorld live test requires CUA_EVAL_OSWORLD=1")
    for item in items:
        if item.get_closest_marker("osworld"):
            item.add_marker(skip)
