from __future__ import annotations

from dataclasses import dataclass, replace
from http.cookies import SimpleCookie

from webclient.base import (
    ClientHttpRequest,
    ClientHttpResponse,
    Configurable,
    CookieStore,
    ExchangeFilter,
    ExchangeFunction,
)
from webclient.plugin import plugin_impl


@dataclass(frozen=True)
class CookieSessionOptions:
    """状態維持（有状態セッション）を自動化するクッキー管理フィルター設定"""
    order: int = 40

@plugin_impl("cookie_management", priority=80)
class CookieManagementFilter(ExchangeFilter, Configurable[CookieSessionOptions]):
    def __init__(self, cookie_store: CookieStore, /) -> None:
        self.cookie_store: CookieStore = cookie_store

    async def __call__(
        self, request: ClientHttpRequest, next_exchange: ExchangeFunction, stream: bool, /
    ) -> ClientHttpResponse:
        # 保存されているCookieがあればリクエストへ透過注入
        stored_cookies = self.cookie_store.load(request.url)
        if stored_cookies:
            current_cookies = dict(request.cookies) if request.cookies is not None else {}
            for key, val in stored_cookies.items():
                current_cookies.setdefault(key, val)
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
