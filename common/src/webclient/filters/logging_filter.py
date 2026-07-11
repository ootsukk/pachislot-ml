from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from webclient.base import ClientHttpRequest, ClientHttpResponse, Configurable, ExchangeFilter, ExchangeFunction
from webclient.plugin import plugin_impl
from webclient.types import CHARSET_UTF8, MediaType


class LogFormatType(StrEnum):
    SIMPLE = "simple"
    VERBOSE = "verbose"
    JSON = "json"

    @property
    def formatter_class(self) -> type[LogFormatter]:
        return {
            LogFormatType.SIMPLE: SimpleLogFormatter,
            LogFormatType.VERBOSE: VerboseLogFormatter,
            LogFormatType.JSON: JsonLogFormatter,
        }[self]


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    @property
    def int_value(self) -> int:
        """StrEnumの文字列に対応する標準loggingモジュールの整数値レベル(int)を返却します。"""
        return logging.getLevelNamesMapping().get(self.value, logging.INFO)


@dataclass(frozen=True)
class LogTrackerOptions:
    """リクエスト・レスポンス・エラーのロギングフィルター設定"""

    format_type: LogFormatType = LogFormatType.SIMPLE
    request_level: LogLevel = LogLevel.INFO
    response_level: LogLevel = LogLevel.INFO
    error_level: LogLevel = LogLevel.WARNING
    show_request_headers: bool = True
    show_response_headers: bool = True
    show_request_body: bool = True
    show_response_body: bool = True
    max_html_body_length: int = 200


@plugin_impl(value="logging", priority=200)
class LoggingFilter(ExchangeFilter, Configurable[LogTrackerOptions]):
    def __init__(self, config: LogTrackerOptions, /, logger: logging.Logger | None = None) -> None:
        self.config: LogTrackerOptions = config
        self.logger: logging.Logger = logger if logger is not None else logging.getLogger("webclient.filter.logging")

        self.formatter: LogFormatter = config.format_type.formatter_class(config)

        self._request_level_int = config.request_level.int_value
        self._response_level_int = config.response_level.int_value
        self._error_level_int = config.error_level.int_value

    async def __call__(
        self, request: ClientHttpRequest, next_exchange: ExchangeFunction, stream: bool, /
    ) -> ClientHttpResponse:
        request_log = await self.formatter.format_request(request)
        self.logger.log(self._request_level_int, request_log)

        start_time = time.perf_counter()
        try:
            response = await next_exchange.exchange(request, stream=stream)
            duration = time.perf_counter() - start_time

            response_log = await self.formatter.format_response(response, duration, stream=stream)
            self.logger.log(self._response_level_int, response_log)
            return response
        except Exception as err:
            duration = time.perf_counter() - start_time

            error_log = await self.formatter.format_error(request, err, duration)
            self.logger.log(self._error_level_int, error_log)
            raise


class LogFormatter(ABC):
    """すべてのログフォーマッタの基盤となる抽象クラス"""

    def __init__(self, config: LogTrackerOptions, /) -> None:
        self._config: LogTrackerOptions = config

    @abstractmethod
    async def format_request(self, request: ClientHttpRequest, /) -> str: ...

    @abstractmethod
    async def format_response(
        self, response: ClientHttpResponse, duration: float, /, *, stream: bool = False
    ) -> str: ...

    @abstractmethod
    async def format_error(self, request: ClientHttpRequest, error: Exception, duration: float, /) -> str: ...

    def _extract_content_type(self, headers: Mapping[str, str], /) -> str:
        for key, value in headers.items():
            if key.lower() == "content-type":
                return value.lower()
        return ""

    def _format_body_by_type(self, content_type: str, raw_bytes: bytes, /) -> str:
        if MediaType.JSON in content_type:
            return raw_bytes.decode(CHARSET_UTF8, errors="replace")
        if "text/html" in content_type:
            decoded = raw_bytes.decode(CHARSET_UTF8, errors="replace")
            if len(decoded) > self._config.max_html_body_length:
                return f"{decoded[: self._config.max_html_body_length]}... [Truncated]"
            return decoded
        return raw_bytes.decode(CHARSET_UTF8, errors="replace")


class SimpleLogFormatter(LogFormatter):
    """入出力の事実と処理速度のみをミニマルに出力するテキストフォーマッタ"""

    async def format_request(self, request: ClientHttpRequest, /) -> str:
        return f"HTTP Request: {request.method} {request.url}"

    async def format_response(self, response: ClientHttpResponse, duration: float, /, *, stream: bool = False) -> str:
        return f"HTTP Response: {response.status_code} (Duration: {duration:.3f}s)"

    async def format_error(self, request: ClientHttpRequest, error: Exception, duration: float, /) -> str:
        return (
            f"HTTP Request Failed: {request.method} {request.url} - Error: {error} (Duration: {duration:.3f}s)"
        )


class JsonLogFormatter(LogFormatter):
    """構造化ログ解析エンジンに完全適合するJSONフォーマッタ"""

    async def format_request(self, request: ClientHttpRequest, /) -> str:
        record: dict[str, Any] = {
            "type": "request",
            "method": request.method,
            "url": str(request.url),
        }
        if self._config.show_request_headers:
            record["headers"] = dict(request.headers)

        if self._config.show_request_body:
            body_str = ""
            content_type = self._extract_content_type(request.headers)
            if request.json_body is not None:
                body_str = json.dumps(request.json_body, ensure_ascii=False)
            elif request.content is not None:
                body_str = self._format_body_by_type(content_type, request.content)
            if body_str:
                record["body"] = body_str

        return json.dumps(record, ensure_ascii=False)

    async def format_response(self, response: ClientHttpResponse, duration: float, /, *, stream: bool = False) -> str:
        record: dict[str, Any] = {
            "type": "response",
            "status_code": response.status_code,
            "duration_sec": round(duration, 4),
        }
        if self._config.show_response_headers:
            record["headers"] = dict(response.headers)

        if self._config.show_response_body and not stream:
            content_type = self._extract_content_type(response.headers)
            raw_body = await response.read_body()
            body_str = self._format_body_by_type(content_type, raw_body)
            record["body"] = body_str

        return json.dumps(record, ensure_ascii=False)

    async def format_error(self, request: ClientHttpRequest, error: Exception, duration: float, /) -> str:
        record: dict[str, Any] = {
            "type": "error",
            "method": request.method,
            "url": str(request.url),
            "error_class": error.__class__.__name__,
            "error_message": str(error),
            "duration_sec": round(duration, 4),
        }
        return json.dumps(record, ensure_ascii=False)


class VerboseLogFormatter(LogFormatter):
    """コンテキスト情報と経過時間を視覚的にインデント出力する詳細テキストフォーマッタ"""

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

    async def format_response(self, response: ClientHttpResponse, duration: float, /, *, stream: bool = False) -> str:
        components: list[str] = [f"Verbose HTTP Response: {response.status_code}"]
        if self._config.show_response_headers:
            components.append(f"  Headers: {dict(response.headers)}")
        if self._config.show_response_body and not stream:
            content_type = self._extract_content_type(response.headers)
            raw_body = await response.read_body()
            body_str = self._format_body_by_type(content_type, raw_body)
            components.append(f"  Body: {body_str}")
        components.append(f"  Duration: {duration:.3f}s")
        return "\n".join(components)

    async def format_error(self, request: ClientHttpRequest, error: Exception, duration: float, /) -> str:
        components: list[str] = [
            f"Verbose HTTP Request Failed: {request.method} {request.url}",
            f"  Error Class: {error.__class__.__name__}",
            f"  Message: {error}",
            f"  Duration: {duration:.3f}s",
        ]
        return "\n".join(components)
