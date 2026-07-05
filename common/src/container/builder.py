from __future__ import annotations

import contextlib
import graphlib
import types
import typing
from collections.abc import Mapping, Sequence
from typing import Final, cast

from container.component import Component, ComponentRegistry, PluginSetting
from container.context import CacheKey, ComponentFactoryRegistry, Container
from container.exceptions import ComponentInstantiationError
from container.factory import CollectionFactory, CollectionInstanceFactory, ConfigFactory, ConfigInstanceFactory, PluginFactory, PluginInstanceFactory
from container.interfaces import ApplicationContext, BeanPostProcessor
from container.register import PluginRegistry, PluginScanner
from container.resolvable_type import ResolvableType


class DependencyGraphSorter:
    """コンポーネント定義書およびレジストリストアの静的メタデータから、型安全なトポロジカルソート順を算出する専任クラス。"""

    def __init__(
        self,
        registry_data: ComponentRegistry,
        registry: PluginRegistry,
        raw_config: Mapping[str, object],
        components: Sequence[Component[object]],
        /,
    ) -> None:
        self._registry_data: Final[ComponentRegistry] = registry_data
        self._registry: Final[PluginRegistry] = registry
        self._raw_config: Final[Mapping[str, object]] = raw_config
        self._components: Final[Sequence[Component[object]]] = tuple(components)

    def sort_nodes(self, config_type: type[object], /) -> Sequence[type[object] | types.GenericAlias]:
        """直交依存関係グラフを網羅的に構築し、閉路（循環参照）の検証を行った後、ソート済みのノードシーケンスを返却します。"""
        sorter: graphlib.TopologicalSorter[type[object] | types.GenericAlias] = graphlib.TopologicalSorter()

        # 構成設定のルートノードを登録
        sorter.add(config_type)

        # 登録されている全コンポーネントの基本仕様型をノード登録
        for comp in self._components:
            sorter.add(comp.target_type)

        # 具象レジストリ情報を走査してエッジを確定
        for comp in self._components:
            if hasattr(comp, "plugin_spec_type") and (spec_type := getattr(comp, "plugin_spec_type")) is not None:
                if not isinstance(spec_type, type):
                    continue

                definitions = self._registry.get_all_definitions(spec_type)

                for definition in definitions:
                    raw_payload = self._raw_config.get(definition.plugin_name)
                    setting = PluginSetting(raw_payload, comp.naming_strategy)
                    if not setting.enabled:
                        continue

                    impl_node = definition.impl_class
                    sorter.add(impl_node, spec_type)

                    for dep_spec in definition.depends_on:
                        sorter.add(impl_node, dep_spec)

                    for dep_param_type in definition.constructor_dependencies.values():
                        resolvable_dep = ResolvableType[typing.Any](dep_param_type)
                        target_lookup = resolvable_dep.raw_type

                        # 【移譲完了】内部マップへの直通参照を廃止し、値オブジェクトの探索関数へ一本化
                        if self._registry_data.lookup(target_lookup) is not None:
                            sorter.add(impl_node, target_lookup)
                        elif self._registry_data.lookup(resolvable_dep.origin) is not None:
                            sorter.add(impl_node, resolvable_dep.origin)

        # 静的解析フェーズにおける循環参照の検出
        try:
            return list(sorter.static_order())
        except graphlib.CycleError as err:
            raise RuntimeError(
                f"トポロジー上に閉路（循環参照）が検出されたため、コンテナの構築を安全に停止します: {err}"
            ) from err


class ApplicationContextBuilder:
    """SpringのContext.refresh思想をカプセル化し、自動コンポーネントスキャンから検証、Contextの鋳造までを司る専任ビルダー。"""

    def __init__(
        self,
        config: object,
        components: Sequence[Component[object]],
        /,
        *,
        root_package_names: Sequence[str] | None = None,
        plugin_groups: Sequence[str] = (),
        ignored_types: Sequence[type[object]] = (),
        cache_index_data: Mapping[str, Mapping[str, Mapping[str, object]]] | None = None,
        explicit_registry: PluginRegistry | None = None,
        provided_instances: Mapping[type[object] | types.GenericAlias | tuple[type[object], str], object] | None = None,
        post_processors: Sequence[BeanPostProcessor] = (),
    ) -> None:
        self._config: Final[object] = config
        self._components: Final[Sequence[Component[object]]] = tuple(components)

        self._root_package_names: Final[Sequence[str] | None] = (
            tuple(root_package_names) if root_package_names is not None else None
        )
        self._plugin_groups: Final[Sequence[str]] = tuple(plugin_groups)
        self._ignored_types: Final[Sequence[type[object]]] = tuple(ignored_types)
        self._cache_index_data: Final[Mapping[str, Mapping[str, Mapping[str, object]]] | None] = cache_index_data

        self._explicit_registry: Final[PluginRegistry | None] = explicit_registry
        self._provided_instances: Final[
            Mapping[type[object] | types.GenericAlias | tuple[type[object], str], object]
        ] = provided_instances or {}
        self._post_processors: Final[Sequence[BeanPostProcessor]] = tuple(
            sorted(post_processors, key=lambda x: x.priority)
        )

    def build(self) -> ApplicationContext:
        """静的グラフトポロジー検証を執行し、シングルトンの先行生成（Eager Init）を完遂した不変のContextを返却します。"""
        raw_config_map: dict[str, object] = {}
        if hasattr(self._config, "__dict__"):
            raw_config_map.update({str(k): v for k, v in self._config.__dict__.items()})

        if self._explicit_registry is not None:
            registry = self._explicit_registry
        elif self._root_package_names is not None:
            scanner = PluginScanner(
                self._root_package_names, self._plugin_groups, self._components, ignored_types=self._ignored_types
            )
            registry = scanner.scan(cache_index_data=self._cache_index_data)
        else:
            raise ValueError(
                "ApplicationContextBuilder の構築には、root_package_names または explicit_registry のいずれかが必須です。"
            )

        registry_data = ComponentRegistry(self._components)

        # 依存関係グラフ解析責任を専用クラスへ完全委譲
        graph_sorter = DependencyGraphSorter(registry_data, registry, raw_config_map, self._components)
        config_type = type(self._config)
        perfectly_ordered_nodes = graph_sorter.sort_nodes(config_type)

        # インフラ構築に必要なサブファクトリ群を初期化
        config_factory = ConfigInstanceFactory()
        plugin_factory = PluginInstanceFactory()
        collection_factory = CollectionInstanceFactory(plugin_factory)
        factory_registry = ComponentFactoryRegistry(config_factory, plugin_factory, collection_factory)

        # 純化されたコンテナコアの生成
        container = Container(registry_data, registry, raw_config_map, self._post_processors, factory_registry)

        # 【キャッシュ安全化】PEP 634 match-caseを用いて提供インスタンスを二重ロックキャッシュへ安全に登録
        for p_key, p_inst in self._provided_instances.items():
            match p_key:
                case tuple() as pair:
                    t_type, p_name = pair
                    cache_key = CacheKey(t_type, p_name)
                case _:
                    cache_key = CacheKey(p_key, None)
            container._cache.put_if_absent(cache_key, p_inst)

        container._cache.put_if_absent(CacheKey(config_type, None), self._config)

        # トポロジカルソート順に従い、シングルトンの先行実体化（Eager Initialization）を型安全に完遂
        for node in perfectly_ordered_nodes:
            if registry_data.lookup(node) is not None:
                container.get_instance(node)

        return container
