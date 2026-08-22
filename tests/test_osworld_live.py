"""OSWorld 真跑。默认 skip，且禁止在测试里下载 qcow2。"""

from __future__ import annotations

import os

import pytest


@pytest.mark.osworld
def test_osworld_opt_in_does_not_download_images() -> None:
    assert os.environ.get("CUA_EVAL_OSWORLD") == "1"
    pytest.skip("live OSWorld runner is M5/M6; this test only wires the skip marker")
