from __future__ import annotations

import threading
import types
from collections.abc import Callable, Sequence
from typing import Final, Protocol, cast, overload

from container.common.exceptions import ContainerError
from container.common.interfaces import Closable, RuntimeContainer


class _ExtendedContainer(Protocol):
    def resolve[T](
        self,
        target_type: type[T] | types.GenericAlias | types.UnionType,
        /,
        *,
        name: str | None = None,
    ) -> T | None: ...

    def resolve_all[T](
        self,
        spec_type: type[T],
        /,
        *,
        name: str | None = None,
    ) -> Sequence[T]: ...

    def resolve_provider[T](
        self,
        target_type: type[T] | types.GenericAlias,
        /,
        *,
        name: str | None = None,
    ) -> Callable[[], T]: ...

    def __getitem__[T](
        self,
        item: type[T]
        | types.GenericAlias
        | types.UnionType
        | tuple[type[T] | types.GenericAlias | types.UnionType, str],
        /,
    ) -> T | None: ...

    def __contains__(
        self,
        item: type[object]
        | types.GenericAlias
        | types.UnionType
        | tuple[type[object] | types.GenericAlias | types.UnionType, str],
        /,
    ) -> bool: ...


class _ContainerFacadeMeta(type):
    _instance: RuntimeContainer | None = None
    _lock: Final[threading.Lock] = threading.Lock()

    @property
    def instance(cls) -> RuntimeContainer:
        inst = cls._instance
        if inst is None:
            raise ContainerError("Global container instance is not initialized yet.")
        return inst

    @overload
    def __getitem__[T](cls, item: type[T] | types.GenericAlias, /) -> T: ...

    @overload
    def __getitem__[T](cls, item: type[T] | types.GenericAlias | types.UnionType, /) -> T | None: ...

    @overload
    def __getitem__[T](cls, item: tuple[type[T] | types.GenericAlias, str], /) -> T: ...

    @overload
    def __getitem__[T](cls, item: tuple[type[T] | types.GenericAlias | types.UnionType, str], /) -> T | None: ...

    def __getitem__[T](
        cls,
        item: type[T]
        | types.GenericAlias
        | types.UnionType
        | tuple[type[T] | types.GenericAlias | types.UnionType, str],
        /,
    ) -> T | None:
        inst = cast(_ExtendedContainer, cls.instance)
        return inst.__getitem__(item)

    def __contains__(
        cls,
        item: type[object]
        | types.GenericAlias
        | types.UnionType
        | tuple[type[object] | types.GenericAlias | types.UnionType, str],
        /,
    ) -> bool:
        inst = cls._instance
        if inst is not None and hasattr(inst, "__contains__"):
            return cast(_ExtendedContainer, inst).__contains__(item)
        return False


class ContainerFacade(metaclass=_ContainerFacadeMeta):
    def __init__(self) -> None:
        raise TypeError("ContainerFacade cannot be instantiated directly.")

    @classmethod
    def initialize(cls, container: RuntimeContainer, /) -> None:
        meta = cast(_ContainerFacadeMeta, type(cls))
        with meta._lock:
            if meta._instance is not None:
                raise ContainerError("Global container instance has already been initialized.")
            meta._instance = container

    @classmethod
    def clear(cls) -> None:
        meta = cast(_ContainerFacadeMeta, type(cls))
        with meta._lock:
            if meta._instance is not None:
                if isinstance(meta._instance, Closable):
                    meta._instance.close()
                meta._instance = None

    @classmethod
    def resolve[T](
        cls,
        target_type: type[T] | types.GenericAlias | types.UnionType,
        /,
        *,
        name: str | None = None,
    ) -> T | None:
        meta = cast(_ContainerFacadeMeta, type(cls))
        return meta.instance.resolve(target_type, name=name)

    @classmethod
    def resolve_all[T](cls, spec_type: type[T], /, *, name: str | None = None) -> Sequence[T]:
        meta = cast(_ContainerFacadeMeta, type(cls))
        inst = cast(_ExtendedContainer, meta.instance)
        return inst.resolve_all(spec_type, name=name)

    @classmethod
    def resolve_provider[T](
        cls, target_type: type[T] | types.GenericAlias, /, *, name: str | None = None
    ) -> Callable[[], T]:
        meta = cast(_ContainerFacadeMeta, type(cls))
        inst = cast(_ExtendedContainer, meta.instance)
        return inst.resolve_provider(target_type, name=name)
