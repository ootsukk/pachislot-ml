from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine, Mapping
from contextvars import ContextVar
from http.cookies import SimpleCookie

from webclient.config import LoggingConfig, RetryConfig
from webclient.types import ClientHttpConnector, ClientHttpRequest, ClientHttpResponse, CookieStore


class ExchangeFunction(ABC):
    @abstractmethod
    async def exchange(self, request: ClientHttpRequest, *, stream: bool = False) -> ClientHttpResponse: ...


class ExchangeFilter(ABC):
    @abstractmethod
    async def __call__(
        self, request: ClientHttpRequest, next_exchange: ExchangeFunction, stream: bool, /
    ) -> ClientHttpResponse: ...


ExchangeFilterFunction = (
    ExchangeFilter
    | Callable[[ClientHttpRequest, ExchangeFunction, bool], Coroutine[object, object, ClientHttpResponse]]
)


class DefaultConnectorExchangeFunction(ExchangeFunction):
    def __init__(self, connector: ClientHttpConnector, /) -> None:
        self._connector: ClientHttpConnector = connector

    async def exchange(self, request: ClientHttpRequest, *, stream: bool = False) -> ClientHttpResponse:
        return await self._connector.connect(request, stream=stream)


class FilteredExchangeFunction(ExchangeFunction):
    def __init__(self, filter_element: ExchangeFilterFunction, next_exchange: ExchangeFunction, /) -> None:
        self._filter_element: ExchangeFilterFunction = filter_element
        self._next_exchange: ExchangeFunction = next_exchange

    async def exchange(self, request: ClientHttpRequest, *, stream: bool = False) -> ClientHttpResponse:
        return await self._filter_element(request, self._next_exchange, stream)


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

            from dataclasses import replace

            request = replace(request, attributes=updated_attributes)
        except LookupError:
            pass

        return await next_exchange.exchange(request, stream=stream)


class CookieManagementFilter(ExchangeFilter):
    def __init__(self, cookie_store: CookieStore, /) -> None:
        self.cookie_store: CookieStore = cookie_store

    async def __call__(
        self, request: ClientHttpRequest, next_exchange: ExchangeFunction, stream: bool, /
    ) -> ClientHttpResponse:
        stored_cookies = self.cookie_store.load(request.url)
        if stored_cookies:
            current_cookies = dict(request.cookies) if request.cookies is not None else {}
            for key, val in stored_cookies.items():
                current_cookies.setdefault(key, val)

            from dataclasses import replace

            request = replace(request, cookies=current_cookies)

        response = await next_exchange.exchange(request, stream=stream)

        set_cookie_headers = [v for k, v in response.headers.items() if k.lower() == "set-cookie"]
        if set_cookie_headers:
            extracted: dict[str, str] = {}
            for header_value in set_cookie_headers:
                cookie_parser = SimpleCookie()
                cookie_parser.load(header_value)
                for key, morsel in cookie_parser.items():
                    extracted[key] = morsel.value
            if extracted:
                self.cookie_store.save(request.url, extracted)

        return response


class RetryFilter(ExchangeFilter):
    def __init__(self, config: RetryConfig | None = None, /, logger: logging.Logger | None = None) -> None:
        self.config: RetryConfig = config if config is not None else RetryConfig()
        self.logger: logging.Logger = logger if logger is not None else logging.getLogger("webclient.filter.retry")
        self._retry_statuses: set[int] = {408, 429, 500, 502, 503, 504}
        self._total_retries_metric: int = 0

    @property
    def total_retries_metric(self) -> int:
        return self._total_retries_metric

    async def __call__(
        self, request: ClientHttpRequest, next_exchange: ExchangeFunction, stream: bool, /
    ) -> ClientHttpResponse:
        attempts = 0
        while True:
            attempts += 1
            response = await next_exchange.exchange(request, stream=stream)

            if response.status_code in self._retry_statuses:
                if attempts < self.config.max_attempts:
                    await response.close()
                    self._total_retries_metric += 1

                    sleep_time = self.config.backoff_factor * (2 ** (attempts - 1))
                    self.logger.info(
                        f"HTTP status {response.status_code} detected. "
                        f"Retrying request ({attempts}/{self.config.max_attempts}) "
                        f"after {sleep_time:.2f}s backoff... URL: {request.url}"
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                self.logger.warning(
                    f"HTTP status {response.status_code} detected. "
                    f"Maximum retry attempts ({self.config.max_attempts}) reached. Retry out. "
                    f"Final status: {response.status_code}, URL: {request.url}"
                )

            return response


class LogFormatter(ABC):
    @abstractmethod
    async def format_request(self, request: ClientHttpRequest, /) -> str: ...

    @abstractmethod
    async def format_response(self, response: ClientHttpResponse, /, *, stream: bool = False) -> str: ...


class SimpleLogFormatter(LogFormatter):
    async def format_request(self, request: ClientHttpRequest, /) -> str:
        return f"HTTP Request: {request.method.value} {request.url} - Headers: {dict(request.headers)}"

    async def format_response(self, response: ClientHttpResponse, /, *, stream: bool = False) -> str:
        return f"HTTP Response: {response.status_code} - Headers: {dict(response.headers)}"


class VerboseLogFormatter(LogFormatter):
    def __init__(self, config: LoggingConfig, /) -> None:
        self._config: LoggingConfig = config

    def _extract_content_type(self, headers: Mapping[str, str], /) -> str:
        for key, value in headers.items():
            if key.lower() == "content-type":
                return value.lower()
        return ""

    def _format_body_by_type(self, content_type: str, raw_bytes: bytes, /) -> str:
        if "application/json" in content_type:
            return raw_bytes.decode("utf-8", errors="replace")
        if "text/html" in content_type:
            decoded = raw_bytes.decode("utf-8", errors="replace")
            if len(decoded) > self._config.max_html_body_length:
                return f"{decoded[: self._config.max_html_body_length]}... [Truncated]"
            return decoded
        return raw_bytes.decode("utf-8", errors="replace")

    async def format_request(self, request: ClientHttpRequest, /) -> str:
        components: list[str] = [f"Verbose HTTP Request: {request.method.value} {request.url}"]
        if self._config.show_request_headers:
            components.append(f"  Headers: {dict(request.headers)}")
        if self._config.show_request_body:
            body_str = ""
            content_type = self._extract_content_type(request.headers)
            if request.json_body is not None:
                body_str = json.dumps(request.json_body, ensure_ascii=False)
            elif request.content is not None:
                body_str = self._format_body_by_type(content_type, request.content)
            elif request.data is not None:
                body_str = str(dict(request.data))
            if body_str:
                components.append(f"  Body: {body_str}")
        return "\n".join(components)

    async def format_response(self, response: ClientHttpResponse, /, *, stream: bool = False) -> str:
        components: list[str] = [f"Verbose HTTP Response: {response.status_code}"]
        if self._config.show_response_headers:
            components.append(f"  Headers: {dict(response.headers)}")
        if self._config.show_response_body:
            if stream:
                components.append("  Body: [Stream Body Omitted]")
            else:
                content_type = self._extract_content_type(response.headers)
                raw_body = await response.read_body()
                body_str = self._format_body_by_type(content_type, raw_body)
                components.append(f"  Body: {body_str}")
        return "\n".join(components)


class LoggingFilter(ExchangeFilter):
    def __init__(self, formatter: LogFormatter, /, logger: logging.Logger | None = None) -> None:
        self.formatter: LogFormatter = formatter
        self.logger: logging.Logger = logger if logger is not None else logging.getLogger("webclient.filter.logging")

    async def __call__(
        self, request: ClientHttpRequest, next_exchange: ExchangeFunction, stream: bool, /
    ) -> ClientHttpResponse:
        request_log = await self.formatter.format_request(request)
        self.logger.info(request_log)
        try:
            response = await next_exchange.exchange(request, stream=stream)
            response_log = await self.formatter.format_response(response, stream=stream)
            self.logger.info(response_log)
            return response
        except Exception as err:
            self.logger.error(f"HTTP Request failed: {request.method.value} {request.url} - Error: {err}")
            raise
