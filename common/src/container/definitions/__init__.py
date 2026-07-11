from __future__ import annotations

from container.definitions.component import (
    CollectionComponent,
    Component,
    ComponentRegistry,
    InstanceComponent,
    PluginComponent,
    PropertyComponent,
)
from container.definitions.decorator import (
    DependencyModuleMeta,
    MetadataAccessor,
    PluginImplMeta,
    PluginMeta,
    VersionConstraint,
    dependency_module,
    plugin,
    plugin_impl,
)
from container.definitions.descriptor import PluginDescriptor
from container.definitions.naming import (
    ChainNamingStrategy,
    ClassNameAutomaticKeyStrategy,
    FixedKeyStrategy,
    NamingStrategy,
    PluginNameKeyStrategy,
    naming_chain,
)
from container.definitions.registry import PluginDefinition, PluginRegistry
from container.definitions.resolvable import ResolvableType

__all__: list[str] = [
    "ChainNamingStrategy",
    "ClassNameAutomaticKeyStrategy",
    "CollectionComponent",
    "Component",
    "ComponentRegistry",
    "DependencyModuleMeta",
    "FixedKeyStrategy",
    "InstanceComponent",
    "MetadataAccessor",
    "NamingStrategy",
    "PluginComponent",
    "PluginDefinition",
    "PluginDescriptor",
    "PluginImplMeta",
    "PluginMeta",
    "PluginNameKeyStrategy",
    "PluginRegistry",
    "PropertyComponent",
    "ResolvableType",
    "VersionConstraint",
    "dependency_module",
    "naming_chain",
    "plugin",
    "plugin_impl",
]
