# -*- coding: utf-8 -*-
"""飞书群通知工具。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from urllib import request
from urllib.error import HTTPError, URLError


飞书应用ID环境变量 = "FEISHU_APP_ID"
飞书应用密钥环境变量 = "FEISHU_APP_SECRET"
飞书通知群环境变量 = "FEISHU_NOTIFY_CHAT_ID"
飞书Webhook环境变量 = "FEISHU_WEBHOOK_URL"
飞书通知提醒用户环境变量 = "FEISHU_NOTIFY_AT_USER_ID"
飞书通知提醒用户名环境变量 = "FEISHU_NOTIFY_AT_USER_NAME"
飞书告警群环境变量 = "FEISHU_ALERT_CHAT_ID"
飞书告警提醒用户环境变量 = "FEISHU_ALERT_AT_USER_ID"
飞书告警提醒用户名环境变量 = "FEISHU_ALERT_AT_USER_NAME"
飞书告警CLI路径环境变量 = "FEISHU_ALERT_LARK_CLI_PATH"
飞书告警CLI配置环境变量 = "FEISHU_ALERT_LARK_CLI_PROFILE"

飞书开放平台地址 = "https://open.feishu.cn"

try:
    import feishu_notify_config as 本地飞书配置
except ImportError:
    本地飞书配置 = None


class 飞书通知错误(RuntimeError):
    """飞书通知失败。"""


def _读取环境变量(name: str) -> str:
    return os.environ.get(name, "").strip()


def _读取配置(name: str) -> str:
    if 本地飞书配置 is not None and hasattr(本地飞书配置, name):
        value = getattr(本地飞书配置, name)
        config_value = str(value or "").strip()
        if config_value:
            return config_value
    return _读取环境变量(name)


def _请求_json(url: str, payload: dict, headers: dict[str, str] | None = None, timeout: int = 30) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            **(headers or {}),
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
    except (HTTPError, URLError) as exc:
        raise 飞书通知错误(f"请求飞书失败: {exc}") from exc

    try:
        result = json.loads(data)
    except json.JSONDecodeError as exc:
        raise 飞书通知错误(f"飞书返回不是 JSON: {data[:300]}") from exc

    if int(result.get("code", -1)) != 0:
        raise 飞书通知错误(f"飞书接口返回错误: {result}")
    return result


def _获取租户访问令牌(app_id: str, app_secret: str) -> str:
    result = _请求_json(
        f"{飞书开放平台地址}/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
    )
    token = str(result.get("tenant_access_token") or "").strip()
    if not token:
        raise 飞书通知错误("飞书返回缺少 tenant_access_token")
    return token


def _发送消息(token: str, chat_id: str, msg_type: str, content: dict) -> None:
    _请求_json(
        f"{飞书开放平台地址}/open-apis/im/v1/messages?receive_id_type=chat_id",
        {
            "receive_id": chat_id,
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
        },
        headers={"Authorization": f"Bearer {token}"},
    )


def _发送_webhook文本(webhook_url: str, text: str) -> None:
    _请求_json(webhook_url, {"msg_type": "text", "content": {"text": text}})


def _发送_lark_cli文本(chat_id: str, text: str, profile: str) -> None:
    cli = (
        _读取配置(飞书告警CLI路径环境变量)
        or shutil.which("lark-cli")
        or str(os.path.expanduser("~/.npm-global/bin/lark-cli"))
    )
    command = [cli, "--profile", profile, "im", "+messages-send", "--as", "bot"]
    command.extend(
        [
            "--chat-id",
            chat_id,
            "--content",
            json.dumps({"text": text}, ensure_ascii=False),
            "--format",
            "json",
        ]
    )
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise 飞书通知错误(f"无法调用飞书告警 CLI: {exc.__class__.__name__}") from exc
    if result.returncode != 0:
        raise 飞书通知错误("飞书告警 CLI 发送失败")
    try:
        response = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise 飞书通知错误("飞书告警 CLI 返回不是 JSON") from exc
    if not response.get("ok", False):
        raise 飞书通知错误("飞书告警接口返回失败")


def _构造提醒文本(text: str, *, 告警: bool) -> str:
    if not 告警:
        return text
    user_id = _读取配置(飞书告警提醒用户环境变量)
    user_name = _读取配置(飞书告警提醒用户名环境变量) or "Jiangyi"
    if not user_id:
        return text
    return f'<at user_id="{user_id}">{user_name}</at> {text}'


def 发送文本(text: str, *, 告警: bool = False) -> bool:
    """发送普通或告警文本；返回 False 表示未配置发送通道。"""

    chat_name = 飞书告警群环境变量 if 告警 else 飞书通知群环境变量
    webhook_name = "" if 告警 else 飞书Webhook环境变量
    chat_id = _读取配置(chat_name)
    app_id = _读取配置(飞书应用ID环境变量)
    app_secret = _读取配置(飞书应用密钥环境变量)
    message_text = _构造提醒文本(text, 告警=告警)

    if 告警 and chat_id:
        alert_profile = _读取配置(飞书告警CLI配置环境变量)
        if alert_profile:
            _发送_lark_cli文本(chat_id, message_text, alert_profile)
            return True

    if app_id and app_secret and chat_id:
        token = _获取租户访问令牌(app_id, app_secret)
        _发送消息(token, chat_id, "text", {"text": message_text})
        return True

    webhook_url = _读取配置(webhook_name) if webhook_name else ""
    if webhook_url:
        _发送_webhook文本(webhook_url, message_text)
        return True

    return False


def 发送告警(text: str) -> bool:
    return 发送文本(text, 告警=True)
