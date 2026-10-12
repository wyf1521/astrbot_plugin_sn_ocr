"""AstrBot plugin for sending message images to Sn_OCR."""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star
from astrbot.core.message.components import Image

_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
import deploy_cmd


def _admin_command(func):
    permission = getattr(filter, "permission_type", None)
    permission_type = getattr(filter, "PermissionType", None)
    if permission is None or permission_type is None:
        return func
    return permission(permission_type.ADMIN)(func)



try:
    from astrbot.core.utils.session_waiter import (
        SessionController,
        SessionFilter,
        session_waiter,
    )
except ImportError:  # AstrBot < 3.4.36 cannot wait for a later image.
    SessionController = None
    SessionFilter = None
    session_waiter = None

_CANCEL_WORDS = {"取消", "cancel", "退出", "exit"}
SenderSessionFilter = None


def _event_sender_id(event: AstrMessageEvent) -> str:
    sender_id = event.get_sender_id()
    if isinstance(sender_id, str) and sender_id:
        return sender_id
    message_obj = getattr(event, "message_obj", None)
    sender = getattr(message_obj, "sender", None)
    user_id = getattr(sender, "user_id", None)
    if user_id is None or user_id == "":
        return ""
    return str(user_id)


if SessionFilter is not None:

    class SenderSessionFilter(SessionFilter):
        """只匹配同一会话中的同一发送人，避免群聊里其他人的消息被接走。"""

        def filter(self, event: AstrMessageEvent) -> str:
            return f"snocr:{event.unified_msg_origin}:{_event_sender_id(event)}"


class SnOcrPlugin(Star):
    """Upload images from an AstrBot message to the Sn_OCR HTTP API."""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    @filter.command("snocr")
    async def snocr(self, event: AstrMessageEvent) -> MessageEventResult:
        """识别图片。用法：/snocr [skip|overwrite] [部门 姓名]，图片可同发，也可随后发送。"""
        api_base = str(self.config.get("api_base", "")).strip().rstrip("/")
        api_token = str(self.config.get("api_token", "")).strip()
        if not api_base or not api_token:
            yield event.plain_result("Sn_OCR 插件未配置 API 地址或 Token，请先在插件配置中填写。")
            return

        policy, department, person_name = self._command_options(event.message_str)
        images = self._message_images(event)
        if images:
            async for text in self._recognize(api_base, api_token, images, policy, department, person_name):
                yield event.plain_result(text)
            return

        wait_seconds = self._image_wait_seconds()
        if wait_seconds <= 0 or session_waiter is None or SenderSessionFilter is None:
            yield event.plain_result(self._missing_image_text())
            return

        yield event.plain_result(
            f"请在 {wait_seconds} 秒内发送要识别的图片，回复“取消”可结束等待。"
        )

        @session_waiter(timeout=wait_seconds, record_history_chains=False)
        async def image_waiter(
            controller: SessionController,
            followup: AstrMessageEvent,
        ) -> None:
            followup_images = self._message_images(followup)
            if followup_images:
                controller.keep(
                    timeout=max(wait_seconds, self._request_timeout() + 30),
                    reset_timeout=True,
                )
                async for text in self._recognize(
                    api_base,
                    api_token,
                    followup_images,
                    policy,
                    department,
                    person_name,
                ):
                    await followup.send(followup.plain_result(text))
                controller.stop()
                return

            if self._is_cancel(followup.message_str):
                await followup.send(followup.plain_result("已取消本次识别。"))
                controller.stop()
                return

            await followup.send(
                followup.plain_result("还没有收到图片，请发送图片，或回复“取消”。")
            )
            controller.keep(timeout=wait_seconds, reset_timeout=True)

        try:
            await image_waiter(event, session_filter=SenderSessionFilter())
        except TimeoutError:
            yield event.plain_result("等待图片超时，请重新发送 /snocr。")
        except Exception as exc:
            logger.exception("Sn_OCR image wait failed")
            yield event.plain_result(f"等待图片时发生错误：{self._short_error(exc)}")
        finally:
            event.stop_event()


    @_admin_command
    @filter.command("snupdate")
    async def snupdate(self, event: AstrMessageEvent):
        """拉取 /opt/Sn_OCR 并重启 sn_ocr。仅 AstrBot 管理员可用。"""
        if not deploy_cmd.deploy_allowed(
            self._event_is_admin(event),
            _event_sender_id(event),
            str(self.config.get("deploy_user_ids", "")),
        ):
            yield event.plain_result("没有权限更新仓库。")
            return
        if not self._as_bool(self.config.get("deploy_enabled", True)):
            yield event.plain_result("更新指令已关闭，请在插件配置中启用 deploy_enabled。")
            return
        try:
            repo, service = deploy_cmd.validate_target(
                str(self.config.get("deploy_repo", "/opt/Sn_OCR")),
                str(self.config.get("deploy_service", "sn_ocr")),
            )
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        use_sudo = self._as_bool(self.config.get("deploy_use_sudo", False))
        yield event.plain_result(f"开始更新 {repo} ……")
        code, output = await deploy_cmd.run_command(deploy_cmd.pull_command(repo), timeout=180)
        if code != 0:
            yield event.plain_result(f"git pull 失败（退出码 {code}）：\n{output or '没有输出'}")
            return

        yield event.plain_result(
            f"代码已更新。\n{output or '没有新的提交'}\n正在重启 {service} ……"
        )
        code, output = await deploy_cmd.run_command(
            deploy_cmd.restart_command(service, use_sudo),
            timeout=90,
        )
        if code != 0:
            yield event.plain_result(f"重启失败（退出码 {code}）：\n{output or '没有输出'}")
            return

        status_code, status = await deploy_cmd.run_command(
            deploy_cmd.status_command(service, use_sudo),
            timeout=20,
        )
        state = status.strip() or f"退出码 {status_code}"
        yield event.plain_result(f"已重启 {service}，当前状态：{state}")
        event.stop_event()

    def _missing_image_text(self) -> str:
        if self._image_wait_seconds() > 0 and session_waiter is None:
            return (
                "未找到图片。当前 AstrBot 版本不支持分步识别，"
                "请把图片和 /snocr 一起发送，或升级到 v3.4.36 及以上。"
            )
        return "未找到图片，请把图片和 /snocr 一起发送。"

    async def _recognize(
        self,
        api_base: str,
        api_token: str,
        images: list[Image],
        duplicate_policy: str,
        department: str,
        person_name: str,
    ) -> AsyncIterator[str]:
        max_images = self._max_images()
        truncated = len(images) > max_images
        selected = images[:max_images]
        yield f"正在识别 {len(selected)} 张图片，请稍候……"
        try:
            result = await self._upload_images(
                api_base,
                api_token,
                selected,
                duplicate_policy,
                department,
                person_name,
            )
        except Exception as exc:
            logger.exception("Sn_OCR request failed")
            yield f"Sn_OCR 请求失败：{self._short_error(exc)}"
            return

        text = self._format_response(result)
        if truncated:
            text = f"已按插件上限截取前 {max_images} 张图片。\n{text}"
        yield text

    def _message_images(self, event: AstrMessageEvent) -> list[Image]:
        message_obj = getattr(event, "message_obj", None)
        chain = getattr(message_obj, "message", None)
        if chain is None:
            chain = getattr(message_obj, "chain", [])
        return [component for component in chain if isinstance(component, Image)]

    def _command_options(self, message: str) -> tuple[str, str, str]:
        parts = (message or "").split()
        options = parts[1:] if parts and parts[0].lower().lstrip("/") == "snocr" else parts
        policy_words = {"overwrite", "覆盖", "skip", "跳过"}
        if any(part.lower() in {"overwrite", "覆盖"} for part in options):
            policy = "overwrite"
        if any(part.lower() in {"skip", "跳过"} for part in options):
            policy = "skip"
        configured = str(self.config.get("duplicate_policy", "skip")).lower()
        if "policy" not in locals():
            policy = configured if configured in {"skip", "overwrite"} else "skip"
        identity = [part for part in options if part.lower() not in policy_words]
        department = identity[0] if identity else ""
        person_name = " ".join(identity[1:]) if len(identity) > 1 else ""
        return policy, department, person_name

    def _event_is_admin(self, event: AstrMessageEvent) -> bool:
        checker = getattr(event, "is_admin", None)
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            return False

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value).strip().lower() in {"1", "true", "yes", "on", "开启"}

    def _image_wait_seconds(self) -> int:
        value = self._as_int(self.config.get("image_wait_seconds", 120), 120)
        return min(1800, max(0, value))

    def _request_timeout(self) -> int:
        return max(1, self._as_int(self.config.get("timeout", 180), 180))

    def _max_images(self) -> int:
        return min(500, max(1, self._as_int(self.config.get("max_images", 20), 20)))

    @staticmethod
    def _is_cancel(message: str) -> bool:
        normalized = (message or "").strip().lower().lstrip("/")
        return normalized in _CANCEL_WORDS

    async def _upload_images(
        self,
        api_base: str,
        api_token: str,
        images: list[Image],
        duplicate_policy: str,
        department: str,
        person_name: str,
    ) -> dict[str, Any]:
        form = aiohttp.FormData()
        opened_files = []
        try:
            for index, image in enumerate(images, start=1):
                path = Path(await image.convert_to_file_path())
                if not path.is_file():
                    raise ValueError(f"第 {index} 张图片无法读取")
                stream = path.open("rb")
                opened_files.append(stream)
                form.add_field(
                    "file",
                    stream,
                    filename=path.name or f"image-{index}.png",
                    content_type=self._content_type(path.suffix),
                )
            form.add_field("duplicate_policy", duplicate_policy)
            if department:
                form.add_field("department", department)
            if person_name:
                form.add_field("person_name", person_name)
            timeout_seconds = self._request_timeout()
            headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json"}
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(f"{api_base}/api/v1/ocr", data=form, headers=headers) as response:
                    body = await response.text()
                    try:
                        payload = json.loads(body)
                    except json.JSONDecodeError:
                        payload = {"success": False, "error": f"服务返回非 JSON（HTTP {response.status}）"}
                    if not isinstance(payload, dict):
                        payload = {"success": False, "error": f"服务返回了无效 JSON（HTTP {response.status}）"}
                    if response.status >= 400 and "error" not in payload:
                        payload["error"] = f"HTTP {response.status}"
                    return payload
        finally:
            for stream in opened_files:
                stream.close()

    @staticmethod
    def _content_type(suffix: str) -> str:
        return {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".png": "image/png",
        }.get(suffix.lower(), "application/octet-stream")

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _short_error(error: Exception) -> str:
        message = str(error).strip() or error.__class__.__name__
        return message[:300]

    @classmethod
    def _format_response(cls, payload: dict[str, Any]) -> str:
        if not payload.get("success"):
            return f"识别失败：{payload.get('error', '服务未返回错误信息')}"
        results = payload.get("results")
        if isinstance(results, list):
            lines = [f"识别完成：成功 {payload.get('succeeded', 0)}，失败 {payload.get('failed', 0)}。"]
            for index, result in enumerate(results, start=1):
                lines.append(f"\n[{index}] {cls._format_one(result)}")
            return "".join(lines)
        return f"识别完成：{cls._format_one(payload)}"

    @staticmethod
    def _format_one(result: dict[str, Any]) -> str:
        if not result.get("success"):
            return f"{result.get('image_name', '图片')}：失败，{result.get('error', '未知错误')}"
        fields = result.get("fields") or {}
        image_name = result.get("image_name", "图片")
        if result.get("duplicate"):
            prefix = f"{image_name}：重复记录，已跳过"
        elif result.get("overwritten"):
            prefix = f"{image_name}：已覆盖原记录"
        else:
            prefix = f"{image_name}：识别成功"
        field_lines = [f"{key}={value}" for key, value in fields.items() if value not in (None, "")]
        return prefix + ("\n" + "\n".join(field_lines) if field_lines else "（没有提取到字段）")
