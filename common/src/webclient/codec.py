from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import cast


class BodyEncoder(ABC):
    # オブジェクトをHTTPリクエストで送信可能な形式に変換する抽象エンコーダー

    @abstractmethod
    def encode(self, obj: object, /) -> object: ...


# TODO: Implement PydanticBodyEncoder using pydantic.RootModel / TypeAdapter to support BaseModel serialization.
# TODO: Implement MsgspecBodyEncoder using msgspec.json.encode for ultra-fast request serialization.
class DefaultBodyEncoder(BodyEncoder):
    # Python標準ライブラリ（json, dataclasses）に準拠したデフォルトのエンコーダー

    def encode(self, obj: object, /) -> object:
        if obj is None or isinstance(obj, bytes | str | int | float | bool):
            return obj
        if isinstance(obj, Mapping | list):
            return obj
        if is_dataclass(obj) and not isinstance(obj, type):
            return asdict(obj)
        raise ValueError(f"サポートされていないボディのオブジェクト型です: {type(obj)}")


class BodyDecoder(ABC):

    @abstractmethod
    def decode[T](self, data: bytes | str, element_type: type[T], /) -> T: ...


# TODO: Implement PydanticBodyDecoder using pydantic.TypeAdapter to support BaseModel and robust data validation.
# TODO: Implement MsgspecBodyDecoder using msgspec.json.Decoder to achieve ultra-high-performance JSON parsing.
class DefaultBodyDecoder(BodyDecoder):

    def decode[T](self, data: bytes | str, element_type: type[T], /) -> T:
        raw_bytes = data if isinstance(data, bytes) else data.encode("utf-8")
        if element_type is bytes:
            return cast(T, raw_bytes)

        decoded_str = raw_bytes.decode("utf-8")
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
