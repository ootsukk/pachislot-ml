from __future__ import annotations

from enum import StrEnum
from typing import Final

YAML_KEY_ENABLED: Final[str] = "enabled"
YAML_KEY_PLUGIN_NAME: Final[str] = "plugin_name"
YAML_VAL_AUTO: Final[str] = "auto"
YAML_SUFFIX_OPTIONS: Final[str] = "_options"

ENTRY_POINT_SUFFIX: Final[str] = "components"


class ComponentScope(StrEnum):
    """コンポーネントのライフサイクル生存期間を管理する文字列列挙型。"""

    SINGLETON = "singleton"
    TRANSIENT = "transient"
