from __future__ import annotations

import inspect
import sys
import types
from collections.abc import Generator
from unittest.mock import MagicMock, patch
import pytest

from container.definitions.component import Component
from container.definitions.registry import PluginDefinition, PluginRegistry
from container.scanner.scanner import (
    DynamicScanDefinitionReader,
    InternalPackageDetector,
    PluginScanner,
)


# ==============================================================================
# 検証用スタブ定義
# ==============================================================================
class MockSpecType: ...


class ValidExtensionImpl(MockSpecType):
    """コンテナメタ属性を内包する正常な実装候補スタブクラス。"""

    __plugin_impl_meta__ = types.SimpleNamespace(priority=150, depends_on=(), value="valid_extension")

    def __init__(self, dependency_item: MockSpecType) -> None:
        self.dependency_item = dependency_item


# from __future__ import annotations による実行時の文字列化を回避するため、型メタデータを明示的に再代入
ValidExtensionImpl.__init__.__annotations__ = {"dependency_item": MockSpecType}


class TestInternalPackageDetector:
    def test_detect_yields_classes_in_target_packages(self) -> None:
        # Arrange
        detector = InternalPackageDetector(["dummy_pkg"])
        mock_module = types.ModuleType("dummy_pkg")
        mock_module.__path__ = ["/fake/path"]  # type: ignore

        class DiscoveredClass:
            pass

        DiscoveredClass.__module__ = "dummy_pkg.sub"

        mock_module_info = MagicMock()
        mock_module_info.name = "dummy_pkg.sub"

        # importlib.import_moduleのパッチを完全に排除し、sys.modulesに擬似モジュールを事前登録することで、
        # mock.patchの内部動作(inspectモジュール等のインポート処理)の破壊を防御します。
        with (
            patch.dict(sys.modules, {"dummy_pkg": mock_module, "dummy_pkg.sub": mock_module}),
            patch("pkgutil.walk_packages", return_value=[mock_module_info]),
            patch("inspect.getmembers", return_value=[("DiscoveredClass", DiscoveredClass)]),
        ):
            # Act
            discovered = list(detector.detect())

            # Assert
            assert len(discovered) == 1
            assert discovered[0] is DiscoveredClass


class TestDynamicScanDefinitionReader:
    def test_read_definitions_parses_constructors_and_metadata(self) -> None:
        # Arrange
        reader = DynamicScanDefinitionReader(
            ["dummy_pkg"],
            [],
            {MockSpecType},
            set(),
        )

        with patch.object(InternalPackageDetector, "detect", return_value=[ValidExtensionImpl]):
            # Act
            definitions = list(reader.read_definitions())

            # Assert
            assert len(definitions) == 1
            definition = definitions[0]

            assert definition.spec_type is MockSpecType
            assert definition.impl_class is ValidExtensionImpl
            assert definition.priority == 150
            assert "dependency_item" in definition.constructor_dependencies
            assert definition.constructor_dependencies["dependency_item"] is MockSpecType


class TestPluginScanner:
    def test_scan_dispatches_to_dynamic_reader_and_builds_registry(self) -> None:
        # Arrange
        mock_component = MagicMock(spec=Component)
        mock_component.plugin_spec_type = MockSpecType

        scanner = PluginScanner(
            ["dummy_pkg"],
            [],
            [mock_component],
        )

        mock_definition = PluginDefinition(
            spec_type=MockSpecType,
            impl_class=ValidExtensionImpl,
            plugin_name="valid_extension",
            priority=100,
            constructor_dependencies={},
            depends_on=(),
        )

        with patch.object(DynamicScanDefinitionReader, "read_definitions", return_value=[mock_definition]):
            # Act
            registry = scanner.scan(cache_index_data=None)

            # Assert
            assert isinstance(registry, PluginRegistry)

            resolved_definition = registry.get_definition(MockSpecType, "valid_extension")
            assert resolved_definition is mock_definition
