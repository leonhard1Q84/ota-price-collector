# -*- coding: utf-8 -*-
"""
Klook crawler browser/session core helpers.
"""

import asyncio
import json
import os
import random
import re
import time
import urllib
from datetime import datetime
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext

from browser_profile_manager import acquire_browser_profile_lock, ensure_browser_profile
from data_processor import process_data, save_to_csv
from klook_network_guard import (
    KlookNetworkGuardError,
    safe_proxy_location,
    verify_clash_route,
    verify_proxy_egress,
)
from logger import log_error, log_info, log_success, log_warning
from path_utils import get_data_dir, get_screenshots_dir
from url_handler import update_url_dates


DEFAULT_URL_DATE_TIMEOUT_SECONDS = 7 * 60
DEFAULT_INITIAL_PAGE_SETTLE_SECONDS = 20
DEFAULT_EMPTY_RESPONSE_RELOAD_LIMIT = 0
DEFAULT_NO_RESPONSE_RELOAD_SECONDS = 20
DEFAULT_NO_NEW_DATA_BREAK_SECONDS = 30
DEFAULT_SCROLL_PAUSE_MIN_SECONDS = 1
DEFAULT_SCROLL_PAUSE_MAX_SECONDS = 3
DEFAULT_MIN_SCROLL_OBSERVE_SECONDS = 10
DEFAULT_LOADING_CLEAR_GRACE_SECONDS = 5
DEFAULT_RESULT_STABLE_SECONDS = 12
DEFAULT_LOADING_STUCK_RELOAD_SECONDS = 60
DEFAULT_LOADING_HEIGHT_CHANGE_THRESHOLD_PX = 100
DEFAULT_LOADING_SELECTOR_CANDIDATES = [
    ".loading-SMrOu6",
    ".tip-q4NxXN",
]
DEFAULT_LOADING_TEXT_CANDIDATES = [
    "正在为你查找最优选择",
    "载入中",
    "Loading...",
]
DEFAULT_WINDOWS_CHROME_PATH = "C:/Program Files/Google/Chrome/Application/chrome.exe"
DEFAULT_MAC_CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEFAULT_KLOOK_BROWSER_PROXY_SERVER = "http://127.0.0.1:7897"
KLOOK_SITE_PREFERENCE_COOKIE = "klk_ps"
KLOOK_REDIRECT_COOKIE = "klk_rdc"
KLOOK_RESULT_API_PATH = "/transfercarrentalapisrv/product/result"
DEFAULT_RISK_PAGE_TEXT_CANDIDATES = [
    "你的访问可能存在风险，已被拦截",
    "无法访问原因",
    "Risk ID",
    "RISK ID",
    "Too Many Requests",
    "请求过于频繁",
]


class UrlDateTimeoutError(RuntimeError):
    """单个 URL 在指定日期对下执行超时。"""


class UrlNoDataError(RuntimeError):
    """单个 URL 在指定日期对下没有获取到数据。"""


class SessionInterceptionError(RuntimeError):
    """Klook 会话被风控或当前 context 已污染。"""

    def __init__(self, message, partial_data=False, metadata=None):
        super().__init__(message)
        self.partial_data = bool(partial_data)
        self.metadata = metadata if isinstance(metadata, dict) else {}


class NonRetryableSessionInterceptionError(SessionInterceptionError):
    """国内站跳转或明确风控；当前任务不得自动刷新或重建 Profile。"""


def _url_hostname(url):
    try:
        return (urlsplit(str(url or "")).hostname or "").lower().rstrip(".")
    except (TypeError, ValueError):
        return ""


def _host_matches_domain(host, domain):
    normalized_host = str(host or "").lower().rstrip(".")
    normalized_domain = str(domain or "").lower().lstrip(".").rstrip(".")
    return normalized_host == normalized_domain or normalized_host.endswith(
        "." + normalized_domain
    )


def _is_klook_com_url(url):
    try:
        parsed = urlsplit(str(url or ""))
        if parsed.scheme.lower() != "https":
            return False
        if parsed.username or parsed.password or parsed.port not in {None, 443}:
            return False
        return _host_matches_domain(parsed.hostname, "klook.com")
    except (TypeError, ValueError):
        return False


def _is_klook_cn_url(url):
    return _host_matches_domain(_url_hostname(url), "klook.cn")


def _redact_url(url):
    try:
        parsed = urlsplit(str(url or ""))
        return parsed._replace(query="", fragment="").geturl()
    except (TypeError, ValueError):
        return "<invalid-url>"


def _is_target_result_api_url(url):
    try:
        return KLOOK_RESULT_API_PATH in urlsplit(str(url or "")).path.lower()
    except (TypeError, ValueError):
        return False


def _is_allowed_target_result_api_url(url):
    return _is_target_result_api_url(url) and _is_klook_com_url(url)


def _is_captcha_frame_url(url):
    try:
        parsed = urlsplit(str(url or ""))
    except (TypeError, ValueError):
        return False
    return _host_matches_domain(parsed.hostname, "captcha-delivery.com") and (
        "/captcha" in parsed.path.lower()
    )


def _is_auto_interstitial_frame_url(url):
    try:
        parsed = urlsplit(str(url or ""))
    except (TypeError, ValueError):
        return False
    return _host_matches_domain(parsed.hostname, "captcha-delivery.com") and (
        "/interstitial" in parsed.path.lower()
    )


def _is_klook_guarded_service_url(url):
    host = _url_hostname(url)
    return any(
        _host_matches_domain(host, domain)
        for domain in ("klook.com", "captcha-delivery.com", "datadome.co")
    )


def _get_browser_executable_path():
    """返回可用的浏览器路径；为空时使用 Playwright 自带 Chromium。"""
    configured_path = os.environ.get("KLOOK_CHROME_EXECUTABLE")
    if configured_path:
        return configured_path

    if os.name == "nt" and os.path.exists(DEFAULT_WINDOWS_CHROME_PATH):
        return DEFAULT_WINDOWS_CHROME_PATH

    if os.path.exists(DEFAULT_MAC_CHROME_PATH):
        return DEFAULT_MAC_CHROME_PATH

    return None


def save_abnormal_state(
    url_info,
    source_country,
    p_date,
    d_date,
    currency,
    reason,
    abnormal_states_file="abnormal_states.json",
):
    """保存异常状态到文件。"""
    try:
        abnormal_state = {
            "timestamp": datetime.now().isoformat(),
            "url_info": url_info,
            "source_country": source_country,
            "p_date": p_date,
            "d_date": d_date,
            "currency": currency,
            "reason": reason,
            "retry_count": 3,
        }

        data_dir = get_data_dir()
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)

        abnormal_states_path = os.path.join(data_dir, abnormal_states_file)
        abnormal_states = []
        if os.path.exists(abnormal_states_path):
            try:
                with open(abnormal_states_path, "r", encoding="utf-8") as file:
                    abnormal_states = json.load(file)
            except Exception:
                abnormal_states = []

        exists = False
        for state in abnormal_states:
            if (
                state["url_info"]["url"] == url_info["url"]
                and state["source_country"] == source_country
                and state["p_date"] == p_date
                and state["d_date"] == d_date
            ):
                exists = True
                break

        if not exists:
            abnormal_states.append(abnormal_state)
            with open(abnormal_states_path, "w", encoding="utf-8") as file:
                json.dump(abnormal_states, file, ensure_ascii=False, indent=2)

            log_info(
                f"已保存异常状态: {url_info['city_name']} - {url_info['poi_name']}, "
                f"{source_country}, {p_date}->{d_date}, 原因: {reason}"
            )
    except Exception as exc:
        log_error(f"保存异常状态时出错: {exc}")


def _coerce_float(value, default, minimum):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(number, minimum)


def _coerce_int(value, default, minimum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    return max(number, minimum)


def _normalize_string_list(value, defaults):
    if not isinstance(value, list):
        return list(defaults)

    normalized = []
    for item in value:
        text = str(item).strip() if item is not None else ""
        if text:
            normalized.append(text)

    return normalized or list(defaults)


def _normalize_page_timing_config(page_timing):
    config = page_timing if isinstance(page_timing, dict) else {}
    scroll_pause_min_seconds = _coerce_float(
        config.get("scroll_pause_min_seconds"),
        DEFAULT_SCROLL_PAUSE_MIN_SECONDS,
        0.2,
    )
    normalized = {
        "initial_page_settle_seconds": _coerce_int(
            config.get("initial_page_settle_seconds"),
            DEFAULT_INITIAL_PAGE_SETTLE_SECONDS,
            0,
        ),
        "empty_response_reload_limit": _coerce_int(
            config.get("empty_response_reload_limit"),
            DEFAULT_EMPTY_RESPONSE_RELOAD_LIMIT,
            0,
        ),
        "no_response_reload_seconds": _coerce_float(
            config.get("no_response_reload_seconds"),
            DEFAULT_NO_RESPONSE_RELOAD_SECONDS,
            5,
        ),
        "no_new_data_break_seconds": _coerce_float(
            config.get("no_new_data_break_seconds"),
            DEFAULT_NO_NEW_DATA_BREAK_SECONDS,
            5,
        ),
        "scroll_pause_min_seconds": scroll_pause_min_seconds,
        "scroll_pause_max_seconds": _coerce_float(
            config.get("scroll_pause_max_seconds"),
            DEFAULT_SCROLL_PAUSE_MAX_SECONDS,
            scroll_pause_min_seconds,
        ),
        "min_scroll_observe_seconds": _coerce_float(
            config.get("min_scroll_observe_seconds"),
            DEFAULT_MIN_SCROLL_OBSERVE_SECONDS,
            0,
        ),
        "loading_clear_grace_seconds": _coerce_float(
            config.get("loading_clear_grace_seconds"),
            DEFAULT_LOADING_CLEAR_GRACE_SECONDS,
            1,
        ),
        "result_stable_seconds": _coerce_float(
            config.get("result_stable_seconds"),
            DEFAULT_RESULT_STABLE_SECONDS,
            1,
        ),
        "loading_stuck_reload_seconds": _coerce_float(
            config.get("loading_stuck_reload_seconds"),
            DEFAULT_LOADING_STUCK_RELOAD_SECONDS,
            1,
        ),
        "loading_height_change_threshold_px": _coerce_int(
            config.get("loading_height_change_threshold_px"),
            DEFAULT_LOADING_HEIGHT_CHANGE_THRESHOLD_PX,
            20,
        ),
        "loading_selector_candidates": _normalize_string_list(
            config.get("loading_selector_candidates"),
            DEFAULT_LOADING_SELECTOR_CANDIDATES,
        ),
        "loading_text_candidates": _normalize_string_list(
            config.get("loading_text_candidates"),
            DEFAULT_LOADING_TEXT_CANDIDATES,
        ),
    }
    if normalized["empty_response_reload_limit"] != 0:
        raise ValueError("Klook 安全基线禁止无结果时自动刷新页面")
    return normalized


DEFAULT_BROWSER_CONFIG = {
    "strip_transient_query_params": True,
    "transient_query_params": ["search_id", "clickId", "spm"],
    "reuse_page_across_dates": True,
    "profile_mode": "base_plus_runtime",
    "rebuild_runtime_on_crash": False,
    "context_relaunch_after_reload_failures": 1,
    "profile_rebuild_after_context_failures": 0,
    "max_date_pairs_per_context": 0,
    "max_urls_per_context": 0,
    "max_minutes_per_context": 0,
    "consecutive_result_403_threshold": 3,
    "consecutive_navigation_failure_threshold": 2,
    "risk_page_text_candidates": list(DEFAULT_RISK_PAGE_TEXT_CANDIDATES),
    "drop_cache_control_headers": True,
    "accept_language": "zh-CN,zh;q=0.9,en;q=0.8",
    "warmup_entry_url": None,
    "proxy_server": DEFAULT_KLOOK_BROWSER_PROXY_SERVER,
    "proxy_username": None,
    "proxy_password": None,
}


def _normalize_browser_config(browser_config):
    config = dict(DEFAULT_BROWSER_CONFIG)
    if isinstance(browser_config, dict):
        config.update(browser_config)

    config["strip_transient_query_params"] = bool(config.get("strip_transient_query_params", True))
    config["reuse_page_across_dates"] = bool(config.get("reuse_page_across_dates", False))
    config["profile_mode"] = str(config.get("profile_mode") or DEFAULT_BROWSER_CONFIG["profile_mode"])
    config["rebuild_runtime_on_crash"] = bool(config.get("rebuild_runtime_on_crash", True))
    config["context_relaunch_after_reload_failures"] = _coerce_int(
        config.get("context_relaunch_after_reload_failures"),
        DEFAULT_BROWSER_CONFIG["context_relaunch_after_reload_failures"],
        1,
    )
    config["profile_rebuild_after_context_failures"] = _coerce_int(
        config.get("profile_rebuild_after_context_failures"),
        DEFAULT_BROWSER_CONFIG["profile_rebuild_after_context_failures"],
        0,
    )
    config["max_date_pairs_per_context"] = _coerce_int(
        config.get("max_date_pairs_per_context"),
        DEFAULT_BROWSER_CONFIG["max_date_pairs_per_context"],
        0,
    )
    config["max_urls_per_context"] = _coerce_int(
        config.get("max_urls_per_context"),
        DEFAULT_BROWSER_CONFIG["max_urls_per_context"],
        0,
    )
    config["max_minutes_per_context"] = _coerce_int(
        config.get("max_minutes_per_context"),
        DEFAULT_BROWSER_CONFIG["max_minutes_per_context"],
        0,
    )
    config["consecutive_result_403_threshold"] = _coerce_int(
        config.get("consecutive_result_403_threshold"),
        DEFAULT_BROWSER_CONFIG["consecutive_result_403_threshold"],
        1,
    )
    config["consecutive_navigation_failure_threshold"] = _coerce_int(
        config.get("consecutive_navigation_failure_threshold"),
        DEFAULT_BROWSER_CONFIG["consecutive_navigation_failure_threshold"],
        1,
    )
    config["risk_page_text_candidates"] = _normalize_string_list(
        config.get("risk_page_text_candidates"),
        DEFAULT_BROWSER_CONFIG["risk_page_text_candidates"],
    )
    config["drop_cache_control_headers"] = bool(config.get("drop_cache_control_headers", True))

    accept_language = str(config.get("accept_language") or "").strip()
    config["accept_language"] = accept_language or DEFAULT_BROWSER_CONFIG["accept_language"]

    warmup_entry_url = str(config.get("warmup_entry_url") or "").strip()
    config["warmup_entry_url"] = warmup_entry_url or None

    proxy_server = str(
        os.environ.get("KLOOK_BROWSER_PROXY_SERVER")
        or config.get("proxy_server")
        or DEFAULT_KLOOK_BROWSER_PROXY_SERVER
    ).strip()
    parsed_proxy = urlsplit(proxy_server)
    if parsed_proxy.scheme not in {"http", "https", "socks5"} or not parsed_proxy.hostname:
        raise ValueError(
            "Klook 浏览器代理格式无效，请使用 http://、https:// 或 socks5:// 地址"
        )
    config["proxy_server"] = proxy_server
    config["proxy_username"] = str(
        os.environ.get("KLOOK_BROWSER_PROXY_USERNAME")
        or config.get("proxy_username")
        or ""
    ).strip() or None
    config["proxy_password"] = str(
        os.environ.get("KLOOK_BROWSER_PROXY_PASSWORD")
        or config.get("proxy_password")
        or ""
    ).strip() or None

    transient_params = config.get("transient_query_params")
    if not isinstance(transient_params, list):
        transient_params = DEFAULT_BROWSER_CONFIG["transient_query_params"]
    config["transient_query_params"] = [
        str(item).strip()
        for item in transient_params
        if str(item).strip()
    ]
    unsafe_options = []
    if not config["reuse_page_across_dates"]:
        unsafe_options.append("reuse_page_across_dates=false")
    if config["rebuild_runtime_on_crash"]:
        unsafe_options.append("rebuild_runtime_on_crash=true")
    if config["profile_rebuild_after_context_failures"]:
        unsafe_options.append("profile_rebuild_after_context_failures>0")
    if config["max_date_pairs_per_context"]:
        unsafe_options.append("max_date_pairs_per_context>0")
    if config["max_urls_per_context"]:
        unsafe_options.append("max_urls_per_context>0")
    if config["max_minutes_per_context"]:
        unsafe_options.append("max_minutes_per_context>0")
    if unsafe_options:
        raise ValueError(
            "Klook 安全基线禁止自动轮换或重建浏览器/Profile: "
            + ", ".join(unsafe_options)
        )
    return config


def _build_playwright_proxy(browser_options):
    proxy = {"server": browser_options["proxy_server"]}
    if browser_options.get("proxy_username"):
        proxy["username"] = browser_options["proxy_username"]
    if browser_options.get("proxy_password"):
        proxy["password"] = browser_options["proxy_password"]
    return proxy


async def _verify_context_egress(browser_context, browser_options):
    """每次 Klook 导航前确认 Clash 仍使用启动时的固定节点。"""
    try:
        route_report = await verify_clash_route(browser_options["proxy_server"])
    except KlookNetworkGuardError as exc:
        raise NonRetryableSessionInterceptionError(
            f"Klook 出口门禁失败: {exc}",
            metadata={
                "session_state": "EGRESS_UNSAFE",
                "non_retryable": True,
            },
        ) from exc

    expected_fingerprint = getattr(
        browser_context,
        "_klook_clash_proxy_fingerprint",
        None,
    )
    if (
        expected_fingerprint
        and route_report["selected_proxy_fingerprint"] != expected_fingerprint
    ):
        raise NonRetryableSessionInterceptionError(
            "Clash 固定节点在任务中发生变化，已在请求 Klook 前停止",
            metadata={
                "session_state": "EGRESS_CHANGED",
                "non_retryable": True,
            },
        )


async def normalize_klook_international_cookies(browser_context):
    """定向修复 Klook 国际站路由 Cookie，保留 DataDome 等其他状态。"""
    cookies = await browser_context.cookies()
    redirect_cookies = [
        cookie for cookie in cookies if cookie.get("name") == KLOOK_REDIRECT_COOKIE
    ]
    if redirect_cookies:
        await browser_context.clear_cookies(name=KLOOK_REDIRECT_COOKIE)
        log_info("已清理 Klook 国内站路由 Cookie klk_rdc")

    preference_cookies = [
        cookie for cookie in cookies if cookie.get("name") == KLOOK_SITE_PREFERENCE_COOKIE
    ]
    preference_is_canonical = (
        len(preference_cookies) == 1
        and preference_cookies[0].get("domain") == ".klook.com"
        and preference_cookies[0].get("path") == "/"
        and preference_cookies[0].get("value") == "1"
    )
    if preference_is_canonical:
        return

    if preference_cookies:
        await browser_context.clear_cookies(name=KLOOK_SITE_PREFERENCE_COOKIE)
    await browser_context.add_cookies(
        [
            {
                "name": KLOOK_SITE_PREFERENCE_COOKIE,
                "value": "1",
                "domain": ".klook.com",
                "path": "/",
                "expires": int(time.time()) + 365 * 24 * 60 * 60,
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            }
        ]
    )
    log_info("已设置 Klook 国际站路由 Cookie klk_ps=1")


def _build_page_headers(source_country, currency, browser_options):
    headers = {
        "currency": currency,
        "x-klook-user-residence": source_country,
        "Accept": "application/json, text/plain, */*",
    }
    accept_language = str((browser_options or {}).get("accept_language") or "").strip()
    if accept_language:
        headers["Accept-Language"] = accept_language
    if not bool((browser_options or {}).get("drop_cache_control_headers", True)):
        headers["Cache-Control"] = "no-cache"
        headers["Pragma"] = "no-cache"
    return headers


def _is_navigation_context_error(exc):
    error_text = str(exc)
    return (
        "Execution context was destroyed" in error_text
        or "most likely because of a navigation" in error_text
    )


def _is_connection_closed_error(exc):
    return "ERR_CONNECTION_CLOSED" in str(exc)


def _extract_risk_text_hits(text, candidates):
    normalized_text = str(text or "").lower()
    hits = []
    for candidate in candidates or []:
        candidate_text = str(candidate or "").strip()
        if candidate_text and candidate_text.lower() in normalized_text:
            hits.append(candidate_text)
    return hits


def _extract_risk_identifier(text):
    match = re.search(r"(?:风险ID|Risk ID|RISK ID)\s*[:：]?\s*([A-Za-z0-9-]{6,})", str(text or ""))
    if match:
        return match.group(1)
    return None


def _get_browser_context_metadata(browser_context):
    profile_dir = getattr(browser_context, "_klook_profile_dir", None)
    created_monotonic = getattr(browser_context, "_klook_context_created_monotonic", None)
    context_age_seconds = None
    if created_monotonic is not None:
        try:
            context_age_seconds = round(max(0.0, time.monotonic() - created_monotonic), 2)
        except Exception:
            context_age_seconds = None
    return {
        "current_profile_dir": profile_dir,
        "context_age_seconds": context_age_seconds,
    }


async def _detect_risk_page(page, browser_options):
    candidates = list(
        (browser_options or {}).get("risk_page_text_candidates")
        or DEFAULT_RISK_PAGE_TEXT_CANDIDATES
    )
    surfaces = []
    try:
        frames = list(page.frames)
    except Exception:
        frames = []

    for index, frame in enumerate(frames[:20]):
        try:
            frame_url = frame.url
        except Exception:
            frame_url = ""
        inspect_text = (
            index == 0
            or _is_klook_cn_url(frame_url)
            or _host_matches_domain(_url_hostname(frame_url), "captcha-delivery.com")
        )
        title = ""
        body_text = ""
        if inspect_text:
            try:
                title = await frame.title()
            except Exception:
                title = ""
            try:
                body_text = await frame.evaluate(
                    """
                    () => {
                        const text = (document.body && (document.body.innerText || document.body.textContent)) || "";
                        return text.slice(0, 4000);
                    }
                    """
                )
            except Exception:
                body_text = ""
        surfaces.append(
            {
                "label": "main" if index == 0 else f"frame[{index}]",
                "url": str(frame_url or ""),
                "title": str(title or "")[:300],
                "body_excerpt": str(body_text or "")[:1000],
                "_body_for_detection": str(body_text or ""),
            }
        )

    if not surfaces:
        try:
            page_url = page.url
        except Exception:
            page_url = ""
        try:
            title = await page.title()
        except Exception:
            title = ""
        surfaces.append(
            {
                "label": "main",
                "url": str(page_url or ""),
                "title": str(title or "")[:300],
                "body_excerpt": "",
                "_body_for_detection": "",
            }
        )

    combined_text = "\n".join(
        "\n".join([item["title"], item["_body_for_detection"]])
        for item in surfaces
    )
    risk_text_hits = _extract_risk_text_hits(combined_text, candidates)
    risk_text_hits = [hit for hit in risk_text_hits if str(hit).strip() != "403"]
    risk_id = _extract_risk_identifier(combined_text)
    rate_limit_hits = [
        hit
        for hit in risk_text_hits
        if str(hit).strip().lower() in {"too many requests", "请求过于频繁"}
    ]
    hard_block_text_hits = [
        hit
        for hit in risk_text_hits
        if str(hit).strip().lower()
        not in {"risk id", "风险id", "too many requests", "请求过于频繁"}
    ]
    main_url = surfaces[0]["url"]
    domestic_urls = [item["url"] for item in surfaces if _is_klook_cn_url(item["url"])]
    captcha_urls = [item["url"] for item in surfaces if _is_captcha_frame_url(item["url"])]
    interstitial_urls = [
        item["url"]
        for item in surfaces
        if _is_auto_interstitial_frame_url(item["url"])
    ]

    state = None
    if domestic_urls:
        state = "DOMESTIC_REDIRECT"
    elif hard_block_text_hits:
        state = "L2_HARD_BLOCK"
    elif captcha_urls:
        state = "L1_SLIDER"
    elif interstitial_urls:
        state = "AUTO_INTERSTITIAL"
    elif rate_limit_hits:
        state = "RATE_LIMITED"
    elif main_url and not _is_klook_com_url(main_url):
        state = "INVALID_FINAL_DOMAIN"
    elif not main_url:
        state = "UNKNOWN"

    non_retryable_states = {
        "DOMESTIC_REDIRECT",
        "L1_SLIDER",
        "L2_HARD_BLOCK",
        "RATE_LIMITED",
        "INVALID_FINAL_DOMAIN",
        "UNKNOWN",
    }

    public_surfaces = []
    for item in surfaces[:10]:
        public_surfaces.append(
            {
                "label": item["label"],
                "url": _redact_url(item["url"]),
                "title": item["title"],
                "body_excerpt": item["body_excerpt"],
            }
        )

    return {
        "risk_page_detected": bool(state),
        "non_retryable": state in non_retryable_states,
        "session_state": state or "NORMAL",
        "risk_text_hits": risk_text_hits,
        "risk_id": risk_id,
        "page_title": surfaces[0]["title"],
        "page_url": _redact_url(main_url),
        "domestic_urls": [_redact_url(item) for item in domestic_urls[:10]],
        "captcha_urls": [_redact_url(item) for item in captcha_urls[:10]],
        "interstitial_urls": [_redact_url(item) for item in interstitial_urls[:10]],
        "surfaces": public_surfaces,
    }


async def _wait_for_actionable_page_state(page, browser_options):
    """自动过渡页只在原页等待；不刷新、不重建 Page。"""
    risk_info = await _detect_risk_page(page, browser_options)
    if risk_info.get("session_state") != "AUTO_INTERSTITIAL":
        return risk_info

    log_warning("检测到 DataDome 自动过渡页，原页等待最多 30 秒")
    deadline = time.monotonic() + 30
    healthy_since = None
    while time.monotonic() < deadline:
        await asyncio.sleep(1)
        risk_info = await _detect_risk_page(page, browser_options)
        state = risk_info.get("session_state")
        if state == "NORMAL":
            healthy_since = healthy_since or time.monotonic()
            if time.monotonic() - healthy_since >= 2:
                log_success("DataDome 自动过渡页已稳定恢复")
                return risk_info
            continue
        healthy_since = None
        if state not in {"AUTO_INTERSTITIAL", "UNKNOWN"}:
            return risk_info

    timeout_info = dict(risk_info or {})
    timeout_info.update(
        {
            "risk_page_detected": True,
            "non_retryable": True,
            "session_state": "UNKNOWN",
            "risk_text_hits": list(timeout_info.get("risk_text_hits") or []),
        }
    )
    return timeout_info


def _build_session_interception_error(message, payload, partial_data=False):
    error_type = (
        NonRetryableSessionInterceptionError
        if (payload or {}).get("non_retryable")
        else SessionInterceptionError
    )
    return error_type(message, partial_data=partial_data, metadata=payload)


async def _capture_debug_snapshot(page, reason, metadata=None):
    try:
        os.makedirs(get_screenshots_dir(), exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_reason = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in str(reason))[:40]
        screenshot_path = os.path.join(get_screenshots_dir(), f"debug_{safe_reason}_{timestamp}.png")
        metadata_path = os.path.join(get_screenshots_dir(), f"debug_{safe_reason}_{timestamp}.json")

        title = ""
        current_url = ""
        ready_state = "unknown"

        if page and not page.is_closed():
            try:
                title = await page.title()
            except Exception:
                title = ""
            try:
                current_url = page.url
            except Exception:
                current_url = ""
            try:
                ready_state = await page.evaluate("document.readyState")
            except Exception:
                ready_state = "unknown"
            try:
                await page.screenshot(path=screenshot_path, full_page=False)
            except Exception:
                screenshot_path = ""

        snapshot_metadata = dict(metadata or {})
        snapshot_metadata.update(
            {
                "reason": reason,
                "title": title,
                "url": _redact_url(current_url),
                "ready_state": ready_state,
                "screenshot_path": screenshot_path,
                "captured_at": datetime.now().isoformat(),
            }
        )
        with open(metadata_path, "w", encoding="utf-8") as file:
            json.dump(snapshot_metadata, file, ensure_ascii=False, indent=2)

        log_warning(
            f"页面诊断快照: reason={reason}, title={title}, readyState={ready_state}, "
            f"url={_redact_url(current_url)}, screenshot={screenshot_path}, metadata={metadata_path}"
        )
        return metadata_path
    except Exception as exc:
        log_warning(f"保存页面诊断快照失败: {exc}")
        return None


async def process_single_url_with_browser(
    browser_context: BrowserContext,
    url_info,
    all_results,
    source_country="None",
    expires_timestamp=None,
    p_date=None,
    d_date=None,
    csv_filename="output.csv",
    currency="None",
    record_abnormal=True,
    max_duration_seconds=DEFAULT_URL_DATE_TIMEOUT_SECONDS,
    retry_count=0,
    max_retries=3,
    page_timing=None,
    browser_config=None,
):
    """处理单个 URL，复用外部浏览器上下文。"""
    del expires_timestamp

    url = url_info.get("url", "")
    city_name = url_info.get("city_name", "未知城市")
    poi_name = url_info.get("poi_name", "未知地点")
    timing = _normalize_page_timing_config(page_timing)
    browser_options = _normalize_browser_config(browser_config)
    headers = _build_page_headers(source_country, currency, browser_options)

    settle_seconds = timing["initial_page_settle_seconds"]
    empty_response_reload_limit = timing["empty_response_reload_limit"]
    no_response_reload_seconds = timing["no_response_reload_seconds"]
    no_new_data_break_seconds = timing["no_new_data_break_seconds"]
    scroll_pause_min_seconds = timing["scroll_pause_min_seconds"]
    scroll_pause_max_seconds = timing["scroll_pause_max_seconds"]
    min_scroll_observe_seconds = timing["min_scroll_observe_seconds"]
    loading_clear_grace_seconds = timing["loading_clear_grace_seconds"]
    result_stable_seconds = timing["result_stable_seconds"]
    loading_stuck_reload_seconds = timing["loading_stuck_reload_seconds"]
    loading_height_change_threshold_px = timing["loading_height_change_threshold_px"]
    loading_selector_candidates = timing["loading_selector_candidates"]
    loading_text_candidates = timing["loading_text_candidates"]

    if retry_count > 0:
        log_info(f"\n{'#' * 60}")
        log_info(f"第 {retry_count}/{max_retries} 次重试: {city_name} - {poi_name}")
        log_info(f"客源地: {source_country}, 日期对: {p_date} -> {d_date}")
        log_info(f"{'#' * 60}\n")

    start_monotonic = time.monotonic()

    pick_value = None
    if "pick=" in url:
        try:
            pick_start = url.index("pick=") + 5
            pick_end = url.find("&", pick_start)
            if pick_end == -1:
                pick_end = len(url)
            pick_value = urllib.parse.unquote(url[pick_start:pick_end])
        except Exception as exc:
            log_warning(f"提取 pick 值时出错: {exc}")

    if p_date and d_date:
        url = update_url_dates(
            url,
            p_date,
            d_date,
            strip_transient_query_params=browser_options["strip_transient_query_params"],
            transient_query_params=browser_options["transient_query_params"],
        )

    if not _is_klook_com_url(url):
        raise NonRetryableSessionInterceptionError(
            "目标 URL 不是受支持的 Klook 国际站 HTTPS 地址，已停止本次任务",
            metadata={
                "session_state": "INVALID_INPUT_DOMAIN",
                "non_retryable": True,
                "url": url,
            },
        )

    await _verify_context_egress(browser_context, browser_options)

    log_info(f"\n{'=' * 60}")
    log_info(f"开始处理 {city_name} - {poi_name} (客源地: {source_country}, 货币: {currency})")
    log_info(f"取车点: {pick_value if pick_value else '未指定'}")
    log_info(f"URL: {_redact_url(url)}")
    log_info(f"{'=' * 60}\n")

    page = None
    event_tasks = set()
    response_handler = None
    request_handler = None
    request_failed_handler = None

    total_response_count = 0
    useful_response_count = 0
    last_response_monotonic = None
    last_successful_result_response_monotonic = None
    consecutive_result_403_count = 0
    consecutive_navigation_failures = 0
    session_risk_hit_count = 0
    pending_interception = None
    staged_results = []
    session_intercepted_partial = False
    page_recovery_attempt_count = 0

    def build_diagnostic_metadata(extra=None):
        metadata = {
            "source_country": source_country,
            "currency": currency,
            "pick": pick_value,
            "url": _redact_url(url),
            "date_pair": [p_date, d_date],
            "city_name": city_name,
            "poi_name": poi_name,
            "retry_count": retry_count,
            "consecutive_result_403_count": consecutive_result_403_count,
            "consecutive_navigation_failures": consecutive_navigation_failures,
            "session_risk_hit_count": session_risk_hit_count,
            "last_successful_result_response_monotonic": last_successful_result_response_monotonic,
            "captured_result_response_count": len(staged_results),
            "session_intercepted_partial": session_intercepted_partial,
        }
        metadata.update(_get_browser_context_metadata(browser_context))
        if isinstance(extra, dict):
            metadata.update(extra)
        return metadata

    def reset_result_buffer(reset_partial_state=False):
        nonlocal total_response_count
        nonlocal useful_response_count
        nonlocal last_response_monotonic
        nonlocal last_successful_result_response_monotonic
        nonlocal consecutive_result_403_count
        nonlocal pending_interception
        nonlocal session_intercepted_partial

        staged_results.clear()
        total_response_count = 0
        useful_response_count = 0
        last_response_monotonic = None
        last_successful_result_response_monotonic = None
        consecutive_result_403_count = 0
        pending_interception = None
        if reset_partial_state:
            session_intercepted_partial = False

    def mark_pending_interception(reason, extra=None):
        nonlocal pending_interception
        nonlocal session_intercepted_partial
        if staged_results:
            session_intercepted_partial = True
        payload = build_diagnostic_metadata(extra)
        payload["reason"] = reason
        pending_interception = payload

    def track_task(coro):
        task = asyncio.create_task(coro)
        event_tasks.add(task)

        def _done(done_task):
            event_tasks.discard(done_task)
            try:
                done_task.result()
            except Exception as exc:
                log_warning(f"页面事件处理异常: {exc}")

        task.add_done_callback(_done)

    async def handle_response(response):
        nonlocal total_response_count
        nonlocal useful_response_count
        nonlocal last_response_monotonic
        nonlocal last_successful_result_response_monotonic
        nonlocal consecutive_result_403_count
        nonlocal consecutive_navigation_failures
        nonlocal pending_interception
        nonlocal page_recovery_attempt_count

        response_url = response.url
        if response.status == 429 and _is_klook_guarded_service_url(response_url):
            mark_pending_interception(
                "Klook 或 DataDome 返回 HTTP 429，已停止任务并等待人工恢复出口",
                {
                    "risk_page_detected": True,
                    "risk_text_hits": ["HTTP 429"],
                    "trigger": "rate_limited_response",
                    "session_state": "RATE_LIMITED",
                    "non_retryable": True,
                    "response_status": 429,
                },
            )
            return
        if not _is_target_result_api_url(response_url):
            return

        if not _is_allowed_target_result_api_url(response_url):
            mark_pending_interception(
                "租车结果接口不是 Klook 国际站域名，拒绝接收本次数据",
                {
                    "risk_page_detected": True,
                    "risk_text_hits": [],
                    "trigger": "invalid_result_api_domain",
                    "session_state": "DOMESTIC_REDIRECT"
                    if _is_klook_cn_url(response_url)
                    else "INVALID_RESULT_API_DOMAIN",
                    "non_retryable": True,
                    "response_url": _redact_url(response_url),
                    "response_status": response.status,
                },
            )
            return

        total_response_count += 1
        last_response_monotonic = time.monotonic()
        log_info(f"[目标接口状态] status={response.status}")
        log_info(f"[接口触发] {_redact_url(response_url)}")

        if response.status == 403:
            consecutive_result_403_count += 1
            log_warning(f"目标结果接口连续 403 次数: {consecutive_result_403_count}")
            if consecutive_result_403_count >= browser_options["consecutive_result_403_threshold"]:
                mark_pending_interception(
                    f"目标结果接口连续返回 403（阈值 {browser_options['consecutive_result_403_threshold']}）",
                    {
                        "risk_page_detected": True,
                        "risk_text_hits": [],
                        "trigger": "result_403_threshold",
                        "session_state": "UNKNOWN",
                        "non_retryable": True,
                        "response_status": response.status,
                    },
                )
            return

        consecutive_result_403_count = 0
        consecutive_navigation_failures = 0
        if not (pending_interception or {}).get("non_retryable"):
            pending_interception = None
        if page_recovery_attempt_count:
            log_info("目标接口已恢复正常响应，重置 page 级恢复计数")
            page_recovery_attempt_count = 0

        try:
            json_data = await response.json()
        except Exception as exc:
            log_error(f"解析接口响应时出错: {exc}")
            return

        if pick_value:
            if isinstance(json_data, dict):
                json_data["pick_value"] = pick_value
            else:
                json_data = {"original_data": json_data, "pick_value": pick_value}

        if isinstance(json_data, dict):
            json_data["source_country"] = source_country
            result = json_data.get("result")
            if isinstance(result, dict) and result.get("has_more") is True:
                log_info("当前接口返回 has_more=true，继续等待后续结果，不再提前判定完成")

        try:
            process_data(json_data)
        except Exception as exc:
            log_warning(f"process_data 预处理失败，但仍保留原始响应用于后续落盘: {exc}")

        staged_results.append(json_data)
        useful_response_count += 1
        last_successful_result_response_monotonic = time.monotonic()
        log_success(
            f"已暂存结果接口数据（当前日期组 {len(staged_results)} 个响应包，正式完成后再统一写入 CSV）"
        )

    async def handle_request_failed(request):
        failure = request.failure
        if callable(failure):
            try:
                failure = failure()
            except Exception:
                failure = None
        failure_text = json.dumps(failure, ensure_ascii=False) if isinstance(failure, dict) else str(failure or "unknown")

        request_url = request.url
        is_target_api = _is_target_result_api_url(request_url)
        if is_target_api:
            log_warning(
                f"[目标接口请求失败] failure={failure_text} url={_redact_url(request_url)}"
            )

        if request.is_navigation_request() and "ERR_CONNECTION_CLOSED" in failure_text:
            log_warning("检测到导航请求连接被关闭，等待显式的 Page.goto/Page.reload 结果确认是否需要升级恢复")

    def handle_request(request):
        request_url = request.url
        if request.is_navigation_request() and _is_klook_cn_url(request_url):
            mark_pending_interception(
                "检测到页面导航至 Klook 国内站，已停止本次任务",
                {
                    "risk_page_detected": True,
                    "risk_text_hits": [],
                    "trigger": "domestic_navigation_request",
                    "session_state": "DOMESTIC_REDIRECT",
                    "non_retryable": True,
                    "request_url": _redact_url(request_url),
                },
            )
        if _is_target_result_api_url(request_url) and not _is_allowed_target_result_api_url(
            request_url
        ):
            mark_pending_interception(
                "检测到非国际站租车结果接口请求，已停止本次任务",
                {
                    "risk_page_detected": True,
                    "risk_text_hits": [],
                    "trigger": "invalid_result_api_request_domain",
                    "session_state": "DOMESTIC_REDIRECT"
                    if _is_klook_cn_url(request_url)
                    else "INVALID_RESULT_API_DOMAIN",
                    "non_retryable": True,
                    "request_url": _redact_url(request_url),
                },
            )

    def response_handler_wrapper(response):
        track_task(handle_response(response))

    def request_failed_handler_wrapper(request):
        track_task(handle_request_failed(request))

    response_handler = response_handler_wrapper
    request_handler = handle_request
    request_failed_handler = request_failed_handler_wrapper

    async def detach_page_handlers(target_page):
        if not target_page:
            return
        try:
            target_page.remove_listener("response", response_handler)
        except Exception:
            pass
        try:
            target_page.remove_listener("request", request_handler)
        except Exception:
            pass
        try:
            target_page.remove_listener("requestfailed", request_failed_handler)
        except Exception:
            pass

    async def prepare_page(new_page):
        await new_page.bring_to_front()
        await new_page.set_extra_http_headers(headers)
        new_page.set_default_timeout(120000)
        new_page.on("response", response_handler)
        new_page.on("request", request_handler)
        new_page.on("requestfailed", request_failed_handler)
        return new_page

    async def create_or_replace_page(close_existing):
        nonlocal page

        old_page = page
        new_page = await browser_context.new_page()
        await prepare_page(new_page)
        page = new_page

        if close_existing and old_page and not old_page.is_closed():
            await detach_page_handlers(old_page)
            try:
                await old_page.close()
            except Exception as exc:
                log_warning(f"关闭旧页面失败: {exc}")

        return page

    async def capture_page_state():
        return await page.evaluate(
            """
            (params) => {
                const selectors = Array.isArray(params.selectors) ? params.selectors : [];
                const texts = Array.isArray(params.texts) ? params.texts : [];

                function isVisible(element) {
                    if (!element) {
                        return false;
                    }
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return (
                        style.display !== "none" &&
                        style.visibility !== "hidden" &&
                        style.opacity !== "0" &&
                        rect.width > 0 &&
                        rect.height > 0
                    );
                }

                const selectorVisible = selectors.some((selector) => {
                    const element = document.querySelector(selector);
                    return isVisible(element);
                });

                let textVisible = false;
                const root = document.body || document.documentElement;
                if (root && texts.length > 0) {
                    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
                    let currentNode = walker.currentNode;
                    while (currentNode) {
                        if (isVisible(currentNode)) {
                            const text = (currentNode.innerText || currentNode.textContent || "").trim();
                            if (texts.some((candidate) => candidate && text.includes(candidate))) {
                                textVisible = true;
                                break;
                            }
                        }
                        currentNode = walker.nextNode();
                    }
                }

                const docEl = document.documentElement;
                const body = document.body;
                const scrollHeight = Math.max(
                    docEl ? docEl.scrollHeight : 0,
                    body ? body.scrollHeight : 0
                );

                return {
                    loading_visible: selectorVisible || textVisible,
                    scroll_height: scrollHeight,
                    ready_state: document.readyState || "unknown",
                };
            }
            """,
            {
                "selectors": loading_selector_candidates,
                "texts": loading_text_candidates,
            },
        )

    def reset_observation_state():
        now = time.monotonic()
        return (
            total_response_count,
            now,
            None,
            None,
            now,
            None,
        )

    async def check_pending_interception(trigger_reason):
        if not pending_interception:
            return None
        payload = dict(pending_interception)
        payload["trigger_reason"] = trigger_reason
        payload["risk_page_detected"] = bool(payload.get("risk_page_detected"))
        payload["risk_text_hits"] = list(payload.get("risk_text_hits") or [])
        return payload

    async def check_page_risk(trigger_reason):
        risk_info = await _wait_for_actionable_page_state(page, browser_options)
        if risk_info["risk_page_detected"]:
            payload = build_diagnostic_metadata(risk_info)
            payload["trigger_reason"] = trigger_reason
            return payload
        return None

    async def wait_after_navigation(is_reload):
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
            log_success("页面网络空闲")
        except Exception as exc:
            log_warning(f"等待页面 networkidle 超时，但继续执行: {exc}")

        if page.is_closed():
            return False

        risk_payload = await check_page_risk("reload_post_navigation" if is_reload else "goto_post_navigation")
        if risk_payload:
            raise _build_session_interception_error(
                "目标页面打开后立即命中 Klook 风险拦截页",
                risk_payload,
                partial_data=session_intercepted_partial or bool(staged_results),
            )

        await asyncio.sleep(settle_seconds)

        risk_payload = await check_page_risk("post_settle")
        if risk_payload:
            raise _build_session_interception_error(
                "目标页面静置后命中 Klook 风险拦截页",
                risk_payload,
                partial_data=session_intercepted_partial or bool(staged_results),
            )

        if is_reload:
            log_info(f"页面刷新恢复成功，重新静置 {settle_seconds} 秒")
        else:
            log_info(f"页面加载完成，开始静置 {settle_seconds} 秒")
        log_info("静置完成，开始滚动采集")
        return True

    def note_navigation_failure(exc, action):
        nonlocal consecutive_navigation_failures

        if _is_connection_closed_error(exc):
            consecutive_navigation_failures += 1
            log_warning(
                f"{action} 出现 ERR_CONNECTION_CLOSED，连续导航失败次数: {consecutive_navigation_failures}"
            )
        else:
            log_warning(f"{action} 失败: {exc}")

        if consecutive_navigation_failures >= browser_options["consecutive_navigation_failure_threshold"]:
            raise SessionInterceptionError(
                f"{action} 连续出现导航失败，疑似当前会话已被风控污染",
                partial_data=session_intercepted_partial or bool(staged_results),
                metadata=build_diagnostic_metadata(
                    {
                        "risk_page_detected": False,
                        "risk_text_hits": [],
                        "trigger": "navigation_failure_threshold",
                        "navigation_action": action,
                        "navigation_error": str(exc),
                    }
                ),
            )

    def note_navigation_success():
        nonlocal consecutive_navigation_failures
        consecutive_navigation_failures = 0

    async def open_results_page():
        log_info(f"正在加载目标页面: {city_name} - {poi_name}")
        try:
            await _verify_context_egress(browser_context, browser_options)
            await normalize_klook_international_cookies(browser_context)
            navigation_response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=120000,
            )
            if navigation_response and navigation_response.status == 429:
                raise NonRetryableSessionInterceptionError(
                    "Klook 主导航返回 HTTP 429，已停止任务",
                    metadata={
                        "session_state": "RATE_LIMITED",
                        "non_retryable": True,
                        "response_status": 429,
                    },
                )
            note_navigation_success()
            log_success("目标页面加载完成")
            return await wait_after_navigation(False)
        except SessionInterceptionError:
            raise
        except Exception as exc:
            note_navigation_failure(exc, "Page.goto")
            log_error(f"目标页面加载失败，本时间点不做快速重试: {exc}")
            return False

    async def reload_results_page(reload_index, reason):
        nonlocal page

        if reason == "loading_stuck":
            log_warning(
                f"当前页面连续载入中超过 {int(loading_stuck_reload_seconds)} 秒且无进展，"
                f"执行第 {reload_index}/{empty_response_reload_limit} 次重新加载"
            )
        else:
            log_warning(
                f"当前页面滚动 {int(no_response_reload_seconds)} 秒后仍无接口返回，"
                f"执行第 {reload_index}/{empty_response_reload_limit} 次重新加载"
            )

        try:
            await normalize_klook_international_cookies(browser_context)
            if reload_index >= 2:
                log_warning("当前页面恢复不稳定，关闭旧 page 并在同一 context 内重开目标页")
                await create_or_replace_page(close_existing=True)
                reset_result_buffer(reset_partial_state=True)
                return await open_results_page()

            await page.reload(wait_until="domcontentloaded", timeout=120000)
            note_navigation_success()
            return await wait_after_navigation(True)
        except SessionInterceptionError:
            raise
        except Exception as exc:
            note_navigation_failure(exc, "Page.reload")
            log_warning(f"页面刷新失败，尝试重新打开目标页面: {exc}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=120000)
                note_navigation_success()
                log_success("目标页面重新打开成功")
                return await wait_after_navigation(True)
            except SessionInterceptionError:
                raise
            except Exception as goto_exc:
                note_navigation_failure(goto_exc, "Page.goto after reload")
                log_error(f"重新打开目标页面失败: {goto_exc}")
                return False

    async def upgrade_page_once_or_raise(trigger_reason, payload):
        nonlocal session_risk_hit_count
        nonlocal page_recovery_attempt_count

        soft_page_recovery_limit = 2
        session_risk_hit_count += 1
        diagnostic_payload = dict(payload or {})
        diagnostic_payload["trigger_reason"] = trigger_reason
        diagnostic_payload["session_risk_hit_count"] = session_risk_hit_count
        diagnostic_path = await _capture_debug_snapshot(page, trigger_reason, diagnostic_payload)
        diagnostic_payload["diagnostic_path"] = diagnostic_path

        if diagnostic_payload.get("non_retryable"):
            staged_results.clear()
            raise NonRetryableSessionInterceptionError(
                f"检测到不可自动重试的 Klook 会话状态: {trigger_reason}",
                partial_data=session_intercepted_partial,
                metadata=diagnostic_payload,
            )

        if page_recovery_attempt_count >= soft_page_recovery_limit:
            raise SessionInterceptionError(
                f"会话风险信号重复出现，升级到 context/profile 恢复链: {trigger_reason}",
                partial_data=session_intercepted_partial or bool(staged_results),
                metadata=diagnostic_payload,
            )

        page_recovery_attempt_count += 1
        log_warning("检测到会话污染信号，先关闭当前 page 并在同一 context 内重试当前日期组")
        reset_result_buffer(reset_partial_state=True)
        try:
            await create_or_replace_page(close_existing=True)
            page_ready = await open_results_page()
            if not page_ready:
                raise RuntimeError("page 级恢复后仍无法打开目标页面")
            return True
        except SessionInterceptionError:
            raise
        except Exception as exc:
            diagnostic_payload["page_recovery_error"] = str(exc)
            raise SessionInterceptionError(
                f"page 级恢复失败，需要升级到 context/profile: {exc}",
                partial_data=session_intercepted_partial or bool(staged_results),
                metadata=diagnostic_payload,
            )

    try:
        open_pages = [item for item in browser_context.pages if not item.is_closed()]
        if browser_options["reuse_page_across_dates"] and open_pages:
            page = open_pages[-1]
            log_info("复用现有浏览器页面")
            await prepare_page(page)
        else:
            if open_pages:
                log_info(f"发现 {len(open_pages)} 个旧页面，创建新页面后关闭旧页面")
            await create_or_replace_page(close_existing=False)
            for old_page in open_pages:
                if old_page == page or old_page.is_closed():
                    continue
                await detach_page_handlers(old_page)
                try:
                    await old_page.close()
                except Exception as exc:
                    log_warning(f"关闭旧页面失败: {exc}")

        page_ready = await open_results_page()
        if not page_ready:
            raise UrlNoDataError(f"{city_name}-{poi_name} 页面加载失败")

        reload_attempt = 0
        (
            load_response_baseline,
            scroll_phase_started_at,
            loading_visible_since,
            loading_cleared_since,
            last_dom_growth_monotonic,
            last_scroll_height,
        ) = reset_observation_state()

        while True:
            elapsed_total = time.monotonic() - start_monotonic
            if elapsed_total > max_duration_seconds:
                if record_abnormal:
                    save_abnormal_state(
                        url_info,
                        source_country,
                        p_date,
                        d_date,
                        currency,
                        f"执行超时（>{max_duration_seconds // 60} 分钟），重试次数: {retry_count}",
                    )
                raise UrlDateTimeoutError(
                    f"{city_name}-{poi_name} 在日期对 {p_date} -> {d_date} 执行超过 {max_duration_seconds} 秒"
                )

            if page.is_closed():
                raise SessionInterceptionError(
                    "当前 page 被关闭，无法继续抓取",
                    partial_data=session_intercepted_partial or bool(staged_results),
                    metadata=build_diagnostic_metadata(
                        {
                            "risk_page_detected": False,
                            "risk_text_hits": [],
                            "trigger": "page_closed",
                        }
                    ),
                )

            pending_payload = await check_pending_interception("event_listener")
            if pending_payload:
                await upgrade_page_once_or_raise("event_listener", pending_payload)
                reload_attempt = 0
                (
                    load_response_baseline,
                    scroll_phase_started_at,
                    loading_visible_since,
                    loading_cleared_since,
                    last_dom_growth_monotonic,
                    last_scroll_height,
                ) = reset_observation_state()
                continue

            try:
                risk_payload = await check_page_risk("scroll_loop")
            except Exception as exc:
                log_warning(f"风险页检查失败，继续按普通页面状态处理: {exc}")
                risk_payload = None
            if risk_payload:
                await upgrade_page_once_or_raise("scroll_loop", risk_payload)
                reload_attempt = 0
                (
                    load_response_baseline,
                    scroll_phase_started_at,
                    loading_visible_since,
                    loading_cleared_since,
                    last_dom_growth_monotonic,
                    last_scroll_height,
                ) = reset_observation_state()
                continue

            try:
                await page.evaluate("window.scrollBy(0, window.innerHeight);")
            except Exception as exc:
                if _is_navigation_context_error(exc):
                    log_warning("页面上下文被销毁，等待重新加载")
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                    await asyncio.sleep(1)
                    continue
                raise

            await asyncio.sleep(random.uniform(scroll_pause_min_seconds, scroll_pause_max_seconds))

            now_monotonic = time.monotonic()
            responses_this_load = total_response_count - load_response_baseline

            try:
                page_state = await capture_page_state()
            except Exception as exc:
                if _is_navigation_context_error(exc):
                    log_warning("页面状态检测时上下文被销毁，等待重新加载")
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                    await asyncio.sleep(1)
                    continue
                raise

            loading_visible = bool(page_state.get("loading_visible"))
            scroll_height = int(page_state.get("scroll_height") or 0)

            if last_scroll_height is None:
                last_scroll_height = scroll_height
            elif abs(scroll_height - last_scroll_height) >= loading_height_change_threshold_px:
                last_scroll_height = scroll_height
                last_dom_growth_monotonic = now_monotonic
                log_info("结果区域仍在增长，继续滚动")

            if loading_visible:
                if loading_visible_since is None:
                    loading_visible_since = now_monotonic
                    log_info("检测到页面仍在载入中，继续等待")
                loading_cleared_since = None
            else:
                if loading_visible_since is not None:
                    loading_visible_since = None
                    loading_cleared_since = now_monotonic
                    log_info("页面载入标记已消失，开始稳定性观察")
                elif loading_cleared_since is None:
                    loading_cleared_since = now_monotonic

            if responses_this_load == 0:
                if now_monotonic - scroll_phase_started_at >= no_response_reload_seconds:
                    if reload_attempt < empty_response_reload_limit:
                        reload_attempt += 1
                        page_ready = await reload_results_page(reload_attempt, "no_response")
                        if not page_ready:
                            raise UrlNoDataError(f"{city_name}-{poi_name} 重新加载后仍无法打开页面")
                        (
                            load_response_baseline,
                            scroll_phase_started_at,
                            loading_visible_since,
                            loading_cleared_since,
                            last_dom_growth_monotonic,
                            last_scroll_height,
                        ) = reset_observation_state()
                        continue

                    await _capture_debug_snapshot(
                        page,
                        "no_response_reload_exhausted",
                        build_diagnostic_metadata(
                            {
                                "risk_page_detected": False,
                                "risk_text_hits": [],
                                "trigger": "no_response_reload_exhausted",
                            }
                        ),
                    )
                    if not staged_results:
                        raise UrlNoDataError(
                            f"{city_name}-{poi_name} 连续重新加载 {empty_response_reload_limit} 次后仍无接口返回"
                        )
                    raise UrlDateTimeoutError(
                        f"{city_name}-{poi_name} 连续重新加载 {empty_response_reload_limit} 次后仍无接口返回，未确认加载完成"
                    )
                continue

            if last_response_monotonic is None:
                continue

            if loading_visible and loading_visible_since is not None:
                no_recent_response = now_monotonic - last_response_monotonic >= no_new_data_break_seconds
                no_recent_dom_growth = now_monotonic - last_dom_growth_monotonic >= result_stable_seconds
                if (
                    now_monotonic - loading_visible_since >= loading_stuck_reload_seconds
                    and no_recent_response
                    and no_recent_dom_growth
                ):
                    if reload_attempt < empty_response_reload_limit:
                        reload_attempt += 1
                        page_ready = await reload_results_page(reload_attempt, "loading_stuck")
                        if not page_ready:
                            raise UrlNoDataError(f"{city_name}-{poi_name} 重新加载后仍无法打开页面")
                        (
                            load_response_baseline,
                            scroll_phase_started_at,
                            loading_visible_since,
                            loading_cleared_since,
                            last_dom_growth_monotonic,
                            last_scroll_height,
                        ) = reset_observation_state()
                        continue

                    await _capture_debug_snapshot(
                        page,
                        "loading_stuck_reload_exhausted",
                        build_diagnostic_metadata(
                            {
                                "risk_page_detected": False,
                                "risk_text_hits": [],
                                "trigger": "loading_stuck_reload_exhausted",
                            }
                        ),
                    )
                    if not staged_results:
                        raise UrlNoDataError(
                            f"{city_name}-{poi_name} 页面长时间处于载入中且无进展，未获取到数据"
                        )
                    raise UrlDateTimeoutError(
                        f"{city_name}-{poi_name} 页面长时间处于载入中且无进展，未确认加载完成"
                    )
                continue

            if now_monotonic - scroll_phase_started_at < min_scroll_observe_seconds:
                continue

            loading_cleared_enough = (
                loading_cleared_since is not None
                and now_monotonic - loading_cleared_since >= loading_clear_grace_seconds
            )
            result_stable_enough = now_monotonic - last_dom_growth_monotonic >= result_stable_seconds
            response_quiet_enough = now_monotonic - last_response_monotonic >= no_new_data_break_seconds

            if loading_cleared_enough and result_stable_enough and response_quiet_enough:
                log_success(
                    f"页面稳定完成（载入已消失，结果区稳定 {int(result_stable_seconds)} 秒，"
                    f"{int(no_new_data_break_seconds)} 秒无新接口数据），切换到下一日期组"
                )
                break

        if event_tasks:
            await asyncio.gather(*list(event_tasks), return_exceptions=True)

        pending_payload = await check_pending_interception("before_save")
        if pending_payload:
            await upgrade_page_once_or_raise("before_save", pending_payload)

        risk_payload = await check_page_risk("before_save")
        if risk_payload:
            await upgrade_page_once_or_raise("before_save", risk_payload)

        if not staged_results:
            if record_abnormal:
                log_warning(f"未获取到任何数据: {city_name} - {poi_name}")
                save_abnormal_state(
                    url_info,
                    source_country,
                    p_date,
                    d_date,
                    currency,
                    f"未获取到数据（数据条数为 0），重试次数: {retry_count}",
                )
            raise UrlNoDataError(f"{city_name}-{poi_name} 在日期对 {p_date} -> {d_date} 未获取到数据")

        save_to_csv(staged_results, csv_filename, source_country, p_date, d_date, pick_value)
        all_results.extend(staged_results)
        log_success(
            f"\n{city_name} - {poi_name} 处理完成，共获取 {len(staged_results)} 个结果响应包，"
            f"累计总响应包 {len(all_results)}\n"
        )
        if retry_count > 0:
            log_success(f"第 {retry_count} 次重试成功！")
        return list(staged_results)
    finally:
        if event_tasks:
            await asyncio.gather(*list(event_tasks), return_exceptions=True)

        if page and response_handler:
            await detach_page_handlers(page)

        if page and not page.is_closed() and not browser_options["reuse_page_across_dates"]:
            try:
                open_pages = [item for item in browser_context.pages if not item.is_closed()]
            except Exception:
                open_pages = [page]

            try:
                if len(open_pages) <= 1:
                    await page.goto("about:blank", wait_until="domcontentloaded", timeout=10000)
                    log_info("保留当前页面作为占位页，避免 persistent context 被意外关闭")
                else:
                    await page.close()
                    log_info("当前日期页面已关闭")
            except Exception as exc:
                log_warning(f"关闭当前日期页面失败: {exc}")


async def create_persistent_browser_context(
    playwright,
    source_country="CN",
    currency="CNY",
    browser_config=None,
    force_rebuild_runtime=False,
):
    """创建 persistent browser context。"""
    del source_country, currency

    browser_options = _normalize_browser_config(browser_config)
    try:
        egress_report = await verify_proxy_egress(
            playwright,
            browser_options["proxy_server"],
            browser_options.get("proxy_username"),
            browser_options.get("proxy_password"),
        )
    except KlookNetworkGuardError as exc:
        raise NonRetryableSessionInterceptionError(
            f"Klook 出口预检失败: {exc}",
            metadata={
                "session_state": "EGRESS_UNSAFE",
                "non_retryable": True,
            },
        ) from exc
    profile_lock = acquire_browser_profile_lock(browser_options)
    try:
        user_data_dir = ensure_browser_profile(
            browser_options,
            force_rebuild_runtime=force_rebuild_runtime,
        )
    except Exception:
        profile_lock.release()
        raise
    log_info(f"浏览器资料目录: {user_data_dir}")

    launch_options = {
        "user_data_dir": user_data_dir,
        "headless": False,
        "viewport": {"width": 1440, "height": 900},
        "proxy": _build_playwright_proxy(browser_options),
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-infobars",
            "--disable-dev-shm-usage",
            "--start-maximized",
        ],
    }
    executable_path = _get_browser_executable_path()
    if executable_path:
        launch_options["executable_path"] = executable_path

    log_info(
        "Klook 网络预检通过：固定代理=%s，出口指纹=%s，探针=%s"
        % (
            safe_proxy_location(browser_options["proxy_server"]),
            egress_report["egress_fingerprint"],
            egress_report["probe_count"],
        )
    )
    try:
        browser_context = await playwright.chromium.launch_persistent_context(**launch_options)
    except Exception:
        profile_lock.release()
        raise
    try:
        await normalize_klook_international_cookies(browser_context)
        browser_context._klook_profile_dir = user_data_dir
        browser_context._klook_profile_lock = profile_lock
        browser_context._klook_context_created_monotonic = time.monotonic()
        browser_context._klook_browser_config = browser_options
        browser_context._klook_clash_proxy_fingerprint = egress_report[
            "clash_proxy_fingerprint"
        ]
    except Exception:
        try:
            await browser_context.close()
        finally:
            profile_lock.release()
        raise
    log_success("persistent 浏览器上下文已创建")
    return browser_context


async def close_browser_context(browser_context):
    """关闭浏览器上下文。"""
    if not browser_context:
        return

    profile_lock = getattr(browser_context, "_klook_profile_lock", None)
    try:
        try:
            for page in list(browser_context.pages):
                if not page.is_closed():
                    await page.close()
            await browser_context.close()
            log_success("浏览器上下文已关闭")
        except Exception as exc:
            if "closed" not in str(exc).lower():
                log_warning(f"关闭浏览器上下文时出错: {exc}")
    finally:
        if profile_lock:
            profile_lock.release()
            browser_context._klook_profile_lock = None


async def warmup_browser_session(
    browser_context: BrowserContext,
    source_country="CN",
    currency="CNY",
    browser_config=None,
):
    """预热浏览器会话。"""
    browser_options = _normalize_browser_config(browser_config)
    warmup_entry_url = browser_options.get("warmup_entry_url") or "https://www.klook.com/cars/"
    if not _is_klook_com_url(warmup_entry_url):
        raise NonRetryableSessionInterceptionError(
            "浏览器预热地址不是受支持的 Klook 国际站 HTTPS 地址",
            metadata={
                "session_state": "INVALID_INPUT_DOMAIN",
                "non_retryable": True,
                "warmup_entry_url": warmup_entry_url,
            },
        )
    headers = _build_page_headers(source_country, currency, browser_options)

    page = None
    created_page = False
    try:
        open_pages = [item for item in browser_context.pages if not item.is_closed()]
        if open_pages:
            page = open_pages[-1]
        else:
            page = await browser_context.new_page()
            created_page = True

        await page.set_extra_http_headers(headers)
        page.set_default_timeout(120000)

        log_info("正在预热浏览器会话，访问 Klook 首页...")
        await _verify_context_egress(browser_context, browser_options)
        await normalize_klook_international_cookies(browser_context)
        navigation_response = await page.goto(
            "https://www.klook.com/",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        if navigation_response and navigation_response.status == 429:
            raise NonRetryableSessionInterceptionError(
                "Klook 预热首页返回 HTTP 429",
                metadata={"session_state": "RATE_LIMITED", "non_retryable": True},
            )
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        await asyncio.sleep(2)
        try:
            await page.evaluate(
                """
                ({currency, sourceCountry}) => {
                    try { window.localStorage.setItem("currency", currency); } catch (e) {}
                    try { window.localStorage.setItem("userResidence", sourceCountry); } catch (e) {}
                }
                """,
                {
                    "currency": currency,
                    "sourceCountry": source_country,
                },
            )
        except Exception:
            pass

        risk_info = await _wait_for_actionable_page_state(page, browser_options)
        if risk_info["risk_page_detected"]:
            metadata = {
                "source_country": source_country,
                "currency": currency,
            }
            metadata.update(risk_info)
            await _capture_debug_snapshot(page, "warmup_homepage_risk", metadata)
            raise _build_session_interception_error(
                "浏览器预热首页即命中 Klook 风险拦截",
                metadata,
            )

        warmup_cooldown = random.uniform(30, 35)
        log_info(f"首页预热完成，静默 {warmup_cooldown:.2f} 秒后进入租车页面")
        await asyncio.sleep(warmup_cooldown)

        log_info(f"继续预热租车入口页: {warmup_entry_url}")
        await _verify_context_egress(browser_context, browser_options)
        await normalize_klook_international_cookies(browser_context)
        navigation_response = await page.goto(
            warmup_entry_url,
            wait_until="domcontentloaded",
            timeout=30000,
        )
        if navigation_response and navigation_response.status == 429:
            raise NonRetryableSessionInterceptionError(
                "Klook 预热入口页返回 HTTP 429",
                metadata={"session_state": "RATE_LIMITED", "non_retryable": True},
            )
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(2)

        risk_info = await _wait_for_actionable_page_state(page, browser_options)
        if risk_info["risk_page_detected"]:
            metadata = {
                "source_country": source_country,
                "currency": currency,
                "warmup_entry_url": warmup_entry_url,
            }
            metadata.update(risk_info)
            await _capture_debug_snapshot(page, "warmup_entry_risk", metadata)
            raise _build_session_interception_error(
                "浏览器预热入口页命中 Klook 风险拦截",
                metadata,
            )

        log_success("浏览器会话预热完成")
    except SessionInterceptionError:
        raise
    except Exception as exc:
        raise SessionInterceptionError(
            f"浏览器会话预热失败: {exc}",
            metadata={
                "source_country": source_country,
                "currency": currency,
                "warmup_entry_url": warmup_entry_url,
            },
        ) from exc
    finally:
        if created_page and page and not page.is_closed():
            try:
                await page.goto("about:blank", wait_until="domcontentloaded", timeout=10000)
            except Exception:
                pass
