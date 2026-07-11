from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Self, cast

from webclient.base import (
    ClientHttpConnector,
    DefaultConnectorExchangeFunction,
    ExchangeFilter,
    ExchangeFilterFunction,
    ExchangeFunction,
    FilteredExchangeFunction,
    HttpMethod,
    PrioritizedFilter,
)
from webclient.codec import BodyDecoder, BodyEncoder
from webclient.config import WebClientConfig
from webclient.resolver import UniversalPluginResolver
from webclient.specs import RequestBodySpec, RequestBodyUriSpec, RequestHeadersUriSpec

if TYPE_CHECKING:
    from webclient.base import CookieStore  # type: ignore


class WebClient:

    def __init__(
        self,
        config: WebClientConfig | None = None,
        /,
        *,
        base_url: str = "",
        api_version: str = "",
        default_timeout: float | object | None = None,
        default_headers: Mapping[str, str] | None = None,
        default_cookies: Mapping[str, str] | None = None,
        cookie_store: CookieStore | None = None,
        encoder: BodyEncoder | None = None,
        decoder: BodyDecoder | None = None,
        connector: ClientHttpConnector | None = None,
        filters: Sequence[ExchangeFilter] | None = None,
        context_attributes: Sequence[tuple[ContextVar[object], str]] | None = None,
        plugin_groups: Sequence[str] | None = None,
    ) -> None:
        self._config: WebClientConfig = config if config is not None else WebClientConfig()

        # =====================================================================
        # ユーザーからの直接生成ルート(コアパーツが足りない場合)
        # =====================================================================
        if connector is None or encoder is None or decoder is None:
            # その場でDIコンテナを起動し、環境から具象を全自動引き当て
            resolved_pool = UniversalPluginResolver.resolve_all(self._config, {}, {})
            self._connector = resolved_pool[ClientHttpConnector]
            self._encoder = resolved_pool[BodyEncoder]
            self._decoder = resolved_pool[BodyDecoder]
            self._cookie_store = resolved_pool.get(CookieStore)
            self._filters = list(resolved_pool.get(PrioritizedFilter, []))
            self._context_attributes = []
            self._plugin_groups = list(self._config.plugin_groups)

            # 設定オブジェクト側に記述されている初期値をドメインへ完全マウント(設定漏れの防止)
            self._base_url = self._config.base_url
            self._api_version = self._config.api_version
            self._default_timeout = self._config.timeout
            self._default_headers = dict(self._config.default_headers)
            self._default_cookies = dict(self._config.default_cookies)

        # =====================================================================
        # ビルダーからの精緻なインジェクションルート(パーツが揃っている場合)
        # =====================================================================
        else:
            self._connector = connector
            self._encoder = encoder
            self._decoder = decoder
            self._cookie_store = cookie_store
            self._filters = list(filters) if filters is not None else []
            self._context_attributes = list(context_attributes) if context_attributes is not None else []
            self._plugin_groups = list(plugin_groups) if plugin_groups is not None else list(self._config.plugin_groups)

            self._base_url = base_url
            self._api_version = api_version
            self._default_timeout = default_timeout
            self._default_headers = default_headers if default_headers is not None else {}
            self._default_cookies = default_cookies if default_cookies is not None else {}

        # 共通の実行パイプライン(マトリョーシカチェーン)の動的組み立て
        self._exchange_function = self._init_exchange_pipeline()

    def _init_exchange_pipeline(self) -> ExchangeFunction:
        """フィルター群とコネクターを逆順走査し、マトリョーシカ状にラップされた実行パイプラインを動的に組み立てます。"""
        pipeline_filters: list[ExchangeFilter] = []

        # コンテキスト属性フィルターを最外周(先頭)へ配置する規約の執行
        if self._context_attributes:
            from webclient.filters.context_attributes_filter import ContextAttributesFilter

            for c_var, attr_key in self._context_attributes:
                pipeline_filters.append(ContextAttributesFilter(c_var, attr_key))

        pipeline_filters.extend(self._filters)

        pipeline: ExchangeFunction = DefaultConnectorExchangeFunction(self._connector)
        for raw_filter in reversed(pipeline_filters):
            pipeline = FilteredExchangeFunction(raw_filter, pipeline)

        return pipeline

    @classmethod
    def builder(cls, config: WebClientConfig | None = None) -> WebClientBuilder:
        return WebClientBuilder(config)

    def mutate(self) -> WebClientBuilder:
        """現在の不変状態を完全に引き継いだ、新しい WebClientBuilder を安全に再構成します。"""
        builder = WebClientBuilder(self._config)
        builder.base_url(self._base_url)
        builder.api_version(self._api_version)

        builder.default_timeout(self._default_timeout)

        builder.default_headers(self._default_headers)
        builder.default_cookies(self._default_cookies)

        if self._cookie_store is not None:
            builder.cookie_store(self._cookie_store)

        builder.body_encoder(self._encoder)
        builder.body_decoder(self._decoder)
        builder.client_connector(self._connector)

        cast_builder = cast(Any, builder)
        if self._filters:
            if PrioritizedFilter not in cast_builder._explicit_pool:
                cast_builder._explicit_pool[PrioritizedFilter] = []
            for f in self._filters:
                cast_builder._explicit_pool[PrioritizedFilter].append(f)

        if self._context_attributes:
            cast_builder._context_attributes = list(self._context_attributes)

        builder.plugin_groups(list(self._plugin_groups))

        cast_builder._is_mutated = True
        return builder

    def create(self, base_url: str, /) -> WebClient:
        return self.mutate().base_url(base_url).build()

    def switch_connector(self, connector: ClientHttpConnector, /) -> WebClient:
        return self.mutate().client_connector(connector).build()

    def get(self) -> RequestHeadersUriSpec:
        return RequestHeadersUriSpec(
            self._exchange_function,
            self._encoder,
            self._decoder,
            HttpMethod.GET,
            self._api_version,
            self._default_headers,
            self._default_cookies,
            self._default_timeout,
        )

    def post(self) -> RequestBodyUriSpec:
        return RequestBodyUriSpec(
            self._exchange_function,
            self._encoder,
            self._decoder,
            HttpMethod.POST,
            self._api_version,
            self._default_headers,
            self._default_cookies,
            self._default_timeout,
        )

    def put(self) -> RequestBodyUriSpec:
        return RequestBodyUriSpec(
            self._exchange_function,
            self._encoder,
            self._decoder,
            HttpMethod.PUT,
            self._api_version,
            self._default_headers,
            self._default_cookies,
            self._default_timeout,
        )

    def delete(self) -> RequestHeadersUriSpec:
        return RequestHeadersUriSpec(
            self._exchange_function,
            self._encoder,
            self._decoder,
            HttpMethod.DELETE,
            self._api_version,
            self._default_headers,
            self._default_cookies,
            self._default_timeout,
        )

    def patch(self) -> RequestBodyUriSpec:
        return RequestBodyUriSpec(
            self._exchange_function,
            self._encoder,
            self._decoder,
            HttpMethod.PATCH,
            self._api_version,
            self._default_headers,
            self._default_cookies,
            self._default_timeout,
        )

    def head(self) -> RequestHeadersUriSpec:
        return RequestHeadersUriSpec(
            self._exchange_function,
            self._encoder,
            self._decoder,
            HttpMethod.HEAD,
            self._api_version,
            self._default_headers,
            self._default_cookies,
            self._default_timeout,
        )

    def options(self) -> RequestHeadersUriSpec:
        return RequestHeadersUriSpec(
            self._exchange_function,
            self._encoder,
            self._decoder,
            HttpMethod.OPTIONS,
            self._api_version,
            self._default_headers,
            self._default_cookies,
            self._default_timeout,
        )

    def method(self, method: HttpMethod, /) -> RequestBodySpec:
        return RequestBodySpec(
            self._exchange_function,
            self._encoder,
            self._decoder,
            method,
            self._api_version,
            self._default_headers,
            self._default_cookies,
            self._default_timeout,
        )

    async def close(self) -> None:
        if self._connector is not None:
            await self._connector.close()


class WebClientBuilder:
    """指示役・ファクトリの責務に特化した不純物なしの WebClient ビルダー。"""

    def __init__(self, config: WebClientConfig | None = None) -> None:
        self._config = config or WebClientConfig()
        self._overrides: dict[str, Any] = {}
        self._explicit_pool: dict[type[Any], Any] = {}
        self._client_extension_pool: dict[type[Any], Any] = {}
        self._context_attributes: list[tuple[ContextVar[object], str]] = []

    def base_url(self, base_url: str, /) -> Self:
        self._overrides["base_url"] = base_url
        return self

    def api_version(self, api_version: str, /) -> Self:
        self._overrides["api_version"] = api_version
        return self

    def default_timeout(self, timeout: float | object | None, /) -> Self:
        self._overrides["timeout"] = timeout
        return self

    def default_headers(self, headers: Mapping[str, str], /) -> Self:
        current = dict(self._overrides.get("default_headers", self._config.default_headers))
        current.update(headers)
        self._overrides["default_headers"] = current
        return self

    def default_cookies(self, cookies: Mapping[str, str], /) -> Self:
        current = dict(self._overrides.get("default_cookies", self._config.default_cookies))
        current.update(cookies)
        self._overrides["default_cookies"] = current
        return self

    def cookie_store(self, store: CookieStore, /) -> Self:
        self._explicit_pool[CookieStore] = store
        return self

    def body_encoder(self, encoder: BodyEncoder, /) -> Self:
        self._explicit_pool[BodyEncoder] = encoder
        return self

    def body_decoder(self, decoder: BodyDecoder, /) -> Self:
        self._explicit_pool[BodyDecoder] = decoder
        return self

    def client_connector(self, connector: ClientHttpConnector, /) -> Self:
        self._explicit_pool[ClientHttpConnector] = connector
        return self

    def filter(
        self, filter_func: ExchangeFilterFunction, /, *, priority: int = 100, shortcut_name: str | None = None
    ) -> Self:
        if PrioritizedFilter not in self._explicit_pool:
            self._explicit_pool[PrioritizedFilter] = []
        self._explicit_pool[PrioritizedFilter].append(
            PrioritizedFilter(filter_func=filter_func, priority=priority, name_key=shortcut_name)
        )
        return self

    def context_attribute(self, context_var: ContextVar[object], attribute_key: str, /) -> Self:
        self._context_attributes.append((context_var, attribute_key))
        return self

    def plugin_groups(self, groups: list[str], /) -> Self:
        self._plugin_groups = list(groups)
        return self

    def register_client_extension(self, type_key: type[Any], instance: Any, /) -> Self:
        self._client_extension_pool[type_key] = instance
        return self

    def build(self) -> WebClient:
        """DIコンテナによる環境解決を執行し、解決済みパーツを安全に WebClient へインジェクションします。"""
        final_config = self._config
        if self._overrides:
            final_config = self._config.model_copy(update=self._overrides)

        resolved_pool = UniversalPluginResolver.resolve_all(
            config=final_config, client_extension_pool=self._client_extension_pool, explicit_pool=self._explicit_pool
        )

        return WebClient(
            final_config,
            base_url=final_config.base_url,
            api_version=final_config.api_version,
            default_timeout=final_config.timeout,
            default_headers=final_config.default_headers,
            default_cookies=final_config.default_cookies,
            cookie_store=resolved_pool.get(CookieStore),
            encoder=resolved_pool[BodyEncoder],
            decoder=resolved_pool[BodyDecoder],
            connector=resolved_pool[ClientHttpConnector],
            filters=resolved_pool.get(PrioritizedFilter, []),
            context_attributes=self._context_attributes,
            plugin_groups=getattr(self, "_plugin_groups", list(final_config.plugin_groups)),
        )
