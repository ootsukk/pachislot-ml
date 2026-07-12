from __future__ import annotations

import contextvars
import types
from collections.abc import Awaitable, Callable, Mapping, Sequence
from enum import StrEnum
from typing import Final, Protocol, TypedDict, cast, runtime_checkable

from container.common.exceptions import ContainerError
from container.common.interfaces import AsyncClosable, Closable, RuntimeContainer


class AsgiLifespanType(StrEnum):
    STARTUP = "lifespan.startup"
    STARTUP_COMPLETE = "lifespan.startup.complete"
    STARTUP_FAILED = "lifespan.startup.failed"
    SHUTDOWN = "lifespan.shutdown"
    SHUTDOWN_COMPLETE = "lifespan.shutdown.complete"
    SHUTDOWN_FAILED = "lifespan.shutdown.failed"


class AsgiLifespanMessage(TypedDict):
    type: AsgiLifespanType


class AsgiApplication(Protocol):
    def __call__(
        self,
        scope: Mapping[str, object],
        receive: Callable[[], Awaitable[object]],
        send: Callable[[Mapping[str, object]], Awaitable[None]],
        /,
    ) -> Awaitable[None]: ...


class _ExtendedAsgiContainer(Protocol):
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


class AsgiContainerMiddleware:
    _request_container_var: Final[contextvars.ContextVar[RuntimeContainer]] = contextvars.ContextVar(
        "asgi_request_container"
    )

    def __init__(
        self,
        app: AsgiApplication,
        container_factory: Callable[[], RuntimeContainer],
        /,
    ) -> None:
        self._app: Final[AsgiApplication] = app
        self._container_factory: Final[Callable[[], RuntimeContainer]] = container_factory

    async def __call__(
        self,
        scope: Mapping[str, object],
        receive: Callable[[], Awaitable[object]],
        send: Callable[[Mapping[str, object]], Awaitable[None]],
        /,
    ) -> None:
        scope_type: Final[object | None] = scope.get("type")

        if scope_type == "lifespan":
            await self._handle_lifespan(scope, receive, send)
            return

        if scope_type not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return

        request_container = self._container_factory()
        token = self._request_container_var.set(request_container)

        try:
            mutable_scope = cast(dict[str, object], scope)
            mutable_scope["container"] = request_container
            await self._app(scope, receive, send)
        finally:
            self._request_container_var.reset(token)
            await self._execute_cleanup(request_container)

    async def _handle_lifespan(
        self,
        scope: Mapping[str, object],
        receive: Callable[[], Awaitable[object]],
        send: Callable[[Mapping[str, object]], Awaitable[None]],
        /,
    ) -> None:
        from container.core.facade import ContainerFacade

        async def wrapped_receive() -> object:
            raw_message = await receive()
            match raw_message:
                case {"type": AsgiLifespanType.SHUTDOWN}:
                    pass
                case _:
                    pass
            return raw_message

        try:
            global_container = self._container_factory()
            ContainerFacade.initialize(global_container)
            await self._app(scope, wrapped_receive, send)
        finally:
            ContainerFacade.clear()

    async def _execute_cleanup(self, container: RuntimeContainer, /) -> None:
        if isinstance(container, AsyncClosable):
            await container.close()
        elif isinstance(container, Closable):
            container.close()

    @classmethod
    def get_request_context_container(cls) -> RuntimeContainer:
        try:
            return cls._request_container_var.get()
        except LookupError as err:
            raise ContainerError("Active container context is missing for the current async task.") from err

    @classmethod
    def resolve[T](
        cls,
        target_type: type[T] | types.GenericAlias | types.UnionType,
        /,
        *,
        name: str | None = None,
    ) -> T | None:
        container = cast(_ExtendedAsgiContainer, cls.get_request_context_container())
        return container.resolve(target_type, name=name)

    @classmethod
    def resolve_all[T](cls, spec_type: type[T], /, *, name: str | None = None) -> Sequence[T]:
        """Resolves all component instances for the spec type in the request context."""
        container = cast(_ExtendedAsgiContainer, cls.get_request_context_container())
        return container.resolve_all(spec_type, name=name)

    @classmethod
    def resolve_provider[T](
        cls, target_type: type[T] | types.GenericAlias, /, *, name: str | None = None
    ) -> Callable[[], T]:
        """Resolves a provider function for the target type in the request context."""
        container = cast(_ExtendedAsgiContainer, cls.get_request_context_container())
        return container.resolve_provider(target_type, name=name)
