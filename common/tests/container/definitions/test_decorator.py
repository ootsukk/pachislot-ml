
from __future__ import annotations

from unittest.mock import patch

import pytest
from container.definitions.decorator import (
    MetadataAccessor,
    VersionConstraint,
    dependency_module,
    plugin,
    plugin_impl,
)


class TestVersionConstraint:
    @pytest.mark.parametrize(
        ("expr", "actual", "expected"),
        [
            (">=1.2.0", "1.2.3", True),
            (">=1.2.0", "1.1.9", False),
            ("<=2.0", "2.0.0", True),
            ("<=2.0", "2.1.0", False),
            ("==3.14", "3.14.0", True),
            ("==3.14", "3.14.1", False),
            (">1.0", "1.0.1", True),
            ("<5.0", "4.9.9", True),
        ],
    )
    def test_version_satisfies_constraints(self, expr: str, actual: str, expected: bool) -> None:
        constraint = VersionConstraint(expr)
        assert constraint.is_satisfied_by(actual) == expected


class TestDecoratorMetadataMetadataAccessor:
    def test_plugin_decorator_injects_metadata(self) -> None:
        # Arrange & Act
        class BaseInterface: ...

        @plugin
        class TargetInterface(BaseInterface): ...

        # Assert
        meta = MetadataAccessor.get_plugin_meta(TargetInterface)
        assert meta is not None
        assert isinstance(meta.depends_on, tuple)

    def test_plugin_impl_decorator_injects_metadata(self) -> None:
        # Arrange & Act
        @plugin_impl(value="custom_service", priority=250)
        class TargetImplementation: ...

        # Assert
        meta = MetadataAccessor.get_plugin_impl_meta(TargetImplementation)
        assert meta is not None
        assert meta.value == "custom_service"
        assert meta.priority == 250

    def test_dependency_module_evaluation_lifecycle(self) -> None:
        # Arrange
        @dependency_module(module_name="dummy_lib", version=">=2.0.0")
        class DependentComponent: ...

        # Act & Assert
        meta = MetadataAccessor.get_dependency_meta(DependentComponent)
        assert meta is not None
        assert meta.module_name == "dummy_lib"
        assert meta.version == ">=2.0.0"

        # 外部モジュールチェックの遅延評価のモック検証
        with patch("importlib.metadata.version", return_value="2.1.5"):
            assert meta.check_satisfied() is True

        # 冪等性(キャッシュ効果)の検証: 異なるバージョン環境になっても結果が固定されること
        with patch("importlib.metadata.version", return_value="1.0.0"):
            assert meta.check_satisfied() is True