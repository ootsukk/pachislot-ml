from __future__ import annotations

import contextlib
import types
import typing
from collections.abc import Callable, Mapping, Sequence
from typing import Final, cast

from container.component import ComponentRegistry
from container.constants import ComponentScope
from container.context import (
    CacheKey,
    Closable,
    ComponentFactoryRegistry,
    ComponentInstantiationEngine,
    ResolutionSession,
    SingletonBeanCache,
)
from container.exceptions import ComponentInstantiationError
from container.interfaces import ApplicationContext, BeanPostProcessor
from container.register import PluginRegistry
from container.resolvable_type import ResolvableType


class Container(ApplicationContext):
    """型解決の整合性を完全回復し、複数ファイル間の公称的一致を保証したコアコンテナ"""

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

    # --------------------------------------------------------------------------
    # 公開解決API (型パラメータ [T] が必ず2回出現するようオーバーロードをアライン)
    # --------------------------------------------------------------------------

    @typing.overload
    def resolve[T](self, target_type: type[T] | types.GenericAlias, /, *, name: str | None = None) -> T: ...

    @typing.overload
    def resolve[T](
        self, target_type: type[T] | types.GenericAlias | types.UnionType, /, *, name: str | None = None
    ) -> T | None: ...

    def resolve[T](
        self,
        target_type: type[T] | types.GenericAlias | types.UnionType,
        /,
        *,
        name: str | None = None,
    ) -> T | None:
        return self._get_internal_instance(target_type, set(), plugin_name=name)

    def resolve_all[T](self, spec_type: type[T], /) -> Sequence[T]:
        session = ResolutionSession(self, set())
        resolvable = ResolvableType[T](list[spec_type])
        return self._instantiation_engine.instantiate_dynamic_collection(resolvable, session)

    # --------------------------------------------------------------------------
    # 状態検証および遅延解決API
    # --------------------------------------------------------------------------

    def contains_instance(
        self, target_type: type[object] | types.GenericAlias | types.UnionType, /, *, name: str | None = None
    ) -> bool:
        if isinstance(target_type, types.UnionType):
            args = typing.get_args(target_type)
            remaining = [a for a in args if a is not type(None)]
            if remaining and isinstance(remaining[0], type | types.GenericAlias):
                actual_type: type[object] | types.GenericAlias = remaining[0]
            else:
                actual_type = object
        else:
            actual_type = target_type

        cache_key = CacheKey(actual_type, name)
        if self._cache.contains(cache_key):
            return True
        return self._registry_data.lookup(actual_type, name) is not None

    def is_singleton(self, target_type: type[object] | types.GenericAlias, /, *, name: str | None = None) -> bool:
        component = self._registry_data.lookup(target_type, name)
        if component is not None:
            return component.scope == ComponentScope.SINGLETON
        return False

    def resolve_provider[T](
        self, target_type: type[T] | types.GenericAlias, /, *, name: str | None = None
    ) -> Callable[[], T]:
        if not self.contains_instance(target_type, name=name):
            raise ComponentInstantiationError(f"プロバイダを構築するための型定義が未登録です: {target_type}")
        return lambda: cast(T, self.resolve(target_type, name=name))

    # --------------------------------------------------------------------------
    # マジックメソッド多重定義 (出現回数警告ルールに基づき完全にアライン)
    # --------------------------------------------------------------------------

    @typing.overload
    def __getitem__[T](self, item: type[T] | types.GenericAlias, /) -> T: ...

    @typing.overload
    def __getitem__[T](self, item: type[T] | types.GenericAlias | types.UnionType, /) -> T | None: ...

    @typing.overload
    def __getitem__[T](self, item: tuple[type[T] | types.GenericAlias, str], /) -> T: ...

    @typing.overload
    def __getitem__[T](self, item: tuple[type[T] | types.GenericAlias | types.UnionType, str], /) -> T | None: ...

    def __getitem__[T](
        self,
        item: type[T]
        | types.GenericAlias
        | types.UnionType
        | tuple[type[T] | types.GenericAlias | types.UnionType, str],
        /,
    ) -> T | None:
        match item:
            case tuple() as pair:
                t_type, p_name = pair
                return self.resolve(t_type, name=p_name)
            case _:
                return self.resolve(item, name=None)

    @typing.overload
    def __contains__(self, item: type[object] | types.GenericAlias | types.UnionType, /) -> bool: ...

    @typing.overload
    def __contains__(self, item: tuple[type[object] | types.GenericAlias | types.UnionType, str], /) -> bool: ...

    def __contains__(
        self,
        item: type[object]
        | types.GenericAlias
        | types.UnionType
        | tuple[type[object] | types.GenericAlias | types.UnionType, str],
        /,
    ) -> bool:
        match item:
            case tuple() as pair:
                t_type, p_name = pair
                return self.contains_instance(t_type, name=p_name)
            case _:
                return self.contains_instance(item, name=None)

    # --------------------------------------------------------------------------
    # 内部解決コアインフラ
    # --------------------------------------------------------------------------

    def _get_internal_instance[T](
        self,
        target_type: type[T] | types.GenericAlias | types.UnionType,
        stack: set[CacheKey],
        *,
        plugin_name: str | None = None,
    ) -> T | None:
        is_optional = False
        actual_type: type[object] | types.GenericAlias

        if isinstance(target_type, types.UnionType):
            args = typing.get_args(target_type)
            if type(None) in args:
                is_optional = True
                remaining = [a for a in args if a is not type(None)]
                if remaining and isinstance(remaining[0], type | types.GenericAlias):
                    actual_type = remaining[0]
                else:
                    actual_type = object
            else:
                actual_type = args[0] if args and isinstance(args[0], type | types.GenericAlias) else object
        else:
            actual_type = target_type

        cache_key = CacheKey(actual_type, plugin_name)
        if (cached := self._cache.get(cache_key)) is not None:
            return cast(T, cached)

        component = self._registry_data.lookup(actual_type, plugin_name)
        if component is None and isinstance(actual_type, types.GenericAlias):
            session = ResolutionSession(self, stack, plugin_name)
            resolvable_lookup = ResolvableType[T](actual_type)
            dynamic_collection = self._instantiation_engine.instantiate_dynamic_collection(resolvable_lookup, session)
            self._cache.put_if_absent(cache_key, dynamic_collection)
            return cast(T, dynamic_collection)

        if component is None:
            if plugin_name:
                return cast(T, self._get_internal_instance(actual_type, stack, plugin_name=None))
            if is_optional:
                return None
            raise ComponentInstantiationError(f"未登録型: {actual_type}")

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
