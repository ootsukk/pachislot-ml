from __future__ import annotations

import collections.abc
import types
import typing
from typing import Any
from unittest.mock import MagicMock, patch
import pytest

from container.definitions.registry import PluginDefinition
from container.scanner.serializer import PluginIndexSerializer, CacheIndexDefinitionReader

if typing.TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


# ==============================================================================
# テスト資産(仕様・実装クラス・汎用ダミースタブ)
# ==============================================================================
class StubSpecInterface:
    """シリアライズ検証用の仕様インターフェーススタブ。"""

    pass


class StubPluginImpl:
    """シリアライズ検証用のプラグイン実装スタブ。"""

    def __init__(self, dep: StubSpecInterface) -> None:
        self.dep = dep


def not_a_class_function() -> None:
    """_resolve_type_path の型チェックエラーを誘発するための関数スタブ。"""
    pass


# ==============================================================================
# テストクラス定義
# ==============================================================================
class TestPluginIndexSerializer:
    def test_serialize_standard_and_generic_alias_dependencies(self) -> None:
        # 位置引数の不足を回避するため、本物のクラスは使わず、最外殻を MagicMock で完全代替
        mock_registry = MagicMock()

        # 内部に配置される擬似的な定義オブジェクトの構築
        mock_def = MagicMock()
        mock_def.impl_class = StubPluginImpl
        mock_def.priority = 120
        # GenericAlias型(list[StubSpecInterface]) と 通常型(StubSpecInterface) を混ぜて注入
        mock_def.constructor_dependencies = {
            "normal_param": StubSpecInterface,
            "generic_param": list[StubSpecInterface],
        }
        mock_def.depends_on = [StubSpecInterface]

        # シリアライザが内部で参照する '_registry' アトリビュートを直接モックへマッピング
        mock_registry._registry = {StubSpecInterface: {"test_plugin_name": mock_def}}

        # Act: シリアライズの実行 (型キャストを挟みランタイム上の安全を確保)
        exported = PluginIndexSerializer.serialize(typing.cast(Any, mock_registry))

        # Assert: 期待される文字列表現に構造化マッピングされているか検証
        spec_key = f"{StubSpecInterface.__module__}.{StubSpecInterface.__name__}"
        assert spec_key in exported
        assert "test_plugin_name" in exported[spec_key]

        plugin_data = exported[spec_key]["test_plugin_name"]
        assert plugin_data[PluginDefinition.FIELD_PRIORITY] == 120

        # object型の nominal 制約を突破するため、明示的に dict[str, str] へキャスト
        deps = typing.cast(dict[str, str], plugin_data[PluginDefinition.FIELD_CONSTRUCTOR_DEPS])

        assert deps["normal_param"] == f"{StubSpecInterface.__module__}.{StubSpecInterface.__name__}"
        # list[T] の場合、__origin__ である 'builtins.list' に変換されているか検証
        assert deps["generic_param"] == "builtins.list"


class TestCacheIndexDefinitionReader:
    def test_resolve_type_path_raises_attribute_error(self) -> None:
        reader = CacheIndexDefinitionReader({}, set(), set())

        with patch("importlib.import_module") as mock_import, patch("typing.cast", side_effect=lambda t, v: v):
            mock_mod = MagicMock()
            setattr(mock_mod, "FakeTarget", not_a_class_function)
            mock_import.return_value = mock_mod

            with pytest.raises(AttributeError) as exc_info:
                reader._resolve_type_path("dummy.module.FakeTarget")
            assert "クラスオブジェクトではありません" in str(exc_info.value)

    def test_read_definitions_skips_on_spec_type_resolution_error(self) -> None:
        cache_data = {"invalid.module.GhostInterface": {"plugin_a": {}}}
        reader = CacheIndexDefinitionReader(cache_data, set(), set())

        with patch.object(reader, "_resolve_type_path", side_effect=ImportError("Module not found")):
            results = reader.read_definitions()

        assert len(results) == 0

    def test_read_definitions_filters_unwanted_or_ignored_spec_types(self) -> None:
        cache_data = {f"{StubSpecInterface.__module__}.{StubSpecInterface.__name__}": {"plugin_a": {}}}

        reader_unwanted = CacheIndexDefinitionReader(cache_data, set(), set())
        with patch.object(reader_unwanted, "_resolve_type_path", return_value=StubSpecInterface):
            assert len(reader_unwanted.read_definitions()) == 0

        reader_ignored = CacheIndexDefinitionReader(cache_data, {StubSpecInterface}, {StubSpecInterface})
        with patch.object(reader_ignored, "_resolve_type_path", return_value=StubSpecInterface):
            assert len(reader_ignored.read_definitions()) == 0

    def test_read_definitions_skips_on_inner_element_resolution_error(self) -> None:
        spec_key = f"{StubSpecInterface.__module__}.{StubSpecInterface.__name__}"
        cache_data = {
            spec_key: {
                "broken_plugin": {PluginDefinition.FIELD_IMPL_CLASS: "broken.module.CrashClass"},
                "valid_plugin": {
                    PluginDefinition.FIELD_IMPL_CLASS: f"{StubPluginImpl.__module__}.{StubPluginImpl.__name__}",
                    PluginDefinition.FIELD_CONSTRUCTOR_DEPS: {"dep": spec_key},
                    PluginDefinition.FIELD_DEPENDS_ON: [spec_key],
                },
            }
        }

        reader = CacheIndexDefinitionReader(cache_data, {StubSpecInterface}, set())

        def side_effect_resolve(path: str) -> type[object]:
            if "CrashClass" in path:
                raise AttributeError("Forced class missing error")
            if "StubPluginImpl" in path:
                return StubPluginImpl
            return StubSpecInterface

        with patch.object(reader, "_resolve_type_path", side_effect=side_effect_resolve):
            results = reader.read_definitions()

        assert len(results) == 1
        assert results[0].plugin_name == "valid_plugin"
        assert results[0].impl_class is StubPluginImpl
        assert results[0].constructor_dependencies["dep"] is StubSpecInterface
        assert results[0].depends_on == (StubSpecInterface,)
