from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


@dataclass(frozen=True)
class ClientHttpRequest:
    method: HttpMethod
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    params: Mapping[str, str | Sequence[str]] | None = None
    cookies: Mapping[str, str] | None = None
    auth: ClientHttpAuth | None = None
    timeout: float | object | None = None
    content: bytes | None = None
    data: Mapping[str, object] | None = None
    json_body: object | None = None
    files: Mapping[str, object] | None = None
    attributes: Mapping[str, object] = field(default_factory=dict)


class ClientHttpResponse(ABC):

    @abstractmethod
    async def read_body(self) -> bytes: ...

    @abstractmethod
    def stream_lines(self) -> AsyncIterator[str]: ...

    @property
    @abstractmethod
    def status_code(self) -> int: ...

    @property
    @abstractmethod
    def headers(self) -> Mapping[str, str]: ...

    @abstractmethod
    async def close(self) -> None: ...


class ClientHttpConnector(ABC):
    # HTTPクライアントエンジンを抽象化する基底クラス

    @abstractmethod
    async def connect(
        self,
        request: ClientHttpRequest,
        *,
        stream: bool = False,
    ) -> ClientHttpResponse: ...

    @abstractmethod
    async def close(self) -> None: ...


class ClientHttpAuth(ABC):
    # すべての認証方式の基底となる抽象インターフェース

    @abstractmethod
    def apply(self, request: ClientHttpRequest, /) -> ClientHttpRequest:
        return request

class CookieStore(ABC):
    @abstractmethod
    def save(self, url: str, cookies: Mapping[str, str], /) -> None: ...

    @abstractmethod
    def load(self, url: str, /) -> Mapping[str, str]: ...
