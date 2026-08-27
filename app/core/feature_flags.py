"""最小 Feature Flag 服务（阶段 6 发布流程）：运行时开关、不重启生效。

设计：
- 存储：进程内内存（单实例部署适用）；**运行时可 set/get 立即生效**（不重启）；
- 初始化：seed 自静态配置（app/core/config 的 `*_ENABLED` 布尔项），部署期默认；
- API：``is_enabled(name, default)`` / ``set(name, bool)`` / ``get_all()`` / ``reset()``；
- 幂等：重复 set 覆盖；未注册 key 走 default。
- 约定：flag 名 kebab-case（如 ``legal-agentic-rag-v2``）；灰度消费方在业务层
  调用 ``is_enabled``，灰度中/回滚仅需 set(false) 即时生效，无需发版。

局限与边界：内存存储不跨多副本共享（多副本灰度请叠加 DB/Redis 实现——见
docs/CANARY_AND_RELEASE.md §Feature Flag）。本模块保持零外部依赖（红线 3）。
"""

from __future__ import annotations

import threading

from app.core.config import get_settings

# 静态配置中可作为默认 seed 的布尔开关（部署期基线）
_SEEDABLE_SETTINGS = (
    "OPEN_API_ENABLED",
    "CONNECTOR_SYNC_ENABLED",
    "MAILBOX_SYNC_ENABLED",
    "AGENTIC_RAG_ENABLED",
    "LLM_MODEL_ROUTING_ENABLED",
    "LLM_MODEL_RELEASES_ENABLED",
    "LLM_MODEL_FALLBACK_ENABLED",
    "LLM_OUTBOUND_DLP_ENABLED",
    "OBS_METRICS_SNAPSHOT_ENABLED",
)


class FeatureFlagStore:
    """进程内 flag 存储（线程安全）；支持运行时切换。"""

    def __init__(self, seed: dict[str, bool] | None = None) -> None:
        self._store: dict[str, bool] = {}
        self._lock = threading.Lock()
        if seed:
            self._store.update(seed)

    def is_enabled(self, name: str, default: bool = False) -> bool:
        with self._lock:
            if name in self._store:
                return self._store[name]
        return default

    def set(self, name: str, value: bool) -> None:
        """运行时切换：立即对后续 is_enabled 调用生效（不重启）。"""
        with self._lock:
            self._store[name] = bool(value)

    def get_all(self) -> dict[str, bool]:
        with self._lock:
            return dict(self._store)

    def reset(self, seed: dict[str, bool] | None = None) -> None:
        with self._lock:
            self._store = dict(seed or {})


def _seed_from_settings() -> dict[str, bool]:
    """从静态配置提取部署期默认（用于首次启动基线）。"""
    settings = get_settings()
    seeded = {}
    for field in _SEEDABLE_SETTINGS:
        value = getattr(settings, field, None)
        if isinstance(value, bool):
            seeded[field.lower()] = value
    return seeded


# 模块级单例（serivce 层 import 即用）
feature_flags = FeatureFlagStore(_seed_from_settings())
