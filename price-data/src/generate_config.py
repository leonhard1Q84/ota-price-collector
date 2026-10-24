from datetime import datetime, timedelta
import argparse
import json
import os

# ============================
# 默认追加的指定日期对
# - 直接在这里修改即可（不需要命令行参数）
# - 会与自动生成日期对合并：指定日期优先执行、完全重复自动去重
# - 支持 YYYY-MM-DD 或 YYYY-MM-DD HH:MM（日期不带时间默认补 10:00）
# ============================
DEFAULT_EXTRA_DATE_PAIRS = [
    # ["2026-02-15 10:00", "2026-02-17 10:00"],
    # ["2026-02-15 10:00", "2026-02-19 10:00"],
    # ["2026-02-17 10:00", "2026-02-23 10:00"],
]

BLACKOUT_DATE_RANGES = []

PICK_BLACKOUT_DATE_RANGES = {
    "FUK": [
        ["2026-06-19", "2026-06-21"],
    ],
}

AUTO_WINDOW_DEFINITIONS = [
    ("window1", 1, 7, "late"),
    ("window2", 8, 14, "late"),
    ("window3", 15, 30, "middle"),
    ("window4", 31, 60, "middle"),
]


def normalize_picks(picks):
    if not picks:
        return []
    if isinstance(picks, (list, tuple, set)):
        raw_items = []
        for item in picks:
            if isinstance(item, str):
                raw_items.extend(item.replace("，", ",").replace(";", ",").split(","))
            else:
                raw_items.append(str(item))
    else:
        raw_items = str(picks).replace("，", ",").replace(";", ",").split(",")

    normalized = []
    seen = set()
    for item in raw_items:
        pick = item.strip().upper()
        if pick and pick not in seen:
            normalized.append(pick)
            seen.add(pick)
    return normalized


def get_config_path():
    """获取配置文件路径"""
    return os.path.join(os.path.dirname(__file__), "config.json")


def parse_base_datetime(base_datetime, date_only_default_time=None):
    if base_datetime is None:
        return None
    if isinstance(base_datetime, datetime):
        return base_datetime

    value = str(base_datetime).strip()
    if not value:
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        pass

    try:
        parsed_date = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError("不支持的日期格式，请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM")

    if not date_only_default_time:
        return parsed_date

    try:
        hour_str, minute_str = str(date_only_default_time).strip().split(":")
        hour = int(hour_str)
        minute = int(minute_str)
    except Exception:
        raise ValueError("date_only_default_time格式错误，应为HH:MM，例如10:00")

    return parsed_date.replace(hour=hour, minute=minute)


def normalize_date_pair(p_date, d_date, default_time="10:00"):
    p_datetime = parse_base_datetime(p_date, date_only_default_time=default_time)
    d_datetime = parse_base_datetime(d_date, date_only_default_time=default_time)
    if p_datetime is None or d_datetime is None:
        raise ValueError("日期对不能为空")

    if d_datetime <= p_datetime:
        raise ValueError(f"还车日期必须晚于取车日期: {p_date} -> {d_date}")

    return [
        p_datetime.strftime("%Y-%m-%d %H:%M"),
        d_datetime.strftime("%Y-%m-%d %H:%M"),
    ]


def normalize_extra_date_pairs(extra_date_pairs):
    """规范化用户额外指定的日期对列表"""
    if not extra_date_pairs:
        return []

    normalized = []
    for idx, pair in enumerate(extra_date_pairs, 1):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"第{idx}个日期对格式错误，期望两个元素: {pair}")
        normalized.append(normalize_date_pair(pair[0], pair[1]))

    return normalized


def get_effective_extra_date_pairs(extra_date_pairs):
    combined = []
    if extra_date_pairs:
        combined.extend(list(extra_date_pairs))
    if DEFAULT_EXTRA_DATE_PAIRS:
        combined.extend(copy_date_pairs(DEFAULT_EXTRA_DATE_PAIRS))
    return combined


def merge_date_pairs(auto_date_pairs, extra_date_pairs):
    """
    合并自动生成日期对和用户指定日期对：
    - 自动生成日期对优先执行（放在前面）
    - 用户指定日期对追加在后面
    - 完全相同的日期对自动去重（保留首次出现的位置）
    """
    merged = []
    seen = set()

    for pair in (auto_date_pairs or []) + (extra_date_pairs or []):
        if not pair or len(pair) != 2:
            continue
        key = (pair[0], pair[1])
        if key in seen:
            continue
        merged.append([pair[0], pair[1]])
        seen.add(key)

    return merged


def copy_date_pairs(date_pairs):
    """深拷贝日期对列表，避免不同配置共享同一份可变对象"""
    return [pair.copy() for pair in date_pairs]


def _parse_blackout_ranges(blackout_date_ranges):
    normalized = []
    for start_text, end_text in blackout_date_ranges or []:
        start_dt = parse_base_datetime(start_text).replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = parse_base_datetime(end_text).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        if end_dt <= start_dt:
            raise ValueError(f"禁用日期范围无效: {start_text} -> {end_text}")
        normalized.append((start_dt, end_dt))
    return normalized


def date_pair_overlaps_blackout(pickup_date, dropoff_date, blackout_ranges):
    if pickup_date is None or dropoff_date is None:
        return False

    pair_start_date = pickup_date.date()
    pair_end_date = dropoff_date.date()

    for blackout_start, blackout_end in blackout_ranges:
        blackout_start_date = blackout_start.date()
        blackout_end_date = (blackout_end - timedelta(days=1)).date()
        if pair_start_date <= blackout_end_date and pair_end_date >= blackout_start_date:
            return True
    return False


def filter_date_pairs_against_blackout(date_pairs, blackout_ranges):
    filtered = []
    for pair in date_pairs or []:
        if not pair or len(pair) != 2:
            continue

        pickup_date = parse_base_datetime(pair[0])
        dropoff_date = parse_base_datetime(pair[1])
        if date_pair_overlaps_blackout(pickup_date, dropoff_date, blackout_ranges):
            continue
        filtered.append([pair[0], pair[1]])
    return filtered


def _is_valid_auto_pickup_date(
    pickup_date,
    window_start,
    window_end,
    lor,
    current_date,
    blackout_ranges,
    require_dropoff_within_window=True,
):
    if pickup_date is None:
        return False
    if pickup_date < window_start:
        return False
    if pickup_date > window_end:
        return False
    if require_dropoff_within_window and pickup_date + timedelta(days=lor) > window_end:
        return False
    if pickup_date.date() == current_date.date():
        return False
    return not date_pair_overlaps_blackout(
        pickup_date,
        pickup_date + timedelta(days=lor),
        blackout_ranges,
    )


def schedule_in_window(start_bound, end_bound, lor, current_date, blackout_ranges):
    """在窗口内安排租期（用于1-7天和8-14天窗口，优先安排在最后位置）"""
    if start_bound > end_bound:
        return None

    latest_pickup = end_bound - timedelta(days=lor)
    if latest_pickup < start_bound:
        return None

    pickup = latest_pickup
    while pickup >= start_bound:
        if _is_valid_auto_pickup_date(pickup, start_bound, end_bound, lor, current_date, blackout_ranges):
            return pickup
        pickup -= timedelta(days=1)
    return None


def schedule_middle_in_window(start_bound, end_bound, lor, current_date, blackout_ranges):
    """在窗口内安排中间位置的租期（用于15-30天和30-60天窗口）"""
    if start_bound > end_bound:
        return None

    window_days = (end_bound - start_bound).days
    if window_days <= 0:
        return None

    middle_offset = window_days // 2
    ideal_pickup = start_bound + timedelta(days=middle_offset)

    candidate_days = []
    day = start_bound
    while day <= end_bound:
        candidate_days.append(day)
        day += timedelta(days=1)

    candidate_days.sort(
        key=lambda candidate: (
            abs((candidate - ideal_pickup).days),
            -candidate.toordinal(),
        )
    )

    for pickup in candidate_days:
        if _is_valid_auto_pickup_date(
            pickup,
            start_bound,
            end_bound,
            lor,
            current_date,
            blackout_ranges,
            require_dropoff_within_window=False,
        ):
            return pickup
    return None


def schedule_shared_middle_pickup(start_bound, end_bound, lors, current_date, blackout_ranges):
    """为中长窗口优先选择一个可同时承载多个LOR的公共pickup。"""
    if start_bound > end_bound:
        return None

    valid_lors = sorted({lor for lor in lors if isinstance(lor, int) and lor > 0})
    if not valid_lors:
        return None

    max_lor = max(valid_lors)
    window_days = (end_bound - start_bound).days
    middle_offset = window_days // 2
    ideal_pickup = start_bound + timedelta(days=middle_offset)

    candidate_days = []
    day = start_bound
    while day <= end_bound:
        candidate_days.append(day)
        day += timedelta(days=1)

    candidate_days.sort(
        key=lambda candidate: (
            abs((candidate - ideal_pickup).days),
            -candidate.toordinal(),
        )
    )

    for pickup in candidate_days:
        if all(
            _is_valid_auto_pickup_date(
                pickup,
                start_bound,
                end_bound,
                lor,
                current_date,
                blackout_ranges,
                require_dropoff_within_window=False,
            )
            for lor in valid_lors
        ):
            return pickup
    return None


def make_pair(pickup_date, lor, current_date):
    """创建日期对，如果取车日是当天则返回None"""
    if pickup_date is None:
        return None

    if pickup_date.date() == current_date.date():
        return None

    dropoff_date = pickup_date + timedelta(days=lor)
    return [
        pickup_date.strftime("%Y-%m-%d 10:00"),
        dropoff_date.strftime("%Y-%m-%d 10:00"),
    ]


def get_blackout_ranges_for_pick(pick=None):
    if pick:
        pick_key = str(pick).strip().upper()
        if pick_key in PICK_BLACKOUT_DATE_RANGES:
            return _parse_blackout_ranges(PICK_BLACKOUT_DATE_RANGES[pick_key])
    return _parse_blackout_ranges(BLACKOUT_DATE_RANGES)


def calculate_date_pairs(base_datetime=None, blackout_ranges=None):
    """
    计算日期对 - 统一版本

    基于当前运行时间计算，窗口划分：
    - 窗口1: 1-7天
    - 窗口2: 8-14天
    - 窗口3: 15-30天
    - 窗口4: 30-60天
    """
    current_date = parse_base_datetime(base_datetime) or datetime.now()
    current_date = current_date.replace(hour=0, minute=0, second=0, microsecond=0)
    blackout_ranges = blackout_ranges if blackout_ranges is not None else _parse_blackout_ranges(BLACKOUT_DATE_RANGES)

    date_pairs = []
    lors = [2, 4, 6]

    for _, start_offset, end_offset, strategy in AUTO_WINDOW_DEFINITIONS:
        window_start = current_date + timedelta(days=start_offset)
        window_end = current_date + timedelta(days=end_offset)
        if window_start > window_end:
            continue

        shared_middle_pickup = None
        if strategy == "middle":
            shared_middle_pickup = schedule_shared_middle_pickup(
                window_start,
                window_end,
                lors,
                current_date,
                blackout_ranges,
            )

        for lor in lors:
            if strategy == "late":
                pickup_date = schedule_in_window(window_start, window_end, lor, current_date, blackout_ranges)
            else:
                pickup_date = shared_middle_pickup
                if pickup_date is None or not _is_valid_auto_pickup_date(
                    pickup_date,
                    window_start,
                    window_end,
                    lor,
                    current_date,
                    blackout_ranges,
                    require_dropoff_within_window=False,
                ):
                    pickup_date = schedule_middle_in_window(
                        window_start,
                        window_end,
                        lor,
                        current_date,
                        blackout_ranges,
                    )
            pair = make_pair(pickup_date, lor, current_date)
            if pair:
                date_pairs.append(pair)

    unique_pairs = []
    seen_keys = set()
    for pair in date_pairs:
        key = (pair[0], pair[1])
        if key not in seen_keys:
            unique_pairs.append(pair)
            seen_keys.add(key)
    return unique_pairs


def generate_config(base_datetime=None, extra_date_pairs=None, picks=None, replace_date_pairs=False):
    """
    生成所有机场的配置 - 统一版本
    所有POI都使用相同的日期生成规则
    """
    effective_extra_date_pairs = list(extra_date_pairs or [])
    if not replace_date_pairs:
        effective_extra_date_pairs = get_effective_extra_date_pairs(extra_date_pairs)
    normalized_extra_date_pairs = normalize_extra_date_pairs(effective_extra_date_pairs)

    poi_configs = {
        "BKS": {"source_countries": ["2_HK"], "currency": "JPY"},
        "CTS": {"source_countries": ["10_KR"], "currency": "JPY"},
        "FUK": {"source_countries": ["2_HK"], "currency": "JPY"},
        "JDM": {"source_countries": ["2_HK"], "currency": "JPY"},
        "NRT": {"source_countries": ["2_HK"], "currency": "JPY"},
        "QCQ": {"source_countries": ["2_HK"], "currency": "JPY"},
        "KUL": {"source_countries": ["14_MY"], "currency": "MYR"},
        "OKA": {"source_countries": ["10_KR"], "currency": "JPY"},
        "SMV": {"source_countries": ["14_MY"], "currency": "MYR"},
        "KHH": {"source_countries": ["2_HK"], "currency": "TWD"},
        "GTZY": {"source_countries": ["2_HK"], "currency": "TWD"},
        "GXCZ": {"source_countries": ["2_HK"], "currency": "TWD"},
        "TPE": {"source_countries": ["2_HK"], "currency": "TWD"},
        "NGO": {"source_countries": ["10_KR"], "currency": "JPY"},
        "HKT": {"source_countries": ["20_CN"], "currency": "THB"},
    }

    selected_picks = normalize_picks(picks)
    if selected_picks:
        missing = [pick for pick in selected_picks if pick not in poi_configs]
        if missing:
            raise ValueError(f"未找到以下 pick 的配置: {', '.join(missing)}")
        poi_configs = {key: value for key, value in poi_configs.items() if key in selected_picks}

    config = {}
    for poi_name, poi_info in poi_configs.items():
        blackout_ranges = get_blackout_ranges_for_pick(poi_name)
        auto_date_pairs = [] if replace_date_pairs else calculate_date_pairs(
            base_datetime,
            blackout_ranges=blackout_ranges,
        )
        filtered_extra_date_pairs = filter_date_pairs_against_blackout(
            copy_date_pairs(normalized_extra_date_pairs),
            blackout_ranges,
        )
        if replace_date_pairs:
            combined_date_pairs = copy_date_pairs(filtered_extra_date_pairs)
        else:
            combined_date_pairs = merge_date_pairs(auto_date_pairs, filtered_extra_date_pairs)

        config[poi_name] = {
            "source_countries": poi_info["source_countries"],
            "currency": poi_info["currency"],
            "date_pairs": copy_date_pairs(combined_date_pairs),
            "enable_filter": True,
            "enable_analyzer": True,
        }

    lor_stats = {2: 0, 4: 0, 6: 0}
    for pair in combined_date_pairs:
        try:
            pickup_date = datetime.strptime(pair[0], "%Y-%m-%d %H:%M")
            dropoff_date = datetime.strptime(pair[1], "%Y-%m-%d %H:%M")
            lor = (dropoff_date - pickup_date).days
            if lor in lor_stats:
                lor_stats[lor] += 1
        except Exception:
            pass

    print("[OK] 统一日期生成规则，所有POI使用相同配置")
    print(f"[OK] 自动生成的日期对数量: {len(auto_date_pairs)}")
    print(f"[OK] 额外指定的日期对数量: {len(normalized_extra_date_pairs)}")
    print(f"[OK] 最终执行的日期对总数: {len(combined_date_pairs)}")
    print(f"[OK] 租期分布: 2天{lor_stats[2]}个 4天{lor_stats[4]}个 6天{lor_stats[6]}个")

    return config


def save_config_to_file(config, filename=None):
    """将配置保存到JSON文件"""
    try:
        config_path = get_config_path() if filename is None else filename
        config_dir = os.path.dirname(config_path)
        if config_dir and not os.path.exists(config_dir):
            os.makedirs(config_dir, exist_ok=True)
            print(f"创建目录: {config_dir}")

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"配置已成功保存到 {config_path}")
        return config_path
    except Exception as e:
        print(f"保存配置到文件时出错: {e}")
        return None


def main(base_datetime_str=None, extra_date_pairs=None, picks=None, replace_date_pairs=False):
    """
    主函数

    Args:
        base_datetime_str: 可选，指定生成日期的基准日期/时间，YYYY-MM-DD 或 YYYY-MM-DD HH:MM
        extra_date_pairs: 可选，额外指定要运行的日期对列表
    """
    print("=" * 50)
    print("配置生成工具 - 统一版本")
    print("=" * 50)

    base_datetime = None
    if base_datetime_str:
        try:
            base_datetime = parse_base_datetime(base_datetime_str)
            print(f"使用指定基准日期: {base_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
        except ValueError as e:
            print(f"[ERROR] 基准日期解析失败: {e}")
            return None
    else:
        base_datetime = datetime.now()
        print(f"使用当前时间作为基准: {base_datetime.strftime('%Y-%m-%d %H:%M:%S')}")

    current_dir = os.path.dirname(__file__) or os.getcwd()
    print(f"当前目录: {current_dir}")
    print(f"脚本位置: {__file__}")

    config_path = get_config_path()
    print(f"配置文件将保存到: {config_path}")

    print("\n正在生成配置...")
    try:
        config = generate_config(
            base_datetime=base_datetime,
            extra_date_pairs=extra_date_pairs,
            picks=picks,
            replace_date_pairs=replace_date_pairs,
        )
    except ValueError as e:
        print(f"[ERROR] 日期对生成失败: {e}")
        return None

    print(f"\n{'-' * 50}")
    saved_path = save_config_to_file(config, config_path)

    run_time = datetime.now()
    print(f"\n运行时间: {run_time.strftime('%Y-%m-%d %H:%M:%S')}")
    if base_datetime_str:
        print(f"基准日期: {base_datetime.strftime('%Y-%m-%d %H:%M:%S')}")

    current_date = base_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
    print("\n日期窗口信息:")
    print(f"  窗口1 (1-7天): {current_date + timedelta(days=1)} 至 {current_date + timedelta(days=7)}")
    print(f"  窗口2 (8-14天): {current_date + timedelta(days=8)} 至 {current_date + timedelta(days=14)}")
    print(f"  窗口3 (15-30天): {current_date + timedelta(days=15)} 至 {current_date + timedelta(days=30)}")
    print(f"  窗口4 (30-60天): {current_date + timedelta(days=31)} 至 {current_date + timedelta(days=60)}")

    return saved_path


def cli_main():
    """命令行入口：支持指定基准日期生成配置"""
    parser = argparse.ArgumentParser(description="Klook 配置生成工具（统一版本）")
    parser.add_argument(
        "--base-date",
        dest="base_date",
        default=None,
        help="指定生成日期的基准日期/时间：YYYY-MM-DD 或 YYYY-MM-DD HH:MM（不传则按当前时间自动生成）",
    )
    parser.add_argument(
        "--date-pair",
        dest="date_pairs",
        action="append",
        nargs=2,
        metavar=("取车日期", "还车日期"),
        default=None,
        help="额外指定要运行的日期对，可重复传入（支持YYYY-MM-DD或YYYY-MM-DD HH:MM，日期不带时间则默认10:00）",
    )
    parser.add_argument(
        "--picks",
        dest="picks",
        default=None,
        help="Only generate selected picks, for example BKS or BKS,FUK",
    )
    parser.add_argument(
        "--replace-date-pairs",
        dest="replace_date_pairs",
        action="store_true",
        help="Use only the provided --date-pair values and skip auto-generated date pairs",
    )
    args = parser.parse_args()
    return main(
        base_datetime_str=args.base_date,
        extra_date_pairs=args.date_pairs,
        picks=args.picks,
        replace_date_pairs=args.replace_date_pairs,
    )


if __name__ == "__main__":
    cli_main()
