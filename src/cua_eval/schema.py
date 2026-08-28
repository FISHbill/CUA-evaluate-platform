"""实验配置与结果的类型层。

规格见 AGENTS.md 第 4 节。这一层的职责是**在跑题之前**把不自洽的配置拦下来：
协议与模型能力矛盾、密钥字面值写进了配置、bench 没有 pin 版本，这些错误如果
漏到运行期，代价是几十分钟的桌面 VM 时间加一份不可信的结果。
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple, Self

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from cua_eval.errors import ConfigError

# OSWorld 的 commit pin 下限。低于此版本每题会遗留约 32 GB 匿名卷直到写满磁盘，
# 见 AGENTS.md 第 7 节。sha 无法比较先后，所以这里只能要求「必须 pin」，具体
# 值由 doctor 在 checkout 后核对。
OSWORLD_MIN_COMMIT = "091f5ef"

_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_HEX_RE = re.compile(r"^[0-9a-f]{7,40}$")
# 常见密钥前缀。命中说明有人把密钥本身写进了配置，而不是写环境变量名。
_SECRET_PREFIXES = ("sk-", "sk_", "ghp_", "gho_", "hf_", "AIza", "xoxb-", "Bearer ")


class Observation(StrEnum):
    """给模型的观测形态。两者都禁止 a11y 树 / DOM / 应用脚本接口。"""

    SCREENSHOT = "screenshot"
    TEXT = "text"


class GuestActions(StrEnum):
    """桌面 VM 内的动作空间。在 guest 里开终端打字属于合法键鼠操作。"""

    MOUSE_KEYBOARD = "mouse_keyboard"
    NONE = "none"


class Modality(StrEnum):
    """模型 route 声明的请求模态。未声明 image 的 route 收不到截图。"""

    TEXT = "text"
    IMAGE = "image"


class ModelBackend(StrEnum):
    DUMMY = "dummy"
    OPENAI_COMPAT = "openai_compat"


class EndpointKind(StrEnum):
    """端点在谁家。`api` 是云端模型 API，`local` 是本地 vLLM 或集群网关。"""

    API = "api"
    LOCAL = "local"


class HarnessId(StrEnum):
    STUB = "stub"
    DEEPSEEK_HARNESS = "deepseek_harness"


class BenchId(StrEnum):
    FAKE = "fake"
    OSWORLD_VERIFIED = "osworld_verified"
    SCIENCEBOARD = "scienceboard"
    MAC_AGENT_BENCH = "mac_agent_bench"
    MACOS = "macos"
    WINDOWS = "windows"


class ComputeBackend(StrEnum):
    """评测作业跑在哪台机器上，与 bench 客户机的操作系统是两件事。"""

    LOCAL_LINUX = "local_linux"
    WINDOWS_PC = "windows_pc"
    CLOUD_SINGLE = "cloud_single"
    SMALL_CLUSTER = "small_cluster"
    GPU_CLUSTER = "gpu_cluster"


class FailureClass(StrEnum):
    """失败分类。`infra_error` 不计入成功率分母，绝不能记成模型 0 分。"""

    OK = "ok"
    TASK_FAIL = "task_fail"
    INFRA_ERROR = "infra_error"
    MODEL_ERROR = "model_error"


class _Base(BaseModel):
    """公共配置：拒绝未知字段（挡住 YAML 里的拼写错误），配置对象不可变。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Protocol(_Base):
    """协议的四个独立开关，见 AGENTS.md 2.1。

    `guest_shell` 与 `harness_shell` 的区别是**命令在哪台机器上执行**：前者在桌面
    VM 内（走 OSWorld 官方通道），后者在评测宿主机上。后者当前被硬性拒绝。
    """

    observation: Observation
    guest_actions: GuestActions = GuestActions.MOUSE_KEYBOARD
    guest_shell: bool = False
    harness_shell: bool = False

    @model_validator(mode="after")
    def _reject_harness_shell(self) -> Self:
        if self.harness_shell:
            raise ValueError(
                "harness_shell 必须为 false：它在评测宿主机上执行命令，既改不动 guest "
                "状态（因此解不了题），又把模型密钥、results/ 和官方 evaluator 交给了被"
                "评测的模型。要给模型 bash 请用 guest_shell=true。见 AGENTS.md 2.1。"
            )
        return self

    @property
    def slug(self) -> str:
        """用于 Table 1 分列的稳定标识。取值不同的 run 不得进同一列。"""
        parts = [self.observation.value, self.guest_actions.value]
        if self.guest_shell:
            parts.append("guest_shell")
        return "+".join(parts)


class Limits(_Base):
    """运行限制。

    `max_screenshot_history` 是配置项而不是写死的 20：一张 1920×1080 截图对
    常见 7B VLM 约 2,700 视觉 token，20 张约 54K，自托管时很容易超过推理服务的
    `--max-model-len`。见 AGENTS.md 6.4。
    """

    max_steps: int = Field(default=50, gt=0)
    num_envs: int = Field(default=1, gt=0)
    task_timeout_seconds: int = Field(default=1800, gt=0)
    max_screenshot_history: int = Field(default=20, gt=0)


class ModelSpec(_Base):
    """模型接入。厂商与端点全在 `provider_route` 指向的 cordis.yml route 里，
    这一层只认 route 名——**不得出现厂商分支**。
    """

    backend: ModelBackend
    name: str = Field(min_length=1)
    endpoint_kind: EndpointKind | None = None
    provider_route: str | None = None
    api_key_env: str | None = None
    input_modalities: list[Modality] = Field(default_factory=lambda: [Modality.TEXT])

    @model_validator(mode="after")
    def _check_modalities(self) -> Self:
        if not self.input_modalities:
            raise ValueError("input_modalities 不能为空")
        if len(set(self.input_modalities)) != len(self.input_modalities):
            raise ValueError(f"input_modalities 有重复项: {self.input_modalities}")
        return self

    @model_validator(mode="after")
    def _check_api_key_env(self) -> Self:
        value = self.api_key_env
        if value is None:
            return self
        if any(value.startswith(p) for p in _SECRET_PREFIXES):
            raise ValueError(
                "api_key_env 看起来是密钥本身而不是环境变量名。密钥只从环境变量读，"
                "不得进 git / YAML / 桌面 VM。"
            )
        if not _ENV_NAME_RE.match(value):
            raise ValueError(
                f"api_key_env 只接受环境变量名（大写字母、数字、下划线），得到 {value!r}。"
                "若这是密钥本身，请改为写它的环境变量名。"
            )
        return self

    @model_validator(mode="after")
    def _check_backend_fields(self) -> Self:
        endpoint_fields = {
            "endpoint_kind": self.endpoint_kind,
            "provider_route": self.provider_route,
            "api_key_env": self.api_key_env,
        }
        if self.backend is ModelBackend.OPENAI_COMPAT:
            missing = sorted(k for k, v in endpoint_fields.items() if v is None)
            if missing:
                raise ValueError(f"backend=openai_compat 时必须提供: {', '.join(missing)}")
        else:
            present = sorted(k for k, v in endpoint_fields.items() if v is not None)
            if present:
                raise ValueError(
                    f"backend={self.backend.value} 没有模型端点，不应设置: {', '.join(present)}"
                )
        return self

    @property
    def accepts_images(self) -> bool:
        return Modality.IMAGE in self.input_modalities


class AgentSpec(_Base):
    """Agent = 模型 + harness + 协议 + 限制。"""

    model: ModelSpec
    harness: HarnessId
    protocol: Protocol
    limits: Limits = Field(default_factory=Limits)
    harness_version: str | None = None
    cordis_config: Path | None = None

    @model_validator(mode="after")
    def _check_observation_matches_model(self) -> Self:
        if self.protocol.observation is Observation.SCREENSHOT and not self.model.accepts_images:
            raise ValueError(
                "protocol.observation=screenshot 但 model.input_modalities 未含 image。"
                "未声明 image 的 route 会在图片附加之前就把它拒掉，模型将看不到任何截图。"
                "要么给 route 声明 image，要么改用 observation=text。"
            )
        return self

    @model_validator(mode="after")
    def _check_harness_fields(self) -> Self:
        if self.harness is HarnessId.DEEPSEEK_HARNESS:
            missing = [
                name
                for name, value in (
                    ("harness_version", self.harness_version),
                    ("cordis_config", self.cordis_config),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"harness=deepseek_harness 时必须提供: {', '.join(missing)}。"
                    "dsh SDK 只有预发布版，版本必须 pin；且禁止使用它的零配置默认组合"
                    "（那会挂上宿主机 bash），必须自备 cordis.yml。见 AGENTS.md 6.1 与 6.2。"
                )
        elif self.cordis_config is not None:
            raise ValueError(f"harness={self.harness.value} 不使用 cordis_config")
        return self


class Metric(_Base):
    """单个指标。ASR 这类「越低越好」的指标靠 `higher_is_better` 表达。"""

    name: str = Field(min_length=1)
    value: float
    higher_is_better: bool
    unit: str | None = None


class MetricSet(_Base):
    """一次 run 在一个 bench 上的聚合指标。

    `scored + infra_skipped == total`：每个 trial 要么计入分母，要么因环境失败被
    排除。report 必须把排除数写出来，否则成功率会骗人。
    """

    metrics: list[Metric] = Field(default_factory=list)
    scored: int = Field(default=0, ge=0)
    infra_skipped: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check_counts(self) -> Self:
        if self.scored + self.infra_skipped != self.total:
            raise ValueError(
                f"scored({self.scored}) + infra_skipped({self.infra_skipped}) "
                f"必须等于 total({self.total})"
            )
        return self

    def get(self, name: str) -> Metric | None:
        return next((m for m in self.metrics if m.name == name), None)


class TrialResult(_Base):
    """单题结果。

    `score` 是官方 evaluator 原样返回的 0.0–1.0，聚合成百分数的事交给 report。
    `infra_error` 必须没有分数——它被排除在分母之外，给它记 0 分就是把环境故障
    算成模型无能。
    """

    task_id: str = Field(min_length=1)
    failure_class: FailureClass
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    steps: int = Field(default=0, ge=0)
    wall_time_seconds: float = Field(default=0.0, ge=0.0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    error_message: str | None = None

    @model_validator(mode="after")
    def _check_score_matches_failure_class(self) -> Self:
        if self.failure_class is FailureClass.INFRA_ERROR:
            if self.score is not None:
                raise ValueError(
                    "infra_error 不得带分数：它不计入成功率分母，记 0 分会把环境故障"
                    "算成模型无能。"
                )
        elif self.score is None:
            raise ValueError(f"failure_class={self.failure_class.value} 必须给出 score")
        return self

    @property
    def counts_toward_score(self) -> bool:
        return self.failure_class is not FailureClass.INFRA_ERROR


class EvaluationKey(NamedTuple):
    """评测对象主键。可哈希，供 report 按列分组。"""

    model: str
    endpoint_kind: str
    harness: str
    protocol: str
    bench: str
    bench_version: str
    compute_backend: str


class Experiment(_Base):
    """一份 YAML = 一次可复现实验。"""

    name: str = Field(min_length=1)
    bench: BenchId
    task_ids: list[str] = Field(min_length=1)
    agent: AgentSpec
    compute_backend: ComputeBackend
    # OSWorld 用它存 commit sha；AGENTS.md 第 5 节里写作 bench_commit，两个名字都收。
    bench_version: str | None = Field(
        default=None,
        validation_alias=AliasChoices("bench_version", "bench_commit"),
    )
    artifact_retention_days: int = Field(default=14, gt=0)
    results_dir: Path = Path("results")

    @model_validator(mode="after")
    def _check_task_ids(self) -> Self:
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("task_ids 有重复项")
        return self

    @model_validator(mode="after")
    def _check_bench_pin(self) -> Self:
        pinned = {
            BenchId.OSWORLD_VERIFIED: (
                "bench=osworld_verified 必须 pin bench_version（官方仓库 commit），"
                f"不要浮动 main；下限见 AGENTS.md 第 7 节（>= {OSWORLD_MIN_COMMIT}）。"
            ),
            BenchId.SCIENCEBOARD: (
                "bench=scienceboard 必须 pin bench_version（官方 ScienceBoard 仓库 commit），"
                "不要浮动 main。"
            ),
            BenchId.MAC_AGENT_BENCH: (
                "bench=mac_agent_bench 必须 pin bench_version（官方 MacAgentBench 仓库 commit），"
                "不要浮动 main。"
            ),
        }
        message = pinned.get(self.bench)
        if message is None:
            return self
        if self.bench_version is None:
            raise ValueError(message)
        if not _HEX_RE.match(self.bench_version):
            raise ValueError(
                f"bench_version 应是 7–40 位小写十六进制 commit sha，得到 "
                f"{self.bench_version!r}"
            )
        return self

    def evaluation_key(self) -> EvaluationKey:
        """还原主键。`report` 用它决定哪些 run 能进同一列。"""
        model = self.agent.model
        return EvaluationKey(
            model=model.name,
            endpoint_kind=model.endpoint_kind.value if model.endpoint_kind else model.backend.value,
            harness=self.agent.harness.value,
            protocol=self.agent.protocol.slug,
            bench=self.bench.value,
            bench_version=self.bench_version or "unpinned",
            compute_backend=self.compute_backend.value,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> Experiment:
        """从 YAML 读一份实验配置，失败一律包成 `ConfigError` 并带上文件路径。"""
        p = Path(path)
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigError(f"读不到实验配置 {p}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise ConfigError(f"{p} 不是合法的 YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{p} 的顶层必须是映射，得到 {type(raw).__name__}")
        try:
            experiment = cls.model_validate(raw)
        except ValueError as exc:
            raise ConfigError(f"实验配置 {p} 不合法:\n{exc}") from exc
        return experiment._resolve_relative_paths(p)

    def _resolve_relative_paths(self, yaml_path: Path) -> Experiment:
        """相对路径相对 YAML 所在仓库解析，避免 `chdir` 后找不到 cordis。"""
        cordis = self.agent.cordis_config
        if cordis is None:
            return self
        resolved = _resolve_repo_path(yaml_path, cordis)
        if resolved == cordis:
            return self
        return self.model_copy(
            update={"agent": self.agent.model_copy(update={"cordis_config": resolved})}
        )


def _resolve_repo_path(yaml_path: Path, relative: Path) -> Path:
    if relative.is_absolute():
        return relative
    search_roots = [Path.cwd(), yaml_path.resolve().parent, *yaml_path.resolve().parents]
    for root in search_roots:
        candidate = root / relative
        if candidate.is_file():
            return candidate.resolve()
    return relative


class RunRecord(_Base):
    """一次 run 的完整记录。写进 results/<run_id>/ 供 report 解析。"""

    run_id: str = Field(min_length=1)
    created_at: datetime
    experiment: Experiment
    trials: list[TrialResult] = Field(default_factory=list)
    cua_eval_version: str | None = None

    def evaluation_key(self) -> EvaluationKey:
        return self.experiment.evaluation_key()

    def metric_set(self) -> MetricSet:
        """按 AGENTS.md 第 4 节的口径聚合：逐题 0.0–1.0，聚合用百分数。"""
        scored = [t for t in self.trials if t.counts_toward_score]
        infra_skipped = len(self.trials) - len(scored)
        metrics: list[Metric] = []
        if scored:
            rate = sum(t.score or 0.0 for t in scored) / len(scored) * 100.0
            metrics.append(
                Metric(
                    name="success_rate",
                    value=rate,
                    higher_is_better=True,
                    unit="percent",
                )
            )
        return MetricSet(
            metrics=metrics,
            scored=len(scored),
            infra_skipped=infra_skipped,
            total=len(self.trials),
        )

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = [
    "OSWORLD_MIN_COMMIT",
    "AgentSpec",
    "BenchId",
    "ComputeBackend",
    "EndpointKind",
    "EvaluationKey",
    "Experiment",
    "FailureClass",
    "GuestActions",
    "HarnessId",
    "Limits",
    "Metric",
    "MetricSet",
    "Modality",
    "ModelBackend",
    "ModelSpec",
    "Observation",
    "Protocol",
    "RunRecord",
    "TrialResult",
]
