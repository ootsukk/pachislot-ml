from __future__ import annotations

import contextlib
import types
import typing
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Final, Protocol, cast, runtime_checkable

from container.component import ChainNamingStrategy, Component, ComponentRegistry
from container.constants import ComponentScope
from container.exceptions import CircularDependencyError, ComponentInstantiationError
from container.factory import (
    CollectionFactory,
    ComponentFactory,
    ConfigFactory,
    InstanceComponentFactory,
    PluginFactory,
)
from container.interfaces import ApplicationContext, BeanPostProcessor, Initializable
from container.resolvable_type import ResolvableType

if typing.TYPE_CHECKING:
    from container.register import PluginDefinition, PluginRegistry


@runtime_checkable
class Closable(Protocol):
    """構造的適合性を保証するためのリソース管理プロトコル"""

    def close(self) -> None: ...


@dataclass(frozen=True)
class CacheKey:
    """コンテナ内のインスタンスを一意に識別するための不変値オブジェクト [Robust Python]"""

    target_type: type[object] | types.GenericAlias
    plugin_name: str | None = None


@dataclass(frozen=True)
class BeanName:
    """基本型強迫(Primitive Obsession)を排除し、識別名セマンティクスを統治する不変値オブジェクト [Robust Python]"""

    value: Final[str]

    @classmethod
    def from_context(cls, component: Component[object], cache_key: CacheKey, /) -> BeanName:
        """型トポロジーとメタデータから、規約に準拠したBeanNameを一意に鋳造するファクトリ [Effective Python Item 77]"""
        match cache_key.target_type:
            case types.GenericAlias() as alias:
                resolved_value = (
                    component.key
                    if component.key
                    else f"{alias.__origin__.__name__.lower()}_of_{alias.__args__[0].__name__.lower()}"
                )
            case type() as t:
                resolved_value = (
                    f"{t.__name__.lower()}:{cache_key.plugin_name}"
                    if cache_key.plugin_name
                    else component.key
                    if component.key
                    else t.__name__.lower()
                )
            case _:
                resolved_value = str(cache_key.target_type)
        return cls(resolved_value)

    def __str__(self) -> str:
        """既存の文字列ベースのインフラやログとの透過的な互換性を保証"""
        return self.value


class SingletonBeanCache:
    """ダブルチェックロッキングによるスレッド安全なシングルトンBeanの保管および検索を専門に司るキャッシュ領域"""

    def __init__(self) -> None:
        self._lock: Final[Lock] = Lock()
        self._instances: Final[dict[CacheKey, object]] = {}

    def contains(self, key: CacheKey, /) -> bool:
        return key in self._instances

    def get(self, key: CacheKey, /) -> object | None:
        return self._instances.get(key)

    def put_if_absent(self, key: CacheKey, instance: object, /) -> None:
        with self._lock:
            if key not in self._instances:
                self._instances[key] = instance

    @contextlib.contextmanager
    def synchronize_instantiation(self, key: CacheKey, stack: set[CacheKey], /) -> typing.Iterator[None]:
        if key in stack:
            raise CircularDependencyError(f"循環依存が検出されました。型閉路パス: {key.target_type}")

        with self._lock:
            if key in self._instances:
                return

            stack.add(key)
            try:
                yield
            finally:
                stack.remove(key)

    def set_alias(self, alias_key: CacheKey, target_key: CacheKey, /) -> None:
        with self._lock:
            instance = self._instances.get(target_key)
            if instance is not None and alias_key not in self._instances:
                self._instances[alias_key] = instance

    def clear(self) -> None:
        with self._lock:
            self._instances.clear()


class ResolutionSession:
    """単一の解決要求のライフサイクルをホールドしファクトリ層への文脈仲介とBPP適用を統括するコンテキスト"""

    def __init__(
        self,
        container: Container,
        stack: set[CacheKey],
        requested_plugin_name: str | None = None,
        /,
    ) -> None:
        self._container: Final[Container] = container
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

    def apply_lifecycle_pipeline(self, instance: object, bean_name: BeanName, /) -> object:
        """【型安全化】シグネチャをプレイン文字列からBeanName不変値オブジェクトへ変更"""
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
        from container.component import InstanceComponent, PluginComponent, PluginListComponent, PropertyComponent

        if component_type is InstanceComponent:
            return cast(ComponentFactory[C], self._instance_factory)
        if component_type is PropertyComponent:
            return cast(ComponentFactory[C], self._config_factory)
        if component_type is PluginComponent:
            return cast(ComponentFactory[C], self._plugin_factory)
        if component_type is PluginListComponent:
            return cast(ComponentFactory[C], self._collection_factory)
        return None


class ComponentInstantiationEngine:
    """静的定義、動的コレクション、およびコンポーネントスコープの全解決責任を一元統括するエンジン"""

    def __init__(
        self,
        registry: PluginRegistry,
        raw_config: Mapping[str, object],
        factory_registry: ComponentFactoryRegistry,
        bpp_chain: Sequence[BeanPostProcessor],
        /,
    ) -> None:
        self.registry: Final[PluginRegistry] = registry
        self.raw_config: Final[Mapping[str, object]] = raw_config
        self._factory_registry: Final[ComponentFactoryRegistry] = factory_registry
        self._collection_factory: Final[CollectionFactory] = factory_registry.collection_factory
        self._bpp_chain: Final[tuple[BeanPostProcessor, ...]] = tuple(bpp_chain)

    def resolve_plugin_stream(self, spec_type: type[object], /) -> list[PluginDefinition[object]]:
        definitions = self.registry.get_all_definitions(spec_type)
        return sorted(definitions, key=lambda d: d.priority, reverse=True)

    def apply_pipeline(self, instance: object, bean_name: BeanName, /) -> object:
        """【型安全化】BPP適用境界において、BeanNameオブジェクトから透過的に値を取り出してパイプラインを実行"""
        current_bean = instance
        name_str = bean_name.value
        for bpp in self._bpp_chain:
            current_bean = bpp.post_process_before_initialization(current_bean, name_str)
        if isinstance(current_bean, Initializable):
            current_bean.initialize()
        for bpp in self._bpp_chain:
            current_bean = bpp.post_process_after_initialization(current_bean, name_str)
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
        # FIXME type: ignore
        return self._collection_factory.create_collection(
            sorted_defs, self.raw_config, resolvable, session, ChainNamingStrategy()
        ) # type: ignore

    def resolve_scoped_instance(
        self, component: Component[object], cache_key: CacheKey, actual_key: CacheKey, session: ResolutionSession, /
    ) -> object:
        """【複雑性解消】命名ロジックをBeanName値オブジェクトへ完全委譲し、自身の責務を実体化オーケストレーションへ集約"""
        bean_name = BeanName.from_context(component, cache_key)

        if component.scope == ComponentScope.TRANSIENT:
            raw_inst = self.instantiate(component, session)
            return self.apply_pipeline(raw_inst, bean_name)

        def factory_action() -> object:
            raw_inst = self.instantiate(component, session)
            processed_bean = self.apply_pipeline(raw_inst, bean_name)
            session.register_resource(processed_bean)
            session.put_cached_instance(cache_key, processed_bean)
            if cache_key != actual_key:
                session.set_cache_alias(actual_key, cache_key)
            return processed_bean

        return session.execute_with_lock(cache_key, factory_action)


class Container(ApplicationContext):
    """シングルトンキャッシュとスレッド安全なルックアップ、および破棄ライフサイクルに特化したランタイムコア"""

    def __init__(
        self,
        registry_data: ComponentRegistry,
        registry: PluginRegistry,
        raw_config: Mapping[str, object],
        post_processors: Sequence[BeanPostProcessor],
        factory_registry: ComponentFactoryRegistry,
        /,
    ) -> None:
        self._registry_data: Final[ComponentRegistry] = registry_data
        self._exit_stack: Final[contextlib.ExitStack] = contextlib.ExitStack()
        self._cache: Final[SingletonBeanCache] = SingletonBeanCache()

        self._instantiation_engine: Final[ComponentInstantiationEngine] = ComponentInstantiationEngine(
            registry,
            raw_config,
            factory_registry,
            post_processors,
        )

    def get_instance[T](self, target_type: type[T] | types.GenericAlias, /, *, name: str | None = None) -> T:
        return self._get_internal_instance(target_type, set(), plugin_name=name)

    def get_instances_by_spec[T](self, spec_type: type[T], /) -> Sequence[T]:
        session = ResolutionSession(self, set())
        resolvable = ResolvableType[T](list[spec_type])
        return self._instantiation_engine.instantiate_dynamic_collection(resolvable, session)

    def _get_internal_instance[T](
        self,
        target_type: type[T] | types.GenericAlias,
        stack: set[CacheKey],
        *,
        plugin_name: str | None = None,
    ) -> T:
        cache_key = CacheKey(target_type, plugin_name)

        if (cached := self._cache.get(cache_key)) is not None:
            return cast(T, cached)

        component = self._registry_data.lookup(target_type, plugin_name)
        if component is None and isinstance(target_type, types.GenericAlias):
            session = ResolutionSession(self, stack, plugin_name)
            resolvable_lookup = ResolvableType[typing.Any](target_type)
            dynamic_collection = self._instantiation_engine.instantiate_dynamic_collection(resolvable_lookup, session)
            self._cache.put_if_absent(cache_key, dynamic_collection)
            return cast(T, dynamic_collection)

        if component is None:
            if plugin_name:
                return self._get_internal_instance(target_type, stack, plugin_name=None)
            raise ComponentInstantiationError(f"未登録型: {target_type}")

        session = ResolutionSession(self, stack, plugin_name)

        result = self._instantiation_engine.resolve_scoped_instance(
            component, cache_key, CacheKey(component.target_type, plugin_name), session
        )
        return cast(T, result)

    def _register_resource(self, instance: object, /) -> None:
        if isinstance(instance, Closable):
            self._exit_stack.callback(instance.close)

    def close(self) -> None:
        self._cache.clear()
        self._exit_stack.close()
