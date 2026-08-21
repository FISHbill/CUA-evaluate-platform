"""CUA 评测平台。

评测对象是 `(model, endpoint_kind, harness, protocol, bench, bench_version,
compute_backend)` 这个七元组，而不是「纯模型」。需求见 docs/REQUIREMENTS.md，
实现规格见 AGENTS.md。
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
