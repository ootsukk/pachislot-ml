from __future__ import annotations

import dataclasses
import inspect
import typing
from typing import Any
from unittest.mock import MagicMock, patch
import pytest

from pydantic import BaseModel

from container.common.exceptions import ComponentInstantiationError
from container.definitions.component import (
    CollectionComponent,
    Component,
    InstanceComponent,
    PluginComponent,
    PropertyComponent,
)
from container.instantiation.factory import (
    CollectionComponentFactory,
    ComponentFactoryRegistry,
    InstanceComponentFactory,
    PluginComponentFactory,
    PropertyComponentFactory,
    MetadataWrapperFactory,
    ElementMetadata,
    ConstructorResolver,
)

if typing.TYPE_CHECKING:
    from container.core.session import ResolutionSession


# ==============================================================================
# テスト資産(構造化設定スタブ & 例外・フォールバック誘発用コンストラクタ群)
# ==============================================================================
class DummyService:
    """テスト用のシンプルな引数なし具象サービス。"""

    def __init__(self) -> None:
        pass


@dataclasses.dataclass
class DummyConfigDataClass:
    """データクラス形式の構造化設定テスト用スタブ。"""

    val: int
    name: str


class DummyConfigPydantic(BaseModel):
    """Pydantic形式の構造化設定テスト用スタブ。"""

    val: int
    name: str


class StubWrapperEdgeCases:
    """MetadataWrapperFactory の引数走査の全条件分岐を検証するためのラッパースタブ。"""

    def __init__(
        self,
        priority: int,
        plugin_name: str,
        optional_val: DummyService | None,
        has_default: int = 99,
    ) -> None:
        self.priority = priority
        self.plugin_name = plugin_name
        self.optional_val = optional_val
        self.has_default = has_default


class StubInitNoHint:
    """型ヒントを持たないコンストラクタスタブ。"""

    def __init__(self, value: Any) -> None:
        self.value = value


class StubInitRequired:
    """必須の依存関係を要求するコンストラクタスタブ。"""

    def __init__(self, dep: DummyService) -> None:
        self.dep = dep


class CustomCollectionSuccess:
    """正常にシーケンスを受け入れるカスタムコレクション。"""

    def __init__(self, seq: Any) -> None:
        self.items = list(seq)


class CustomCollectionFailure:
    """インスタンス化時に確定でクラッシュするカスタムコレクション(list不継承)。"""

    def __init__(self, seq: Any) -> None:
        raise ValueError("Forced collection crash")


# ==============================================================================
# テストクラス定義
# ==============================================================================
class TestComponentFactoryRegistry:
    def test_registry_lifecycle_lookup(self) -> None:
        # Arrange: レジストリの生成(内部自動組み込み仕様に適合)
        registry = ComponentFactoryRegistry()

        # Act: 各コンポーネント型に対応するファクトリを取得
        factory_1 = registry.get_factory(InstanceComponent)
        factory_2 = registry.get_factory(PluginComponent)
        factory_3 = registry.get_factory(PropertyComponent)
        factory_4 = registry.get_factory(CollectionComponent)

        # Assert: 正しいファクトリインスタンスが定数時間で選出されることを検証
        assert isinstance(factory_1, InstanceComponentFactory)
        assert isinstance(factory_2, PluginComponentFactory)
        assert isinstance(factory_3, PropertyComponentFactory)
        assert isinstance(factory_4, CollectionComponentFactory)


class TestMetadataWrapperFactory:
    def test_create_wrapper_returns_instance_on_signature_error(self) -> None:
        # 組み込み型(int)などを渡すことで inspect.signature での TypeError/ValueError ルートを走破 (行55-56)
        factory = MetadataWrapperFactory()
        meta = ElementMetadata(instance="primitive_payload")

        res = factory.create_wrapper(int, int, meta)
        assert res is meta.instance

    def test_create_wrapper_edge_cases(self) -> None:
        # 引数解析分岐における int(行66), default値(行72), optional(行74) の未踏パスを一挙に走破
        factory = MetadataWrapperFactory()
        meta = ElementMetadata(instance="not_dummy_service", priority=10, name="test_name")

        with patch("container.definitions.resolvable.ResolvableType.from_annotation") as mock_from:
            mock_res_optional = MagicMock()
            mock_res_optional.is_assignable_from.return_value = False
            mock_res_optional.is_optional = True

            # return_value に置換することで __future__ annotations 由来の文字列化の影響を完全に無効化
            mock_from.return_value = mock_res_optional

            res = factory.create_wrapper(StubWrapperEdgeCases, DummyService, meta)
            assert isinstance(res, StubWrapperEdgeCases)
            assert res.priority == 10
            assert res.plugin_name == "test_name"
            assert res.optional_val is None
            assert res.has_default == 99


class TestConstructorResolver:
    def test_resolve_dependencies_missing_type_hint_raises_error(self) -> None:
        # 型ヒントがなく自動解決できない場合の例外パスを走破 (行121)
        resolver = ConstructorResolver()
        mock_def = MagicMock()
        mock_def.impl_class = StubInitNoHint

        with pytest.raises(ComponentInstantiationError):
            resolver.resolve_dependencies(mock_def, typing.cast("ResolutionSession", MagicMock()))

    def test_resolve_dependencies_fallback_and_exception_paths(self) -> None:
        # 依存解決失敗時のデフォルト値適用、Optional適用、必須依存エラー、予期せぬ例外の全パスを走破 (行145-166)
        resolver = ConstructorResolver()

        # 1. デフォルト値の適用 ＆ Optional型の適用パス
        class StubMixed:
            def __init__(self, val: int = 100, dep: DummyService | None = None) -> None:
                self.val = val
                self.dep = dep

        mock_def_mixed = MagicMock()
        mock_def_mixed.impl_class = StubMixed
        mock_session = MagicMock()
        mock_session.resolve_dependency_instance.return_value = None

        with patch("container.definitions.resolvable.ResolvableType.from_annotation") as mock_from:
            mock_res_val = MagicMock()
            mock_res_val.is_optional = False
            mock_res_dep = MagicMock()
            mock_res_dep.is_optional = True
            mock_from.side_effect = [mock_res_val, mock_res_dep]

            res = resolver.resolve_dependencies(mock_def_mixed, mock_session)
            assert res["val"] == 100
            assert res["dep"] is None

        # 2. 必須依存が解決できない場合のエラーパス
        mock_def_req = MagicMock()
        mock_def_req.impl_class = StubInitRequired
        mock_session_req = MagicMock()
        mock_session_req.resolve_dependency_instance.return_value = None

        with patch("container.definitions.resolvable.ResolvableType.from_annotation") as mock_from:
            mock_res = MagicMock()
            mock_res.is_optional = False
            mock_from.return_value = mock_res

            with pytest.raises(ComponentInstantiationError):
                resolver.resolve_dependencies(mock_def_req, mock_session_req)

        # 3. 解決中に予期せぬ例外が発生したパス
        mock_session_err = MagicMock()
        mock_session_err.resolve_dependency_instance.side_effect = RuntimeError("Internal crash")

        with patch("container.definitions.resolvable.ResolvableType.from_annotation") as mock_from:
            mock_from.return_value = MagicMock()
            with pytest.raises(ComponentInstantiationError):
                resolver.resolve_dependencies(mock_def_req, mock_session_err)


class TestInstanceComponentFactory:
    def test_create_instance_returns_embedded_instance(self) -> None:
        # Arrange: ファクトリとコンポーネントのセットアップ
        wrapper_factory = MetadataWrapperFactory()
        factory = InstanceComponentFactory(wrapper_factory)

        expected_instance = DummyService()

        # モックオブジェクトを型キャストして安全に要求型へ適合
        mock_component = MagicMock()
        mock_component.instance = expected_instance
        component_param = typing.cast(InstanceComponent[object], mock_component)

        session_param = typing.cast("ResolutionSession", MagicMock())

        # Act: インスタンス解決の実行
        result = factory.create_instance(component_param, session_param, {})

        # Assert: 事前に埋め込まれたインスタンスがそのまま透過的に返却されることを検証
        assert result is expected_instance


class TestPropertyComponentFactory:
    def test_create_instance_invalid_mapping_raises_error(self) -> None:
        # Pydantic/dataclassターゲットに対して設定値が辞書型でない場合のガードパスを走破 (行253-254)
        factory = PropertyComponentFactory(MetadataWrapperFactory())
        mock_component = MagicMock()
        mock_component.key = "prop"
        mock_component.target_type = DummyConfigPydantic

        with patch("container.definitions.resolvable.ResolvableType.from_annotation") as mock_from:
            mock_res = MagicMock()
            mock_res.origin = DummyConfigPydantic
            mock_from.return_value = mock_res

            with pytest.raises(ComponentInstantiationError):
                factory.create_instance(mock_component, MagicMock(), {"prop": "string_is_not_mapping"})

    def test_create_instance_conversion_failure_raises_error(self) -> None:
        # プリミティブ変換失敗にともなう ValueError ラップパスを走破 (行262)
        factory = PropertyComponentFactory(MetadataWrapperFactory())
        mock_component = MagicMock()
        mock_component.key = "timeout"
        mock_component.target_type = int

        with patch("container.definitions.resolvable.ResolvableType.from_annotation") as mock_from:
            mock_res = MagicMock()
            mock_res.origin = int
            mock_from.return_value = mock_res

            with pytest.raises(ComponentInstantiationError):
                factory.create_instance(mock_component, MagicMock(), {"timeout": "not_a_number"})

    def test_create_collection_elements_loop_path(self) -> None:
        # コレクション要素の走査ループおよび、内部でのデシリアライズ連携パスを走破 (行279-291)
        factory = PropertyComponentFactory(MetadataWrapperFactory())
        mock_component = MagicMock()
        mock_component.target_type = list[DummyConfigDataClass]

        with patch("container.definitions.resolvable.ResolvableType.from_annotation") as mock_from:
            mock_res = MagicMock()
            mock_res.origin = DummyConfigDataClass
            mock_from.return_value = mock_res

            outer_config = [{"val": 10, "name": "a"}, {"val": 20, "name": "b"}]
            res = factory.create_collection_elements(mock_component, MagicMock(), outer_config, DummyConfigDataClass)

            assert len(res) == 2
            assert isinstance(res[0], DummyConfigDataClass)
            assert res[0].val == 10
            assert res[1].val == 20 # type: ignore


class TestPluginComponentFactory:
    def test_create_instance_executes_constructor_injection(self) -> None:
        # Arrange: ファクトリと依存コンテキストのセットアップ
        wrapper_factory = MetadataWrapperFactory()
        factory = PluginComponentFactory(wrapper_factory)

        # 1. PluginComponent のモック構成と型キャスト
        mock_component = MagicMock()
        mock_component.plugin_spec_type = DummyService
        mock_component.key = "dummy_plugin_key"
        mock_component.naming_strategy = MagicMock()
        component_param = typing.cast(PluginComponent[object], mock_component)

        # 2. PluginDefinition のモック構成(ハッピーパス通過用)
        mock_definition = MagicMock()
        mock_definition.plugin_name = "dummy_plugin"
        mock_definition.impl_class = DummyService
        mock_definition.priority = 100

        # 3. ResolutionSession のモック構成と型キャスト
        mock_session = MagicMock()
        mock_session.resolve_plugin_stream.return_value = [mock_definition]
        mock_session.requested_plugin_name = "dummy_plugin"
        session_param = typing.cast("ResolutionSession", mock_session)

        # 4. 設定辞書ペイロードの準備
        raw_config = {"dummy_plugin_key": {"enabled": True, "plugin_name": "dummy_plugin"}}

        # Act: 正確な引数構造(component, session, raw_config)で解決を執行
        with (
            patch("container.instantiation.factory.PluginDescriptor") as mock_descriptor_cls,
            patch("container.instantiation.validator.PluginEligibilityValidator.validate", return_value=True),
        ):
            # バリデータ通過のため、内部で生成される PluginDescriptor の挙動をエミュレート
            mock_descriptor = MagicMock()
            mock_descriptor.plugin_name = "dummy_plugin"
            mock_descriptor_cls.return_value = mock_descriptor

            result = factory.create_instance(component_param, session_param, raw_config)

        # Assert: コアチェインを経て DummyService が正常に実体化されているか検証
        assert isinstance(result, DummyService)

    def test_create_instance_plugin_name_not_found_raises_error(self) -> None:
        # 指定された名前のプラグインがストリームに見つからず、明示的に例外を投げるパスを走破 (行356)
        factory = PluginComponentFactory(MetadataWrapperFactory())
        mock_component = MagicMock()
        mock_component.key = "plugin"
        mock_component.plugin_spec_type = DummyService

        mock_def = MagicMock()
        mock_def.plugin_name = "real_plugin"

        mock_session = MagicMock()
        mock_session.resolve_plugin_stream.return_value = [mock_def]
        mock_session.requested_plugin_name = "ghost_plugin"

        with pytest.raises(ComponentInstantiationError):
            factory.create_instance(mock_component, mock_session, {"plugin": {}})

    def test_create_collection_elements_success_append_path(self) -> None:
        # プラグインの複数解決時に正常にインスタンスがラップされ、返却配列に詰められるパスを走破 (行381-385)
        factory = PluginComponentFactory(MetadataWrapperFactory())
        mock_component = MagicMock()
        mock_component.plugin_spec_type = DummyService
        mock_component.target_type = DummyService
        mock_component.naming_strategy = MagicMock()

        mock_def = MagicMock()
        mock_def.plugin_name = "plugin_a"
        mock_def.priority = 10

        mock_session = MagicMock()
        mock_session.resolve_plugin_stream.return_value = [mock_def]

        expected_inst = DummyService()
        with (
            patch("container.instantiation.factory.PluginDescriptor") as mock_desc_cls,
            patch.object(factory, "create_instance_direct", return_value=expected_inst),
        ):
            mock_desc = MagicMock()
            mock_desc.enabled = True
            mock_desc_cls.return_value = mock_desc

            res = factory.create_collection_elements(mock_component, mock_session, {"plugin_a": {}}, DummyService)
            assert len(res) == 1
            assert res[0] is expected_inst


class TestCollectionComponentFactory:
    def test_create_instance_tuple_and_custom_collections(self) -> None:
        # コレクション一括生成時の tuple マッピング(行429)、カスタム型生成(行431)、および例外ラップパス(行449-450)を全走破
        factory = CollectionComponentFactory()
        mock_registry = MagicMock()
        factory.set_registry(mock_registry)

        mock_element_factory = MagicMock()
        mock_element_factory.create_collection_elements.return_value = ["item1", "item2"]
        mock_registry.get_factory.return_value = mock_element_factory

        mock_component = MagicMock()
        mock_component.key = "items"
        mock_nested = MagicMock()
        mock_component.nested_component = mock_nested

        session_param = typing.cast("ResolutionSession", MagicMock())
        raw_config = {"items": []}

        # 1. tuple キャスト返却分岐の検証 (行429)
        with patch("container.definitions.resolvable.ResolvableType.from_annotation") as mock_from:
            mock_res_tuple = MagicMock()
            mock_res_tuple.origin = tuple
            mock_res_tuple.first_generic_argument = str
            mock_from.return_value = mock_res_tuple

            res_tuple = factory.create_instance(mock_component, session_param, raw_config)
            assert isinstance(res_tuple, tuple)
            assert res_tuple == ("item1", "item2")

        # 2. 任意のカスタムコレクション型生成の正常系検証 (行431)
        with patch("container.definitions.resolvable.ResolvableType.from_annotation") as mock_from:
            mock_res_custom = MagicMock()
            mock_res_custom.origin = CustomCollectionSuccess
            mock_res_custom.first_generic_argument = str
            mock_from.return_value = mock_res_custom

            res_custom = factory.create_instance(mock_component, session_param, raw_config)
            assert isinstance(res_custom, CustomCollectionSuccess)
            assert res_custom.items == ["item1", "item2"]

        # 3. カスタムコレクション型生成にともなう TypeError/ValueError のラップ例外検証 (行449-450)
        with patch("container.definitions.resolvable.ResolvableType.from_annotation") as mock_from:
            mock_res_fail = MagicMock()
            mock_res_fail.origin = CustomCollectionFailure
            mock_res_fail.first_generic_argument = str
            mock_from.return_value = mock_res_fail

            with pytest.raises(ComponentInstantiationError):
                factory.create_instance(mock_component, session_param, raw_config)
