from __future__ import annotations

from enum import StrEnum
from typing import Final

CONTENT_TYPE: Final[str] = "Content-Type"
CHARSET_UTF8: Final[str] = "utf-8"

class HttpMethod(StrEnum):

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"

class MediaType(StrEnum):

    # Application系
    JSON = "application/json"
    XML = "application/xml"
    PDF = "application/pdf"
    ZIP = "application/zip"
    OCTET_STREAM = "application/octet-stream"

    # Form / Multipart系
    FORM_URLENCODED = "application/x-www-form-urlencoded"
    MULTIPART_FORM_DATA = "multipart/form-data"

    # Text系
    TEXT_PLAIN = "text/plain"
    TEXT_HTML = "text/html"
    TEXT_CSS = "text/css"
    TEXT_JAVASCRIPT = "text/javascript"
    TEXT_EVENT_STREAM = "text/event-stream"

    # Image系
    JPEG = "image/jpeg"
    PNG = "image/png"
    GIF = "image/gif"
    WEBP = "image/webp"
    SVG = "image/svg+xml"

    def with_charset(self, charset: str = CHARSET_UTF8) -> str:
        """メディアタイプに文字コード属性（charset）を結合した文字列を返します。

        使用例: MediaType.JSON.with_charset() -> "application/json; charset=utf-8"
        """
        return f"{self.value}; charset={charset}"

    def with_parameter(self, name: str, value: str) -> str:
        """メディアタイプに任意の追加パラメータを結合した文字列を返します。

        使用例: MediaType.MULTIPART_FORM_DATA.with_parameter("boundary", "something")
        """
        return f"{self.value}; {name}={value}"
