from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from webclient.base import ProxyOptions, RedirectOptions
from webclient.types import CHARSET_UTF8


class WebClientConfig(BaseModel):

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True,
        "extra": "allow",
    }

    base_url: str = ""
    api_version: str = ""
    default_headers: Mapping[str, str] = Field(default_factory=dict)
    default_cookies: Mapping[str, str] = Field(default_factory=dict)
    timeout: float | None = None
    proxy: ProxyOptions | None = None
    redirect: RedirectOptions = Field(default_factory=RedirectOptions)
    encoder: str = "auto"
    decoder: str = "auto"
    http_connector: str = "auto"
    cookie_store: str = "auto"
    filters: Mapping[str, Any] = Field(default_factory=dict)
    plugin_groups: Sequence[str] = Field(default_factory=lambda: ["webclient.plugins"])

    @model_validator(mode="before")
    @classmethod
    def _preprocess_config(cls, data: Any) -> Any:
        """ルート直下か、"webclient" キーによるネスト構造かの歪みだけを矯正する最小限のゲート。"""
        if isinstance(data, dict) and "webclient" in data:
            return data["webclient"]
        return data

    @classmethod
    def load(cls, source: str | Path | Mapping[str, Any], /) -> WebClientConfig:
        """ファイルパス(YAML)または生の辞書(Mapping)を自動判別し、WebClientConfigを構築します。"""
        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.exists():
                return cls()
            with path.open(encoding=CHARSET_UTF8) as f:
                raw_mapping = yaml.safe_load(f) or {}
            return cls.model_validate(raw_mapping)

        return cls.model_validate(source)
