"""Fixed local commands for updating Sn_OCR and restarting its service."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

_SERVICE_NAME = re.compile(r"^[A-Za-z0-9_.@:-]+$")
_MAX_OUTPUT = 1200


def normalize_allowlist(raw: str) -> set[str]:
    text = (raw or "").replace("\uff0c", ",")
    return {part.strip() for part in text.split(",") if part.strip()}


def deploy_allowed(is_admin: bool, sender_id: str, allowlist: str) -> bool:
    """AstrBot admins may deploy. A non-empty allowlist narrows that further."""
    if not is_admin:
        return False
    allowed = normalize_allowlist(allowlist)
    if not allowed:
        return True
    return str(sender_id or "") in allowed


def validate_target(repo: str, service: str) -> tuple[Path, str]:
    service_name = (service or "").strip()
    if not _SERVICE_NAME.fullmatch(service_name):
        raise ValueError("\u670d\u52a1\u540d\u4e0d\u5408\u6cd5\uff0c\u53ea\u80fd\u5305\u542b\u5b57\u6bcd\u3001\u6570\u5b57\u3001\u4e0b\u5212\u7ebf\u3001\u70b9\u548c\u8fde\u5b57\u7b26")
    path = Path(repo).expanduser()
    if not path.is_dir() or not (path / ".git").exists():
        raise ValueError(f"\u4ed3\u5e93\u76ee\u5f55\u4e0d\u5b58\u5728\u6216\u4e0d\u662f Git \u4ed3\u5e93\uff1a{path}")
    return path, service_name


def pull_command(repo: Path) -> list[str]:
    return ["git", "-C", str(repo), "pull", "--ff-only"]


def restart_command(service: str, use_sudo: bool = False) -> list[str]:
    command = ["systemctl", "restart", service]
    if use_sudo:
        return ["sudo", "-n", *command]
    return command


def status_command(service: str, use_sudo: bool = False) -> list[str]:
    command = ["systemctl", "is-active", service]
    if use_sudo:
        return ["sudo", "-n", *command]
    return command


async def run_command(args: list[str], timeout: int = 180) -> tuple[int, str]:
    """Run a fixed argv command. No shell, so chat text cannot change it."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_MERGE_AUTOEDIT"] = "no"
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except FileNotFoundError:
        return 127, f"\u627e\u4e0d\u5230\u547d\u4ee4\uff1a{args[0]}"
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return 124, "\u547d\u4ee4\u6267\u884c\u8d85\u65f6"
    text = (stdout or b"").decode("utf-8", errors="replace").strip()
    if len(text) > _MAX_OUTPUT:
        text = text[-_MAX_OUTPUT:]
    return int(process.returncode or 0), text
