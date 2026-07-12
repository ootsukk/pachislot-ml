from __future__ import annotations

from container.common.exceptions import (
    CircularDependencyError,
    ComponentInstantiationError,
    ContainerError,
)
from container.common.interfaces import (
    Initializable,
    InstancePostProcessor,
    RuntimeContainer,
)
from container.core.builder import InstanceResolverBuilder
from container.core.facade import ContainerFacade
from container.definitions.component import (
    CollectionComponent,
    Component,
    InstanceComponent,
    PluginComponent,
    PropertyComponent,
)
from container.definitions.decorator import (
    dependency_module,
    plugin,
    plugin_impl,
)

__all__: list[str] = [
    "CircularDependencyError",
    "CollectionComponent",
    "Component",
    "ComponentInstantiationError",
    "ContainerError",
    "ContainerFacade",
    "Initializable",
    "InstanceComponent",
    "InstancePostProcessor",
    "InstanceResolverBuilder",
    "PluginComponent",
    "PropertyComponent",
    "RuntimeContainer",
    "dependency_module",
    "plugin",
    "plugin_impl",
]
