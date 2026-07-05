from __future__ import annotations

import typing

if typing.TYPE_CHECKING:
    from container.definitions.descriptor import PluginDescriptor
    from container.definitions.registry import PluginDefinition


class PluginEligibilityValidator:
    """プラグイン実装クラスの装飾規約および環境適合性を専門に検証するバリデーター。"""

    def validate(self, definition: PluginDefinition[object], setting: PluginDescriptor, /) -> bool:
        impl_class = definition.impl_class
        plugin_name = setting.plugin_name

        if not hasattr(impl_class, "__plugin_impl_meta__"):
            raise TypeError(f"具象実装クラス '{impl_class.__name__}' に必要なアノテーションメタデータが存在しません。")

        dep_meta = getattr(impl_class, "__dependency_meta__", None)
        is_satisfied = bool(dep_meta.check_satisfied()) if dep_meta else True

        if plugin_name and plugin_name != "auto":
            if not is_satisfied:
                module_name = getattr(dep_meta, "module_name", "Unknown") if dep_meta else "Unknown"
                version = getattr(dep_meta, "version", "Unknown") if dep_meta else "Unknown"
                raise LookupError(
                    f"指定されたプラグイン '{plugin_name}' の実行に必要な外部モジュール '{module_name} ({version})' がインストールされていません。"
                )
            return True

        return is_satisfied
