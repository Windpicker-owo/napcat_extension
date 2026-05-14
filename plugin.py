"""napcat_extension 插件入口。

注册 7 个 NapCat 扩展 Action，并通过 go_activate() 按配置开关控制是否暴露给 LLM。
"""

from __future__ import annotations

from typing import cast

from src.core.components import BasePlugin, register_plugin

from .actions import (
    GroupSignAction,
    KickGroupMemberAction,
    MuteGroupMemberAction,
    PokeGroupMemberAction,
    ReactToMessageAction,
    RecallMessageAction,
    SendFileAction,
    UnmuteGroupMemberAction,
)
from .config import NapcatExtensionConfig
from .tools import SaveMessageFileTool


@register_plugin
class NapcatExtensionPlugin(BasePlugin):
    """napcat_extension 插件。"""

    plugin_name: str = "napcat_extension"
    plugin_description: str = "NapCat 扩展操作插件（通过 napcat_adapter 调用 NapCat API）"
    plugin_version: str = "1.0.0"

    configs: list[type] = [NapcatExtensionConfig]
    dependent_components: list[str] = ["napcat_adapter:adapter:napcat_adapter"]

    def get_components(self) -> list[type]:
        """返回本插件提供的组件类。"""

        components: list[type] = [
            MuteGroupMemberAction,
            UnmuteGroupMemberAction,
            ReactToMessageAction,
            PokeGroupMemberAction,
            RecallMessageAction,
            GroupSignAction,
            KickGroupMemberAction,
            SendFileAction,
        ]
        config = cast(NapcatExtensionConfig | None, self.config)
        if config and config.features.enable_save_message_file:
            components.append(SaveMessageFileTool)
        return components
