from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass

from webclient.base import (
    ClientHttpRequest,
    ClientHttpResponse,
    Configurable,
    ExchangeFilter,
    ExchangeFunction,
)
from webclient.plugin import plugin_impl
from webclient.types import CHARSET_UTF8


@dataclass(frozen=True)
class LogTrackerOptions:
    """リクエスト・レスポンスの核心コンテキストを透過追跡する可視化フィルター設定"""
    order: int = 20
    show_request_headers: bool = True
    show_response_headers: bool = True
    show_request_body: bool = True
    show_response_body: bool = True
    max_html_body_length: int = 200


@plugin_impl(value="logging", priority=200)
class LoggingFilter(ExchangeFilter, Configurable[LogTrackerOptions]):
    def __init__(self, config: LogTrackerOptions, /, logger: logging.Logger | None = None) -> None:
        self.config: LogTrackerOptions = config
        self.formatter: LogFormatter = VerboseLogFormatter(config)
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
            self.logger.error(f"HTTP Request failed: {request.method} {request.url} - Error: {err}")
            raise


class LogFormatter(ABC):
    @abstractmethod
    async def format_request(self, request: ClientHttpRequest, /) -> str: ...
    @abstractmethod
    async def format_response(self, response: ClientHttpResponse, /, *, stream: bool = False) -> str: ...


class VerboseLogFormatter(LogFormatter):
    def __init__(self, config: LogTrackerOptions, /) -> None:
        self._config: LogTrackerOptions = config

    def _extract_content_type(self, headers: Mapping[str, str], /) -> str:
        for key, value in headers.items():
            if key.lower() == "content-type":
                return value.lower()
        return ""

    def _format_body_by_type(self, content_type: str, raw_bytes: bytes, /) -> str:
        if "application/json" in content_type:
            return raw_bytes.decode(CHARSET_UTF8, errors="replace")
        if "text/html" in content_type:
            decoded = raw_bytes.decode(CHARSET_UTF8, errors="replace")
            if len(decoded) > self._config.max_html_body_length:
                return f"{decoded[: self._config.max_html_body_length]}... [Truncated]"
            return decoded
        return raw_bytes.decode(CHARSET_UTF8, errors="replace")

    async def format_request(self, request: ClientHttpRequest, /) -> str:
        components: list[str] = [f"Verbose HTTP Request: {request.method} {request.url}"]
        if self._config.show_request_headers:
            components.append(f"  Headers: {dict(request.headers)}")
        if self._config.show_request_body:
            body_str = ""
            content_type = self._extract_content_type(request.headers)
            if request.json_body is not None:
                body_str = json.dumps(request.json_body, ensure_ascii=False)
            elif request.content is not None:
                body_str = self._format_body_by_type(content_type, request.content)
            if body_str:
                components.append(f"  Body: {body_str}")
        return "\n".join(components)

    async def format_response(self, response: ClientHttpResponse, /, *, stream: bool = False) -> str:
        components: list[str] = [f"Verbose HTTP Response: {response.status_code}"]
        if self._config.show_response_headers:
            components.append(f"  Headers: {dict(response.headers)}")
        if self._config.show_response_body and not stream:
            content_type = self._extract_content_type(response.headers)
            raw_body = await response.read_body()
            body_str = self._format_body_by_type(content_type, raw_body)
            components.append(f"  Body: {body_str}")
        return "\n".join(components)
