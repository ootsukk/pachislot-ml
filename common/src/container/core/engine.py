from __future__ import annotations

import typing
from collections.abc import Mapping, Sequence
from typing import Final

from container.common.constants import ComponentScope
from container.common.exceptions import ComponentInstantiationError
from container.common.interfaces import Initializable, InstancePostProcessor
from container.common.metadata import CacheKey, ComponentId
from container.definitions.component import Component
from container.definitions.naming import ChainNamingStrategy
from container.definitions.resolvable import ResolvableType

if typing.TYPE_CHECKING:
    from container.core.context import ComponentFactoryRegistry, ResolutionSession
    from container.definitions.registry import PluginDefinition, PluginRegistry


class ComponentInstantiationEngine:
    """静的定義、動的コレクション、およびコンポーネントスコープの全解決責任を一元統括するエンジン"""

    def __init__(
        self,
        registry: PluginRegistry,
        raw_config: Mapping[str, object],
        factory_registry: ComponentFactoryRegistry,
        ipp_chain: Sequence[InstancePostProcessor],
        /,
    ) -> None:
        self.registry: Final[PluginRegistry] = registry
        self.raw_config: Final[Mapping[str, object]] = raw_config
        self._factory_registry: Final[ComponentFactoryRegistry] = factory_registry
        self._collection_factory: Final = factory_registry.collection_factory
        self._ipp_chain: Final[tuple[InstancePostProcessor, ...]] = tuple(ipp_chain)

    def resolve_plugin_stream(self, spec_type: type[object], /) -> list[PluginDefinition[object]]:
        definitions = self.registry.get_all_definitions(spec_type)
        return sorted(definitions, key=lambda d: d.priority, reverse=True)

    def apply_pipeline(self, instance: object, bean_name: ComponentId, /) -> object:
        current_bean = instance
        name_str = bean_name.value
        for ipp in self._ipp_chain:
            current_bean = ipp.post_process_before(current_bean, name_str)
        if isinstance(current_bean, Initializable):
            current_bean.initialize()
        for ipp in self._ipp_chain:
            current_bean = ipp.post_process_after(current_bean, name_str)
        return current_bean

    def instantiate(self, component: Component[object], session: ResolutionSession, /) -> object | None:
        comp_type = type(component)
        factory = self._factory_registry.get_factory(comp_type)
        if factory is None:
            raise ComponentInstantiationError(f"未対応の仕様コンポーネント型: {comp_type.__name__}")

        instance = factory.create_instance(component, session, self.raw_config)
        if instance is None and component.mandatory:
            raise ComponentInstantiationError(f"必須コンポーネントの解決に失敗: {component.key}")
        return instance

    def instantiate_dynamic_collection[E](
        self, resolvable: ResolvableType[E], session: ResolutionSession, /
    ) -> Sequence[E]:
        element_type = resolvable.first_generic_argument
        sorted_defs = self.resolve_plugin_stream(element_type)
        collection_instance = self._collection_factory.create_collection(
            sorted_defs, self.raw_config, resolvable, session, ChainNamingStrategy()
        )
        return typing.cast(Sequence[E], collection_instance)

    def resolve_scoped_instance(
        self, component: Component[object], cache_key: CacheKey, actual_key: CacheKey, session: ResolutionSession, /
    ) -> object:
        bean_name = ComponentId.from_context(component, cache_key)

        if component.scope == ComponentScope.TRANSIENT:
            raw_inst = self.instantiate(component, session)
            return self.apply_pipeline(raw_inst, bean_name)

        def factory_action() -> object:
            raw_inst = self.instantiate(component, session)
            processed_bean = self.apply_pipeline(raw_inst, bean_name)
            session.register_resource(processed_bean)
            session.put_cached_instance(cache_key, processed_bean)
            if cache_key != actual_key:
                session.put_cached_instance(actual_key, processed_bean)
            return processed_bean

        return session.execute_with_lock(cache_key, factory_action)
