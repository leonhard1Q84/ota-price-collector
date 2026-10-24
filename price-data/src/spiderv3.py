# -*- coding: utf-8 -*-
"""
Klook rental crawler runner with short-lived browser session batches.
"""

import argparse
import asyncio
import importlib
import json
import os
import random
import sys
import time
from datetime import datetime

from playwright.async_api import async_playwright

from config_reader import (
    get_browser_config,
    get_config_for_pick,
    get_page_timing_config,
    load_config,
)
from ix_rating_summary import rebuild_daily_rating_summary
from klook_egress_guard import KlookEgressGuardError, KlookEgressLock, open_circuit
from logger import close_logger, init_logger, log_error, log_info, log_success, log_warning
from path_utils import (
    ensure_dir,
    get_crawler_config_path,
    get_data_dir,
    get_price_data_dir,
)
from spider_core import (
    NonRetryableSessionInterceptionError,
    SessionInterceptionError,
    UrlDateTimeoutError,
    UrlNoDataError,
    close_browser_context,
    create_persistent_browser_context,
    process_single_url_with_browser,
    save_abnormal_state,
    warmup_browser_session,
)
from url_handler import calculate_expires_timestamp, load_urls_from_json, normalize_url_for_matching


sys.path.append(os.path.dirname(os.path.abspath(__file__)))

results = []
generated_csv_files = []

DEFAULT_CONTEXT_COOLDOWN_SECONDS = 45
DEFAULT_PROFILE_REBUILD_COOLDOWN_SECONDS = 90
QUERY_INTERVAL_SECONDS = (2, 5)
QUERY_COOLDOWN_SECONDS = (30, 35)


def _has_materialized_output_file(path):
    try:
        return os.path.exists(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _is_browser_context_closed_error(exc):
    message = str(exc)
    return (
        "BrowserContext.new_page: Target page, context or browser has been closed" in message
        or "Target page, context or browser has been closed" in message
        or "Browser has been closed" in message
    )


def _wrap_session_recovery_error(exc, phase):
    if isinstance(exc, NonRetryableSessionInterceptionError):
        metadata = dict(exc.metadata or {})
        metadata.setdefault("recovery_phase", phase)
        return NonRetryableSessionInterceptionError(
            str(exc),
            partial_data=exc.partial_data,
            metadata=metadata,
        )
    if isinstance(exc, SessionInterceptionError):
        metadata = dict(exc.metadata or {})
        metadata.setdefault("recovery_phase", phase)
        return SessionInterceptionError(str(exc), partial_data=exc.partial_data, metadata=metadata)

    return SessionInterceptionError(
        f"{phase} 恢复失败: {exc}",
        metadata={"recovery_phase": phase},
    )


def parse_picks(value):
    if not value:
        return []
    raw_items = str(value).replace("，", ",").replace(";", ",").split(",")
    picks = []
    seen = set()
    for item in raw_items:
        pick = item.strip().upper()
        if pick and pick not in seen:
            picks.append(pick)
            seen.add(pick)
    return picks


def parse_url_selectors(value):
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]

    selectors = []
    seen = set()
    for item in raw_items:
        selector_text = str(item or "").strip()
        if not selector_text:
            continue
        normalized_selector = normalize_url_for_matching(selector_text) or selector_text
        if normalized_selector not in seen:
            selectors.append(normalized_selector)
            seen.add(normalized_selector)
    return selectors


async def process_url_with_retry(
    browser_context,
    url_info,
    all_results,
    source_country,
    expires_timestamp,
    p_date,
    d_date,
    csv_filename,
    currency,
    max_retries=0,
    page_timing=None,
    browser_config=None,
):
    """Retry ordinary timeout/no-data cases inside one healthy browser context."""
    city_name = url_info.get("city_name", "未知城市")
    poi_name = url_info.get("poi_name", "未知地点")

    for retry_count in range(max_retries + 1):
        try:
            if retry_count == 0:
                log_info(f"首次执行: {city_name} - {poi_name}")
            else:
                log_info(f"准备第 {retry_count} 次重试...")

            return await process_single_url_with_browser(
                browser_context,
                url_info,
                all_results,
                source_country,
                expires_timestamp,
                p_date,
                d_date,
                csv_filename,
                currency,
                record_abnormal=True,
                retry_count=retry_count,
                max_retries=max_retries,
                page_timing=page_timing,
                browser_config=browser_config,
            )
        except SessionInterceptionError:
            raise
        except (UrlDateTimeoutError, UrlNoDataError) as exc:
            if retry_count < max_retries:
                wait_minutes = 3
                log_warning(
                    f"第 {retry_count + 1} 次执行失败，等待 {wait_minutes} 分钟后重试..."
                )
                log_warning(f"失败原因: {exc}")
                await asyncio.sleep(wait_minutes * 60)
                continue

            reason = f"重试 {max_retries} 次后仍失败"
            if isinstance(exc, UrlDateTimeoutError):
                reason = "执行超时，" + reason
            else:
                reason = "数据为空，" + reason

            log_error(
                f"{city_name} - {poi_name} 重试 {max_retries} 次后仍失败，跳过日期对: {p_date} -> {d_date}"
            )
            save_abnormal_state(url_info, source_country, p_date, d_date, currency, reason)
            return []
        except Exception as exc:
            log_error(f"处理 URL 时出现其他异常: {exc}")
            raise


def _build_rotation_reasons(context_state, browser_config, next_url_key):
    reasons = []
    if not context_state:
        return reasons

    max_date_pairs = int(browser_config.get("max_date_pairs_per_context") or 0)
    max_urls = int(browser_config.get("max_urls_per_context") or 0)
    max_minutes = int(browser_config.get("max_minutes_per_context") or 0)

    if max_date_pairs and context_state["processed_date_pairs"] >= max_date_pairs:
        reasons.append(f"日期组数量达到阈值 {max_date_pairs}")

    if (
        max_urls
        and next_url_key not in context_state["seen_url_keys"]
        and context_state["processed_urls"] >= max_urls
    ):
        reasons.append(f"URL 数量达到阈值 {max_urls}")

    if max_minutes:
        context_age_minutes = (time.monotonic() - context_state["created_monotonic"]) / 60.0
        if context_age_minutes >= max_minutes:
            reasons.append(f"context 存活时长达到阈值 {max_minutes} 分钟")

    return reasons


def _get_recovery_limits(browser_config):
    """读取会话恢复次数；Profile 重建的显式 0 必须保持为 0。"""
    config = browser_config if isinstance(browser_config, dict) else {}
    try:
        context_limit = int(
            config.get("context_relaunch_after_reload_failures", 1)
        )
    except (TypeError, ValueError):
        context_limit = 1
    try:
        profile_limit = int(
            config.get("profile_rebuild_after_context_failures", 0)
        )
    except (TypeError, ValueError):
        profile_limit = 0
    return max(context_limit, 1), max(profile_limit, 0)


def _mark_context_progress(context_state, url_key):
    if not context_state:
        return
    context_state["processed_date_pairs"] += 1
    if url_key not in context_state["seen_url_keys"]:
        context_state["seen_url_keys"].add(url_key)
        context_state["processed_urls"] += 1


def _describe_session_error(exc):
    if isinstance(exc, SessionInterceptionError):
        metadata = exc.metadata or {}
        risk_hits = metadata.get("risk_text_hits") or []
        risk_suffix = f"，risk_text_hits={risk_hits}" if risk_hits else ""
        return f"{exc}{risk_suffix}"
    return str(exc)


async def generate(picks=None, url_selectors=None):
    """Main entry: crawl grouped URLs with short-lived browser context batches."""
    config = load_config()
    if not config:
        log_error("无法加载配置文件，程序退出")
        return False

    expires_timestamp = calculate_expires_timestamp()
    log_info(
        f"本次运行使用 expires 时间戳: {expires_timestamp} "
        f"({datetime.fromtimestamp(expires_timestamp / 1000)})"
    )

    urls = load_urls_from_json("urls/urls.json")
    selected_picks = set(parse_picks(picks))
    selected_url_selectors = set(parse_url_selectors(url_selectors))
    if selected_picks:
        urls = [
            url_info
            for url_info in urls
            if str(url_info.get("pick", "")).upper() in selected_picks
        ]
    if selected_url_selectors:
        urls = [
            url_info
            for url_info in urls
            if normalize_url_for_matching(url_info.get("url", "")) in selected_url_selectors
        ]
    if not urls:
        log_error("没有可处理的 URL，程序退出")
        return False

    log_info(f"共加载 {len(urls)} 个 URL")
    if selected_url_selectors:
        log_info(f"URL selector 过滤后剩余 {len(urls)} 个 URL")

    grouped_urls = {}
    for url_info in urls:
        pick_value = url_info.get("pick", "unknown")
        grouped_urls.setdefault(pick_value, []).append(url_info)

    log_info(f"按 pick 分组结果: {list(grouped_urls.keys())}")

    global results, generated_csv_files
    results = []
    generated_csv_files = []

    today_date = datetime.now().strftime("%Y%m%d")
    data_date_dir = ensure_dir(os.path.join(get_data_dir(), today_date))
    price_date_dir = ensure_dir(os.path.join(get_price_data_dir(), today_date))
    log_info(f"原始数据将保存到: {data_date_dir}")
    log_info(f"处理后数据将保存到: {price_date_dir}")

    playwright = None
    browser_context = None
    egress_lock = KlookEgressLock()

    try:
        egress_lock.acquire()
        log_success("已获取本机 Klook 共享出口锁")
    except KlookEgressGuardError as exc:
        log_error(str(exc))
        return False

    success = False
    overall_success = True
    try:
        playwright = await async_playwright().start()

        for pick_idx, (pick_value, url_list) in enumerate(grouped_urls.items(), 1):
            try:
                pick_config = get_config_for_pick(pick_value, config)
                if not pick_config:
                    log_warning(f"跳过 {pick_value} 组，无法获取配置")
                    continue

                source_countries = pick_config.get("source_countries", ["KR"])
                date_pairs = pick_config.get("date_pairs", [])
                currency = pick_config.get("currency", "JPY")
                page_timing = get_page_timing_config(config, pick_config)
                browser_config = get_browser_config(config, pick_config)

                log_info(f"\n{'*' * 80}")
                log_info(f"开始处理 {pick_idx}/{len(grouped_urls)}: {pick_value} 组")
                log_info(
                    f"配置: {len(source_countries)} 个客源地, {len(date_pairs)} 组日期对, 货币: {currency}"
                )
                log_info(f"包含 {len(url_list)} 个 URL")
                log_info(f"{'*' * 80}\n")

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                csv_filename = os.path.join(data_date_dir, f"output_{pick_value}_{timestamp}.csv")
                generated_csv_files.append(csv_filename)
                pick_results_before = len(results)
                pick_failure_reasons = []
                log_info(f"本组原始数据将保存到: {csv_filename}")

                for source_idx, source_country in enumerate(source_countries, 1):
                    context_state = None

                    async def close_current_context():
                        nonlocal browser_context
                        if browser_context:
                            try:
                                await close_browser_context(browser_context)
                            finally:
                                browser_context = None

                    async def relaunch_context(force_rebuild_runtime=False, reason=None):
                        nonlocal browser_context
                        nonlocal context_state

                        if reason:
                            log_info(reason)
                        phase_name = "runtime profile" if force_rebuild_runtime else "context"
                        try:
                            await close_current_context()
                            browser_context = await create_persistent_browser_context(
                                playwright,
                                source_country=source_country,
                                currency=currency,
                                browser_config=browser_config,
                                force_rebuild_runtime=force_rebuild_runtime,
                            )
                            await warmup_browser_session(
                                browser_context,
                                source_country=source_country,
                                currency=currency,
                                browser_config=browser_config,
                            )
                            warmup_cooldown = random.uniform(*QUERY_COOLDOWN_SECONDS)
                            log_info(
                                f"浏览器预热完成，静默 {warmup_cooldown:.2f} 秒后开始查询..."
                            )
                            await asyncio.sleep(warmup_cooldown)
                            context_state = {
                                "created_monotonic": time.monotonic(),
                                "processed_date_pairs": 0,
                                "processed_urls": 0,
                                "seen_url_keys": set(),
                            }
                        except Exception as exc:
                            await close_current_context()
                            raise _wrap_session_recovery_error(exc, phase_name) from exc
                        log_success(
                            f"已创建新的浏览器 context（pick={pick_value}, source_country={source_country}）"
                        )

                    try:
                        log_info(f"\n{'=' * 60}")
                        log_info(
                            f"处理 {pick_value} 组的客源地 {source_idx}/{len(source_countries)}: {source_country}"
                        )
                        log_info(f"{'=' * 60}\n")

                        for url_idx, url_info in enumerate(url_list, 1):
                            url_key = url_info.get("url", "")
                            log_info(f"\n[进度] {pick_value} 组 - URL {url_idx}/{len(url_list)}")
                            log_info(f"城市: {url_info.get('city_name')}, 地点: {url_info.get('poi_name')}")

                            for date_idx, (p_date, d_date) in enumerate(date_pairs, 1):
                                log_info(f"\n{'-' * 50}")
                                log_info(f"处理日期对 {date_idx}/{len(date_pairs)}")
                                log_info(f"取车日期: {p_date}")
                                log_info(f"还车日期: {d_date}")
                                log_info(f"{'-' * 50}\n")

                                date_pair_done = False
                                context_relaunch_failures = 0
                                profile_rebuild_failures = 0

                                while not date_pair_done:
                                    try:
                                        rotation_reasons = _build_rotation_reasons(
                                            context_state,
                                            browser_config,
                                            url_key,
                                        )
                                        if browser_context is None:
                                            await relaunch_context(
                                                force_rebuild_runtime=False,
                                                reason=(
                                                    f"初始化批次浏览器 context（pick={pick_value}, "
                                                    f"source_country={source_country}）"
                                                ),
                                            )
                                        elif rotation_reasons:
                                            await relaunch_context(
                                                force_rebuild_runtime=False,
                                                reason=(
                                                    "触发主动轮换 context: "
                                                    + "；".join(rotation_reasons)
                                                ),
                                            )

                                        await process_url_with_retry(
                                            browser_context,
                                            url_info,
                                            results,
                                            source_country,
                                            expires_timestamp,
                                            p_date,
                                            d_date,
                                            csv_filename,
                                            currency,
                                            page_timing=page_timing,
                                            browser_config=browser_config,
                                        )
                                        _mark_context_progress(context_state, url_key)
                                        date_pair_done = True
                                    except Exception as exc:
                                        if isinstance(exc, NonRetryableSessionInterceptionError):
                                            log_error(
                                                "检测到国内站跳转或明确风控，本次任务立即停止且不自动重试"
                                            )
                                            raise
                                        is_session_issue = isinstance(exc, SessionInterceptionError) or _is_browser_context_closed_error(exc)
                                        if not is_session_issue:
                                            raise

                                        error_message = _describe_session_error(exc)
                                        log_warning(
                                            f"检测到会话污染/上下文失效，当前日期组将升级恢复链: {error_message}"
                                        )

                                        (
                                            context_relaunch_limit,
                                            profile_rebuild_limit,
                                        ) = _get_recovery_limits(browser_config)

                                        if context_relaunch_failures < context_relaunch_limit:
                                            context_relaunch_failures += 1
                                            try:
                                                await relaunch_context(
                                                    force_rebuild_runtime=False,
                                                    reason=(
                                                        f"当前日期组命中会话污染，执行 context 重建 "
                                                        f"{context_relaunch_failures}/{context_relaunch_limit}"
                                                    ),
                                                )
                                            except Exception as recovery_exc:
                                                if isinstance(
                                                    recovery_exc,
                                                    NonRetryableSessionInterceptionError,
                                                ):
                                                    raise
                                                error_message = _describe_session_error(recovery_exc)
                                                log_warning(f"当前日期组 context 重建失败，继续升级恢复链: {error_message}")
                                            continue

                                        if profile_rebuild_failures < profile_rebuild_limit:
                                            profile_rebuild_failures += 1
                                            context_relaunch_failures = 0
                                            try:
                                                await relaunch_context(
                                                    force_rebuild_runtime=True,
                                                    reason=(
                                                        f"context 重建后仍失败，执行 runtime profile 重建 "
                                                        f"{profile_rebuild_failures}/{profile_rebuild_limit}"
                                                    ),
                                                )
                                                log_warning(
                                                    f"本批次进入冷却 {DEFAULT_CONTEXT_COOLDOWN_SECONDS} 秒后再继续当前日期组"
                                                )
                                                await asyncio.sleep(DEFAULT_CONTEXT_COOLDOWN_SECONDS)
                                            except Exception as recovery_exc:
                                                if isinstance(
                                                    recovery_exc,
                                                    NonRetryableSessionInterceptionError,
                                                ):
                                                    raise
                                                error_message = _describe_session_error(recovery_exc)
                                                log_warning(f"当前日期组 runtime profile 重建失败，将按日期组级恢复失败处理: {error_message}")
                                            continue

                                        reason = f"会话被拦截导致结果可能不完整: {error_message}"
                                        save_abnormal_state(
                                            url_info,
                                            source_country,
                                            p_date,
                                            d_date,
                                            currency,
                                            reason,
                                        )
                                        log_error(
                                            "当前日期组已耗尽 context/profile 恢复额度，进入批次冷却后跳过该日期组，并继续后续日期组"
                                        )
                                        await close_current_context()
                                        await asyncio.sleep(DEFAULT_PROFILE_REBUILD_COOLDOWN_SECONDS)
                                        date_pair_done = True

                                if date_idx < len(date_pairs):
                                    wait_time = random.uniform(*QUERY_INTERVAL_SECONDS)
                                    log_info(f"等待 {wait_time:.2f} 秒后处理该 URL 的下一个日期对...")
                                    await asyncio.sleep(wait_time)

                            if url_idx < len(url_list):
                                wait_time = random.uniform(*QUERY_INTERVAL_SECONDS)
                                log_info(f"等待 {wait_time:.2f} 秒后处理下一个 URL...")
                                await asyncio.sleep(wait_time)

                        log_info(f"\n{pick_value} 组 - 客源地 {source_country} 处理完成")
                    except NonRetryableSessionInterceptionError:
                        raise
                    except Exception as exc:
                        pick_failure_reasons.append(f"{source_country}: {exc}")
                        log_error(f"处理客源地 {source_country} 时出错: {exc}")
                        log_info("继续处理下一个客源地...")
                    finally:
                        await close_current_context()
                        if source_idx < len(source_countries):
                            wait_time = random.uniform(*QUERY_INTERVAL_SECONDS)
                            log_info(f"等待 {wait_time:.2f} 秒后处理下一个客源地...")
                            await asyncio.sleep(wait_time)

                pick_results_added = len(results) - pick_results_before
                raw_output_ready = _has_materialized_output_file(csv_filename)
                if pick_failure_reasons or pick_results_added <= 0 or not raw_output_ready:
                    overall_success = False
                    if pick_failure_reasons:
                        log_error(f"{pick_value} 组存在未恢复的执行错误: {' | '.join(pick_failure_reasons)}")
                    if pick_results_added <= 0:
                        log_error(f"{pick_value} 组未获取到任何结果响应包")
                    if not raw_output_ready:
                        log_error(f"{pick_value} 组未生成有效原始数据文件: {csv_filename}")
                    log_error(f"{pick_value} 组执行不完整，本次运行标记为失败并跳过后处理")
                    if pick_idx < len(grouped_urls):
                        wait_time = random.uniform(*QUERY_INTERVAL_SECONDS)
                        log_info(f"等待 {wait_time:.2f} 秒后处理下一个 pick 组...")
                        await asyncio.sleep(wait_time)
                    continue

                log_info(f"\n{'=' * 60}")
                log_success(f"{pick_value} 组处理完成！")
                log_success(
                    f"共处理 {len(url_list)} 个 URL，{len(source_countries)} 个客源地，{len(date_pairs)} 组日期对"
                )
                log_info(f"{'=' * 60}\n")

                log_info(f"开始对 {pick_value} 组执行后处理...")
                filter_success = process_pick_specific_module(pick_value, "filter", csv_filename, config)
                analyzer_success = process_pick_specific_module(pick_value, "analyzer", csv_filename, config)

                if filter_success or analyzer_success:
                    log_success(f"{pick_value} 组的后处理执行完成（按配置启用）")
                else:
                    log_info(f"{pick_value} 组未执行任何后处理（按配置跳过）")

                rating_summary_file = rebuild_daily_rating_summary(
                    today_date,
                    [get_data_dir()],
                )
                if rating_summary_file not in generated_csv_files:
                    generated_csv_files.append(rating_summary_file)

                log_info(f"{pick_value} 组后处理完成")

                if pick_idx < len(grouped_urls):
                    wait_time = random.uniform(*QUERY_INTERVAL_SECONDS)
                    log_info(f"等待 {wait_time:.2f} 秒后处理下一个 pick 组...")
                    await asyncio.sleep(wait_time)
            except NonRetryableSessionInterceptionError:
                raise
            except Exception as exc:
                overall_success = False
                log_error(f"处理 {pick_value} 组时出错: {exc}")
                log_info("继续处理下一个 pick 组...")
                if pick_idx < len(grouped_urls):
                    wait_time = random.uniform(*QUERY_INTERVAL_SECONDS)
                    log_info(f"等待 {wait_time:.2f} 秒后处理下一个 pick 组...")
                    await asyncio.sleep(wait_time)
                continue

        log_info(f"\n{'*' * 80}")
        if not overall_success:
            log_error("存在未完整执行的 pick 组，本次运行标记为失败")
        log_success("所有 pick 组处理完成！")
        log_success(f"共处理 {len(grouped_urls)} 个 pick 组，获取 {len(results)} 个结果响应包")
        log_success(f"原始数据保存在: {data_date_dir}")
        log_success(f"处理后数据保存在: {price_date_dir}")
        log_info(f"{'*' * 80}\n")

        config_info = {
            "generated_files": generated_csv_files,
            "generation_time": datetime.now().isoformat(),
            "total_results": len(results),
            "data_directory": data_date_dir,
            "price_data_directory": price_date_dir,
            "date": today_date,
        }
        crawler_config_path = get_crawler_config_path()
        ensure_dir(os.path.dirname(crawler_config_path))
        with open(crawler_config_path, "w", encoding="utf-8") as file:
            json.dump(config_info, file, ensure_ascii=False, indent=2)
        success = overall_success
    except NonRetryableSessionInterceptionError as exc:
        state = str((exc.metadata or {}).get("session_state") or "KLOOK_RISK_STOP")
        circuit_file = open_circuit(state, _describe_session_error(exc))
        log_error(f"不可重试的 Klook 风控已打开共享出口熔断: {circuit_file}")
        raise
    finally:
        if browser_context:
            await close_browser_context(browser_context)
        if playwright:
            await playwright.stop()
        egress_lock.release()
        log_info("已释放本机 Klook 共享出口锁")
    return success


def get_pick_value_from_filename(filename):
    import re

    match = re.search(r"output_([A-Z]+)_", filename)
    if match:
        return match.group(1)
    return None


def load_pick_specific_module(pick_value, module_type):
    if module_type == "filter":
        module_name = f"data_filter_{pick_value}"
        module_path = f"filters.{module_name}"
        function_name = "process_data_filter"
    elif module_type == "analyzer":
        module_name = f"disadvantage_analyzer_{pick_value}"
        module_path = f"analyzers.{module_name}"
        function_name = "process_disadvantage_analysis"
    else:
        raise ValueError(f"未知的模块类型: {module_type}")

    try:
        log_info(f"尝试导入: {module_path}")
        module = importlib.import_module(module_path)
        log_info(f"成功加载 {pick_value} 专用 {module_type} 模块: {module_name}")
        return module, function_name
    except ImportError as exc:
        log_error(f"导入错误详情: {exc}")
        log_error(f"模块路径 '{module_path}' 导入失败")
        log_warning(f"{pick_value} 专用 {module_type} 模块 {module_name} 不存在，跳过")
        return None, None
    except Exception as exc:
        log_error(f"加载 {pick_value} 专用 {module_type} 模块时出错: {exc}")
        return None, None


def process_pick_specific_module(pick_value, module_type, csv_file, config):
    try:
        pick_config = config.get(pick_value, {})
        enabled = True
        if module_type == "filter":
            enabled = pick_config.get("enable_filter", True)
        elif module_type == "analyzer":
            enabled = pick_config.get("enable_analyzer", True)

        if not enabled:
            log_info(f"配置中禁用了 {pick_value} 的 {module_type} 模块，跳过")
            return True

        module, function_name = load_pick_specific_module(pick_value, module_type)
        if not module or not function_name:
            log_warning(f"无法执行 {pick_value} 的 {module_type} 处理，模块不存在")
            return False

        try:
            process_func = getattr(module, function_name)
        except AttributeError as exc:
            log_error(f"模块 {module.__name__} 中未找到函数 {function_name}: {exc}")
            return False

        data_date_dir = os.path.dirname(csv_file)
        date_str = os.path.basename(data_date_dir)
        price_data_root = get_price_data_dir()
        price_date_dir = ensure_dir(os.path.join(price_data_root, date_str))

        log_info(f"原始数据目录: {data_date_dir}")
        log_info(f"处理后文件目录: {price_date_dir}")

        import inspect

        sig = inspect.signature(process_func)
        params = sig.parameters

        if len(params) == 1:
            original_cwd = os.getcwd()
            try:
                os.chdir(price_date_dir)
                log_info(f"在目录中执行后处理: {price_date_dir}")
                process_func(csv_file)
            finally:
                os.chdir(original_cwd)
        elif len(params) >= 2:
            log_info("执行后处理，输出到指定目录...")
            process_func(csv_file, price_date_dir)
        else:
            log_error(f"函数 {function_name} 的参数数量不正确: {len(params)}")
            return False

        log_success(f"{pick_value} 专用 {module_type} 处理执行完成")
        return True
    except Exception as exc:
        log_error(f"执行 {pick_value} 的 {module_type} 模块时出错: {exc}")
        return False


if __name__ == "__main__":
    try:
        log_file = init_logger()
        log_info(f"日志文件: {log_file}")
        log_info("=" * 80)
        log_info("Klook 租车数据爬虫 - 开始运行")
        log_info("启用短生命周期会话、context 主动轮换与风控恢复链")
        log_info("=" * 80)

        config = load_config()
        if not config:
            log_error("无法加载配置文件，程序退出")
            close_logger()
            raise SystemExit(1)

        log_info("配置文件加载成功，开始抓取数据...")

        parser = argparse.ArgumentParser(description="Klook spider runner")
        parser.add_argument(
            "--picks",
            default=None,
            help="Only run selected picks, for example FUK or FUK,TPE",
        )
        parser.add_argument(
            "--url-selector",
            dest="url_selectors",
            action="append",
            default=None,
            help="Run only URLs whose normalized selector matches this value",
        )
        args = parser.parse_args()

        success = asyncio.run(generate(picks=args.picks, url_selectors=args.url_selectors))
        if not success:
            raise SystemExit(1)

        log_info("=" * 80)
        log_info("Klook 租车数据爬虫 - 全部任务完成")
        log_info("=" * 80)
    except Exception as exc:
        log_error(f"程序运行出错: {exc}")
        raise
    finally:
        close_logger()
