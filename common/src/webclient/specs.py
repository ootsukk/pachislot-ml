from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine, Generator, Mapping, Sequence
from dataclasses import is_dataclass
from types import TracebackType
from typing import Self

from webclient.auth import ClientHttpAuth
from webclient.base import ClientHttpRequest, ClientHttpResponse, ExchangeFunction, HttpMethod
from webclient.codec import BodyDecoder, BodyEncoder
from webclient.errors import WebClientResponseError
from webclient.types import CHARSET_UTF8, CONTENT_TYPE, MediaType


class ClientResponse:

    def __init__(self, response: ClientHttpResponse, decoder: BodyDecoder) -> None:
        self._response: ClientHttpResponse = response
        self._decoder: BodyDecoder = decoder
        self._is_closed: bool = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> Mapping[str, str]:
        return self._response.headers

    async def read_body(self) -> bytes:
        if self._is_closed:
            raise RuntimeError("クローズされたレスポンスからボディを読み取ることはできません。")
        return await self._response.read_body()

    async def value[T](self, element_type: type[T], /) -> T:
        body_bytes = await self._response.read_body()
        return self._decoder.decode(body_bytes, element_type)

    def stream[T](self, element_type: type[T], /) -> AsyncIterator[T]:
        if self._is_closed:
            raise RuntimeError("クローズされたレスポンスからストリームを開始することはできません。")

        async def _gen() -> AsyncIterator[T]:
            async for line in self._response.stream_lines():
                yield self._decoder.decode(line, element_type)

        return _gen()

    async def close(self) -> None:
        if not self._is_closed:
            await self._response.close()
            self._is_closed = True

class RequestHeadersSpec:

    def __init__(
        self,
        exchange_function: ExchangeFunction,
        encoder: BodyEncoder,
        decoder: BodyDecoder,
        method: HttpMethod,
        url: str,
        default_headers: Mapping[str, str],
        default_cookies: Mapping[str, str],
        default_timeout: float | object | None,
    ) -> None:
        self._exchange_function: ExchangeFunction = exchange_function
        self._encoder: BodyEncoder = encoder
        self._decoder: BodyDecoder = decoder
        self._method: HttpMethod = method
        self._url: str = url
        self._headers: dict[str, str] = dict(default_headers)
        self._params: Mapping[str, str | Sequence[str]] | None = None
        self._cookies: dict[str, str] = dict(default_cookies)
        self._auth: ClientHttpAuth | None = None
        self._timeout: float | object | None = default_timeout
        self._attributes: dict[str, object] = {}
        self._content: bytes | None = None
        self._data: Mapping[str, object] | None = None
        self._json_body: object | None = None
        self._files: Mapping[str, object] | None = None
        self._status_handlers: list[tuple[Callable[[int], bool], Callable[[ClientResponse], Coroutine[object, object, Exception]]]] = []

    def header(self, name: str, value: str, /) -> Self:
        self._headers[name] = value
        return self

    def accept(self, media_type: MediaType | str, /) -> Self:
        self._headers["Accept"] = media_type
        return self

    def params_map(self, params: Mapping[str, str | Sequence[str]], /) -> Self:
        self._params = params
        return self

    def cookies_map(self, cookies: Mapping[str, str], /) -> Self:
        self._cookies.update(cookies)
        return self

    def auth_info(self, auth: ClientHttpAuth, /) -> Self:
        self._auth = auth
        return self

    def timeout_value(self, timeout: float | object, /) -> Self:
        self._timeout = timeout
        return self

    def attribute(self, key: str, value: object, /) -> Self:
        self._attributes[key] = value
        return self

    def on_status(
        self,
        predicate: Callable[[int], bool],
        handler: Callable[[ClientResponse], Coroutine[object, object, Exception]],
        /
    ) -> Self:
        self._status_handlers.append((predicate, handler))
        return self

    def _build_request(self) -> ClientHttpRequest:
        return ClientHttpRequest(
            method=self._method,
            url=self._url,
            headers=self._headers,
            params=self._params,
            cookies=self._cookies,
            auth=self._auth,
            timeout=self._timeout,
            content=self._content,
            data=self._data,
            json_body=self._json_body,
            files=self._files,
            attributes=self._attributes,
        )

    def _apply_auth(self, request: ClientHttpRequest) -> ClientHttpRequest:
        return request.auth.apply(request) if request.auth is not None else request

    async def _check_status_and_raise(self, raw_response: ClientHttpResponse) -> None:
        client_response = ClientResponse(raw_response, self._decoder)

        for predicate, handler in self._status_handlers:
            if predicate(raw_response.status_code):
                custom_exception = await handler(client_response)
                raise custom_exception

        if raw_response.status_code >= 400:
            error_body = await raw_response.read_body()
            raise WebClientResponseError(
                status_code=raw_response.status_code,
                headers=raw_response.headers,
                body=error_body,
            )

    async def value[T](self, element_type: type[T], /) -> T:
        final_request = self._apply_auth(self._build_request())
        response = await self._exchange_function.exchange(final_request, stream=False)
        try:
            await self._check_status_and_raise(response)
            body_bytes = await response.read_body()
            return self._decoder.decode(body_bytes, element_type)
        finally:
            await response.close()

    def stream[T](self, element_type: type[T], /) -> AsyncIterator[T]:
        request = self._build_request()

        async def _stream_generator() -> AsyncIterator[T]:
            final_request = self._apply_auth(request)
            response = await self._exchange_function.exchange(final_request, stream=True)
            try:
                await self._check_status_and_raise(response)
                async for line in response.stream_lines():
                    if not line.strip():
                        continue
                    yield self._decoder.decode(line, element_type)
            finally:
                await response.close()

        return _stream_generator()

    async def exchange_to_value[T](
        self,
        handler: Callable[[ClientResponse], Coroutine[object, object, T]],
        /
    ) -> T:
        final_request = self._apply_auth(self._build_request())
        raw_response = await self._exchange_function.exchange(final_request, stream=False)
        client_response = ClientResponse(raw_response, self._decoder)
        try:
            return await handler(client_response)
        finally:
            await raw_response.close()

    def exchange_to_stream[T](
        self,
        handler: Callable[[ClientResponse], AsyncIterator[T]],
        /
    ) -> AsyncIterator[T]:
        request = self._build_request()

        async def _stream_generator() -> AsyncIterator[T]:
            final_request = self._apply_auth(request)
            raw_response = await self._exchange_function.exchange(final_request, stream=True)
            client_response = ClientResponse(raw_response, self._decoder)
            try:
                async for element in handler(client_response):
                    yield element
            finally:
                await raw_response.close()

        return _stream_generator()

    def __await__(self) -> Generator[object, None, dict[object, object]]:
        return self.value(dict).__await__()


class RequestBodySpec(RequestHeadersSpec):

    def body_value(self, body: object, /) -> RequestHeadersSpec:
        if isinstance(body, bytes):
            self._content = body
        elif isinstance(body, str):
            self._content = body.encode(CHARSET_UTF8)
        elif is_dataclass(body):
            self._json_body = self._encoder.encode(body)
            self._headers.setdefault(CONTENT_TYPE, MediaType.JSON)
        else:
            self._json_body = body
            self._headers.setdefault(CONTENT_TYPE, MediaType.JSON)
        return self

    def body_json(self, json_data: object, /) -> RequestHeadersSpec:
        self._json_body = self._encoder.encode(json_data)
        self._headers.setdefault(CONTENT_TYPE, MediaType.JSON)
        return self

    def body_form(self, form_data: Mapping[str, object], /) -> RequestHeadersSpec:
        self._data = form_data
        self._headers.setdefault(CONTENT_TYPE, MediaType.FORM_URLENCODED)
        return self

    def body_files(self, files: Mapping[str, object], /) -> RequestHeadersSpec:
        self._files = files
        self._headers.setdefault(CONTENT_TYPE, MediaType.MULTIPART_FORM_DATA)
        return self


class RequestHeadersUriSpec:
    def __init__(self, exchange_function: ExchangeFunction, encoder: BodyEncoder, decoder: BodyDecoder, method: HttpMethod, api_version: str, default_headers: Mapping[str, str], default_cookies: Mapping[str, str], default_timeout: float | object | None) -> None:
        self._exchange_function = exchange_function
        self._encoder = encoder
        self._decoder = decoder
        self._method = method
        self._api_version = api_version
        self._default_headers = default_headers
        self._default_cookies = default_cookies
        self._default_timeout = default_timeout

    def uri(self, uri: str, /, variables: Mapping[str, object] | None = None) -> RequestHeadersSpec:
        resolved_uri = uri
        if "{api_version}" in resolved_uri:
            resolved_uri = resolved_uri.replace("{api_version}", self._api_version)
        if variables:
            for key, val in variables.items():
                resolved_uri = resolved_uri.replace(f"{{{key}}}", str(val))

        return RequestHeadersSpec(
            exchange_function=self._exchange_function,
            encoder=self._encoder,
            decoder=self._decoder,
            method=self._method,
            url=resolved_uri,
            default_headers=self._default_headers,
            default_cookies=self._default_cookies,
            default_timeout=self._default_timeout,
        )


class RequestBodyUriSpec:
    def __init__(self, exchange_function: ExchangeFunction, encoder: BodyEncoder, decoder: BodyDecoder, method: HttpMethod, api_version: str, default_headers: Mapping[str, str], default_cookies: Mapping[str, str], default_timeout: float | object | None) -> None:
        self._exchange_function = exchange_function
        self._encoder = encoder
        self._decoder = decoder
        self._method = method
        self._api_version = api_version
        self._default_headers = default_headers
        self._default_cookies = default_cookies
        self._default_timeout = default_timeout

    def uri(self, uri: str, /, variables: Mapping[str, object] | None = None) -> RequestBodySpec:
        resolved_uri = uri
        if "{api_version}" in resolved_uri:
            resolved_uri = resolved_uri.replace("{api_version}", self._api_version)
        if variables:
            for key, val in variables.items():
                resolved_uri = resolved_uri.replace(f"{{{key}}}", str(val))

        return RequestBodySpec(
            exchange_function=self._exchange_function,
            encoder=self._encoder,
            decoder=self._decoder,
            method=self._method,
            url=resolved_uri,
            default_headers=self._default_headers,
            default_cookies=self._default_cookies,
            default_timeout=self._default_timeout,
        )
