from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Self

from webclient.base import MultipartPart
from webclient.types import MediaType


class MultipartBodyBuilder:
    """マルチパートリクエストの構築を支援するビルダークラス。個々のパートを追加し、最終的なシーケンスを生成します。"""

    def __init__(self) -> None:
        self._parts: list[MultipartPart] = []

    def part(
        self,
        name: str,
        value: bytes | str,
        /,
        *,
        media_type: MediaType | str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Self:
        """通常のテキスト値やバイナリの断片を、個別のコンテンツ型を伴ってパートとして追加します。"""
        self._parts.append(
            MultipartPart(
                name=name,
                value=value,
                content_type=str(media_type) if media_type is not None else None,
                headers=headers or {},
            )
        )
        return self

    def file(
        self,
        name: str,
        filename: str,
        content: bytes | str,
        /,
        *,
        media_type: MediaType | str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Self:
        """明示的なファイル名(filename)を伴うファイルアップロード用パートを追加します。"""
        self._parts.append(
            MultipartPart(
                name=name,
                value=content,
                filename=filename,
                content_type=str(media_type) if media_type is not None else None,
                headers=headers or {},
            )
        )
        return self

    def build(self) -> Sequence[MultipartPart]:
        """組み立てられたすべてのマルチパート構成要素のシーケンスを返します。"""
        return list(self._parts)
