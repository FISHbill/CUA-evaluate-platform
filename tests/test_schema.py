"""类型层测试。

重点不是覆盖率，而是把 AGENTS.md 里那些「漏了就会得到不可信结果」的约束钉住：
协议四开关、image modality 强制、密钥字面值拒绝、bench 必须 pin、infra_error
不得带分数。
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from cua_eval.errors import (
    ConfigError,
    CuaEvalError,
    HarnessError,
    InfraError,
    ModelError,
    UnsupportedBenchError,
    UnsupportedComputeBackend,
)
from cua_eval.schema import (
    AgentSpec,
    BenchId,
    ComputeBackend,
    EndpointKind,
    Experiment,
    FailureClass,
    GuestActions,
    HarnessId,
    Limits,
    Metric,
    MetricSet,
    Modality,
    ModelBackend,
    ModelSpec,
    Observation,
    Protocol,
    RunRecord,
    TrialResult,
)

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "experiments"


def _executable_code(path: Path) -> str:
    """返回去掉注释与 docstring 之后的源码。

    用于厂商中立检查：解释性文字里点名厂商是允许的，出现在字面量或标识符里不行。
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ) and (doc := ast.get_docstring(node, clean=False)):
            docstrings.add(doc)

    kept: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING:
            try:
                value = ast.literal_eval(token.string)
            except (ValueError, SyntaxError):
                value = None
            if isinstance(value, str) and value in docstrings:
                continue
        kept.append(token.string)
    return "\n".join(kept)


def _dummy_model(**overrides: object) -> ModelSpec:
    payload: dict[str, object] = {
        "backend": ModelBackend.DUMMY,
        "name": "dummy-fixed-actions",
        "input_modalities": [Modality.TEXT, Modality.IMAGE],
    }
    payload.update(overrides)
    return ModelSpec.model_validate(payload)


def _openai_model(**overrides: object) -> ModelSpec:
    payload: dict[str, object] = {
        "backend": ModelBackend.OPENAI_COMPAT,
        "name": "unit-test-vlm",
        "endpoint_kind": EndpointKind.API,
        "provider_route": "vlm-cloud",
        "api_key_env": "CUA_EVAL_MODEL_API_KEY",
        "input_modalities": [Modality.TEXT, Modality.IMAGE],
    }
    payload.update(overrides)
    return ModelSpec.model_validate(payload)


class TestLimits:
    def test_documented_defaults(self) -> None:
        limits = Limits()
        assert limits.max_steps == 50
        assert limits.num_envs == 1
        assert limits.task_timeout_seconds > 0
        # 截图历史深度必须是配置项，不能写死（AGENTS.md 6.4）。
        assert limits.max_screenshot_history == 20

    @pytest.mark.parametrize("field", ["max_steps", "num_envs", "max_screenshot_history"])
    def test_rejects_non_positive(self, field: str) -> None:
        with pytest.raises(ValidationError):
            Limits.model_validate({field: 0})


class TestProtocol:
    def test_four_independent_switches(self) -> None:
        protocol = Protocol(observation=Observation.SCREENSHOT)
        assert protocol.observation is Observation.SCREENSHOT
        assert protocol.guest_actions is GuestActions.MOUSE_KEYBOARD
        assert protocol.guest_shell is False
        assert protocol.harness_shell is False

    def test_guest_shell_may_be_enabled(self) -> None:
        protocol = Protocol(observation=Observation.SCREENSHOT, guest_shell=True)
        assert protocol.guest_shell is True

    def test_harness_shell_is_rejected(self) -> None:
        """宿主机 shell 改不动 guest 状态，还会泄露密钥并能篡改 evaluator。"""
        with pytest.raises(ValidationError, match="guest_shell"):
            Protocol(observation=Observation.SCREENSHOT, harness_shell=True)

    def test_slug_separates_protocols(self) -> None:
        gui_only = Protocol(observation=Observation.SCREENSHOT).slug
        with_shell = Protocol(observation=Observation.SCREENSHOT, guest_shell=True).slug
        text_only = Protocol(observation=Observation.TEXT).slug
        # 三种协议的成绩不得进 Table 1 的同一列，所以 slug 必须两两不同。
        assert len({gui_only, with_shell, text_only}) == 3
        assert "guest_shell" in with_shell
        assert "guest_shell" not in gui_only


class TestModelSpec:
    def test_openai_compat_requires_endpoint_fields(self) -> None:
        with pytest.raises(ValidationError, match="endpoint_kind"):
            ModelSpec(backend=ModelBackend.OPENAI_COMPAT, name="some-model")

    def test_dummy_must_not_carry_endpoint_fields(self) -> None:
        with pytest.raises(ValidationError, match="provider_route"):
            _dummy_model(provider_route="vlm-cloud")

    def test_defaults_to_text_only(self) -> None:
        """未声明 image 的 route 收不到截图，所以默认必须是保守的纯文本。"""
        model = ModelSpec(backend=ModelBackend.DUMMY, name="d")
        assert model.input_modalities == [Modality.TEXT]
        assert model.accepts_images is False

    def test_rejects_duplicate_modalities(self) -> None:
        with pytest.raises(ValidationError, match="重复"):
            _dummy_model(input_modalities=[Modality.TEXT, Modality.TEXT])

    def test_rejects_empty_modalities(self) -> None:
        with pytest.raises(ValidationError):
            _dummy_model(input_modalities=[])

    @pytest.mark.parametrize(
        "value",
        [
            "sk-abcdef0123456789",
            "ghp_abcdefghijklmnop",
            "hf_abcdefghijklmnop",
            "AIzaSyABCDEFG",
            "Bearer abcdef",
        ],
    )
    def test_rejects_secret_looking_values(self, value: str) -> None:
        """密钥只从环境变量读，不得进 git / YAML / 桌面 VM。"""
        with pytest.raises(ValidationError, match="密钥"):
            _openai_model(api_key_env=value)

    @pytest.mark.parametrize("value", ["lowercase_name", "has-dash", "1LEADING_DIGIT", "A" * 200])
    def test_rejects_non_env_names(self, value: str) -> None:
        with pytest.raises(ValidationError, match="环境变量名"):
            _openai_model(api_key_env=value)

    def test_accepts_env_var_name(self) -> None:
        assert _openai_model(api_key_env="CUA_EVAL_MODEL_API_KEY").api_key_env


class TestAgentSpec:
    def _agent(self, **overrides: object) -> AgentSpec:
        payload: dict[str, object] = {
            "model": _dummy_model(),
            "harness": HarnessId.STUB,
            "protocol": Protocol(observation=Observation.SCREENSHOT),
        }
        payload.update(overrides)
        return AgentSpec.model_validate(payload)

    def test_screenshot_requires_image_modality(self) -> None:
        """observation=screenshot 配纯文本 route 是自相矛盾，必须在配置期就拒掉。"""
        with pytest.raises(ValidationError, match="image"):
            self._agent(model=_dummy_model(input_modalities=[Modality.TEXT]))

    def test_text_observation_allows_text_only_model(self) -> None:
        agent = self._agent(
            model=_dummy_model(input_modalities=[Modality.TEXT]),
            protocol=Protocol(observation=Observation.TEXT),
        )
        assert agent.model.accepts_images is False

    def test_text_observation_also_allows_vision_model(self) -> None:
        """视觉模型跑纯文本协议是合法的对照列，不该被拦。"""
        agent = self._agent(protocol=Protocol(observation=Observation.TEXT))
        assert agent.model.accepts_images is True

    def test_deepseek_harness_requires_pin_and_cordis(self) -> None:
        with pytest.raises(ValidationError, match="cordis_config"):
            self._agent(harness=HarnessId.DEEPSEEK_HARNESS)

    def test_stub_harness_rejects_cordis(self) -> None:
        with pytest.raises(ValidationError, match="cordis_config"):
            self._agent(cordis_config=Path("configs/dsh/osworld.cordis.yml"))

    def test_limits_default_is_applied(self) -> None:
        assert self._agent().limits.max_steps == 50


class TestTrialResult:
    def test_infra_error_must_not_carry_score(self) -> None:
        """环境故障记 0 分等于把它算成模型无能，会污染 Table 1。"""
        with pytest.raises(ValidationError, match="infra_error"):
            TrialResult(task_id="t", failure_class=FailureClass.INFRA_ERROR, score=0.0)

    def test_infra_error_without_score_is_valid(self) -> None:
        trial = TrialResult(task_id="t", failure_class=FailureClass.INFRA_ERROR)
        assert trial.score is None
        assert trial.counts_toward_score is False

    @pytest.mark.parametrize(
        "failure_class",
        [FailureClass.OK, FailureClass.TASK_FAIL, FailureClass.MODEL_ERROR],
    )
    def test_other_classes_require_score(self, failure_class: FailureClass) -> None:
        with pytest.raises(ValidationError, match="score"):
            TrialResult(task_id="t", failure_class=failure_class)

    @pytest.mark.parametrize("score", [-0.1, 1.1])
    def test_score_is_bounded_zero_to_one(self, score: float) -> None:
        """逐题分数是官方 evaluator 原样返回的 0.0–1.0，不是百分数。"""
        with pytest.raises(ValidationError):
            TrialResult(task_id="t", failure_class=FailureClass.OK, score=score)


class TestMetricSet:
    def test_counts_must_add_up(self) -> None:
        with pytest.raises(ValidationError, match="total"):
            MetricSet(scored=1, infra_skipped=1, total=3)

    def test_get_returns_named_metric(self) -> None:
        metric_set = MetricSet(
            metrics=[Metric(name="success_rate", value=50.0, higher_is_better=True)],
            scored=2,
            total=2,
        )
        found = metric_set.get("success_rate")
        assert found is not None
        assert found.value == 50.0
        assert metric_set.get("asr") is None

    def test_asr_style_metric_can_be_lower_is_better(self) -> None:
        metric = Metric(name="asr", value=16.4, higher_is_better=False, unit="percent")
        assert metric.higher_is_better is False


class TestExperiment:
    def _experiment(self, **overrides: object) -> Experiment:
        payload: dict[str, object] = {
            "name": "unit",
            "bench": BenchId.FAKE,
            "task_ids": ["t1"],
            "compute_backend": ComputeBackend.LOCAL_LINUX,
            "agent": {
                "model": _dummy_model(),
                "harness": HarnessId.STUB,
                "protocol": {"observation": Observation.SCREENSHOT},
            },
        }
        payload.update(overrides)
        return Experiment.model_validate(payload)

    def test_retention_default_is_fourteen_days(self) -> None:
        assert self._experiment().artifact_retention_days == 14

    def test_rejects_duplicate_task_ids(self) -> None:
        with pytest.raises(ValidationError, match="重复"):
            self._experiment(task_ids=["t1", "t1"])

    def test_rejects_empty_task_ids(self) -> None:
        with pytest.raises(ValidationError):
            self._experiment(task_ids=[])

    def test_rejects_unknown_field(self) -> None:
        """extra=forbid 才能挡住 YAML 里的拼写错误。"""
        with pytest.raises(ValidationError):
            self._experiment(bench_verison="typo")

    def test_osworld_must_pin_commit(self) -> None:
        with pytest.raises(ValidationError, match="bench_version"):
            self._experiment(bench=BenchId.OSWORLD_VERIFIED)

    def test_osworld_pin_must_look_like_sha(self) -> None:
        with pytest.raises(ValidationError, match="commit sha"):
            self._experiment(bench=BenchId.OSWORLD_VERIFIED, bench_version="main")

    def test_bench_commit_is_accepted_as_alias(self) -> None:
        experiment = self._experiment(bench=BenchId.OSWORLD_VERIFIED, bench_commit="091f5ef")
        assert experiment.bench_version == "091f5ef"

    def test_evaluation_key_recovers_primary_key(self) -> None:
        experiment = self._experiment(
            bench=BenchId.OSWORLD_VERIFIED,
            bench_version="091f5ef",
            agent={
                "model": _openai_model(),
                "harness": HarnessId.DEEPSEEK_HARNESS,
                "harness_version": "0.1.0rc7",
                "cordis_config": "configs/dsh/osworld.cordis.yml",
                "protocol": {"observation": Observation.SCREENSHOT, "guest_shell": True},
            },
        )
        key = experiment.evaluation_key()
        assert key.model == "unit-test-vlm"
        assert key.endpoint_kind == "api"
        assert key.harness == "deepseek_harness"
        assert "guest_shell" in key.protocol
        assert key.bench == "osworld_verified"
        assert key.bench_version == "091f5ef"
        assert key.compute_backend == "local_linux"
        # 主键要能当 dict key 用，report 才能按列分组。
        assert hash(key)


class TestShippedConfigs:
    """T0.4：两份实验 YAML 必须能通过 schema 校验。"""

    def test_smoke_fake_is_valid(self) -> None:
        experiment = Experiment.from_yaml(CONFIG_DIR / "smoke_fake.yaml")
        assert experiment.bench is BenchId.FAKE
        assert experiment.agent.harness is HarnessId.STUB
        assert experiment.agent.model.backend is ModelBackend.DUMMY
        assert experiment.compute_backend is ComputeBackend.LOCAL_LINUX
        # CI 用的配置不能需要密钥。
        assert experiment.agent.model.api_key_env is None

    def test_smoke_osworld_is_valid(self) -> None:
        experiment = Experiment.from_yaml(CONFIG_DIR / "smoke_osworld.yaml")
        assert experiment.bench is BenchId.OSWORLD_VERIFIED
        assert experiment.bench_version is not None
        assert experiment.task_ids == ["5ea617a3-0e86-4ba6-aab2-dac9aa2e8d57"]
        assert experiment.agent.limits.max_steps == 50
        assert experiment.agent.limits.num_envs == 1
        assert experiment.agent.harness is HarnessId.DEEPSEEK_HARNESS
        assert experiment.agent.model.accepts_images is True
        assert experiment.agent.protocol.guest_shell is True
        assert experiment.agent.protocol.harness_shell is False

    def test_smoke_osworld_keeps_vendor_details_configurable(self) -> None:
        """厂商相关的值必须是 YAML 可改字段。"""
        experiment = Experiment.from_yaml(CONFIG_DIR / "smoke_osworld.yaml")
        model = experiment.agent.model
        assert model.provider_route
        assert model.name
        assert model.api_key_env

    def test_no_vendor_names_in_executable_code(self) -> None:
        """厂商中立是硬约束：代码里不得出现厂商名分支。

        只检查**可执行代码**——注释与 docstring 里出现厂商名是允许的（解释某个数字
        的来历时需要点名），但一旦出现在字面量或标识符里，就说明有人在按厂商分支。
        """
        vendor_names = ("qwen", "deepseek-v4", "dashscope", "anthropic", "gpt-")
        for path in (Path(__file__).resolve().parents[1] / "src" / "cua_eval").rglob("*.py"):
            code = _executable_code(path).lower()
            for vendor in vendor_names:
                assert vendor not in code, (
                    f"{path.name} 的可执行代码里出现了厂商名 {vendor!r}；"
                    "厂商与端点只应来自 cordis.yml 的 route 与实验 YAML"
                )

    def test_from_yaml_reports_missing_file_as_config_error(self) -> None:
        with pytest.raises(ConfigError, match="读不到"):
            Experiment.from_yaml(CONFIG_DIR / "does_not_exist.yaml")

    def test_from_yaml_reports_invalid_config_with_path(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("name: x\nbench: not_a_bench\n", encoding="utf-8")
        with pytest.raises(ConfigError, match=re.escape("bad.yaml")):
            Experiment.from_yaml(bad)


class TestRunRecord:
    def _record(self, trials: list[TrialResult]) -> RunRecord:
        return RunRecord(
            run_id="20260821-000000-abcd",
            created_at=datetime(2026, 8, 21, tzinfo=UTC),
            experiment=Experiment.from_yaml(CONFIG_DIR / "smoke_fake.yaml"),
            trials=trials,
        )

    def test_aggregates_success_rate_as_percent(self) -> None:
        """逐题存 0.0/1.0，聚合列显示百分数。"""
        record = self._record(
            [
                TrialResult(task_id="a", failure_class=FailureClass.OK, score=1.0),
                TrialResult(task_id="b", failure_class=FailureClass.TASK_FAIL, score=0.0),
            ]
        )
        metric_set = record.metric_set()
        rate = metric_set.get("success_rate")
        assert rate is not None
        assert rate.value == pytest.approx(50.0)
        assert rate.unit == "percent"
        assert rate.higher_is_better is True

    def test_infra_error_is_excluded_from_denominator(self) -> None:
        record = self._record(
            [
                TrialResult(task_id="a", failure_class=FailureClass.OK, score=1.0),
                TrialResult(task_id="b", failure_class=FailureClass.OK, score=1.0),
                TrialResult(task_id="c", failure_class=FailureClass.INFRA_ERROR),
            ]
        )
        metric_set = record.metric_set()
        assert (metric_set.scored, metric_set.infra_skipped, metric_set.total) == (2, 1, 3)
        rate = metric_set.get("success_rate")
        assert rate is not None
        # 2/2 而不是 2/3——环境故障不摊薄成功率。
        assert rate.value == pytest.approx(100.0)

    def test_all_infra_errors_yield_no_metric(self) -> None:
        record = self._record([TrialResult(task_id="a", failure_class=FailureClass.INFRA_ERROR)])
        metric_set = record.metric_set()
        assert metric_set.metrics == []
        assert metric_set.scored == 0

    def test_round_trips_through_json(self) -> None:
        record = self._record([TrialResult(task_id="a", failure_class=FailureClass.OK, score=1.0)])
        restored = RunRecord.model_validate(record.to_json_dict())
        assert restored.evaluation_key() == record.evaluation_key()


class TestErrorHierarchy:
    """T0.2：异常都挂在同一个基类下，且能映射到失败类。

    未实现的 backend / bench 抛异常的测试随 M3 的 test_unsupported.py 一起来——
    那时被测的模块才存在。
    """

    @pytest.mark.parametrize(
        "exc_type",
        [
            ConfigError,
            UnsupportedComputeBackend,
            UnsupportedBenchError,
            InfraError,
            HarnessError,
            ModelError,
        ],
    )
    def test_all_derive_from_base(self, exc_type: type[CuaEvalError]) -> None:
        assert issubclass(exc_type, CuaEvalError)

    def test_failure_class_mapping(self) -> None:
        assert InfraError.failure_class == FailureClass.INFRA_ERROR
        # harness 起不来算环境失败，不是模型的错。
        assert HarnessError.failure_class == FailureClass.INFRA_ERROR
        assert ModelError.failure_class == FailureClass.MODEL_ERROR

    def test_config_errors_map_to_no_failure_class(self) -> None:
        """配置错误应在跑题之前失败，不该被记成某次 trial 的结果。"""
        assert ConfigError.failure_class is None
        assert UnsupportedComputeBackend.failure_class is None
        assert UnsupportedBenchError.failure_class is None

    def test_mapped_values_are_valid_failure_classes(self) -> None:
        for exc_type in (InfraError, HarnessError, ModelError):
            assert exc_type.failure_class is not None
            FailureClass(exc_type.failure_class)
