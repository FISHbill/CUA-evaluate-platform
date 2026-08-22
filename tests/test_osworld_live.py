"""OSWorld 真跑。默认 skip，且禁止在测试里下载 qcow2。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cua_eval.benches.osworld import OSWorldBench, default_osworld_root, ubuntu_qcow2
from cua_eval.errors import ConfigError
from cua_eval.schema import Experiment

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "experiments" / "smoke_osworld.yaml"


@pytest.mark.osworld
def test_osworld_opt_in_does_not_download_images() -> None:
    assert os.environ.get("CUA_EVAL_OSWORLD") == "1"
    root = default_osworld_root()
    qcow2 = ubuntu_qcow2(root)
    if qcow2.is_file():
        pytest.skip("qcow2 已就位；完整真跑是 M6，不在单测里起桌面 VM")
    experiment = Experiment.from_yaml(CONFIG)
    with pytest.raises(ConfigError, match=r"qcow2|checkout|Docker|KVM"):
        OSWorldBench(experiment, root=root).prepare()
