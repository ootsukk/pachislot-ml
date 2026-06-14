from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from webclient.base import ClientHttpRequest, ClientHttpResponse, ExchangeFilter, ExchangeFunction

if TYPE_CHECKING:
    from contextvars import ContextVar


class ContextAttributesFilter(ExchangeFilter):
    def __init__(self, context_var: ContextVar[object], attribute_key: str, /) -> None:
        self.context_var: ContextVar[object] = context_var
        self.attribute_key: str = attribute_key

    async def __call__(
        self, request: ClientHttpRequest, next_exchange: ExchangeFunction, stream: bool, /
    ) -> ClientHttpResponse:
        try:
            current_value = self.context_var.get()
            updated_attributes = dict(request.attributes)
            updated_attributes[self.attribute_key] = current_value

            request = replace(request, attributes=updated_attributes)
        except LookupError:
            pass

        return await next_exchange.exchange(request, stream=stream)
