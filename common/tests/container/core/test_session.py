from __future__ import annotations

import types
import typing
from typing import Any
from unittest.mock import MagicMock, patch
import pytest

from container.common.exceptions import CircularDependencyError
from container.definitions.resolvable import ResolvableType

# ==============================================================================
# 構造吸収レイヤー (単一ファイル型・複数ファイル分割型の双方の配置規約を自動解決)
# ==============================================================================
try:
    from container.core.session import ResolutionSession as _SessionImpl
except ImportError:
    import container.core.session as _SessionImpl  # type: ignore


def _extract_true_class(symbol: Any, class_name: str) -> Any:
    """インポートされたシンボルがモジュール(ファイル)である場合、内部の真の具象クラスを動的に抽出する。"""
    if isinstance(symbol, types.ModuleType):
        return getattr(symbol, class_name)
    return symbol


ResolutionSession = _extract_true_class(_SessionImpl, "ResolutionSession")


# ==============================================================================
# テスト資産(シンプルな仕様インターフェーススタブ)
# ==============================================================================
class MockSpecInterface:
    pass


class MockPluginImpl(MockSpecInterface):
    pass


# ==============================================================================
# テストクラス定義
# ==============================================================================
class TestResolutionSessionLifecycle:
    def test_session_properties_and_initial_state(self) -> None:
        # Arrange: 必須引数群を適正な型で準備
        mock_container = MagicMock()
        test_stack: set[Any] = set()

        # 位置専用引数規約 (container, stack, requested_plugin_name=None, /) を厳密に遵守
        session = ResolutionSession(mock_container, test_stack, "named_plugin")

        # Act & Assert: プロパティを介して初期データが正確に返却されるか検証
        assert session.requested_plugin_name == "named_plugin"
        assert session.stack is test_stack

    def test_resolve_dependency_instance_queries_container_internal(self) -> None:
        # Arrange: コンテナの内部メソッド解決パスを構成
        mock_container = MagicMock()
        expected_instance = MockPluginImpl()
        mock_container._get_internal_instance.return_value = expected_instance

        test_stack: set[Any] = set()
        session = ResolutionSession(mock_container, test_stack)

        resolvable = ResolvableType.from_annotation(MockSpecInterface)
        assert resolvable is not None

        # Act: 依存インスタンスの解決をトリガー
        result = session.resolve_dependency_instance(resolvable, name="target_plugin")

        # Assert: コンテナ側のファストパスへ引数が正確にフォワードされているか検証
        assert result is expected_instance
        mock_container._get_internal_instance.assert_called_once_with(
            MockSpecInterface,
            test_stack,
            name="target_plugin",
        )

    def test_resolve_plugin_stream_delegates_to_instantiation_engine(self) -> None:
        # Arrange: 実際のソース規約( _container._instantiation_engine )に完全準拠したモックの配置
        mock_container = MagicMock()
        mock_definition = MagicMock()

        # ターゲットエンジンへのピンポイントインジェクション
        mock_container._instantiation_engine.resolve_plugin_stream.return_value = [mock_definition]

        test_stack: set[Any] = set()
        session = ResolutionSession(mock_container, test_stack)

        # Act: プラグインストリーム解決を執行
        stream = session.resolve_plugin_stream(MockSpecInterface)

        # Assert: 削り落とされることなく、定義データが正確に1件流出することを確認
        assert len(stream) == 1
        assert stream[0] is mock_definition
        mock_container._instantiation_engine.resolve_plugin_stream.assert_called_once_with(MockSpecInterface)

    def test_apply_lifecycle_pipeline_delegates_to_engine(self) -> None:
        # Arrange: パイプライン適用のモック化
        mock_container = MagicMock()
        raw_instance = MockPluginImpl()
        processed_instance = MockPluginImpl()
        mock_container._instantiation_engine.apply_pipeline.return_value = processed_instance

        mock_component_id = MagicMock()
        session = ResolutionSession(mock_container, set())

        # Act
        result = session.apply_lifecycle_pipeline(raw_instance, mock_component_id)

        # Assert
        assert result is processed_instance
        mock_container._instantiation_engine.apply_pipeline.assert_called_once_with(raw_instance, mock_component_id)

    def test_register_resource_forwards_to_container(self) -> None:
        # Arrange
        mock_container = MagicMock()
        asset = MockPluginImpl()
        session = ResolutionSession(mock_container, set())

        # Act
        session.register_resource(asset)

        # Assert
        mock_container._register_resource.assert_called_once_with(asset)

    def test_put_cached_instance_forwards_to_scope(self) -> None:
        # Arrange
        mock_container = MagicMock()
        mock_key = MagicMock()
        instance = MockPluginImpl()
        session = ResolutionSession(mock_container, set())

        # Act
        session.put_cached_instance(mock_key, instance)

        # Assert
        mock_container._scope.put.assert_called_once_with(mock_key, instance)

    def test_execute_with_lock_raises_circular_dependency_error(self) -> None:
        # Arrange: すでにスタックに同一のキーが登録されている(循環発生)状態を作る
        mock_container = MagicMock()
        mock_key = MagicMock()
        mock_key.target_type = "MockCircularType"

        active_stack = {mock_key}
        session = ResolutionSession(mock_container, active_stack)

        # Act & Assert: 例外が正しく送出されるか検証
        with pytest.raises(CircularDependencyError) as exc_info:
            session.execute_with_lock(mock_key, lambda: MockPluginImpl())

        assert "循環依存が検出されました" in str(exc_info.value)

    def test_execute_with_lock_returns_cached_instantly(self) -> None:
        # Arrange: キャッシュにすでに存在するハッピーパス
        mock_container = MagicMock()
        mock_key = MagicMock()
        cached_obj = MockPluginImpl()
        mock_container._scope.get.return_value = cached_obj

        session = ResolutionSession(mock_container, set())
        callback = MagicMock()

        # Act
        result = session.execute_with_lock(mock_key, callback)

        # Assert: コールバックを起動せず即座にキャッシュを返却することを確認
        assert result is cached_obj
        callback.assert_not_called()

    def test_execute_with_lock_applies_double_check_locking(self) -> None:
        # Arrange: キャッシュがなく、新しくファクトリ関数でオブジェクトを生成するフルライフサイクル
        mock_container = MagicMock()
        mock_key = MagicMock()
        new_obj = MockPluginImpl()

        # 1回目のキャッシュ確認、2回目の確認(ダブルチェック)ともに None を返す
        mock_container._scope.get.return_value = None

        # スレッド安全な synchronize コンテキストマネージャのモック化
        mock_container._scope.synchronize.return_value.__enter__ = MagicMock()
        mock_container._scope.synchronize.return_value.__exit__ = MagicMock()

        test_stack: set[Any] = set()
        session = ResolutionSession(mock_container, test_stack)
        callback = MagicMock(return_value=new_obj)

        # Act
        result = session.execute_with_lock(mock_key, callback)

        # Assert: 生成が成功し、かつ事後スタブのクリーンアップが行われていることを検証
        assert result is new_obj
        callback.assert_called_once()
        assert mock_key not in test_stack  # finallyブロックでのスタック削除検証
