from __future__ import annotations

from collections.abc import Callable, Sequence
import importlib.metadata
import operator
import re
from typing import Any, overload

# 仕様（インターフェース）の登録プール
_REGISTERED_SPECS: list[type[Any]] = []


class PluginMeta:
    """拡張インタフェースクラスのメタデータ"""

    def __init__(self, depends_on: list[type[Any]]) -> None:
        self.depends_on = depends_on


class PluginImplMeta:
    """拡張実装クラスのメタデータ"""

    def __init__(self, value: str, priority: int) -> None:
        self.value = value  # YAMLや設定ファイルで指定される一意のプラグイン名
        self.priority = priority  # 自動選出およびリストソートの双方を司る優先度


class DependencyModuleMeta:
    """ClientHttpConnectorが実装する外部モジュールのメタデータ"""

    def __init__(self, module_name: str, version: str, check_satisfied: Callable[[], bool]) -> None:
        self.module_name = module_name  # pipに登録されている実際のパッケージ名
        self.version = version  # 要求するセマンティックバージョン条件（例: '>=0.5.0'）
        self.check_satisfied = check_satisfied  # 遅延評価用のトリガー関数


# カッコなし@plugin
@overload
def plugin[T: type[Any]](cls: T, /) -> T: ...

# カッコ付き@plugin()
@overload
def plugin(*, depends_on: type[Any] | Sequence[type[Any]] | None = None) -> Callable[[Any], Any]: ...

def plugin(
    cls_obj: type[Any] | None = None,
    *,
    depends_on: type[Any] | Sequence[type[Any]] | None = None,
) -> Any:
    """拡張仕様インターフェース（Protocol/ABC）に付与するドメインアノテーション"""

    def decorator(cls: type[Any]) -> type[Any]:
        deps = [depends_on] if isinstance(depends_on, type) else list(depends_on or [])
        cls.__plugin_meta__ = PluginMeta(depends_on=deps)

        if cls not in _REGISTERED_SPECS:
            _REGISTERED_SPECS.append(cls)
        return cls

    return decorator


def plugin_impl(value: str, priority: int = 100):
    """拡張実装クラスに付与するデコレータ。システム内での一意の名前と優先度を指定する。"""

    def decorator(cls: type[Any]) -> type[Any]:
        cls.__plugin_impl_meta__ = PluginImplMeta(value=value, priority=priority)
        return cls

    return decorator


def dependency_module(module_name: str, version: str):
    """ClientHttpConnectorが対応するHttp Clientライブラリの情報を指定するデコレータ。"""

    def decorator(cls: type[Any]) -> type[Any]:
        # 環境スキャン手続きをクロージャに閉じ込める
        def evaluator() -> bool:
            try:
                for dist in importlib.metadata.distributions():
                    normalized_dist_name = dist.metadata["Name"].lower().replace("-", "_")
                    normalized_target_name = module_name.lower().replace("-", "_")

                    if normalized_dist_name == normalized_target_name and VersionConstraint(version).is_satisfied_by(dist.version):
                        return True
                return False
            except Exception:
                return False

        cls.__dependency_meta__ = DependencyModuleMeta(
            module_name=module_name,
            version=version,
            check_satisfied=evaluator,
        )
        return cls

    return decorator

class VersionConstraint:

    _OPERATORS: dict[str, Callable[[list[int], list[int]], bool]] = {
        "==": operator.eq,
        ">=": operator.ge,
        "<=": operator.le,
        ">": operator.gt,
        "<": operator.lt,
    }

    def __init__(self, constraint_expr: str) -> None:
        self._expr = constraint_expr.strip()
        match = re.match(r"^([>=<!]+)\s*([\d.]+)", self._expr)

        if match:
            self._op_str, self._required_str = match.groups()
            self._required_parts = [int(x) for x in re.findall(r"\d+", self._required_str)]
        else:
            self._op_str, self._required_str, self._required_parts = "==", "0", [0]

    def is_satisfied_by(self, actual_version: str) -> bool:
        """届けられた現在の実バージョンが、この条件を満たしているか自律判定します。"""
        if not self._required_parts:
            return True

        actual_parts = [int(x) for x in re.findall(r"\d+", actual_version)]
        max_len = max(len(actual_parts), len(self._required_parts))

        actual_parts += [0] * (max_len - len(actual_parts))
        required_parts = self._required_parts + [0] * (max_len - len(self._required_parts))

        comp_func = self._OPERATORS.get(self._op_str)
        return comp_func(actual_parts, required_parts) if comp_func else False
