# -*- coding: utf-8 -*-
"""
URL处理模块
负责URL的加载、日期计算和URL参数更新
"""
import json
import os
import urllib.parse
from datetime import datetime, timedelta
from logger import log_success, log_error, log_warning, log_info
from path_utils import resolve_existing_path


def calculate_expires_timestamp():
    """计算当前时间6小时后的时间戳（毫秒）"""
    now = datetime.now()
    expires_time = now + timedelta(hours=6)
    expires_timestamp = int(expires_time.timestamp() * 1000)
    return expires_timestamp


# 注意：calculate_dates函数已被移除，现在从config.json读取日期对

DEFAULT_TRANSIENT_QUERY_PARAMS = {
    "search_id",
    "clickId",
    "clickid",
    "spm",
}

MATCH_IGNORED_QUERY_PARAMS = {
    "pDate",
    "dDate",
}


def _normalize_transient_params(extra_params=None):
    params = set(DEFAULT_TRANSIENT_QUERY_PARAMS)
    if isinstance(extra_params, (list, tuple, set)):
        for item in extra_params:
            text = str(item).strip()
            if text:
                params.add(text)
    return params


def _should_strip_query_param(name, values, transient_params):
    if name in transient_params:
        return True
    if name.lower().startswith("utm_"):
        return True
    if name in {"promoKey", "supplierId"}:
        return not any(str(value).strip() for value in values)
    return False


def update_url_dates(
    url,
    p_date,
    d_date,
    *,
    strip_transient_query_params=True,
    transient_query_params=None,
):
    """更新URL中的pDate和dDate参数"""
    # 解析URL
    parsed = urllib.parse.urlparse(url)
    query_params = urllib.parse.parse_qs(parsed.query)

    if strip_transient_query_params:
        transient_params = _normalize_transient_params(transient_query_params)
        query_params = {
            key: value
            for key, value in query_params.items()
            if not _should_strip_query_param(key, value, transient_params)
        }

    # 更新日期参数（直接设置，urlencode会自动处理编码）
    query_params['pDate'] = [p_date]
    query_params['dDate'] = [d_date]

    # 重新构建URL（urlencode会将空格编码为+，冒号编码为%3A）
    new_query = urllib.parse.urlencode(query_params, doseq=True)
    new_url = urllib.parse.urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment
    ))

    return new_url


def normalize_url_for_matching(
    url,
    *,
    strip_transient_query_params=True,
    transient_query_params=None,
    strip_date_query_params=True,
):
    """Build a stable URL key for scheduler matching."""
    url_text = str(url or "").strip()
    if not url_text:
        return ""

    parsed = urllib.parse.urlparse(url_text)
    query_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    if strip_transient_query_params:
        transient_params = _normalize_transient_params(transient_query_params)
        query_params = {
            key: value
            for key, value in query_params.items()
            if not _should_strip_query_param(key, value, transient_params)
        }

    if strip_date_query_params:
        for param_name in MATCH_IGNORED_QUERY_PARAMS:
            query_params.pop(param_name, None)

    normalized_items = []
    for key in sorted(query_params.keys()):
        values = query_params.get(key) or [""]
        normalized_items.append((key, [str(value) for value in values]))

    normalized_query = urllib.parse.urlencode(normalized_items, doseq=True)
    normalized_path = parsed.path.rstrip("/") or parsed.path or "/"
    normalized_url = urllib.parse.urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            normalized_path,
            "",
            normalized_query,
            "",
        )
    )
    return normalized_url


def load_urls_from_json(filepath="urls/urls.json"):
    """从JSON文件加载URL列表"""
    try:
        filepath = resolve_existing_path(filepath)
        with open(filepath, "r", encoding="utf-8-sig") as f:
            urls = json.load(f)

        # 检查每个URL是否有pick字段
        for i, url_info in enumerate(urls):
            if "pick" not in url_info:
                log_warning(f"URL {i + 1} 缺少pick字段: {url_info.get('city_name', '未知城市')}")
                # 尝试从URL中提取pick值
                url = url_info.get("url", "")
                if "pick=" in url:
                    try:
                        pick_start = url.index("pick=") + 5
                        pick_end = url.find("&", pick_start)
                        if pick_end == -1:
                            pick_end = len(url)
                        pick_value_encoded = url[pick_start:pick_end]
                        pick_value = urllib.parse.unquote(pick_value_encoded)
                        url_info["pick"] = pick_value
                        log_info(f"从URL中提取pick值: {pick_value}")
                    except:
                        url_info["pick"] = "unknown"
                else:
                    url_info["pick"] = "unknown"

        log_success(f"从 {filepath} 加载了 {len(urls)} 个URL")

        # 统计pick值分布
        pick_counts = {}
        for url_info in urls:
            pick_value = url_info.get("pick", "unknown")
            pick_counts[pick_value] = pick_counts.get(pick_value, 0) + 1

        for pick_value, count in pick_counts.items():
            log_info(f"  {pick_value}: {count} 个URL")

        return urls
    except FileNotFoundError:
        log_error(f"文件不存在: {filepath}")
        return []
    except json.JSONDecodeError as e:
        log_error(f"JSON解析错误: {e}")
        return []
    except Exception as e:
        log_error(f"加载URL文件时出错: {e}")
        return []
#
# def load_urls_from_json(filepath="urls/urls.json"):
#     """从JSON文件加载URL列表"""
#     try:
#         with open(filepath, "r", encoding="utf-8") as f:
#             urls = json.load(f)
#         log_success(f"从 {filepath} 加载了 {len(urls)} 个URL")
#         return urls
#     except FileNotFoundError:
#         log_error(f"文件不存在: {filepath}")
#         return []
#     except json.JSONDecodeError as e:
#         log_error(f"JSON解析错误: {e}")
#         return []
#     except Exception as e:
#         log_error(f"加载URL文件时出错: {e}")
#         return []
