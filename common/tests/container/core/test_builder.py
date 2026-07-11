from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, PropertyMock
import graphlib
import pytest

from container.core.builder import DependencyGraphSorter
from container.definitions.component import Component, ComponentRegistry
from container.definitions.descriptor import PluginDescriptor
from container.definitions.registry import PluginRegistry


# テスト用ダミーインターフェースおよび具象クラス
class ServiceProtocol: ...


class MainService(ServiceProtocol): ...


class DependencyService: ...


class TestDependencyGraphSorter:
    def test_sort_nodes_successful_ordered_sequence(self) -> None:
        # Arrange: 階層構造（MainService -> DependencyService）のトポロジーを構築
        mock_comp_registry = MagicMock(spec=ComponentRegistry)
        mock_plugin_registry = MagicMock(spec=PluginRegistry)

        # 依存解決用のルックアップ設定
        mock_comp_registry.lookup.side_effect = lambda t: t if t in (MainService, DependencyService) else None

        # コンポーネントメタデータの模倣
        mock_component = MagicMock(spec=Component)
        mock_component.target_type = MainService
        mock_component.plugin_spec_type = ServiceProtocol
        mock_component.naming_strategy = MagicMock()

        # プラグイン定義データの模倣
        mock_definition = MagicMock()
        mock_definition.plugin_name = "main_service"
        mock_definition.impl_class = MainService
        mock_definition.depends_on = [DependencyService]
        mock_definition.constructor_dependencies = {"dep": DependencyService}

        mock_plugin_registry.get_all_definitions.return_value = [mock_definition]

        # PluginDescriptorのenabledフラグのパッチング
        with MagicMock() as mock_descriptor_cls:
            instance = mock_descriptor_cls.return_value
            type(instance).enabled = PropertyMock(return_value=True)

            sorter_target = DependencyGraphSorter(
                mock_comp_registry, mock_plugin_registry, {"main_service": {}}, [mock_component]
            )

            # Act
            result = sorter_target.sort_nodes(dict)

        # Assert: 依存下流のDependencyServiceがMainServiceより先に整列されていることを検証
        assert dict in result
        assert MainService in result
        assert DependencyService in result
        assert result.index(DependencyService) < result.index(MainService)

    def test_sort_nodes_raises_runtime_error_on_cyclic_dependency(self) -> None:
        # Arrange: A -> B -> A の循環参照（閉路）を強制的にエミュレート
        mock_comp_registry = MagicMock(spec=ComponentRegistry)
        mock_plugin_registry = MagicMock(spec=PluginRegistry)

        class ComponentA: ...

        class ComponentB: ...

        mock_comp_registry.lookup.side_effect = lambda t: t

        # コンポーネントAの設定
        mock_comp_a = MagicMock(spec=Component)
        mock_comp_a.target_type = ComponentA
        mock_comp_a.plugin_spec_type = ComponentA

        mock_def_a = MagicMock()
        mock_def_a.plugin_name = "comp_a"
        mock_def_a.impl_class = ComponentA
        mock_def_a.depends_on = [ComponentB]
        mock_def_a.constructor_dependencies = {}

        # コンポーネントBの設定
        mock_comp_b = MagicMock(spec=Component)
        mock_comp_b.target_type = ComponentB
        mock_comp_b.plugin_spec_type = ComponentB

        mock_def_b = MagicMock()
        mock_def_b.plugin_name = "comp_b"
        mock_def_b.impl_class = ComponentB
        mock_def_b.depends_on = [ComponentA]
        mock_def_b.constructor_dependencies = {}

        # レジストリの振る舞い振り分け
        def get_defs(spec_type: type[object]) -> list[Any]:
            if spec_type is ComponentA:
                return [mock_def_a]
            if spec_type is ComponentB:
                return [mock_def_b]
            return []

        mock_plugin_registry.get_all_definitions.side_effect = get_defs

        sorter_target = DependencyGraphSorter(
            mock_comp_registry, mock_plugin_registry, {"comp_a": {}, "comp_b": {}}, [mock_comp_a, mock_comp_b]
        )

        # Act & Assert: graphlib.CycleError を内包した RuntimeError が正確に送出されるか検証
        with pytest.raises(RuntimeError) as exc_info:
            sorter_target.sort_nodes(dict)

        assert "トポロジー上に閉路（循環参照）が検出されたため" in str(exc_info.value)
