"""MacAgentBench 真跑。默认 skip，且禁止在测试里下载 HDD。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cua_eval.benches.mac_agent_bench import MacAgentBench, default_root
from cua_eval.errors import ConfigError
from cua_eval.schema import Experiment

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "experiments"
CONFIG = CONFIG_DIR / "smoke_mac_agent_bench.yaml"


@pytest.mark.mac_agent_bench
def test_mac_opt_in_does_not_download_images() -> None:
    assert os.environ.get("CUA_EVAL_MAC_AGENT_BENCH") == "1"
    experiment = Experiment.from_yaml(CONFIG)
    root = default_root()
    with pytest.raises(ConfigError, match=r"checkout|Fleet|HDD|dummy"):
        MacAgentBench(experiment, root=root).prepare()
