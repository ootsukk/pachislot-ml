from __future__ import annotations

# 物理的には core/ サブパッケージに隠蔽されたビルダーをインポート
from container.core.builder import InstanceResolverBuilder
from container.definitions.component import (
    Component,
    InstanceComponent,
    PluginComponent,
    CollectionComponent,
    InstanceComponent,
)
from container.common.interfaces import RuntimeContainer, InstancePostProcessor, Initializable

# 公開APIのみを厳格に管理
__all__ = [
    "RuntimeContainer",
    "InstanceResolverBuilder",
    "InstancePostProcessor",
    "Component",
    "Initializable",
    "InstanceComponent",
    "PluginComponent",
    "CollectionComponent",
    "InstanceComponent",
]
