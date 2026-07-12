from __future__ import annotations

import importlib.metadata
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Final, Protocol, cast, overload


class PluginMeta:
    """拡張インタフェースクラスの不変メタデータ。"""

    def __init__(self) -> None:
        pass


class PluginImplMeta:
    """拡張実装クラスの不変メタデータ。"""

    def __init__(
        self,
        *,
        value: str,
        priority: int,
        depends_on: Sequence[type[object]],
    ) -> None:
        self.value: Final[str] = value
        self.priority: Final[int] = priority
        self.depends_on: Final[Sequence[type[object]]] = tuple(depends_on)


class DependencyModuleMeta:
    """拡張実装が要求する外部モジュールの不変メタデータ。"""

    def __init__(
        self,
        *,
        module_name: str,
        version: str,
        check_satisfied_callback: Callable[[], bool],
    ) -> None:
        self.module_name: Final[str] = module_name
        self.version: Final[str] = version
        self._check_satisfied_callback: Final[Callable[[], bool]] = check_satisfied_callback
        self._lock: Final[threading.Lock] = threading.Lock()
        self._cached_result: bool | None = None

    def check_satisfied(self) -> bool:
        """スレッド安全かつ冪等に外部モジュールの適合性を遅延評価します。"""
        if (res := self._cached_result) is not None:
            return res

        with self._lock:
            if self._cached_result is None:
                self._cached_result = self._check_satisfied_callback()
            return self._cached_result


class MetaAttributes(Protocol):
    """静的型チェッカーに動的属性の存在を認識させるための構造的プロトコル。"""

    __plugin_meta__: PluginMeta
    __plugin_impl_meta__: PluginImplMeta
    __dependency_meta__: DependencyModuleMeta


class MetadataAccessor:
    """MRO(クラス継承)をバイパスし、当該クラス固有のメタデータのみを厳格に抽出する専用アクセサ。"""

    @classmethod
    def get_plugin_meta(cls, target: type[object], /) -> PluginMeta | None:
        meta = target.__dict__.get("__plugin_meta__")
        return meta if isinstance(meta, PluginMeta) else None

    @classmethod
    def get_plugin_impl_meta(cls, target: type[object], /) -> PluginImplMeta | None:
        meta = target.__dict__.get("__plugin_impl_meta__")
        return meta if isinstance(meta, PluginImplMeta) else None

    @classmethod
    def get_dependency_meta(cls, target: type[object], /) -> DependencyModuleMeta | None:
        meta = target.__dict__.get("__dependency_meta__")
        return meta if isinstance(meta, DependencyModuleMeta) else None


class ClassDecorator(Protocol):
    """対象クラスの型情報を完全に維持したままデコレートするための構造的プロトコル。"""

    def __call__[T: type[object]](self, cls: T, /) -> T: ...


@overload
def plugin[T: type[object]](cls: T, /) -> T: ...


@overload
def plugin() -> ClassDecorator: ...


def plugin(
    cls_obj: type[object] | None = None,
    /,
) -> type[object] | ClassDecorator:
    """拡張仕様インターフェースに付与するドメインアノテーション。"""

    def decorator[T: type[object]](cls: T, /) -> T:
        cast(type[MetaAttributes], cls).__plugin_meta__ = PluginMeta()
        return cls

    if cls_obj is not None:
        return decorator(cls_obj)

    return decorator


def plugin_impl(
    value: str,
    *,
    priority: int = 100,
    depends_on: type[object] | Sequence[type[object]] | None = None,
) -> ClassDecorator:
    """拡張実装クラスに付与するデコレータ。"""

    def decorator[T: type[object]](cls: T, /) -> T:
        deps = [depends_on] if isinstance(depends_on, type) else list(depends_on or [])
        cast(type[MetaAttributes], cls).__plugin_impl_meta__ = PluginImplMeta(
            value=value,
            priority=priority,
            depends_on=deps,
        )
        return cls

    return decorator


def dependency_module(
    module_name: str,
    version: str,
) -> ClassDecorator:
    """拡張実装クラスが依存する外部ライブラリの情報を指定するデコレータ。"""

    def decorator[T: type[object]](cls: T, /) -> T:
        def evaluator() -> bool:
            try:
                actual_version = importlib.metadata.version(module_name)
                return VersionConstraint(version).is_satisfied_by(actual_version)
            except importlib.metadata.PackageNotFoundError:
                for dist in importlib.metadata.distributions():
                    name_attr = dist.metadata.get("Name")
                    if name_attr is None:
                        continue
                    if name_attr.lower().replace("-", "_") == module_name.lower().replace(
                        "-", "_"
                    ) and VersionConstraint(version).is_satisfied_by(dist.version):
                        return True
                return False

        cast(type[MetaAttributes], cls).__dependency_meta__ = DependencyModuleMeta(
            module_name=module_name,
            version=version,
            check_satisfied_callback=evaluator,
        )
        return cls

    return decorator


class VersionConstraint:
    """セマンティックバージョンの条件式を自律判定する値オブジェクト。"""

    _OPERATORS: Final[
        Mapping[
            str,
            Callable[[Sequence[int], Sequence[int]], bool],
        ]
    ] = {
        "==": lambda a, b: tuple(a) == tuple(b),
        ">=": lambda a, b: tuple(a) >= tuple(b),
        "<=": lambda a, b: tuple(a) <= tuple(b),
        ">": lambda a, b: tuple(a) > tuple(b),
        "<": lambda a, b: tuple(a) < tuple(b),
    }

    def __init__(self, constraint_expr: str, /) -> None:
        self._expr: Final[str] = constraint_expr.strip()
        match = re.match(r"^([>=<!]+)\s*([\d.]+)", self._expr)

        if match:
            op_str = match.group(1)
            required_str = match.group(2)
            required_parts = [int(x) for x in re.findall(r"\d+", required_str)]
        else:
            op_str = "=="
            required_str = "0"
            required_parts = [0]

        self._op_str: Final[str] = op_str
        self._required_str: Final[str] = required_str
        self._required_parts: Final[Sequence[int]] = required_parts

    def is_satisfied_by(self, actual_version: str, /) -> bool:
        if not self._required_parts:
            return True

        actual_parts = [int(x) for x in re.findall(r"\d+", actual_version)]
        max_len = max(len(actual_parts), len(self._required_parts))

        actual_parts_list = list(actual_parts) + [0] * (max_len - len(actual_parts))
        required_parts_list = list(self._required_parts) + [0] * (max_len - len(self._required_parts))

        comp_func = self._OPERATORS.get(self._op_str)
        return comp_func(actual_parts_list, required_parts_list) if comp_func else False
