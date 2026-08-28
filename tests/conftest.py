"""pytest 钩子：真实 bench 默认 skip。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

_LIVE_MARKERS = (
    ("osworld", "CUA_EVAL_OSWORLD", "OSWorld live test requires CUA_EVAL_OSWORLD=1"),
    (
        "scienceboard",
        "CUA_EVAL_SCIENCEBOARD",
        "ScienceBoard live test requires CUA_EVAL_SCIENCEBOARD=1",
    ),
    (
        "mac_agent_bench",
        "CUA_EVAL_MAC_AGENT_BENCH",
        "MacAgentBench live test requires CUA_EVAL_MAC_AGENT_BENCH=1",
    ),
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    for item in items:
        for mark, env_name, reason in _LIVE_MARKERS:
            if item.get_closest_marker(mark) and os.environ.get(env_name) != "1":
                item.add_marker(pytest.mark.skip(reason=reason))
