"""AstrBot plugin for sending message images to Sn_OCR."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star
from astrbot.core.message.components import Image


class SnOcrPlugin(Star):
    """Upload images from an AstrBot message to the Sn_OCR HTTP API."""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    @filter.command("snocr")
    async def snocr(self, event: AstrMessageEvent) -> MessageEventResult:
        """识别当前消息中的图片。用法：/snocr [skip|overwrite]"""
        api_base = str(self.config.get("api_base", "")).strip().rstrip("/")
        api_token = str(self.config.get("api_token", "")).strip()
        if not api_base or not api_token:
            yield event.plain_result("Sn_OCR 插件未配置 API 地址或 Token，请先在插件配置中填写。")
            return

        policy = self._duplicate_policy(event.message_str)
        images = self._message_images(event)
        max_images = min(500, max(1, self._as_int(self.config.get("max_images", 20), 20)))
        truncated = len(images) > max_images
        images = images[:max_images]
        if not images:
            yield event.plain_result("未找到图片，请把图片和 /snocr 一起发送。")
            return

        yield event.plain_result(f"正在识别 {len(images)} 张图片，请稍候……")
        try:
            result = await self._upload_images(api_base, api_token, images, policy)
        except Exception as exc:
            logger.exception("Sn_OCR request failed")
            yield event.plain_result(f"Sn_OCR 请求失败：{self._short_error(exc)}")
            return

        text = self._format_response(result)
        if truncated:
            text = f"已按插件上限截取前 {max_images} 张图片。\n{text}"
        yield event.plain_result(text)

    def _message_images(self, event: AstrMessageEvent) -> list[Image]:
        message_obj = getattr(event, "message_obj", None)
        chain = getattr(message_obj, "message", None)
        if chain is None:
            chain = getattr(message_obj, "chain", [])
        return [component for component in chain if isinstance(component, Image)]

    def _duplicate_policy(self, message: str) -> str:
        parts = (message or "").split()
        options = parts[1:] if parts and parts[0].lower().lstrip("/") == "snocr" else parts
        if any(part.lower() in {"overwrite", "覆盖"} for part in options):
            return "overwrite"
        if any(part.lower() in {"skip", "跳过"} for part in options):
            return "skip"
        configured = str(self.config.get("duplicate_policy", "skip")).lower()
        return configured if configured in {"skip", "overwrite"} else "skip"

    async def _upload_images(
        self,
        api_base: str,
        api_token: str,
        images: list[Image],
        duplicate_policy: str,
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
            timeout_seconds = max(1, self._as_int(self.config.get("timeout", 180), 180))
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
