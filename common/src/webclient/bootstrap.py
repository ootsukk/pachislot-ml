from __future__ import annotations

from typing import Any
from container import PluginRegistry, UniversalPluginResolver, TypedAssetContext
from webclient.config import WebClientConfig
from webclient.components import create_webclient_components
from webclient.constants import ROOT_PACKAGE_NAME


def bootstrap_webclient(config: WebClientConfig, provided_instances: dict[type, Any]) -> TypedAssetContext:
    components = create_webclient_components()

    registry = PluginRegistry.scan(
        root_package_name=ROOT_PACKAGE_NAME,
        plugin_groups=list(config.plugin_groups),
        components=components,
        ignored_types=(WebClientConfig,),
    )

    resolver = UniversalPluginResolver(
        config=config, registry=registry, components=components, provided_instances=provided_instances
    )
    context = resolver.resolve_all()

    TypedAssetContext.register_global(context)

    return context
