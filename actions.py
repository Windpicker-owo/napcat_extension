"""napcat_extension Actions。

每个 Action 都通过 `napcat_adapter` 的 `send_napcat_api(action, params)` 调用 NapCat API。
并通过 go_activate() 读取配置开关决定是否向 LLM 暴露。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from src.app.plugin_system.api import adapter_api, send_api
from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base.action import BaseAction
from src.core.components.types import ChatType

logger = get_logger("napcat_extension")

_NAPCAT_ADAPTER_SIGNATURE = "napcat_adapter:adapter:napcat_adapter"

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.models.stream import ChatStream


def _coerce_int_if_digit(value: Any) -> Any:
    """将纯数字字符串转换为 int，其他保持原样。"""

    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit():
            try:
                return int(s)
            except Exception:
                return value
    return value


def _get_group_id_from_context(action: BaseAction) -> Any:
    """从当前上下文消息中提取 group_id。"""

    context = action.chat_stream.context

    # 优先使用 BaseAction 的目标消息选择逻辑（会回落到最后一条上下文消息）
    msg = None
    try:
        msg = action._get_context_message_for_target()  # type: ignore[attr-defined]
    except Exception:
        msg = None

    if msg is not None:
        group_id = msg.extra.get("group_id") or msg.extra.get("target_group_id")
        if group_id is not None:
            return group_id

    # 兜底：在上下文候选消息中回溯查找（避免 current_message 为空时误判）
    candidates = []
    candidates.extend(context.unread_messages)
    candidates.extend(context.history_messages)
    candidates.extend(list(context.message_cache))
    candidates.append(context.current_message)

    for m in reversed([c for c in candidates if c is not None]):
        group_id = m.extra.get("group_id") or m.extra.get("target_group_id")
        if group_id is not None:
            return group_id

    return None


def _format_napcat_failure(action: str, resp: dict[str, Any]) -> str:
    """将 NapCat 响应格式化为更易懂的失败文本。"""

    status = str(resp.get("status") or "").strip().lower()
    retcode = resp.get("retcode")
    message = str(resp.get("message") or "").strip()
    wording = str(resp.get("wording") or "").strip()
    detail = wording or message

    if not detail:
        detail = f"retcode={retcode}" if retcode is not None else "未知错误"

    # 常见权限/失败原因提炼
    lowered = detail.lower()
    if "权限" in detail or "permission" in lowered or "not admin" in lowered:
        return (
            f"{action} 失败：权限不足。\n"
            "- 需要机器人为群主/管理员\n"
            "- 目标用户权限必须低于机器人\n"
            f"- 原始信息：{detail}"
        )

    if "不存在" in detail or "not found" in lowered:
        return (
            f"{action} 失败：目标不存在或已失效。\n"
            f"- 原始信息：{detail}"
        )

    if "超时" in detail or "timeout" in lowered:
        return (
            f"{action} 失败：请求超时。\n"
            "- 请检查 napcat_adapter 是否已连接 NapCat\n"
            "- 请检查 NapCat 服务是否正常\n"
            f"- 原始信息：{detail}"
        )

    return f"{action} 失败：{detail}"


async def _call_napcat_api(
    *,
    action_name: str,
    params: dict[str, Any],
    timeout: float = 30.0,
) -> tuple[bool, str]:
    """调用 napcat_adapter API 并统一解析响应。"""

    adapter = adapter_api.get_adapter(_NAPCAT_ADAPTER_SIGNATURE)
    if adapter is None:
        return False, "napcat_adapter 未启动：请先启用并启动 napcat_adapter 插件。"

    if not hasattr(adapter, "send_napcat_api"):
        return False, "napcat_adapter 不支持 send_napcat_api：请确认 napcat_adapter 版本兼容。"

    try:
        resp = await adapter.send_napcat_api(action_name, params, timeout=timeout)  # type: ignore[attr-defined]
    except Exception as exc:
        return (
            False,
            f"调用 NapCat API 异常：{exc}\n- action={action_name}\n- params={params}",
        )

    status = str(resp.get("status") or "").strip().lower()
    retcode = resp.get("retcode")
    if status == "ok" and (retcode == 0 or retcode is None):
        return True, "ok"

    return False, _format_napcat_failure(action_name, resp)


class _NapcatBaseAction(BaseAction):
    """napcat_extension Action 基类：提供通用激活判断。"""

    associated_platforms: list[str] = ["qq"]

    async def go_activate(self) -> bool:  # noqa: D401
        """根据插件配置判定是否激活。"""

        config = getattr(self.plugin, "config", None)
        if config is None:
            return False

        plugin_section = getattr(config, "plugin", None)
        if plugin_section is None or not bool(getattr(plugin_section, "enabled", True)):
            return False

        return await self._feature_enabled(config)

    async def _feature_enabled(self, config: Any) -> bool:
        raise NotImplementedError


class MuteGroupMemberAction(_NapcatBaseAction):
    """群成员禁言。"""

    action_name: str = "mute_group_member"
    action_description: str = (
        "在当前群聊中对指定用户执行禁言（需要机器人为管理员/群主，且目标权限低于机器人）。"
    )
    chat_type: ChatType = ChatType.GROUP

    async def _feature_enabled(self, config: Any) -> bool:
        return bool(getattr(getattr(config, "features", None), "enable_mute", False))

    async def execute(
        self,
        user_id: Annotated[str, "要禁言的目标 QQ 号"],
        duration_seconds: Annotated[int, "禁言时长（秒），例如 600 表示 10 分钟"] = 600,
    ) -> tuple[bool, str]:
        group_id = _get_group_id_from_context(self)
        if not group_id:
            return False, "该动作只能在群聊上下文使用：未获取到 group_id。"

        if duration_seconds <= 0:
            return False, "duration_seconds 必须为正整数（单位：秒）。"

        params = {
            "group_id": _coerce_int_if_digit(group_id),
            "user_id": _coerce_int_if_digit(user_id),
            "duration": int(duration_seconds),
        }

        ok, msg = await _call_napcat_api(action_name="set_group_ban", params=params)
        if ok:
            return True, f"已禁言用户 {user_id}（{duration_seconds} 秒）。"
        return False, msg


class UnmuteGroupMemberAction(_NapcatBaseAction):
    """群成员解除禁言。"""

    action_name: str = "unmute_group_member"
    action_description: str = "在当前群聊中解除指定用户的禁言（duration=0）。"
    chat_type: ChatType = ChatType.GROUP

    async def _feature_enabled(self, config: Any) -> bool:
        return bool(getattr(getattr(config, "features", None), "enable_unmute", False))

    async def execute(
        self,
        user_id: Annotated[str, "要解除禁言的目标 QQ 号"],
    ) -> tuple[bool, str]:
        group_id = _get_group_id_from_context(self)
        if not group_id:
            return False, "该动作只能在群聊上下文使用：未获取到 group_id。"

        params = {
            "group_id": _coerce_int_if_digit(group_id),
            "user_id": _coerce_int_if_digit(user_id),
            "duration": 0,
        }

        ok, msg = await _call_napcat_api(action_name="set_group_ban", params=params)
        if ok:
            return True, f"已解除用户 {user_id} 的禁言。"
        return False, msg


class ReactToMessageAction(_NapcatBaseAction):
    """对指定消息添加表情回应。"""

    action_name: str = "react_to_message"
    action_description: str = (
        "对指定消息添加一个表情回应（emoji_id），常用于群聊互动。"
    )
    chat_type: ChatType = ChatType.GROUP

    async def _feature_enabled(self, config: Any) -> bool:
        return bool(getattr(getattr(config, "features", None), "enable_react", False))

    async def execute(
        self,
        message_id: Annotated[str, "要回应的消息 ID（NapCat message_id）"],
        emoji_id: Annotated[str, "表情 ID（NapCat emoji_id）"],
    ) -> tuple[bool, str]:
        params = {
            "message_id": _coerce_int_if_digit(message_id),
            "emoji_id": str(emoji_id).strip(),
            "set": True,
        }

        ok, msg = await _call_napcat_api(action_name="set_msg_emoji_like", params=params)
        if ok:
            return True, f"已对消息 {message_id} 添加表情回应（emoji_id={emoji_id}）。"
        return False, msg


class PokeGroupMemberAction(_NapcatBaseAction):
    """戳一戳群成员。"""

    action_name: str = "poke_group_member"
    action_description: str = "在当前群聊中戳一戳指定用户。"
    chat_type: ChatType = ChatType.GROUP

    async def _feature_enabled(self, config: Any) -> bool:
        return bool(getattr(getattr(config, "features", None), "enable_poke", False))

    async def execute(
        self,
        user_id: Annotated[str, "要戳一戳的目标 QQ 号"],
    ) -> tuple[bool, str]:
        group_id = _get_group_id_from_context(self)
        if not group_id:
            return False, "该动作只能在群聊上下文使用：未获取到 group_id。"

        params = {
            "group_id": _coerce_int_if_digit(group_id),
            "user_id": _coerce_int_if_digit(user_id),
        }

        ok, msg = await _call_napcat_api(action_name="send_poke", params=params)
        if ok:
            return True, f"已戳一戳用户 {user_id}。"
        return False, msg


class RecallMessageAction(_NapcatBaseAction):
    """撤回消息。"""

    action_name: str = "recall_message"
    action_description: str = (
        "撤回指定消息（需要机器人具备撤回权限；不同场景可能受时效限制）。"
    )
    chat_type: ChatType = ChatType.ALL

    async def _feature_enabled(self, config: Any) -> bool:
        return bool(getattr(getattr(config, "features", None), "enable_recall", False))

    async def execute(
        self,
        message_id: Annotated[str, "要撤回的消息 ID（NapCat message_id）"],
    ) -> tuple[bool, str]:
        params = {
            "message_id": _coerce_int_if_digit(message_id),
        }

        ok, msg = await _call_napcat_api(action_name="delete_msg", params=params)
        if ok:
            return True, f"已撤回消息 {message_id}。"
        return False, msg


class GroupSignAction(_NapcatBaseAction):
    """群打卡。"""

    action_name: str = "group_sign"
    action_description: str = "在当前群聊中执行群打卡。"
    chat_type: ChatType = ChatType.GROUP

    async def _feature_enabled(self, config: Any) -> bool:
        return bool(getattr(getattr(config, "features", None), "enable_group_sign", False))

    async def execute(self) -> tuple[bool, str]:
        group_id = _get_group_id_from_context(self)
        if not group_id:
            return False, "该动作只能在群聊上下文使用：未获取到 group_id。"

        params = {"group_id": _coerce_int_if_digit(group_id)}

        ok, msg = await _call_napcat_api(action_name="send_group_sign", params=params)
        if ok:
            return True, "已执行群打卡。"

        # 文档中也存在 /set_group_sign；失败时给出提示但不额外重试（避免误触）。
        hint = "\n提示：如果 NapCat 版本不兼容，可尝试改用 /set_group_sign。"
        return False, msg + hint


class KickGroupMemberAction(_NapcatBaseAction):
    """踢出群成员。"""

    action_name: str = "kick_group_member"
    action_description: str = (
        "在当前群聊中踢出指定用户（需要机器人为管理员/群主，且目标权限低于机器人）。"
    )
    chat_type: ChatType = ChatType.GROUP

    async def _feature_enabled(self, config: Any) -> bool:
        return bool(getattr(getattr(config, "features", None), "enable_kick", False))

    async def execute(
        self,
        user_id: Annotated[str, "要踢出的目标 QQ 号"],
        reject_add_request: Annotated[bool, "是否拒绝此人再次加群（true=拒绝，false=允许）"] = False,
    ) -> tuple[bool, str]:
        group_id = _get_group_id_from_context(self)
        if not group_id:
            return False, "该动作只能在群聊上下文使用：未获取到 group_id。"

        params = {
            "group_id": _coerce_int_if_digit(group_id),
            "user_id": _coerce_int_if_digit(user_id),
            "reject_add_request": bool(reject_add_request),
        }

        ok, msg = await _call_napcat_api(action_name="set_group_kick", params=params)
        if ok:
            suffix = "（已拒绝再次加群）" if reject_add_request else ""
            return True, f"已踢出用户 {user_id}{suffix}。"
        return False, msg


class SendFileAction(_NapcatBaseAction):
    """发送文件。"""

    action_name: str = "send_file"
    action_description: str = "向当前会话发送一个本地文件，参数必须是文件绝对路径。"
    chat_type: ChatType = ChatType.ALL

    async def _feature_enabled(self, config: Any) -> bool:
        return bool(getattr(getattr(config, "features", None), "enable_send_file", False))

    async def execute(
        self,
        file_path: Annotated[str, "要发送的文件绝对路径，例如 C:/data/report.pdf"],
    ) -> tuple[bool, str]:
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            return False, "file_path 必须是文件绝对路径。"

        if not path.exists():
            return False, f"文件不存在：{path}"

        if not path.is_file():
            return False, f"目标不是文件：{path}"

        success = await send_api.send_file(
            file_path=str(path),
            stream_id=self.chat_stream.stream_id,
            platform=self.chat_stream.platform,
            file_name=path.name,
        )
        if success:
            return True, f"已发送文件：{path.name}"
        return False, f"发送文件失败：{path.name}"


__all__ = [
    "MuteGroupMemberAction",
    "UnmuteGroupMemberAction",
    "ReactToMessageAction",
    "PokeGroupMemberAction",
    "RecallMessageAction",
    "GroupSignAction",
    "KickGroupMemberAction",
    "SendFileAction",
]
