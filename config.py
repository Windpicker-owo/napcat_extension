"""napcat_extension 插件配置。

配置文件默认路径：config/plugins/napcat_extension/config.toml

说明：
- 本插件依赖 `napcat_adapter` 适配器。
- 所有“管理类动作”默认关闭，需显式在配置中开启。
"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


class NapcatExtensionConfig(BaseConfig):
    """napcat_extension 插件配置。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "NapCat 扩展操作插件配置"

    @config_section("plugin")
    class PluginSection(SectionBase):
        """插件总体配置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用 napcat_extension 插件（关闭则所有 Action 不激活）",
        )
        config_version: str = Field(
            default="1.0.0",
            description="配置版本号",
        )

    @config_section("features")
    class FeaturesSection(SectionBase):
        """功能开关（默认全部关闭）。"""

        enable_mute: bool = Field(default=False, description="是否启用：群成员禁言")
        enable_unmute: bool = Field(default=False, description="是否启用：群成员解除禁言")
        enable_react: bool = Field(default=False, description="是否启用：对指定消息添加表情回应")
        enable_poke: bool = Field(default=False, description="是否启用：戳一戳群成员")
        enable_recall: bool = Field(default=False, description="是否启用：撤回指定消息")
        enable_group_sign: bool = Field(default=False, description="是否启用：群打卡")
        enable_kick: bool = Field(default=False, description="是否启用：踢出群成员")
        enable_send_file: bool = Field(default=False, description="是否启用：发送本地文件")
        enable_save_message_file: bool = Field(default=False, description="是否启用：保存指定消息中的文件")

    plugin: PluginSection = Field(default_factory=PluginSection)
    features: FeaturesSection = Field(default_factory=FeaturesSection)


__all__ = ["NapcatExtensionConfig"]
