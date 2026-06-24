from __future__ import annotations

import importlib.resources
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from webclient.base import ProxyOptions, RedirectOptions
from webclient.constants import CONFIG_FILE_NAME, ENTRY_POINT_TARGET, ROOT_PACKAGE_NAME
from webclient.types import CHARSET_UTF8
from webclient.utility import deep_merge


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
    plugin_groups: Sequence[str] = Field(default_factory=lambda: [ENTRY_POINT_TARGET])

    @model_validator(mode="before")
    @classmethod
    def _preprocess_config(cls, data: Any) -> Any:

        base_dict: dict[str, Any] = {}
        try:
            # デフォルト設定ファイルの読み込み
            config_resource = importlib.resources.files(ROOT_PACKAGE_NAME).joinpath(CONFIG_FILE_NAME)
            if config_resource.is_file():
                with config_resource.open(encoding=CHARSET_UTF8) as f:
                    base_dict = yaml.safe_load(f) or {}
        except Exception:
            base_dict = {}

        base_data = base_dict.get(ROOT_PACKAGE_NAME, base_dict) if ROOT_PACKAGE_NAME in base_dict else base_dict
        if not isinstance(base_data, dict):
            base_data = {}

        # ユーザ指定のコンフィグ
        user_raw = data or {}
        user_data = user_raw.get(ROOT_PACKAGE_NAME, user_raw) if ROOT_PACKAGE_NAME in user_raw else user_raw
        if not isinstance(user_data, dict):
            user_data = {}

        return deep_merge(base_data, user_data)

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
