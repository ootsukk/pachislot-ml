from __future__ import annotations

import types
import typing
from typing import Final, Protocol, cast, runtime_checkable

from container.core.cache import CacheKey, ComponentId
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


@runtime_checkable
class Closable(Protocol):
    """構造的適合性を保証するためのリソース管理プロトコル"""

    def close(self) -> None: ...


class ResolutionSession:
    """単一の解決要求のライフサイクルをホールドしファクトリ層への文脈仲介とBPP適用を統括するコンテキスト"""

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
        self._container._cache.put_if_absent(key, instance)

    def set_cache_alias(self, alias_key: CacheKey, target_key: CacheKey, /) -> None:
        self._container._cache.set_alias(alias_key, target_key)

    def execute_with_lock(self, key: CacheKey, factory_callback: typing.Callable[[], object], /) -> object:
        with self._container._cache.synchronize_instantiation(key, self._stack):
            return factory_callback()


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
