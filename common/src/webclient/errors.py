from __future__ import annotations

from collections.abc import Mapping


class WebClientError(Exception):
    pass


class WebClientResponseError(WebClientError):

    def __init__(self, status_code: int, headers: Mapping[str, str], body: bytes) -> None:
        self.status_code: int = status_code
        self.headers: Mapping[str, str] = headers
        self.body: bytes = body
        super().__init__(f"HTTP {status_code} : WebClientリクエストが失敗しました。")
