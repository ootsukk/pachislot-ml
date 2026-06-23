from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import Any, overload

# 仕様（インターフェース）の登録プール
_REGISTERED_SPECS: list[type[Any]] = []


class PluginMeta:
    """仕様クラス側が自律的に保持する、純粋ドメインプラグインメタデータ
    器の都合（keyや形状）や、デフォルトの具象名といった外部知識は1ミリも含まない。
    """

    def __init__(self, depends_on: list[type[Any]]) -> None:
        self.depends_on = depends_on


class PluginImplMeta:
    """プラグイン実装メタデータ"""

    def __init__(self, value: str, priority: int) -> None:
        self.value = value  # YAMLや設定ファイルで指定される一意のプラグイン名
        self.priority = priority  # 自動選出およびリストソートの双方を司る優先度


class DependencyModuleMeta:
    """コネクターが要求する外部モジュールの不変依存メタデータ"""

    def __init__(self, module_name: str, version: str) -> None:
        self.module_name = module_name  # pipに登録されている実際のパッケージ名
        self.version = version  # 要求するセマンティックバージョン条件（例: '>=0.5.0'）

# カッコなし@plugin
@overload
def plugin[T: type[Any]](cls: T, /) -> T: ...

# カッコ付き@plugin()
@overload
def plugin(*, depends_on: type[Any] | Sequence[type[Any]] | None = None) -> Callable[[Any], Any]: ...

def plugin(
    cls_or_none: type[Any] | None = None,
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
    """システム内での一意の名前と優先度を明示する。"""

    def decorator(cls: type[Any]) -> type[Any]:
        cls.__plugin_impl_meta__ = PluginImplMeta(value=value, priority=priority)
        return cls

    return decorator


def dependency_module(module_name: str, version: str):
    """外部ライブラリの生存条件を刻む宣言型クラスデコレータ"""

    def decorator(cls: type[Any]) -> type[Any]:
        cls.__dependency_meta__ = DependencyModuleMeta(module_name=module_name, version=version)
        return cls

    return decorator


class NamingStrategy(ABC):
    """入れ物（Component）が、YAML（Config）上のどこからデータを読み出すべきかを決定する抽象戦略"""

    @abstractmethod
    def get_key(self, cls_obj: type[Any] | None = None, dynamic_name: str | None = None) -> str:
        pass


class FixedKey(NamingStrategy):
    """トップレベル用の固定キー戦略（例: 'encoder' や 'filters'）"""

    def __init__(self, key: str) -> None:
        self._key = key

    def get_key(self, cls_obj: type[Any] | None = None, dynamic_name: str | None = None) -> str:
        return self._key


class PluginNameKey(NamingStrategy):
    """リスト内包用の動的命名則戦略。
    @plugin_impl で宣言された本物の名前（value）から自律的にキー名を導き出す。
    """

    def get_key(self, cls_obj: type[Any] | None = None, dynamic_name: str | None = None) -> str:
        if dynamic_name:
            return dynamic_name
        if cls_obj and hasattr(cls_obj, "__plugin_impl_meta__"):
            return str(cls_obj.__plugin_impl_meta__.value)
        return ""


class Component(ABC):
    """すべての入れ物設計図の頂点に立つ抽象基底クラス"""

    def __init__(
        self,
        target_type: type[Any],
        key: str | NamingStrategy,
        mandatory: bool = True,
    ) -> None:
        self.target_type = target_type  # 解決されて最終的にプールに入る本物のオブジェクトの型
        self.strategy: NamingStrategy = FixedKey(key) if isinstance(key, str) else key
        self.mandatory = mandatory

    @abstractmethod
    def resolve_asset(
        self,
        config_data: Any,
        type_pool: dict[type[Any], Any],
        explicit_pool: dict[type[Any], Any],
        plugin_groups: list[str],
        raw_element_data: Any = None,
        dynamic_name: str | None = None,
    ) -> Any:
        pass


class FlatComponent(Component):
    """単一排他選択（FLAT）の入れ物を司る解決戦略オブジェクト"""

    def resolve_asset(
        self,
        config_data: Any,
        type_pool: dict[type[Any], Any],
        explicit_pool: dict[type[Any], Any],
        plugin_groups: list[str],
        raw_element_data: Any = None,
        dynamic_name: str | None = None,
    ) -> Any:
        # 手動優先プールにダイレクトに型が存在すれば最優先返却
        if self.target_type in explicit_pool:
            return explicit_pool[self.target_type]

        key_name = self.strategy.get_key(self.target_type, dynamic_name)

        # トップレベル（親がいる）かリスト内包（raw_element_dataが直渡しされている）かを安全に抽出
        raw_section = raw_element_data if raw_element_data is not None else getattr(config_data, key_name, None)

        # [enabledの自動スキャン] mandatory=False（任意）かつ明示的に無効化されていればスキップ
        if not self.mandatory and isinstance(raw_section, dict) and raw_section.get("enabled") is False:
            return None

        from webclient.resolver import UniversalPluginResolver

        return UniversalPluginResolver._instantiate_flat_core(self, raw_section, key_name, type_pool, plugin_groups)


class ConfigPropertyComponent(Component):
    """Config 上にすでに実体化されているプロパティ（オブジェクトデータ）を、
    何も加工せずそのまま依存性プールへ横流し（マウント）するだけの自律戦略。
    """

    def resolve_asset(
        self,
        config_data: Any,
        type_pool: dict[type[Any], Any],
        explicit_pool: dict[type[Any], Any],
        plugin_groups: list[str],
        raw_element_data: Any = None,
        dynamic_name: str | None = None,
    ) -> Any:
        key_name = self.strategy.get_key(self.target_type, dynamic_name)
        return getattr(config_data, key_name, None)


class ListComponent(Component):
    """priority 属性を内包した、汎用的な複数配列（LIST）の入れ物を司る解決戦略オブジェクト"""

    def __init__(
        self,
        target_type: type[Any],
        key: str | NamingStrategy,
        nested_component: Component,
        ordered: bool = False,
        mandatory: bool = True,
    ) -> None:
        super().__init__(target_type, key, mandatory)
        self.nested_component = nested_component
        self.ordered = ordered

    def resolve_asset(
        self,
        config_data: Any,
        type_pool: dict[type[Any], Any],
        explicit_pool: dict[type[Any], Any],
        plugin_groups: list[str],
        raw_element_data: Any = None,
        dynamic_name: str | None = None,
    ) -> Any:
        collected_tuples: list[tuple[int, Any]] = []

        # 手動優先プールから、あらかじめ登録されている実体配列を積載
        manual_items = explicit_pool.get(self.target_type, [])
        for item in manual_items:
            priority = getattr(item, "priority", 100) if self.ordered else 0
            collected_tuples.append((priority, getattr(item, "filter_func", item)))

        my_key_name = self.strategy.get_key()
        raw_section = getattr(config_data, my_key_name, {}) or {}

        # 抽象クラスのすべての派生サブクラスをスキャン
        spec_type = self.nested_component.target_type
        for sub_cls in spec_type.__subclasses__():
            impl_meta = getattr(sub_cls, "__plugin_impl_meta__", None)

            if impl_meta and impl_meta.value in raw_section:
                short_name = impl_meta.value
                element_props = raw_section[short_name] or {}

                instance = self.nested_component.resolve_asset(
                    config_data,
                    type_pool,
                    explicit_pool,
                    plugin_groups,
                    raw_element_data=element_props,
                    dynamic_name=short_name,
                )

                if instance is None:
                    continue

                priority = 0
                if self.ordered:
                    priority = element_props.get("priority") if isinstance(element_props, dict) else None
                    if priority is None:
                        priority = getattr(instance, "priority", getattr(sub_cls, "priority", 100))

                collected_tuples.append((priority, instance))

        if self.ordered:
            collected_tuples.sort(key=lambda t: t[0], reverse=True)

        return [t[1] for t in collected_tuples]
