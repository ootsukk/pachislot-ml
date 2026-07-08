from __future__ import annotations

import typing
from collections.abc import Sequence
from typing import Final

from container.common.exceptions import CircularDependencyError
from container.common.metadata import CacheKey, ComponentId
from container.definitions.resolvable import ResolvableType

if typing.TYPE_CHECKING:
    from container.core.container import RuntimeInstanceContainer
    from container.definitions.registry import PluginDefinition


class ResolutionSession:
    """単一の依存関係解決要求のライフサイクル状態を保持し、ファクトリ層への文脈仲介とライフサイクル適用を統括するコンテキストクラス。"""

    def __init__(
        self,
        container: RuntimeInstanceContainer,
        stack: set[CacheKey],
        requested_plugin_name: str | None = None,
        /,
    ) -> None:
        self._container: Final[RuntimeInstanceContainer] = container
        self._stack: Final[set[CacheKey]] = stack
        self._requested_plugin_name: Final[str | None] = requested_plugin_name

    @property
    def requested_plugin_name(self) -> str | None:
        """解決要求時に明示的に指定されたプラグインの指名名称を返却します。"""
        return self._requested_plugin_name

    @property
    def stack(self) -> set[CacheKey]:
        """現在追跡中の依存解決スタックのセットを返却します。"""
        return self._stack

    def resolve_dependency_instance(
        self,
        resolvable: ResolvableType[object],
        /,
        *,
        name: str | None = None,
    ) -> object | None:
        """型メタ操作オブジェクトに基づき、コンテナから対応する依存インスタンスを安全に解決します。"""
        return self._container._get_internal_instance(
            resolvable.raw_type,
            self._stack,
            name=name,
        )

    def resolve_plugin_stream(
        self,
        spec_type: type[object],
        /,
    ) -> Sequence[PluginDefinition[object]]:
        """指定された仕様インターフェースに適合する、事前ソート済みのプラグイン定義ストリームをレジストリから抽出します。"""
        return self._container._instantiation_engine.resolve_plugin_stream(spec_type)

    def apply_lifecycle_pipeline(
        self,
        instance: object,
        component_id: ComponentId,
        /,
    ) -> object:
        """実体化されたオブジェクトに対し、コンテナに登録されたコンポーネント初期化パイプラインを適用します。"""
        return self._container._instantiation_engine.apply_pipeline(instance, component_id)

    def register_resource(self, instance: object, /) -> None:
        """実体化の過程で生成されたクローズ可能アセット（接続等）を、コンテナの終了スタックへ登録します。"""
        self._container._register_resource(instance)

    def put_cached_instance(self, key: CacheKey, instance: object, /) -> None:
        """スコープ戦略に従い、生成されたインスタンスをコンテナのキャッシュストレージへ格納します。"""
        self._container._scope.put(key, instance)

    def execute_with_lock(
        self,
        key: CacheKey,
        factory_callback: typing.Callable[[], object],
        /,
    ) -> object:
        """依存関係の解決処理をロック制御下で安全に実行し、同時にスレッド安全なダブルチェックロッキングと循環依存の検証を執行します。"""
        if key in self._stack:
            raise CircularDependencyError(f"循環依存が検出されました。検出型: {key.target_type}")

        if (cached := self._container._scope.get(key)) is not None:
            return cached

        self._stack.add(key)
        try:
            with self._container._scope.synchronize(key):
                if (cached_double := self._container._scope.get(key)) is not None:
                    return cached_double
                return factory_callback()
        finally:
            self._stack.remove(key)
