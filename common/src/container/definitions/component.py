from __future__ import annotations

import types
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Final

from container.common.constants import ComponentScope
from container.definitions.naming import ChainNamingStrategy, NamingStrategy
from container.definitions.resolvable import ResolvableType


class Component[T](ABC):
    """DIコンテナにおける管理オブジェクト生成のメタデータ定義を司る基底仕様書。"""

    def __init__(
        self,
        target_type: type[T] | types.GenericAlias,
        naming_strategy: NamingStrategy,
        /,
        *,
        mandatory: bool = True,
        scope: ComponentScope = ComponentScope.SINGLETON,
    ) -> None:
        self._target_type: Final[type[T] | types.GenericAlias] = target_type
        self._resolvable: Final[ResolvableType[T]] = ResolvableType[T](target_type)
        self._naming_strategy: Final[NamingStrategy] = naming_strategy
        self._mandatory: Final[bool] = mandatory
        self._scope: Final[ComponentScope] = scope

    @property
    def target_type(self) -> type[T] | types.GenericAlias:
        return self._target_type

    @property
    def naming_strategy(self) -> NamingStrategy:
        return self._naming_strategy

    @property
    def scope(self) -> ComponentScope:
        return self._scope

    @property
    def key(self) -> str:
        resolved = self._naming_strategy.get_key(self._resolvable.origin)
        return resolved if resolved is not None else ""

    @property
    def mandatory(self) -> bool:
        return self._mandatory

    @property
    @abstractmethod
    def plugin_spec_type(self) -> type[object]: ...


class InstanceComponent[T](Component[T]):
    """あらかじめ生成された特定の具象インスタンスを直接登録するための仕様定義書。"""

    def __init__(self, target_type: type[T], instance: T, /) -> None:
        super().__init__(
            target_type,
            ChainNamingStrategy(""),
            mandatory=True,
            scope=ComponentScope.SINGLETON,
        )
        self._instance: Final[T] = instance

    @property
    def instance(self) -> T:
        return self._instance

    @property
    def plugin_spec_type(self) -> type[object]:
        return self._resolvable.origin


class PropertyComponent[T](Component[T]):
    """構成ファイルからの単純なプロパティ値注入、または構造化設定オブジェクトの生成を定義する仕様定義書。"""

    @property
    def plugin_spec_type(self) -> type[object]:
        return self._resolvable.origin


class PluginComponent[T](Component[T]):
    """単一のプラグイン実装を自動選定するための仕様定義書。"""

    @property
    def plugin_spec_type(self) -> type[object]:
        return self._resolvable.origin


class PluginListComponent[R, T](Component[R]):
    """複数のプラグインをリストまたはカスタムコレクションとして解決・注入するための仕様定義書。"""

    def __init__(
        self,
        target_type: type[R],
        naming_strategy: NamingStrategy,
        nested_component: Component[T],
        /,
        *,
        ordered: bool = False,
        mandatory: bool = True,
        scope: ComponentScope = ComponentScope.SINGLETON,
    ) -> None:
        super().__init__(target_type, naming_strategy, mandatory=mandatory, scope=scope)
        self._nested_component: Final[Component[T]] = nested_component
        self._ordered: Final[bool] = ordered

    @property
    def nested_component(self) -> Component[T]:
        return self._nested_component

    @property
    def ordered(self) -> bool:
        return self._ordered

    @property
    def plugin_spec_type(self) -> type[object]:
        return self.nested_component.plugin_spec_type


class ComponentRegistry:
    """コンポーネント定義の集合体であり、検索・解決戦略を統治する不変の値オブジェクト"""

    def __init__(self, components: Sequence[Component[object]], /) -> None:
        """シーケンスを直接受け取り、内部ルックアップマップを安全にカプセル化して構築"""
        components_map: dict[type[object] | types.GenericAlias, Component[object]] = {}
        spec_components_map: dict[type[object], Component[object]] = {}

        for c in components:
            components_map[c.target_type] = c

            spec_type = getattr(c, "plugin_spec_type", None)
            if isinstance(spec_type, type):
                spec_components_map[spec_type] = c

        self._components_map: Final[Mapping[type[object] | types.GenericAlias, Component[object]]] = components_map
        self._spec_components_map: Final[Mapping[type[object], Component[object]]] = spec_components_map

    def lookup(
        self, target_type: type[object] | types.GenericAlias, plugin_name: str | None = None, /
    ) -> Component[object] | None:
        """指定されたターゲット型およびプラグイン名に基づきコンポーネント定義を高速探索"""
        if target_type in self._components_map:
            return self._components_map[target_type]

        if plugin_name and isinstance(target_type, type) and target_type in self._spec_components_map:
            return self._spec_components_map[target_type]

        return None
