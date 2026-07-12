from __future__ import annotations

import contextlib
import types
import typing
from collections.abc import Callable, Sequence
from typing import Final, Self, cast

from container.common.constants import ComponentScope
from container.common.exceptions import ComponentInstantiationError
from container.common.interfaces import AsyncClosable, Closable, ContextBuilder, RuntimeContainer, ScopeStrategy
from container.common.metadata import CacheKey
from container.core.engine import ComponentInstantiationEngine
from container.core.session import ResolutionSession
from container.definitions.component import ComponentRegistry
from container.definitions.naming import ChainNamingStrategy
from container.definitions.resolvable import ResolvableType


class RuntimeInstanceContainer(RuntimeContainer):
    """型解決の整合性を完全回復し、複数ファイル間の公称的一致を保証したコアコンテナ"""

    def __init__(
        self,
        registry_data: ComponentRegistry,
        scope_strategy: ScopeStrategy,
        instantiation_engine: ComponentInstantiationEngine,
        builder_context: ContextBuilder,
        /,
    ) -> None:
        self._registry_data: Final[ComponentRegistry] = registry_data
        self._scope: Final[ScopeStrategy] = scope_strategy
        self._instantiation_engine: Final[ComponentInstantiationEngine] = instantiation_engine
        self._builder_context: Final[ContextBuilder] = builder_context
        self._exit_stack: Final[contextlib.AsyncExitStack] = contextlib.AsyncExitStack()
        self._registered_resource_ids: Final[set[int]] = set()

    def rebuild(self) -> RuntimeContainer:
        if self._builder_context is None:
            raise RuntimeError("現在のコンテナインスタンスにビルド文脈(ContextBuilder)が登録されていません。")
        return self._builder_context.build()

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
        return self._get_internal_instance(target_type, set(), name=name)

    def resolve_all[T](self, spec_type: type[T], /, *, name: str | None = None) -> Sequence[T]:
        list_type = list[spec_type]
        return cast(Sequence[T], self._get_internal_instance(list_type, set(), name=name))

    def contains_instance(
        self, target_type: type[object] | types.GenericAlias | types.UnionType, /, *, name: str | None = None
    ) -> bool:
        resolvable = ResolvableType.from_annotation(target_type)
        if resolvable is None:
            return False

        adjusted_name, cache_key = self._create_cache_context(resolvable, name)

        if self._scope.get(cache_key) is not None:
            return True
        return self._registry_data.lookup(resolvable.raw_type, adjusted_name) is not None

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

    def _create_cache_context(
        self, resolvable: ResolvableType[object], name: str | None, /
    ) -> tuple[str | None, CacheKey]:
        actual_type = resolvable.raw_type
        if isinstance(actual_type, types.GenericAlias):
            element_type = resolvable.first_generic_argument
            adjusted_name = ChainNamingStrategy(name).get_collection_key(element_type)
        else:
            adjusted_name = name
        return adjusted_name, CacheKey(actual_type, adjusted_name)

    def _get_internal_instance[T](
        self,
        target_type: type[T] | types.GenericAlias | types.UnionType,
        stack: set[CacheKey],
        /,
        *,
        name: str | None = None,
    ) -> T | None:
        resolvable = ResolvableType.from_annotation(target_type)
        if resolvable is None:
            raise ComponentInstantiationError(f"指定された型アノテーションを解析できません: {target_type}")

        adjusted_name, cache_key = self._create_cache_context(resolvable, name)
        actual_type = resolvable.raw_type

        if (cached := self._scope.get(cache_key)) is not None:
            return cast(T, cached)

        component = self._registry_data.lookup(actual_type, adjusted_name)

        if component is None and isinstance(actual_type, types.GenericAlias):
            session = ResolutionSession(self, stack, adjusted_name)

            def factory_action() -> object:
                if (cached_collection := self._scope.get(cache_key)) is not None:
                    return cached_collection

                engine_naming_strategy = ChainNamingStrategy(adjusted_name)
                dynamic_collection = self._instantiation_engine.instantiate_dynamic_collection(
                    resolvable, session, engine_naming_strategy
                )
                self._scope.put(cache_key, dynamic_collection)
                return dynamic_collection

            result = session.execute_with_lock(cache_key, factory_action)

            if result is not None:
                self._register_resource(result)

            return cast(T, result)

        if component is None:
            if adjusted_name is not None:
                return cast(
                    T,
                    self._get_internal_instance(actual_type, stack, name=None),
                )
            if resolvable.is_optional:
                return None
            raise ComponentInstantiationError(f"未登録型: {actual_type}")

        session = ResolutionSession(self, stack, adjusted_name)
        result = self._instantiation_engine.resolve_scoped_instance(
            component,
            cache_key,
            CacheKey(component.target_type, adjusted_name),
            session,
        )

        if result is not None:
            self._register_resource(result)

        return cast(T, result)

    def _register_resource(self, instance: object, /) -> None:
        instance_id = id(instance)
        if instance_id in self._registered_resource_ids:
            return

        if isinstance(instance, AsyncClosable):
            self._exit_stack.push_async_callback(instance.close)
            self._registered_resource_ids.add(instance_id)
        elif isinstance(instance, Closable):
            self._exit_stack.callback(instance.close)
            self._registered_resource_ids.add(instance_id)

    async def close(self) -> None:
        self._scope.clear()
        await self._exit_stack.aclose()
        self._registered_resource_ids.clear()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
        /,
    ) -> None:
        await self.close()
