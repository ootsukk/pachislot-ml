from __future__ import annotations

import typing
from abc import ABC, abstractmethod
from collections.abc import Sequence

if typing.TYPE_CHECKING:
    from container.definitions.descriptor import PluginDescriptor
    from container.definitions.registry import PluginDefinition


class PluginValidationRule(ABC):
    """具象プラグインの適用検証および規約評価を規定する抽象基底インターフェース """

    def supports(
        self,
        definition: PluginDefinition[object],
        descriptor: PluginDescriptor,
        /,
    ) -> bool:
        """対象のプラグイン定義および記述子が、本ルールの評価対象であるか判定します。

        デフォルトではすべてのプラグインを対象(True)とします。サブクラスで必要に応じてオーバーライドします。
        """
        return True

    @abstractmethod
    def evaluate(
        self,
        definition: PluginDefinition[object],
        descriptor: PluginDescriptor,
        /,
    ) -> bool:
        pass


class AnnotationMetadataValidationRule(PluginValidationRule):
    """実装クラスの装飾規約(アノテーションメタデータ)の存在を検証する具象ルール。"""

    def evaluate(
        self,
        definition: PluginDefinition[object],
        descriptor: PluginDescriptor,
        /,
    ) -> bool:
        impl_class = definition.impl_class
        if not hasattr(impl_class, PluginDefinition.META_ATTR_CONTAINER):
            raise TypeError(f"具象実装クラス '{impl_class.__name__}' に必要なアノテーションメタデータが存在しません。")
        return True


class DependencyCompatibilityValidationRule(PluginValidationRule):

    def supports(
        self,
        definition: PluginDefinition[object],
        descriptor: PluginDescriptor,
        /,
    ) -> bool:
        return hasattr(definition.impl_class, "__dependency_meta__")

    def evaluate(
        self,
        definition: PluginDefinition[object],
        descriptor: PluginDescriptor,
        /,
    ) -> bool:
        impl_class = definition.impl_class
        plugin_name = descriptor.plugin_name
        dep_meta = impl_class.__dependency_meta__ # type: ignore

        is_satisfied = bool(dep_meta.check_satisfied())

        if plugin_name and plugin_name != "auto":
            if not is_satisfied:
                module_name = getattr(dep_meta, "module_name", "Unknown")
                version = getattr(dep_meta, "version", "Unknown")
                raise LookupError(
                    f"指定されたプラグイン '{plugin_name}' の実行に必要な外部モジュール "
                    f"'{module_name} ({version})' がインストールされていません。"
                )
            return True

        return is_satisfied


class PluginEligibilityValidator:

    def __init__(
        self,
        rules: Sequence[PluginValidationRule] | None = None,
        /,
    ) -> None:
        self._rules: typing.Final[Sequence[PluginValidationRule]] = (
            tuple(rules)
            if rules is not None
            else (
                AnnotationMetadataValidationRule(),
                DependencyCompatibilityValidationRule(),
            )
        )

    def validate(
        self,
        definition: PluginDefinition[object],
        setting: PluginDescriptor,
        /,
    ) -> bool:
        for rule in self._rules:
            if rule.supports(definition, setting) and not rule.evaluate(definition, setting):
                return False
        return True
