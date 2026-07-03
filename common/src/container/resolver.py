from __future__ import annotations

import graphlib
import typing
from collections.abc import Mapping, Sequence

from container.component import Component
from container.context import Container
from container.interfaces import ApplicationContext, BeanPostProcessor
from container.register import PluginRegistry


class UniversalPluginResolver:
    """アプリケーション起動時にトポロジカルソートを実行してコンテナの安全性を確定させる最上位ブートストラッパー。"""

    _DYNAMIC_COMPONENTS: list[Component[typing.Any]] = []

    @classmethod
    def register_component(cls, component: Component[typing.Any], /) -> None:
        if component not in cls._DYNAMIC_COMPONENTS:
            cls._DYNAMIC_COMPONENTS.append(component)

    def __init__(
        self,
        config: object,
        registry: PluginRegistry,
        components: Sequence[Component[typing.Any]],
        provided_instances: Mapping[type[object], object] | None = None,
        post_processors: Sequence[BeanPostProcessor] = (),
        /,
    ) -> None:
        self.config = config
        self.registry = registry
        self.components = list(components)
        self.provided_instances = provided_instances or {}
        self.post_processors = list(post_processors)

    def resolve_all(self) -> ApplicationContext:
        """3つの直線的フェーズを直線執行し堅牢にビルドが完了したApplicationContextを返却します。"""
        type_to_comp = {c.target_type: c for c in self.components}
        sorter: graphlib.TopologicalSorter[type[object]] = graphlib.TopologicalSorter()

        for c in self.components:
            sorter.add(c.target_type)
            if hasattr(c, "plugin_spec_type"):
                plugin_name = c.key if hasattr(c, "key") else c.target_type.__name__.lower()
                definition = self.registry.get_definition(c.plugin_spec_type, plugin_name)
                if definition is not None:
                    for dep_spec_type in definition.depends_on:
                        if dep_spec_type in type_to_comp:
                            sorter.add(c.target_type, dep_spec_type)

        try:
            perfectly_ordered_types = list(sorter.static_order())
        except graphlib.CycleError as err:
            raise RuntimeError(f"トポロジー上の閉路（循環参照）を検出したため、起動を安全に停止します: {err}") from err

        raw_config_map: dict[str, object] = {}
        if hasattr(self.config, "__dict__"):
            raw_config_map.update({str(k): v for k, v in self.config.__dict__.items()})

        container = Container(self.components, self.registry, raw_config_map, self.post_processors)

        for p_type, p_inst in self.provided_instances.items():
            container._instances[p_type] = p_inst
        container._instances[type(self.config)] = self.config

        for target_type in perfectly_ordered_types:
            if target_type in type_to_comp:
                container.get_component(target_type)

        return container
