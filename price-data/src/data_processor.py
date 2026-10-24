# -*- coding: utf-8 -*-
"""
数据处理模块
负责从API响应中提取车辆数据并保存到CSV文件
"""
import os
import csv
import re
from datetime import datetime
from logger import log_info, log_warning, log_success
from path_utils import get_data_dir, get_src_dir, ensure_dir


def process_data(data):
    """处理数据，提取items字段（如果存在）"""
    if isinstance(data, dict) and "items" in data:
        return data["items"]
    return data


all_rows = 0


def _safe_dict(value):
    return value if isinstance(value, dict) else {}


def _safe_list(value):
    return value if isinstance(value, list) else []


def increment(rows):
    global all_rows
    all_rows += rows


def get_advance_booking_period(abp):
    """根据ABP值计算提前预定期分类

    Args:
        abp: 提前预定周期天数

    Returns:
        提前预定期分类字符串
    """
    if abp is None or abp == "":
        return ""
    try:
        abp_int = int(abp)
        if abp_int < 7:
            return "1-7组"
        elif 7 <= abp_int <= 15:
            return "8-15组"
        elif 16 <= abp_int <= 30:
            return "15-30组"
        elif 31 <= abp_int <= 60:
            return "30-60组"
        else:
            return "其他组"
    except (ValueError, TypeError):
        return ""


def extract_car_data(result_data, source_country, p_date, d_date, pick_value=""):
    """从result接口响应数据中提取车辆信息并格式化为CSV行

    Args:
        result_data: result接口响应数据
        source_country: 客源地
        p_date: 取车日期
        d_date: 还车日期
        pick_value: URL中的pick参数值（取车点标识）
    """
    csv_rows = []

    if not isinstance(result_data, dict):
        return csv_rows

    result = _safe_dict(result_data.get("result"))
    if not isinstance(result, dict):
        return csv_rows

    # 获取shop_map和supplier_map
    shop_map = _safe_dict(result.get("shop_map"))
    supplier_map = _safe_dict(result.get("supplier_map"))
    car_card_list = _safe_list(result.get("car_card_list"))

    # 计算ABP（提前预定周期）：取车日期（pDate）减当前日期
    try:
        p_date_obj = datetime.strptime(p_date, "%Y-%m-%d %H:%M")
        now = datetime.now()
        abp_days = (p_date_obj - now).days
    except:
        abp_days = ""

    # 计算提前预定期分类
    advance_booking_period = get_advance_booking_period(abp_days)

    # LOR（租期）
    p_datetime = datetime.strptime(p_date, "%Y-%m-%d %H:%M")
    d_datetime = datetime.strptime(d_date, "%Y-%m-%d %H:%M")
    lor = (d_datetime - p_datetime).days

    # 遍历每辆车
    for car_idx, car in enumerate(car_card_list, 0):
        if not isinstance(car, dict):
            continue

        # 获取车辆基本信息
        car_name = car.get("car_name", "")
        car_brand_name = car.get("car_brand_name", "")
        car_age = car.get("car_age", "")
        brand_model = " ".join(
            str(part).strip()
            for part in (car_brand_name, car_name, car_age)
            if part is not None and str(part).strip()
        )
        car_group_type = car.get("car_group_type", "")
        seat = car.get("seat", "")
        luggage = car.get("luggage", "")
        has_air_condition = car.get("has_air_condition", False)
        transmission_type = car.get("transmission_type", False)

        # A/C：如"空调"
        ac_value = "空调" if has_air_condition else ""

        # Auto: 如"自动"
        auto_value = "自动" if transmission_type else ""

        # 动力类型：从fuel_code或vehicle_tags中提取
        fuel_type = "燃油"  # 默认值（兜底类型）
        fuel_code = car.get("fuel_code")

        # 优先从fuel_code判断
        if fuel_code == "Electric":
            fuel_type = "电动"
        elif fuel_code == "Hybrid":
            fuel_type = "混合动力"
        else:
            # 如果fuel_code为null或不存在，从vehicle_tags中查找
            vehicle_tags = _safe_list(car.get("vehicle_tags"))
            for tag in vehicle_tags:
                if isinstance(tag, dict):
                    tag_type = tag.get("tag_type", "")
                    if tag_type == "FUEL_ELECTRIC_TAG":
                        fuel_type = "电动"
                        break
                    elif tag_type == "FUEL_HYBRID_TAG":
                        fuel_type = "混合动力"
                        break
            # 如果都没找到，使用默认值"燃油"

        # 价格（/天）
        price_info = _safe_dict(car.get("price"))
        price_per_day = price_info.get("amount_per_day", "")
        if price_per_day and isinstance(price_per_day, str):
            # 提取数字部分
            price_match = re.search(r'[\d,.]+', price_per_day)
            if price_match:
                price_per_day = price_match.group()

        # 距站点位置
        min_distance = car.get("min_distance", "")
        distance_text = f"{min_distance}米" if min_distance else ""

        # 评分（从shop_list中获取最高评分，或从supplier_map获取）
        score = ""
        shop_list = _safe_list(car.get("shop_list"))
        if shop_list:
            # 尝试从shop_list中获取评分
            max_rating = max([shop.get("max_rating", 0) for shop in shop_list if isinstance(shop, dict)], default=0)
            if max_rating:
                score = str(max_rating)

        # 遍历每个shop（取车/还车选项）
        for shop_idx, shop in enumerate(shop_list, 1):
            if not isinstance(shop, dict):
                continue

            # 获取便利标签（convenience_label）
            convenience_label = ""
            pickup_shop_key = shop.get("pickup_shop_key", "")

            # 从shop_map中获取pickup_shop并提取convenience_label字段
            if pickup_shop_key and pickup_shop_key in shop_map:
                pickup_shop_info = _safe_dict(_safe_dict(shop_map.get(pickup_shop_key)).get("pickup_shop"))
                if pickup_shop_info:
                    # 提取convenience_label字段的值
                    convenience_label = pickup_shop_info.get("convenience_label", "")
                    # 如果convenience_label是列表，转换为字符串（用逗号分隔）
                    if isinstance(convenience_label, list):
                        convenience_label = ", ".join(str(item) for item in convenience_label if item)

            # 供应商品牌
            supplier_id = shop.get("supplier_id", "")
            supplier_name = ""
            if supplier_id and str(supplier_id) in supplier_map:
                supplier_info = _safe_dict(supplier_map.get(str(supplier_id)))
                supplier_name = supplier_info.get("name", "")

            # 如果shop有特定的价格，使用shop的价格
            shop_price = _safe_dict(shop.get("price"))
            if shop_price.get("amount_per_day"):
                shop_price_per_day = shop_price.get("amount_per_day", "")
                if shop_price_per_day and isinstance(shop_price_per_day, str):
                    price_match = re.search(r'[\d,.]+', shop_price_per_day)
                    if price_match:
                        price_per_day = price_match.group()

            # 如果shop有特定的距离，使用shop的距离
            shop_min_distance = shop.get("min_distance", "")
            if shop_min_distance:
                distance_text = f"{shop_min_distance}米"

            # 如果shop有特定的评分，使用shop的评分
            shop_max_rating = shop.get("max_rating", "")
            if shop_max_rating:
                score = str(shop_max_rating)

            # 排名：shop在shop_list中的位置（从1开始）
            rank = shop_idx

            # 获取pick_value，优先从result_data中获取，如果没有则使用传入的默认值
            current_pick_value = result_data.get("pick_value", pick_value)

            # 创建CSV行，添加pick_value和convenience_label，以及p_datetime和d_datetime
            csv_row = {
                "客源地": source_country,
                "ABP": abp_days,
                "提前预定期": advance_booking_period,  # 新增列
                "LOR": lor,
                "取车点": current_pick_value,
                "便利标签": convenience_label,  # 新增列
                "车型": car_group_type,
                "品牌型号": brand_model,
                "座位": seat,
                "行李": luggage,
                "A/C": ac_value,
                "Auto": auto_value,
                "动力类型": fuel_type,
                "供应商品牌": supplier_name,
                "价格（/天）": price_per_day,
                "距站点位置": distance_text,
                "排名": rank,
                "评分": score,
                "p_datetime": p_date,  # 新增列
                "d_datetime": d_date  # 新增列
            }

            csv_rows.append(csv_row)

    return csv_rows


def save_to_csv(data, filename="output.csv", source_country="", p_date="", d_date="", pick_value=""):
    """将数据保存到CSV文件

    Args:
        data: 要保存的数据
        filename: CSV文件名（默认output.csv）
        source_country: 客源地
        p_date: 取车日期
        d_date: 还车日期
        pick_value: URL中的pick参数值（用于标识取车点）
    """
    if not data:
        log_warning(f"没有数据可保存到 {filename}")
        return

    # 确保data目录存在
    data_dir = ensure_dir(get_data_dir())

    # 如果filename不包含路径，则保存到data目录
    if os.path.dirname(filename) == "":
        filename = os.path.join(data_dir, filename)
    elif not os.path.isabs(filename):
        filename = os.path.normpath(os.path.join(get_src_dir(), filename))

    ensure_dir(os.path.dirname(filename))

    # 提取CSV行数据，传递pick_value参数
    csv_rows = []

    for index, item in enumerate(data, 1):
        extracted_rows = []

        try:
            if isinstance(item, dict):
                extracted_rows = extract_car_data(item, source_country, p_date, d_date, pick_value)
            elif isinstance(item, list):
                for sub_index, sub_item in enumerate(item, 1):
                    if not isinstance(sub_item, dict):
                        continue
                    try:
                        extracted_rows.extend(
                            extract_car_data(sub_item, source_country, p_date, d_date, pick_value)
                        )
                    except Exception as exc:
                        log_warning(f"第{index}组第{sub_index}个响应包提取失败，已跳过：{exc}")
            else:
                log_warning(f"第{index}组数据类型不支持：{type(item)}，已跳过")
                continue
        except Exception as exc:
            log_warning(f"第{index}组响应包提取失败，已跳过：{exc}")
            continue
        csv_rows.extend(extracted_rows)

    if not csv_rows:
        log_warning(f"没有可保存的数据到 {filename}")
        return

    # 定义CSV字段顺序，在最后添加"p_datetime"和"d_datetime"字段
    fieldnames = [
        "客源地", "ABP", "提前预定期", "LOR", "取车点", "便利标签", "车型", "品牌型号",
        "座位", "行李", "A/C", "Auto", "动力类型", "供应商品牌",
        "价格（/天）", "距站点位置", "排名", "评分",
        "p_datetime", "d_datetime"  # 新增字段
    ]

    # 累计总数据量
    increment(len(csv_rows))

    # 写入CSV文件（追加模式）
    file_exists = os.path.exists(filename)
    with open(filename, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerows(csv_rows)

    log_success(f"数据已追加到 {filename}（本次追加 {len(csv_rows)} 条，总共 {all_rows} 条，取车点: {pick_value}）")
