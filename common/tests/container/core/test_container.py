from __future__ import annotations

import contextlib
import types
import typing
from typing import Any
from unittest.mock import MagicMock, patch
import pytest

from container.common.constants import ComponentScope
from container.common.exceptions import ComponentInstantiationError
from container.common.metadata import CacheKey
from container.definitions.resolvable import ResolvableType
from container.core.container import RuntimeInstanceContainer

if typing.TYPE_CHECKING:
    from container.common.interfaces import Closable


# ==============================================================================
# テスト資産(仕様インターフェース & 具象サービススタブ)
# ==============================================================================
class DummyServiceInterface:
    pass


class DummyServiceImpl(DummyServiceInterface):
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class StubClosable:
    """_register_resource の Closable 判定を通過させるためのスタブ。"""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


# ==============================================================================
# テストクラス定義
# ==============================================================================
class TestRuntimeInstanceContainerLifecycle:
    def test_rebuild_success_and_failure(self) -> None:
        # rebuild() の正常系(行38-40)と、ContextBuilder 欠落時の例外ルートを走破
        mock_registry = MagicMock()
        mock_scope = MagicMock()
        mock_engine = MagicMock()
        mock_builder = MagicMock()

        # 1. 正常系
        container = RuntimeInstanceContainer(mock_registry, mock_scope, mock_engine, mock_builder)
        container.rebuild()
        mock_builder.build.assert_called_once()

        # 2. 異常系 (builder_context が None の場合)
        container_fail = RuntimeInstanceContainer(mock_registry, mock_scope, mock_engine, None)  # type: ignore
        with pytest.raises(RuntimeError) as exc_info:
            container_fail.rebuild()
        assert "ビルド文脈" in str(exc_info.value)

    def test_resolve_all_and_dynamic_collection_generation(self) -> None:
        # resolve_all()(行60-61) および、GenericAlias検出時の動的コレクション生成パス(行169-183)を完全走破
        mock_registry = MagicMock()
        mock_scope = MagicMock()
        mock_engine = MagicMock()
        mock_builder = MagicMock()

        # コンポーネント定義が未登録(None)な状態を作り、GenericAlias判定へ進める
        mock_registry.lookup.return_value = None
        mock_scope.get.return_value = None

        expected_collection = [DummyServiceImpl()]
        mock_engine.instantiate_dynamic_collection.return_value = expected_collection

        container = RuntimeInstanceContainer(mock_registry, mock_scope, mock_engine, mock_builder)

        # Act: 複数解決のエントリーポイントを駆動
        result = container.resolve_all(DummyServiceInterface)

        # Assert: 動的コレクション生成が連動しているか検証
        assert result == expected_collection
        mock_engine.instantiate_dynamic_collection.assert_called_once()
        mock_scope.put.assert_called_once()

    def test_contains_instance_all_branches(self) -> None:
        # contains_instance のアノテーション失敗(行67)、キャッシュヒット(行71)、レジストリフォールバック(行73)の全パスを走破
        mock_registry = MagicMock()
        mock_scope = MagicMock()
        container = RuntimeInstanceContainer(mock_registry, mock_scope, MagicMock(), MagicMock())

        # 1. アノテーション解析失敗パス
        with patch("container.definitions.resolvable.ResolvableType.from_annotation", return_value=None):
            assert container.contains_instance("invalid_type") is False # type: ignore

        # 2. キャッシュヒットパス
        mock_scope.get.return_value = "cached_object"
        assert container.contains_instance(DummyServiceInterface) is True

        # 3. キャッシュミス ➔ レジストリ検索ヒットパス
        mock_scope.get.return_value = None
        mock_registry.lookup.return_value = MagicMock()
        assert container.contains_instance(DummyServiceInterface) is True

        # 4. 双方ミスパス
        mock_registry.lookup.return_value = None
        assert container.contains_instance(DummyServiceInterface) is False

    def test_is_singleton_missing_component_fallback(self) -> None:
        # is_singleton() のコンポーネント未登録時(None)の False 返却パスを走破 (行77-80)
        mock_registry = MagicMock()
        mock_registry.lookup.return_value = None  # 未登録状態
        container = RuntimeInstanceContainer(mock_registry, MagicMock(), MagicMock(), MagicMock())

        assert container.is_singleton(DummyServiceInterface) is False

    def test_resolve_provider_success_and_failure(self) -> None:
        # resolve_provider() の正常系および、定義未登録時の例外送出パスを走破 (行85-87)
        mock_registry = MagicMock()
        mock_scope = MagicMock()
        container = RuntimeInstanceContainer(mock_registry, mock_scope, MagicMock(), MagicMock())

        # 1. 異常系 (未登録型による例外)
        mock_scope.get.return_value = None
        mock_registry.lookup.return_value = None
        with pytest.raises(ComponentInstantiationError) as exc_info:
            container.resolve_provider(DummyServiceInterface)
        assert "定義が未登録です" in str(exc_info.value)

        # 2. 正常系 (プロバイダとしての遅延評価関数の検証)
        expected_inst = DummyServiceImpl()
        mock_scope.get.return_value = expected_inst
        provider_func = container.resolve_provider(DummyServiceInterface)

        assert callable(provider_func)
        assert provider_func() is expected_inst

    def test_getitem_and_contains_magic_methods_tuple_handling(self) -> None:
        # __getitem__(行109-114) および __contains__(行130-135) のタプル(名前付き解決)分岐を完全走破
        mock_registry = MagicMock()
        mock_scope = MagicMock()
        container = RuntimeInstanceContainer(mock_registry, mock_scope, MagicMock(), MagicMock())

        expected_inst = DummyServiceImpl()
        mock_scope.get.return_value = expected_inst
        mock_registry.lookup.return_value = MagicMock()

        # 1. __getitem__ のタプル型と通常型
        assert container[(DummyServiceInterface, "custom_name")] is expected_inst
        assert container[DummyServiceInterface] is expected_inst

        # 2. __contains__ のタプル型と通常型
        assert (DummyServiceInterface, "custom_name") in container
        assert DummyServiceInterface in container

    def test_get_internal_instance_annotation_error_raises_exception(self) -> None:
        # アノテーションがパース不可能な場合のエラー送出パスを走破 (行142-143)
        container = RuntimeInstanceContainer(MagicMock(), MagicMock(), MagicMock(), MagicMock())

        with patch("container.definitions.resolvable.ResolvableType.from_annotation", return_value=None):
            with pytest.raises(ComponentInstantiationError) as exc_info:
                container.resolve("unparsable_type") # type: ignore
            assert "型アノテーションを解析できません" in str(exc_info.value)

    def test_get_internal_instance_named_to_unnamed_fallback_and_missing_errors(self) -> None:
        # 名前付きから名前なしへの自動再帰フォールバック(行158, 行187) および 未登録・非Optional例外(行195-202)を全走破
        mock_registry = MagicMock()
        mock_scope = MagicMock()
        container = RuntimeInstanceContainer(mock_registry, mock_scope, MagicMock(), MagicMock())

        # 1. 名前付きで検索 ➔ ヒットせず ➔ 名前なし(name=None)で再帰検索してヒットするパス
        mock_scope.get.return_value = None

        # 1回目(名前付き)は定義なし、2回目(名前なし)で定義を返すように制御
        mock_comp = MagicMock()
        mock_registry.lookup.side_effect = [None, mock_comp]

        # エンジンが2回目の再帰解決でオブジェクトを生成した状況を作る
        expected_inst = DummyServiceImpl()
        container._instantiation_engine.resolve_scoped_instance.return_value = expected_inst # type: ignore

        res = container.resolve(DummyServiceInterface, name="temporary_name")
        assert res is expected_inst
        assert mock_registry.lookup.call_count == 2

        # 2. 再帰しても見つからず、Optional型であれば None を返すパス
        mock_registry.lookup.side_effect = None
        mock_registry.lookup.return_value = None  # どこを探しても定義なし

        with patch("container.definitions.resolvable.ResolvableType.from_annotation") as mock_from:
            mock_res_opt = MagicMock()
            mock_res_opt.raw_type = DummyServiceInterface
            mock_res_opt.is_optional = True  # Optional型として偽装
            mock_from.return_value = mock_res_opt

            assert container.resolve(DummyServiceInterface) is None

        # 3. 再帰しても見つからず、非Optional型であれば ComponentInstantiationError を投げるパス
        with patch("container.definitions.resolvable.ResolvableType.from_annotation") as mock_from:
            mock_res_req = MagicMock()
            mock_res_req.raw_type = DummyServiceInterface
            mock_res_req.is_optional = False  # 必須型として偽装
            mock_from.return_value = mock_res_req

            with pytest.raises(ComponentInstantiationError) as exc_info:
                container.resolve(DummyServiceInterface)
            assert "未登録型" in str(exc_info.value)

    def test_register_resource_and_close_lifecycle(self) -> None:
        # _register_resource 内部の Closable 判定および、close() による一括解放ライフサイクルを検証
        mock_scope = MagicMock()
        container = RuntimeInstanceContainer(MagicMock(), mock_scope, MagicMock(), MagicMock())

        closable_asset = StubClosable()
        non_closable_asset = DummyServiceImpl()  # クローズメソッドはあるがClosable抽象ではない

        # Act: アセット登録の試行
        container._register_resource(closable_asset)
        container._register_resource(non_closable_asset)

        assert closable_asset.closed is False

        # コンテナ全体の破棄を実行
        container.close()

        # Assert: Closable を実装したオブジェクトのみが安全かつ確実に解放されているか検証
        assert closable_asset.closed is True
        mock_scope.clear.assert_called_once()
