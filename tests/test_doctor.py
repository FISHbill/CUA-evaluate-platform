"""doctor：缺项非 0 退出，不把 dummy 说成已验证模型。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from cua_eval.cli import app
from cua_eval.doctor import (
    HostFacts,
    evaluate_experiment,
    evaluate_host,
    route_declares_image,
    run_doctor,
)
from cua_eval.schema import Experiment

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "experiments"


def _healthy() -> HostFacts:
    return HostFacts(
        python=(3, 12),
        uv_path="/usr/bin/uv",
        docker_path="/usr/bin/docker",
        kvm_readable=True,
        cpu_count=8,
        ram_gb=32.0,
        disk_free_gb=200.0,
    )


def _weak_vm() -> HostFacts:
    return HostFacts(
        python=(3, 12),
        uv_path="/usr/bin/uv",
        docker_path=None,
        kvm_readable=False,
        cpu_count=4,
        ram_gb=15.0,
        disk_free_gb=20.0,
    )


def test_host_reports_missing_docker_kvm_and_undersized_machine() -> None:
    checks = {c.name: c for c in evaluate_host(_weak_vm())}
    assert checks["python"].ok
    assert not checks["docker"].ok
    assert not checks["/dev/kvm"].ok
    assert not checks["cpu"].ok
    assert not checks["memory"].ok
    assert not checks["disk"].ok
    assert "当前用户不可读" in checks["/dev/kvm"].detail


def test_healthy_host_passes() -> None:
    assert all(c.ok for c in evaluate_host(_healthy()))


def test_dummy_is_not_claimed_verified() -> None:
    experiment = Experiment.from_yaml(CONFIG_DIR / "smoke_fake.yaml")
    checks = evaluate_experiment(experiment, probe=False)
    model = next(c for c in checks if c.name == "model")
    assert model.ok
    assert "不是推理验证" in model.detail
    assert "不能当成已验证" in model.detail


def test_osworld_without_cordis_does_not_fall_back_to_dummy() -> None:
    experiment = Experiment.from_yaml(CONFIG_DIR / "smoke_osworld.yaml")
    checks = {c.name: c for c in evaluate_experiment(experiment, environ={}, probe=False)}
    assert not checks["cordis"].ok
    assert "dummy" in checks["cordis"].detail.lower()
    assert not checks["api_key"].ok


def test_text_only_route_rejected_for_screenshot(tmp_path: Path) -> None:
    cordis = {
        "plugins": [
            {
                "id": "llm",
                "config": {
                    "providers": {
                        "vlm-cloud": {
                            "baseURL": "https://example.invalid/v1",
                            "defaultInput": ["text"],
                            "models": [{"id": "text-only", "input": ["text"]}],
                        }
                    }
                },
            }
        ]
    }
    assert route_declares_image(cordis, "vlm-cloud") is False
    path = tmp_path / "osworld.cordis.yml"
    path.write_text(yaml.safe_dump(cordis), encoding="utf-8")
    base = Experiment.from_yaml(CONFIG_DIR / "smoke_osworld.yaml")
    experiment = base.model_copy(
        update={"agent": base.agent.model_copy(update={"cordis_config": path})}
    )
    checks = {
        c.name: c
        for c in evaluate_experiment(
            experiment, environ={"CUA_EVAL_MODEL_API_KEY": "x"}, probe=False
        )
    }
    assert not checks["route_image"].ok


def test_image_route_accepted(tmp_path: Path) -> None:
    cordis = {
        "plugins": [
            {
                "config": {
                    "providers": {
                        "vlm-cloud": {
                            "baseURL": "https://example.invalid/v1",
                            "defaultInput": ["text", "image"],
                            "models": [{"id": "vlm"}],
                        }
                    }
                }
            }
        ]
    }
    path = tmp_path / "osworld.cordis.yml"
    path.write_text(yaml.safe_dump(cordis), encoding="utf-8")
    base = Experiment.from_yaml(CONFIG_DIR / "smoke_osworld.yaml")
    experiment = base.model_copy(
        update={"agent": base.agent.model_copy(update={"cordis_config": path})}
    )
    checks = {
        c.name: c
        for c in evaluate_experiment(
            experiment, environ={"CUA_EVAL_MODEL_API_KEY": "x"}, probe=False
        )
    }
    assert checks["route_image"].ok
    assert checks["endpoint"].ok
    assert "example.invalid" in checks["endpoint"].detail


def test_doctor_cli_exits_nonzero_on_this_kind_of_vm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cua_eval.doctor.collect_host_facts", _weak_vm)
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "docker" in result.output
    assert "/dev/kvm" in result.output
    assert "不要退化成 dummy" in result.output


def test_doctor_cli_all_green(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cua_eval.doctor.collect_host_facts", _healthy)
    assert run_doctor(None, facts=_healthy()).ok
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
