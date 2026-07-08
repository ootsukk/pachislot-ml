from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final, Protocol

from container.common.constants import YAML_SUFFIX_OPTIONS, YAML_VAL_AUTO
from container.definitions.registry import PluginDefinition

_SNAKE_CASE_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?<!^)(?=[A-Z])")


class NamingStrategy(Protocol):
    """クラス型から構成ファイル上のキー名を導出するための共通戦略インターフェース。"""

    def get_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None: ...
    def get_options_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None: ...
    def get_collection_key(
        self, cls_obj: type[object] | None = None, dynamic_name: str | None = None
    ) -> str | None: ...


class FixedKeyStrategy:
    """明示的に指定された固定文字列を最優先で返す具象命名戦略。"""

    def __init__(self, key: str | None, /) -> None:
        self._key: Final[str | None] = key

    def get_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None:
        if dynamic_name == YAML_VAL_AUTO or not self._key:
            return None
        return self._key

    def get_options_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None:
        if dynamic_name == YAML_VAL_AUTO or not self._key:
            return None
        return f"{self._key}{YAML_SUFFIX_OPTIONS}"

    def get_collection_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None:
        if dynamic_name == YAML_VAL_AUTO or not self._key:
            return None
        return self._key


class PluginNameKeyStrategy:
    """実装クラスのメタデータからキー名を抽出する具象命名戦略。"""

    def get_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None:
        if dynamic_name and dynamic_name != YAML_VAL_AUTO:
            return dynamic_name

        if cls_obj is not None and hasattr(cls_obj, PluginDefinition.META_ATTR_CONTAINER):
            meta: object = getattr(cls_obj, PluginDefinition.META_ATTR_CONTAINER, None)
            if hasattr(meta, "value"):
                return str(getattr(meta, "value", None))
        return None

    def get_options_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None:
        if (
            cls_obj is not None
            and hasattr(cls_obj, PluginDefinition.META_ATTR_CONTAINER)
            and dynamic_name == YAML_VAL_AUTO
        ):
            return ""
        return None

    def get_collection_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None:
        if dynamic_name and dynamic_name != YAML_VAL_AUTO:
            return dynamic_name
        return None


class ClassNameAutomaticKeyStrategy:
    """クラス名から自動的にスネークケース文字列を生成する最終フォールバック命名戦略。"""

    def __init__(self, cls_obj: type[object] | None = None, /) -> None:
        return

    def get_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None:
        if cls_obj is not None:
            name = cls_obj.__name__
            return _SNAKE_CASE_PATTERN.sub("_", name).lower()
        return None

    def get_options_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None:
        snake_name = self.get_key(cls_obj, dynamic_name)
        return f"{snake_name}{YAML_SUFFIX_OPTIONS}" if snake_name else None

    def get_collection_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None:
        snake_name = self.get_key(cls_obj, dynamic_name)
        return f"{snake_name}s" if snake_name else None


class ChainNamingStrategy:
    """登録された複数の具象戦略を配列順に走査し最初に適合したキー名を採用する複合戦略。"""

    def __init__(self, fixed_key: str | None = None, /) -> None:
        strategies: list[NamingStrategy] = []
        strategies.append(FixedKeyStrategy(fixed_key))
        strategies.append(PluginNameKeyStrategy())
        strategies.append(ClassNameAutomaticKeyStrategy())

        self._strategies: Final[Sequence[NamingStrategy]] = strategies

    def get_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str:
        for strategy in self._strategies:
            resolved = strategy.get_key(cls_obj, dynamic_name)
            if resolved is not None and resolved != "":
                return resolved
        return ""

    def get_options_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str:
        for strategy in self._strategies:
            resolved = strategy.get_options_key(cls_obj, dynamic_name)
            if resolved is not None and resolved != "":
                return resolved
        return ""

    def get_collection_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str:
        for strategy in self._strategies:
            resolved = strategy.get_collection_key(cls_obj, dynamic_name)
            if resolved is not None and resolved != "":
                return resolved
        return ""


def naming_chain(fixed_key_str: str | None, /) -> NamingStrategy:
    return ChainNamingStrategy(fixed_key_str)
