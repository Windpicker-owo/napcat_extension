"""napcat_extension Tools。

提供与 NapCat 文件消息相关的查询/保存能力。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import urlparse

import aiohttp

from src.app.plugin_system.api import adapter_api
from src.app.plugin_system.api.log_api import get_logger
from src.core.components import BaseTool

logger = get_logger("napcat_extension.tool")

_NAPCAT_ADAPTER_SIGNATURE = "napcat_adapter:adapter:napcat_adapter"
_DEFAULT_DOWNLOAD_DIR = Path(__file__).resolve().parents[2] / "data" / "napcat_download"


def _resolve_message_id(adapter: Any, message_id: str) -> int | str | None:
    """解析 MoFox/NapCat 消息 ID。"""

    resolved = message_id.strip()
    if not resolved:
        return None

    if resolved.isdigit():
        return int(resolved)

    if hasattr(adapter, "resolve_message_id"):
        mapped = adapter.resolve_message_id(resolved)
        if mapped is not None:
            return mapped
    return resolved


def _extract_file_segment(message_detail: dict[str, Any]) -> dict[str, Any] | None:
    """从 get_msg 响应中提取第一个文件段。"""

    segments = message_detail.get("message")
    if not isinstance(segments, list):
        return None

    for segment in segments:
        if not isinstance(segment, dict):
            continue
        if segment.get("type") != "file":
            continue
        seg_data = segment.get("data")
        if isinstance(seg_data, dict):
            return seg_data
    return None


def _resolve_file_source(file_segment: dict[str, Any]) -> str | None:
    """解析文件源地址。"""

    for key in ("url", "path", "file"):
        value = file_segment.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _resolve_output_dir(save_dir: str | None) -> Path:
    """解析输出目录。"""

    if save_dir is None or not save_dir.strip():
        return _DEFAULT_DOWNLOAD_DIR

    return Path(save_dir).expanduser()


def _resolve_output_path(output_dir: Path, file_segment: dict[str, Any], source: str, message_id: str) -> Path:
    """根据文件信息生成落盘路径。"""

    file_name = file_segment.get("name") or file_segment.get("file")
    if isinstance(file_name, str) and file_name.strip():
        candidate_name = Path(file_name.strip()).name
    else:
        parsed = urlparse(source)
        candidate_name = Path(parsed.path).name if parsed.path else ""

    if not candidate_name:
        candidate_name = f"message_{message_id}_file"

    return output_dir / candidate_name


async def _download_to_path(source_url: str, output_path: Path) -> None:
    """将 URL 下载到目标路径。"""

    async with aiohttp.ClientSession() as session:
        async with session.get(source_url, timeout=aiohttp.ClientTimeout(total=60)) as response:
            response.raise_for_status()
            output_path.write_bytes(await response.read())


class SaveMessageFileTool(BaseTool):
    """保存指定消息中的文件到本地。"""

    tool_name: str = "save_message_file"
    tool_description: str = "根据消息 ID 获取其中的文件消息并保存到本地，返回保存后的绝对路径。"
    associated_platforms: list[str] = ["qq"]

    async def execute(
        self,
        message_id: Annotated[str, "要保存文件的消息 ID；支持 NapCat 数字 ID，若适配器支持也可传 MoFox message_id"],
        save_dir: Annotated[str | None, "可选保存目录；不传时默认保存到 data/napcat_download"] = None,
    ) -> tuple[bool, str | dict]:
        """保存指定消息中的文件并返回落盘路径。"""

        adapter = adapter_api.get_adapter(_NAPCAT_ADAPTER_SIGNATURE)
        if adapter is None:
            return False, "napcat_adapter 未启动：请先启用并启动 napcat_adapter 插件。"

        if not hasattr(adapter, "send_napcat_api"):
            return False, "napcat_adapter 不支持 send_napcat_api：请确认 napcat_adapter 版本兼容。"

        runtime_adapter = cast(Any, adapter)

        resolved_message_id = _resolve_message_id(adapter, message_id)
        if resolved_message_id is None:
            return False, "message_id 不能为空。"

        try:
            response = await runtime_adapter.send_napcat_api(
                "get_msg",
                {"message_id": resolved_message_id},
                timeout=30.0,
            )
        except Exception as exc:
            return False, f"获取消息详情失败：{exc}"

        detail = response.get("data") if isinstance(response, dict) else None
        if not isinstance(detail, dict):
            return False, "未获取到消息详情。"

        file_segment = _extract_file_segment(detail)
        if file_segment is None:
            return False, "该消息不是文件消息，或消息中不包含文件段。"

        source = _resolve_file_source(file_segment)
        if source is None:
            available_keys = ", ".join(sorted(str(key) for key in file_segment.keys()))
            return False, f"文件消息缺少可下载源，当前可用字段：{available_keys or '无'}"

        output_dir = _resolve_output_dir(save_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = _resolve_output_path(output_dir, file_segment, source, str(resolved_message_id))

        try:
            if source.startswith(("http://", "https://")):
                await _download_to_path(source, output_path)
            elif source.startswith("file://"):
                shutil.copy2(Path(source.removeprefix("file://")), output_path)
            else:
                source_path = Path(source)
                if source_path.is_absolute() and source_path.exists() and source_path.is_file():
                    shutil.copy2(source_path, output_path)
                else:
                    return False, f"暂不支持的文件源：{source}"
        except Exception as exc:
            return False, f"保存文件失败：{exc}"

        return True, {"saved_path": str(output_path.resolve())}


__all__ = ["SaveMessageFileTool"]