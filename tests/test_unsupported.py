"""未实现的 compute backend / bench 必须抛对应异常，不能静默落到本机。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cua_eval.backends.compute import run_job
from cua_eval.benches.base import get_bench
from cua_eval.benches.macos import MacOSBench
from cua_eval.benches.windows import WindowsBench
from cua_eval.errors import UnsupportedBenchError, UnsupportedComputeBackend
from cua_eval.orchestrator.run import run_experiment
from cua_eval.schema import BenchId, ComputeBackend, Experiment

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "experiments"


@pytest.mark.parametrize(
    "backend",
    [
        ComputeBackend.WINDOWS_PC,
        ComputeBackend.CLOUD_SINGLE,
        ComputeBackend.SMALL_CLUSTER,
        ComputeBackend.GPU_CLUSTER,
    ],
)
def test_unimplemented_compute_backend_raises(backend: ComputeBackend) -> None:
    with pytest.raises(UnsupportedComputeBackend, match="local_linux"):
        run_job(backend, lambda: None)


def test_local_linux_executes_in_process() -> None:
    assert run_job(ComputeBackend.LOCAL_LINUX, lambda: 41 + 1) == 42


def test_macos_bench_raises_on_prepare_and_run() -> None:
    bench = MacOSBench()
    with pytest.raises(UnsupportedBenchError, match="macos"):
        bench.prepare()
    with pytest.raises(UnsupportedBenchError, match="macos"):
        bench.run_trial("any", agent=None)  # type: ignore[arg-type]


def test_generic_macos_id_is_not_mac_agent_bench() -> None:
    """通用 macos 客户机仍不支持；MacAgentBench 是另一列。"""
    experiment = Experiment.from_yaml(CONFIG_DIR / "smoke_mac_agent_bench.yaml")
    assert experiment.bench is BenchId.MAC_AGENT_BENCH
    assert experiment.bench is not BenchId.MACOS
    dispatched = get_bench(experiment)
    assert dispatched.id == "mac_agent_bench"
    generic = MacOSBench()
    with pytest.raises(UnsupportedBenchError, match="macos"):
        generic.prepare()


def test_windows_bench_raises_on_prepare_and_run() -> None:
    bench = WindowsBench()
    with pytest.raises(UnsupportedBenchError, match="windows"):
        bench.prepare()
    with pytest.raises(UnsupportedBenchError, match="windows"):
        bench.run_trial("any", agent=None)  # type: ignore[arg-type]


def test_run_rejects_unimplemented_compute_backend(tmp_path: Path) -> None:
    experiment = Experiment.from_yaml(CONFIG_DIR / "smoke_fake.yaml").model_copy(
        update={
            "results_dir": tmp_path / "results",
            "compute_backend": ComputeBackend.GPU_CLUSTER,
        }
    )
    with pytest.raises(UnsupportedComputeBackend):
        run_experiment(experiment)
    assert not (tmp_path / "results").exists()
