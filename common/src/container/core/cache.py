from __future__ import annotations

import contextlib
import types
import typing
from dataclasses import dataclass
from threading import Lock
from typing import Final

from container.common.exceptions import CircularDependencyError
from container.definitions.component import Component


@dataclass(frozen=True)
class CacheKey:
    """コンテナ内のインスタンスを一意に識別するための不変値オブジェクト"""

    target_type: type[object] | types.GenericAlias
    plugin_name: str | None = None


@dataclass(frozen=True)
class ComponentId:
    """基本型強迫(Primitive Obsession)を排除し、識別名セマンティクスを統治する不変値オブジェクト"""

    value: Final[str]

    @classmethod
    def from_context(cls, component: Component[object], cache_key: CacheKey, /) -> ComponentId:
        """型トポロジーとメタデータから、規約に準拠したComponentを一意に鋳造するファクトリ"""
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


class SingletonBeanCache:
    """ダブルチェックロッキングによるスレッド安全なシングルトンBeanの保管および検索を専門に司るキャッシュ領域"""

    def __init__(self) -> None:
        self._lock: Final[Lock] = Lock()
        self._instances: Final[dict[CacheKey, object]] = {}

    def contains(self, key: CacheKey, /) -> bool:
        return key in self._instances

    def get(self, key: CacheKey, /) -> object | None:
        return self._instances.get(key)

    def put_if_absent(self, key: CacheKey, instance: object, /) -> None:
        with self._lock:
            if key not in self._instances:
                self._instances[key] = instance

    @contextlib.contextmanager
    def synchronize_instantiation(self, key: CacheKey, stack: set[CacheKey], /) -> typing.Iterator[None]:
        if key in stack:
            raise CircularDependencyError(f"循環依存が検出されました。型閉路パス: {key.target_type}")

        with self._lock:
            if key in self._instances:
                return

            stack.add(key)
            try:
                yield
            finally:
                stack.remove(key)

    def set_alias(self, alias_key: CacheKey, target_key: CacheKey, /) -> None:
        with self._lock:
            instance = self._instances.get(target_key)
            if instance is not None and alias_key not in self._instances:
                self._instances[alias_key] = instance

    def clear(self) -> None:
        with self._lock:
            self._instances.clear()
