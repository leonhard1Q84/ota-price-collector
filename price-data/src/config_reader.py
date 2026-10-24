# -*- coding: utf-8 -*-
"""
配置读取模块
负责读取配置文件中的设置
"""
import json
import os
from logger import log_success, log_error, log_info, log_warning
from path_utils import resolve_existing_path


DEFAULT_PAGE_TIMING = {
    "initial_page_settle_seconds": 20,
    "empty_response_reload_limit": 0,
    "no_response_reload_seconds": 20,
    "no_new_data_break_seconds": 30,
    "scroll_pause_min_seconds": 0.5,
    "scroll_pause_max_seconds": 1,
    "min_scroll_observe_seconds": 10,
    "loading_clear_grace_seconds": 5,
    "result_stable_seconds": 12,
    "loading_stuck_reload_seconds": 60,
    "loading_height_change_threshold_px": 100,
    "loading_selector_candidates": [
        ".loading-SMrOu6",
        ".tip-q4NxXN",
    ],
    "loading_text_candidates": [
        "正在为你查找最优选择",
        "载入中",
    ],
}


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
    "risk_page_text_candidates": [
        "你的访问可能存在风险，已被拦截",
        "无法访问原因",
        "Risk ID",
        "RISK ID",
        "Too Many Requests",
        "请求过于频繁",
    ],
    "drop_cache_control_headers": True,
    "accept_language": "zh-CN,zh;q=0.9,en;q=0.8",
    "warmup_entry_url": None,
    "proxy_server": "http://127.0.0.1:7897",
    "proxy_username": None,
    "proxy_password": None,
}


def _normalize_runtime_config(config):
    runtime_config = config.get("_runtime")
    if not isinstance(runtime_config, dict):
        runtime_config = {}

    page_timing = runtime_config.get("page_timing")
    if not isinstance(page_timing, dict):
        page_timing = {}

    merged_page_timing = dict(DEFAULT_PAGE_TIMING)
    merged_page_timing.update(page_timing)
    runtime_config["page_timing"] = merged_page_timing

    browser_config = runtime_config.get("browser")
    if not isinstance(browser_config, dict):
        browser_config = {}
    merged_browser_config = dict(DEFAULT_BROWSER_CONFIG)
    merged_browser_config.update(browser_config)
    runtime_config["browser"] = merged_browser_config

    config["_runtime"] = runtime_config
    return config


def load_config(config_file="config.json"):
    """加载配置文件"""
    try:
        config_file = resolve_existing_path(config_file)
        if not os.path.exists(config_file):
            log_error(f"配置文件不存在: {config_file}")
            return None

        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)

        config = _normalize_runtime_config(config)
        log_success(f"配置文件 {config_file} 加载成功")

        # 显示每个pick组的配置信息
        for pick_group, group_config in config.items():
            if str(pick_group).startswith("_"):
                continue
            log_info(f"\n{pick_group} 组配置:")
            log_info(f"  客源地: {group_config.get('source_countries', ['KR'])}")
            log_info(f"  货币: {group_config.get('currency', 'JPY')}")
            log_info(f"  日期对数量: {len(group_config.get('date_pairs', []))}")
            log_info(f"  启用数据过滤: {group_config.get('enable_filter', True)}")
            log_info(f"  启用劣势分析: {group_config.get('enable_analyzer', True)}")

        return config

    except json.JSONDecodeError as e:
        log_error(f"配置文件格式错误: {e}")
        raise
    except Exception as e:
        log_error(f"加载配置文件时出错: {e}")
        raise


def get_config_for_pick(pick_value, config):
    """根据pick值获取对应的配置"""
    if not config:
        log_error("配置文件未加载")
        return None

    if pick_value in config:
        return config[pick_value]
    else:
        log_warning(f"未找到 {pick_value} 的配置，使用默认配置")
        # 返回默认配置
        return {
            "source_countries": ["KR"],
            "currency": "JPY",
            "date_pairs": [
                ["2026-01-16 10:00", "2026-01-22 10:00"]
            ],
            "enable_filter": False,  # 默认禁用后处理
            "enable_analyzer": False,
            "page_timing": dict((config.get("_runtime") or {}).get("page_timing") or DEFAULT_PAGE_TIMING),
        }


def get_page_timing_config(config, pick_config=None):
    runtime_page_timing = dict(((config or {}).get("_runtime") or {}).get("page_timing") or DEFAULT_PAGE_TIMING)
    if isinstance(pick_config, dict):
        pick_page_timing = pick_config.get("page_timing")
        if isinstance(pick_page_timing, dict):
            runtime_page_timing.update(pick_page_timing)
    return runtime_page_timing


def get_browser_config(config, pick_config=None):
    runtime_browser_config = dict(((config or {}).get("_runtime") or {}).get("browser") or DEFAULT_BROWSER_CONFIG)
    if isinstance(pick_config, dict):
        pick_browser_config = pick_config.get("browser")
        if isinstance(pick_browser_config, dict):
            runtime_browser_config.update(pick_browser_config)
    return runtime_browser_config
