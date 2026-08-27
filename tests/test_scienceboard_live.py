"""ScienceBoard 真跑。默认 skip，且禁止在测试里下载 VM.zip。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cua_eval.benches.scienceboard import ScienceBoardBench, default_root
from cua_eval.errors import ConfigError
from cua_eval.schema import Experiment

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "experiments" / "smoke_scienceboard.yaml"


@pytest.mark.scienceboard
def test_scienceboard_opt_in_does_not_download_images() -> None:
    assert os.environ.get("CUA_EVAL_SCIENCEBOARD") == "1"
    experiment = Experiment.from_yaml(CONFIG)
    root = default_root()
    with pytest.raises(ConfigError, match=r"checkout|VM.zip|vmrun|dummy"):
        ScienceBoardBench(experiment, root=root).prepare()
