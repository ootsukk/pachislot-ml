from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine, Generator, Mapping, Sequence
from dataclasses import is_dataclass
from types import TracebackType
from typing import Any, Self

from webclient.auth import ClientHttpAuth
from webclient.base import ClientHttpRequest, ClientHttpResponse, ExchangeFunction, MultipartPart
from webclient.codec import BodyDecoder, BodyEncoder
from webclient.errors import WebClientResponseError
from webclient.multipart import MultipartBodyBuilder
from webclient.types import CHARSET_UTF8, CONTENT_TYPE, HttpMethod, MediaType


class ResponseSpec:
    """レスポンスデータを安全に管理・抽出するための流れるような(Fluent)レスポンス表現スペック。

    非同期コンテキストマネージャに対応しており、async with ブロックを抜ける際に
    下位の通信コネクションやリソースを全自動で確実に解放(リークを物理遮断)します。
    """

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
        """全データを生のバイト配列(bytes)として一括取得します。"""
        if self._is_closed:
            raise RuntimeError("クローズされたレスポンスからボディを読み取ることはできません。")
        return await self._response.read_body()

    async def value[T](self, element_type: type[T], /) -> T:
        """全データをメモリに一括ロードし、指定された型(dataclassやdictなど)に全自動でデコードして返します。"""
        if self._is_closed:
            raise RuntimeError("クローズされたレスポンスからデータを読み取ることはできません。")
        body_bytes = await self._response.read_body()
        return self._decoder.decode(body_bytes, element_type)

    def stream[T](self, element_type: type[T], /) -> AsyncIterator[T]:
        """テキストデータを1行ずつストリーミング読み込みし、指定された型に逐次デコードして流す非同期ジェネレータを返します。"""
        if self._is_closed:
            raise RuntimeError("クローズされたレスポンスからストリームを開始することはできません。")

        async def _gen() -> AsyncIterator[T]:
            async for line in self._response.stream_raw_lines():
                yield self._decoder.decode(line, element_type)

        return _gen()

    def stream_chunks(self, chunk_size: int = 4096) -> AsyncIterator[bytes]:
        """大容量バイナリデータ(ZIPや動画等)をメモリを枯渇させないように一定サイズ(チャンク)ごとに切り出して流す非同期ジェネレータを返します。"""
        if self._is_closed:
            raise RuntimeError("クローズされたレスポンスからストリームを開始することはできません。")

        async def _gen() -> AsyncIterator[bytes]:
            async for chunk in self._response.stream_chunks(chunk_size):
                yield chunk

        return _gen()

    async def close(self) -> None:
        """下位の通信レスポンスを安全にクローズし、リソースを解放します。このメソッドは何度呼び出しても安全です(べき等性担保)。"""
        if not self._is_closed:
            await self._response.close()
            self._is_closed = True


class RequestHeadersSpec:
    """ヘッダーの個別追加、各種メタ情報の付与、および通信実行へと繋ぐ完全不変スペック境界。"""

    def __init__(
        self,
        exchange_function: ExchangeFunction,
        encoder: BodyEncoder,
        decoder: BodyDecoder,
        method: HttpMethod | str,
        url: str,
        default_headers: Mapping[str, str],
        default_cookies: Mapping[str, str],
        default_timeout: float | object | None,
        *,
        params: Mapping[str, str | Sequence[str]] | None = None,
        auth: ClientHttpAuth | None = None,
        attributes: Mapping[str, object] | None = None,
        content: bytes | None = None,
        json_body: object | None = None,
        data: Mapping[str, object] | None = None,
        files: Mapping[str, object] | None = None,
        multipart_body: Sequence[MultipartPart] | None = None,
        status_handlers: list[tuple[Callable[[int], bool], Callable[[ResponseSpec], Coroutine[object, object, Exception]]]] | None = None,
    ) -> None:
        self._exchange_function = exchange_function
        self._encoder = encoder
        self._decoder = decoder
        self._method = method
        self._url = url
        self._headers: dict[str, str] = dict(default_headers)
        self._cookies: dict[str, str] = dict(default_cookies)
        self._timeout = default_timeout

        self._params = params
        self._auth = auth
        self._attributes = dict(attributes) if attributes else {}
        self._content = content
        self._json_body = json_body
        self._data = data
        self._files = files
        self._multipart_body = multipart_body
        self._status_handlers = list(status_handlers) if status_handlers else []

    def _clone(self, **updates: Any) -> Self:
        """自身の構成パラメータを引き継ぎつつ、指定された属性のみをオーバーライドした
        全く新しい同型スペックオブジェクト(不変スナップショット)を新造して返します。
        """
        kwargs = {
            "exchange_function": self._exchange_function,
            "encoder": self._encoder,
            "decoder": self._decoder,
            "method": self._method,
            "url": self._url,
            "default_headers": self._headers,
            "default_cookies": self._cookies,
            "default_timeout": self._timeout,
            "params": self._params,
            "auth": self._auth,
            "attributes": self._attributes,
            "content": self._content,
            "json_body": self._json_body,
            "data": self._data,
            "files": self._files,
            "multipart_body": self._multipart_body,
            "status_handlers": self._status_handlers,
        }
        kwargs.update(updates)

        return self.__class__(**kwargs)

    def header(self, name: str, value: str, /) -> Self:
        new_headers = dict(self._headers)
        new_headers[name] = value
        return self._clone(default_headers=new_headers)

    def accept(self, media_type: MediaType | str, /) -> Self:
        new_headers = dict(self._headers)
        new_headers["Accept"] = str(media_type)
        return self._clone(default_headers=new_headers)

    def params_map(self, params: Mapping[str, str | Sequence[str]], /) -> Self:
        return self._clone(params=params)

    def cookies_map(self, cookies: Mapping[str, str], /) -> Self:
        new_cookies = dict(self._cookies)
        new_cookies.update(cookies)
        return self._clone(default_cookies=new_cookies)

    def auth_info(self, auth: ClientHttpAuth, /) -> Self:
        return self._clone(auth=auth)

    def timeout_value(self, timeout: float | object, /) -> Self:
        return self._clone(default_timeout=timeout)

    def attribute(self, key: str, value: object, /) -> Self:
        new_attrs = dict(self._attributes)
        new_attrs[key] = value
        return self._clone(attributes=new_attrs)

    def on_status(
        self,
        predicate: Callable[[int], bool],
        handler: Callable[[ResponseSpec], Coroutine[object, object, Exception]],
        /
    ) -> Self:
        new_handlers = list(self._status_handlers)
        new_handlers.append((predicate, handler))
        return self._clone(status_handlers=new_handlers)


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
            multipart_body=self._multipart_body,
            attributes=self._attributes,
        )

    def _apply_auth(self, request: ClientHttpRequest) -> ClientHttpRequest:
        return request.auth.apply(request) if request.auth is not None else request

    async def _check_status_and_raise(self, raw_response: ClientHttpResponse) -> None:
        client_response = ResponseSpec(raw_response, self._decoder)

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
                async for line in response.stream_raw_lines():
                    if not line.strip():
                        continue
                    yield self._decoder.decode(line, element_type)
            finally:
                await response.close()

        return _stream_generator()

    def stream_chunks(self, chunk_size: int = 4096) -> AsyncIterator[bytes]:
        """大容量バイナリ(ZIPやPDF等)をメモリを汚さずに一定サイズ(チャンク)ごとに切り出して
        流し込む、完全自律クローズ型のストリーミングゲートウェイを返却します。
        """
        request = self._build_request()

        async def _chunk_generator() -> AsyncIterator[bytes]:
            final_request = self._apply_auth(request)
            response = await self._exchange_function.exchange(final_request, stream=True)
            try:
                await self._check_status_and_raise(response)
                async for chunk in response.stream_chunks(chunk_size):
                    yield chunk
            finally:
                await response.close()

        return _chunk_generator()

    async def exchange_to_value[T](
        self,
        handler: Callable[[ResponseSpec], Coroutine[object, object, T]],
        /
    ) -> T:
        final_request = self._apply_auth(self._build_request())
        raw_response = await self._exchange_function.exchange(final_request, stream=False)
        client_response = ResponseSpec(raw_response, self._decoder)
        try:
            return await handler(client_response)
        finally:
            await raw_response.close()

    def exchange_to_stream[T](
        self,
        handler: Callable[[ResponseSpec], AsyncIterator[T]],
        /
    ) -> AsyncIterator[T]:
        request = self._build_request()

        async def _stream_generator() -> AsyncIterator[T]:
            final_request = self._apply_auth(request)
            raw_response = await self._exchange_function.exchange(final_request, stream=True)
            client_response = ResponseSpec(raw_response, self._decoder)
            try:
                async for element in handler(client_response):
                    yield element
            finally:
                await raw_response.close()

        return _stream_generator()

    def __await__(self) -> Generator[object, None, dict[object, object]]:
        return self.value(dict).__await__()


class RequestBodySpec(RequestHeadersSpec):
    """ボディ(JSON、Form、Multipart)のインジェクション能力を拡張した上位スペック境界。"""

    def body_value(self, body: object, /) -> RequestHeadersSpec:
        new_headers = dict(self._headers)

        if isinstance(body, bytes):
            return self._clone(content=body)
        elif isinstance(body, str):
            return self._clone(content=body.encode(CHARSET_UTF8))
        elif is_dataclass(body):
            new_headers.setdefault(CONTENT_TYPE, MediaType.JSON)
            return self._clone(json_body=self._encoder.encode(body), default_headers=new_headers)
        else:
            new_headers.setdefault(CONTENT_TYPE, MediaType.JSON)
            return self._clone(json_body=body, default_headers=new_headers)

    def body_json(self, json_data: object, /) -> RequestHeadersSpec:
        new_headers = dict(self._headers)
        new_headers.setdefault(CONTENT_TYPE, MediaType.JSON)
        return self._clone(json_body=self._encoder.encode(json_data), default_headers=new_headers)

    def body_form(self, form_data: Mapping[str, object], /) -> RequestHeadersSpec:
        new_headers = dict(self._headers)
        new_headers.setdefault(CONTENT_TYPE, MediaType.FORM_URLENCODED)
        return self._clone(data=form_data, default_headers=new_headers)

    def body_files(self, files: Mapping[str, object], /) -> RequestHeadersSpec:
        new_headers = dict(self._headers)
        new_headers.setdefault(CONTENT_TYPE, MediaType.MULTIPART_FORM_DATA)
        return self._clone(files=files, default_headers=new_headers)

    def body_multipart(self, builder: MultipartBodyBuilder, /) -> RequestHeadersSpec:
        new_headers = dict(self._headers)
        new_headers.setdefault(CONTENT_TYPE, MediaType.MULTIPART_FORM_DATA)
        return self._clone(multipart_body=builder.build(), default_headers=new_headers)


class RequestHeadersUriSpec:
    """リクエストのURIテンプレート解決および初期設定のスペック境界。"""

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
    """ボディ積載が許可されたメソッド(POST, PUT等)専用のURIテンプレート解決スペック境界。"""

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
