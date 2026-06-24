from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from collections.abc import Sequence
from importlib.metadata import entry_points
from typing import Any


def deep_merge(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """2つの辞書構造を再帰的に巡回し、ネストされた子辞書を安全に重ね合わせる汎用関数。

    target 側の内部状態を破壊せず、source 側の指定値を最優先として上書きした
    独立した新しいマージ済みの辞書オブジェクトを返却します。
    """
    merged = dict(target)
    for k, v in source.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            # 双方ともに辞書であれば、さらに深い階層へ再帰的に潜って融合
            merged[k] = deep_merge(merged[k], v)
        else:
            # どちらかが辞書でなければ、source 側の指定値を最優先として上書き
            merged[k] = v
    return merged

def to_snake_case(name: str) -> str:
    """CamelCase を snake_case に汎用変換するユーティリティ"""
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()


def get_component_name(cls: type[Any]) -> str:
    """クラスに設定された @Named アノテーション、またはクラス名のスネークケースから識別名を安全に抽出します"""
    if hasattr(cls, "_custom_name"):
        return cls._custom_name
    return to_snake_case(cls.__name__)


def extract_config_type(cls: type[Any]) -> type[Any] | None:
    """実装クラスが Configurable[T] を継承している場合、その型引数 T（設定クラスの型）をリフレクション抽出します"""
    if hasattr(cls, "__orig_bases__"):
        for base in cls.__orig_bases__:
            origin = getattr(base, "__origin__", None)
            # 💡 循環参照を100%防ぐため、型オブジェクトではなくクラス名（文字列）のメタ比較で安全に抽出を達成
            if origin and origin.__name__ == "Configurable":
                args = getattr(base, "__args__", None)
                if args and isinstance(args[0], type):
                    return args[0]
    return None


def resolve_component_name(plugin_class: type[Any]) -> str:
    """コンポーネントに最適な名前キーを型情報から一元解決します"""
    config_cls = extract_config_type(plugin_class)
    if config_cls:
        return get_component_name(config_cls)
    return get_component_name(plugin_class)


def discover_config_classes[T](target_base: type[T], plugin_groups: Sequence[str]) -> dict[str, type[T]]:
    """指定された基底型を継承しているすべての設定クラスを、システム内から動的に全自動検出します。"""
    registry: dict[str, type[T]] = {}

    # 組み込みモジュールのルート再帰走査
    import webclient
    for module_info in pkgutil.walk_packages(webclient.__path__, webclient.__name__ + "."):
        try:
            mod = importlib.import_module(module_info.name)
            for _, cls_obj in inspect.getmembers(mod, inspect.isclass):
                if not cls_obj.__module__.startswith("webclient."):
                    continue
                if issubclass(cls_obj, target_base) and cls_obj is not target_base:
                    registry[get_component_name(cls_obj)] = cls_obj
        except ImportError:
            continue

    # 複数の外部 Entry Points プラグイングループの並列走査
    for group_name in plugin_groups:
        try:
            for ep in entry_points(group=group_name):
                plugin_class = ep.load()
                config_cls = extract_config_type(plugin_class)
                if config_cls and issubclass(config_cls, target_base):
                    registry[get_component_name(config_cls)] = config_cls
                elif issubclass(plugin_class, target_base) and plugin_class is not target_base:
                    registry[get_component_name(plugin_class)] = plugin_class
        except Exception:
            continue

    return registry
