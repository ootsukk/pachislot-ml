from __future__ import annotations

import contextlib
import types
from collections.abc import Iterator
from dataclasses import dataclass
from threading import Lock
from typing import Final

from container.definitions.component import Component


@dataclass(frozen=True)
class CacheKey:
    """コンテナ内のインスタンスを一意に識別するための不変値オブジェクト。"""

    target_type: type[object] | types.GenericAlias
    plugin_name: str | None = None


@dataclass(frozen=True)
class ComponentId:
    """識別名セマンティクスを統治する不変値オブジェクト。"""

    value: Final[str]

    @classmethod
    def from_context(cls, component: Component[object], cache_key: CacheKey, /) -> ComponentId:
        match cache_key.target_type:
            case types.GenericAlias() as alias:
                resolved_value = (
                    component.key
                    if component.key
                    else f"{getattr(alias.__origin__, '__name__', str(alias.__origin__)).lower()}_of_"
                    f"{getattr(alias.__args__[0], '__name__', str(alias.__args__[0])).lower() if alias.__args__ else 'unknown'}"
                )
            case type() as t:
                resolved_value = (
                    f"{t.__name__.lower()}:{cache_key.plugin_name}"
                    if cache_key.plugin_name
                    else component.key
                    if component.key
                    else t.__name__.lower()
                )
            case _:
                resolved_value = str(cache_key.target_type)
        return cls(resolved_value)

    def __str__(self) -> str:
        return self.value


class StripedLock:
    """キーのハッシュ値に基づいてロックを分散させる細粒度同期機構。"""

    def __init__(self, buckets: int = 16, /) -> None:
        self._locks: Final[tuple[Lock, ...]] = tuple(Lock() for _ in range(buckets))
        self._buckets: Final[int] = buckets

    def get_lock(self, key: CacheKey, /) -> Lock:
        return self._locks[hash(key) % self._buckets]


class SingletonScopeStrategy:
    """グローバルシングルトンの生存期間を統治する、スレッド安全で低競合な標準スコープ実装。"""

    def __init__(self, bucket_count: int = 16, /) -> None:
        self._instances: Final[dict[CacheKey, object]] = {}
        self._striped_lock: Final[StripedLock] = StripedLock(bucket_count)

    def get(self, key: CacheKey, /) -> object | None:
        return self._instances.get(key)

    def put(self, key: CacheKey, instance: object, /) -> None:
        lock = self._striped_lock.get_lock(key)
        with lock:
            if key not in self._instances:
                self._instances[key] = instance

    def remove(self, key: CacheKey, /) -> object | None:
        lock = self._striped_lock.get_lock(key)
        with lock:
            return self._instances.pop(key, None)

    @contextlib.contextmanager
    def synchronize(self, key: CacheKey, /) -> Iterator[None]:
        lock = self._striped_lock.get_lock(key)
        with lock:
            yield

    def clear(self) -> None:
        self._instances.clear()
