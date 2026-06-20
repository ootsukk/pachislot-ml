from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import cast

from webclient.base import BodyDecoder, BodyEncoder
from webclient.plugin import plugin_impl
from webclient.types import CHARSET_UTF8


# TODO: Implement PydanticBodyEncoder using pydantic.RootModel / TypeAdapter to support BaseModel serialization.
# TODO: Implement MsgspecBodyEncoder using msgspec.json.encode for ultra-fast request serialization.
@plugin_impl("encoder")
class DefaultBodyEncoder(BodyEncoder):
    # Python標準ライブラリ（json, dataclasses）に準拠したデフォルトのエンコーダー

    def encode(self, body: object, /) -> object:
        if body is None or isinstance(body, bytes | str | int | float | bool):
            return body
        if isinstance(body, Mapping | list):
            return body
        if is_dataclass(body) and not isinstance(body, type):
            return asdict(body)
        raise ValueError(f"サポートされていないボディのオブジェクト型です: {type(body)}")


# TODO: Implement PydanticBodyDecoder using pydantic.TypeAdapter to support BaseModel and robust data validation.
# TODO: Implement MsgspecBodyDecoder using msgspec.json.Decoder to achieve ultra-high-performance JSON parsing.
@plugin_impl("decoder")
class DefaultBodyDecoder(BodyDecoder):

    def decode[T](self, content: bytes | str, element_type: type[T], /) -> T:
        raw_bytes = content if isinstance(content, bytes) else content.encode(CHARSET_UTF8)
        if element_type is bytes:
            return cast(T, raw_bytes)

        decoded_str = raw_bytes.decode(CHARSET_UTF8)
        if element_type is str:
            return cast(T, decoded_str)

        parsed_json = json.loads(decoded_str)
        if element_type is dict or element_type is list:
            return cast(T, parsed_json)

        if is_dataclass(element_type):
            if isinstance(parsed_json, Mapping):
                return element_type(**parsed_json)
            raise ValueError(f"マッピング対象のデータが辞書型ではありません。型: {type(parsed_json)}")

        try:
            return element_type(**parsed_json)
        except Exception:
            return cast(T, parsed_json)
