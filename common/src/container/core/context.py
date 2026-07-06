from __future__ import annotations

import types
import typing
from typing import Final, cast

from container.common.metadata import CacheKey, ComponentId
from container.definitions.component import Component
from container.instantiation.factory import (
    CollectionFactory,
    ComponentFactory,
    ConfigFactory,
    InstanceComponentFactory,
    PluginFactory,
)

if typing.TYPE_CHECKING:
    from container.core.container import RuntimeInstanceResolver
    from container.definitions.registry import PluginDefinition


class ResolutionSession:
    """単一の解決要求のライフサイクルをホールドしファクトリ層への文脈仲介とIPP適用を統括するコンテキスト"""

    def __init__(
        self,
        container: RuntimeInstanceResolver,
        stack: set[CacheKey],
        requested_plugin_name: str | None = None,
        /,
    ) -> None:
        self._container: Final[RuntimeInstanceResolver] = container
        self._stack: Final[set[CacheKey]] = stack
        self._requested_plugin_name: Final[str | None] = requested_plugin_name

    @property
    def requested_plugin_name(self) -> str | None:
        return self._requested_plugin_name

    @property
    def stack(self) -> set[CacheKey]:
        return self._stack

    def resolve_dependency_node(
        self, target_type: type[object] | types.GenericAlias, param_name: str | None = None, /
    ) -> object:
        return self._container._get_internal_instance(target_type, self._stack, plugin_name=param_name)

    def resolve_plugin_stream(self, spec_type: type[object], /) -> list[PluginDefinition[object]]:
        return self._container._instantiation_engine.resolve_plugin_stream(spec_type)

    def apply_lifecycle_pipeline(self, instance: object, bean_name: ComponentId, /) -> object:
        return self._container._instantiation_engine.apply_pipeline(instance, bean_name)

    def register_resource(self, instance: object, /) -> None:
        self._container._register_resource(instance)

    def put_cached_instance(self, key: CacheKey, instance: object, /) -> None:
        self._container._scope.put(key, instance)

    def execute_with_lock(self, key: CacheKey, factory_callback: typing.Callable[[], object], /) -> object:
        from container.common.exceptions import CircularDependencyError
        if key in self._stack:
            raise CircularDependencyError(f"循環依存が検出されました。パス: {key.target_type}")

        if (cached := self._container._scope.get(key)) is not None:
            return cached

        self._stack.add(key)
        try:
            with self._container._scope.synchronize(key):
                if (cached_double := self._container._scope.get(key)) is not None:
                    return cached_double
                return factory_callback()
        finally:
            self._stack.remove(key)


class ComponentFactoryRegistry:
    """PEP 695上限境界ジェネリクスを用いて静的型安全性を完全保証したファクトリレジストリ"""

    def __init__(
        self,
        config_factory: ConfigFactory,
        plugin_factory: PluginFactory,
        collection_factory: CollectionFactory,
        /,
    ) -> None:
        self._instance_factory: Final = InstanceComponentFactory()
        self._config_factory: Final = config_factory
        self._plugin_factory: Final = plugin_factory
        self._collection_factory: Final = collection_factory

    @property
    def collection_factory(self) -> CollectionFactory:
        return self._collection_factory

    def get_factory[C: Component[object]](self, component_type: type[C], /) -> ComponentFactory[C] | None:
        from container.definitions.component import (
            InstanceComponent,
            PluginComponent,
            PluginListComponent,
            PropertyComponent,
        )

        if component_type is InstanceComponent:
            return cast(ComponentFactory[C], self._instance_factory)
        if component_type is PropertyComponent:
            return cast(ComponentFactory[C], self._config_factory)
        if component_type is PluginComponent:
            return cast(ComponentFactory[C], self._plugin_factory)
        if component_type is PluginListComponent:
            return cast(ComponentFactory[C], self._collection_factory)
        return None
