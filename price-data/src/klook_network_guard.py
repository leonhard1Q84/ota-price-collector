# -*- coding: utf-8 -*-
"""Klook 浏览器启动前的显式代理与 Clash 出口门禁。"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
from urllib.parse import urlsplit

from klook_egress_guard import ensure_circuit_closed, open_circuit


DEFAULT_PROBE_URLS = (
    "https://api.ipify.org?format=json",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
)
MIN_SUCCESSFUL_PROBES = 2
DEFAULT_CLASH_UNIX_SOCKET_PATH = "/tmp/verge/verge-mihomo.sock"


class KlookNetworkGuardError(RuntimeError):
    """网络路径不能证明稳定时禁止访问 Klook。"""


def safe_proxy_location(proxy_server: str) -> str:
    parsed = urlsplit(str(proxy_server or ""))
    if not parsed.scheme or not parsed.hostname:
        return "未配置"
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


def validate_proxy_server(proxy_server: str) -> str:
    value = str(proxy_server or "").strip()
    if not value:
        raise KlookNetworkGuardError("未配置显式代理，已阻止 Klook 访问")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise KlookNetworkGuardError("Klook 显式代理格式无效") from exc
    if parsed.scheme.lower() not in {"http", "https", "socks5"} or not parsed.hostname:
        raise KlookNetworkGuardError("Klook 显式代理必须使用 http、https 或 socks5")
    if parsed.username or parsed.password:
        raise KlookNetworkGuardError("代理凭据必须通过独立配置传入，不能写在代理 URL 中")
    if parsed.port is not None and not (1 <= parsed.port <= 65535):
        raise KlookNetworkGuardError("Klook 显式代理端口无效")
    return value


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _extract_public_ip(body: str) -> str:
    text = str(body or "").strip()
    candidates = [text]
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            candidates.insert(0, str(payload.get("ip") or ""))
    except ValueError:
        pass
    for candidate in candidates:
        match = re.search(
            r"(?<![0-9A-Fa-f:])([0-9A-Fa-f:.]{3,45})(?![0-9A-Fa-f:])",
            candidate,
        )
        if not match:
            continue
        try:
            return str(ipaddress.ip_address(match.group(1)))
        except ValueError:
            continue
    raise KlookNetworkGuardError("出口探针没有返回有效公网 IP")


def _resolve_probe_consensus(observed_ips, total_probe_count: int) -> str:
    if len(observed_ips) < MIN_SUCCESSFUL_PROBES:
        raise KlookNetworkGuardError(
            f"出口探针成功数不足: {len(observed_ips)}/{total_probe_count}，未启动 Klook"
        )
    unique_ips = sorted(set(observed_ips))
    if len(unique_ips) != 1:
        reason = "同一显式代理的公网出口不稳定，疑似 IPv4/IPv6 或节点分流"
        open_circuit("EGRESS_MISMATCH", reason)
        raise KlookNetworkGuardError(reason)
    return unique_ips[0]


async def _read_clash_json(socket_path: str, endpoint: str) -> dict:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(socket_path), timeout=3
        )
        writer.write(
            (
                f"GET {endpoint} HTTP/1.1\r\n"
                "Host: localhost\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(256 * 1024), timeout=3)
        writer.close()
        await writer.wait_closed()
    except Exception as exc:
        raise KlookNetworkGuardError("无法读取 Clash 路由状态，已阻止 Klook 访问") from exc

    header, separator, body = raw.partition(b"\r\n\r\n")
    if not separator or not header.startswith(b"HTTP/1.1 200"):
        raise KlookNetworkGuardError("Clash 路由状态接口不可用，已阻止 Klook 访问")
    if b"transfer-encoding: chunked" in header.lower():
        decoded = bytearray()
        offset = 0
        try:
            while True:
                line_end = body.index(b"\r\n", offset)
                chunk_size = int(body[offset:line_end].split(b";", 1)[0], 16)
                offset = line_end + 2
                if chunk_size == 0:
                    break
                decoded.extend(body[offset:offset + chunk_size])
                offset += chunk_size + 2
            body = bytes(decoded)
        except (ValueError, IndexError) as exc:
            raise KlookNetworkGuardError("Clash 路由状态响应无效，已阻止访问") from exc
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise KlookNetworkGuardError("Clash 路由状态不是有效 JSON，已阻止访问") from exc
    if not isinstance(payload, dict):
        raise KlookNetworkGuardError("Clash 路由状态格式无效，已阻止访问")
    return payload


async def verify_clash_route(proxy_server: str) -> dict:
    """要求 Clash 全局模式固定到单一非直连节点。"""
    ensure_circuit_closed()
    socket_path = os.environ.get(
        "KLOOK_CLASH_UNIX_SOCKET_PATH",
        DEFAULT_CLASH_UNIX_SOCKET_PATH,
    ).strip()
    config = await _read_clash_json(socket_path, "/configs")
    if str(config.get("mode") or "").lower() != "global":
        raise KlookNetworkGuardError(
            "Clash 当前不是 global 模式，无法保证 Klook 与 DataDome 同出口"
        )
    global_proxy = await _read_clash_json(socket_path, "/proxies/GLOBAL")
    selected = str(global_proxy.get("now") or "").strip()
    if not selected or selected.upper() in {"DIRECT", "REJECT", "COMPATIBLE"}:
        raise KlookNetworkGuardError("Clash GLOBAL 未选择固定代理节点")

    parsed = urlsplit(validate_proxy_server(proxy_server))
    configured_port = parsed.port or {"http": 80, "https": 443, "socks5": 1080}[
        parsed.scheme
    ]
    mixed_port = config.get("mixed-port")
    try:
        mixed_port_value = int(mixed_port) if mixed_port is not None else None
    except (TypeError, ValueError) as exc:
        raise KlookNetworkGuardError("Clash mixed-port 状态无效") from exc
    if mixed_port_value is not None and mixed_port_value != configured_port:
        raise KlookNetworkGuardError("浏览器代理没有指向当前 Clash mixed-port")
    return {
        "mode": "global",
        "selected_proxy_fingerprint": _fingerprint("proxy:" + selected),
    }


async def verify_proxy_egress(
    playwright,
    proxy_server: str,
    proxy_username: str | None = None,
    proxy_password: str | None = None,
) -> dict:
    """通过显式代理访问公开探针；不请求 Klook 或 DataDome。"""
    ensure_circuit_closed()
    proxy = validate_proxy_server(proxy_server)
    clash_report = await verify_clash_route(proxy)
    proxy_options = {"server": proxy}
    if proxy_username:
        proxy_options["username"] = proxy_username
    if proxy_password:
        proxy_options["password"] = proxy_password

    request_context = await playwright.request.new_context(proxy=proxy_options, timeout=8000)
    observed_ips = []
    probe_failure_count = 0
    try:
        for probe_url in DEFAULT_PROBE_URLS:
            try:
                response = await request_context.get(probe_url, timeout=8000)
                if not response.ok:
                    raise KlookNetworkGuardError(f"出口探针 HTTP {response.status}")
                observed_ips.append(_extract_public_ip(await response.text()))
            except Exception:
                probe_failure_count += 1
    finally:
        await request_context.dispose()

    egress_ip = _resolve_probe_consensus(observed_ips, len(DEFAULT_PROBE_URLS))
    return {
        "proxy": safe_proxy_location(proxy),
        "clash_mode": clash_report["mode"],
        "clash_proxy_fingerprint": clash_report["selected_proxy_fingerprint"],
        "probe_count": len(observed_ips),
        "probe_failure_count": probe_failure_count,
        "egress_fingerprint": _fingerprint(egress_ip),
    }
