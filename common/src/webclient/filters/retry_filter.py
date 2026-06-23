from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from webclient.base import (
    ClientHttpRequest,
    ClientHttpResponse,
    Configurable,
    ExchangeFilter,
    ExchangeFunction,
)
from webclient.plugin import plugin_impl


@dataclass(frozen=True)
class RetryPolicy:
    """一時的なネットワーク障害を自動救済するインテリジェントリトライポリシー"""
    order: int = 30
    max_attempts: int = 3
    backoff_factor: float = 0.5
    retry_statuses: set[int] = {408, 429, 500, 502, 503, 504}


@plugin_impl(value="retry", priority=100)
class RetryFilter(ExchangeFilter, Configurable[RetryPolicy]):
    def __init__(self, config: RetryPolicy | None = None, /, logger: logging.Logger | None = None) -> None:
        self.config: RetryPolicy = config if config is not None else RetryPolicy()
        self.logger: logging.Logger = logger if logger is not None else logging.getLogger("webclient.filter.retry")
        self._retry_statuses: set[int] = self.config.retry_statuses
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

            # リトライ対象ステータス、かつ上限に達していない場合はバックオフスリープ
            if response.status_code in self._retry_statuses and attempts < self.config.max_attempts:
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
            return response
