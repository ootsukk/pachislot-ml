from __future__ import annotations

import contextlib
import types
import typing
from collections.abc import Callable, Sequence
from typing import Final, cast

from container.common.constants import ComponentScope
from container.common.exceptions import ComponentInstantiationError
from container.common.interfaces import Closable, ContextBuilder, RuntimeContainer, ScopeStrategy
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
        self._exit_stack: Final[contextlib.ExitStack] = contextlib.ExitStack()

    def rebuild(self) -> RuntimeContainer:
        if self._builder_context is None:
            raise RuntimeError("現在のコンテナインスタンスにビルド文脈（ContextBuilder）が登録されていません。")
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

        actual_type = resolvable.raw_type
        cache_key = CacheKey(actual_type, name)

        if self._scope.get(cache_key) is not None:
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

        actual_type = resolvable.raw_type

        cache_key = CacheKey(actual_type, name)
        if (cached := self._scope.get(cache_key)) is not None:
            return cast(T, cached)

        component = self._registry_data.lookup(actual_type, name)

        if component is None and isinstance(actual_type, types.GenericAlias):
            session = ResolutionSession(self, stack, name)

            def factory_action() -> object:
                if (cached_collection := self._scope.get(cache_key)) is not None:
                    return cached_collection

                naming_strategy = ChainNamingStrategy(name)
                dynamic_collection = self._instantiation_engine.instantiate_dynamic_collection(
                    resolvable, session, naming_strategy
                )
                self._scope.put(cache_key, dynamic_collection)
                return dynamic_collection

            result = session.execute_with_lock(cache_key, factory_action)
            return cast(T, result)

        if component is None:
            if name is not None:
                return cast(
                    T,
                    self._get_internal_instance(actual_type, stack, name=None),
                )
            if resolvable.is_optional:
                return None
            raise ComponentInstantiationError(f"未登録型: {actual_type}")

        session = ResolutionSession(self, stack, name)
        result = self._instantiation_engine.resolve_scoped_instance(
            component,
            cache_key,
            CacheKey(component.target_type, name),
            session,
        )
        return cast(T, result)

    def _register_resource(self, instance: object, /) -> None:
        if isinstance(instance, Closable):
            self._exit_stack.callback(instance.close)

    def close(self) -> None:
        self._scope.clear()
        self._exit_stack.close()
