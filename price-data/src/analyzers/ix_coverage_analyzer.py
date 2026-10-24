# -*- coding: utf-8 -*-
"""
IX 覆盖率生成器（基于 car_*.csv）

目标：
- 输入：data_processor 生成的车辆汇总文件 car_取车点_时间戳.csv
- 输出：按“页面URL”拆分的多个 IX 覆盖率文件（每个URL一份文件）

输出格式：
- 列：ABP=xx_LOR=yy（并包含“日期信息”行用于置顶分组表头）
- 行：车辆总数、IX车辆数、IX——覆盖率
- 固定列：客源地、取车点（用于多组合纵向拼接展示）

口径：
- 车辆总数：按(客源地, 取车点, ABP, LOR)分组后计数（每行代表一辆车）
- IX车辆数：supplier_ids 中包含 1516 的车辆数量
- IX——覆盖率：IX车辆数 / 车辆总数
"""

from __future__ import annotations

import os
import re
from datetime import datetime
import hashlib
from typing import List, Optional, Tuple

import pandas as pd

from .output_header_utils import 为输出添加分组置顶表头


_IX_SUPPLIER_ID = 1516
# 使用非捕获分组，避免 pandas 对“含捕获组正则”的告警
_IX_TOKEN_RE = re.compile(rf"(?:^|[,\s]){_IX_SUPPLIER_ID}(?:[,\s]|$)")
_CAR_FILENAME_RE = re.compile(r"^car_(?P<picks>.+)_(?P<ts>\d{8}_\d{6})\.csv$", re.IGNORECASE)


def _安全文件名片段(text: str, *, max_len: int = 60) -> str:
    """将任意文本压缩为安全的文件名片段（兼容Windows非法字符）。"""
    if text is None:
        return "unknown"
    value = str(text).strip()
    if not value or value.lower() in ("nan", "none", "null"):
        return "unknown"

    value = re.sub(r"[\\/:*?\"<>|\r\n\t]", "_", value)
    value = value.replace(" ", "_")
    value = value.strip("._")
    return (value[:max_len] if len(value) > max_len else value) or "unknown"


def _解析car文件名(car_csv_file: str) -> Tuple[str, str]:
    """从 car 文件名中解析 picks_str 与 timestamp（解析失败则回退到 unknown + 当前时间）。"""
    base = os.path.basename(car_csv_file or "")
    match = _CAR_FILENAME_RE.match(base)
    if match:
        return match.group("picks"), match.group("ts")
    return "unknown", datetime.now().strftime("%Y%m%d_%H%M%S")


def _格式化月日(value) -> str:
    """将日期格式化为 m/d（支持 'YYYY-MM-DD HH:MM' / 'YYYY-MM-DD' / pandas Timestamp）。"""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""

    # pandas Timestamp / datetime / date
    if hasattr(value, "month") and hasattr(value, "day"):
        try:
            return f"{int(value.month)}/{int(value.day)}"
        except Exception:
            return ""

    text = str(value).strip()
    if not text or text.lower() in ("nan", "none", "null"):
        return ""

    try:
        dt = pd.to_datetime(text, errors="coerce")
        if pd.isna(dt):
            # 兼容：仅取日期部分再尝试
            date_part = text.split()[0]
            dt = pd.to_datetime(date_part, errors="coerce")
        if pd.isna(dt):
            return ""
        return f"{int(dt.month)}/{int(dt.day)}"
    except Exception:
        return ""


def _默认输出目录(car_csv_file: str) -> str:
    """默认输出目录：与 car 文件同目录。"""
    return os.path.dirname(os.path.abspath(car_csv_file))


def _生成覆盖率文件名(
    *,
    ts: str,
    poi_name: str,
    poi_id: str,
    page_url: str,
) -> str:
    """生成覆盖率文件名（每个URL一份文件）。"""
    name_part = _安全文件名片段(poi_name, max_len=40)
    id_part = _安全文件名片段(poi_id, max_len=30)

    # 用 URL 的短哈希确保“每个URL一个文件”且避免同 poi 覆盖
    url_text = (page_url or "").strip()
    url_hash = hashlib.md5(url_text.encode("utf-8")).hexdigest()[:8] if url_text else "no_url"

    # poi_id 缺失时仍保证可读
    if id_part == "unknown":
        return f"IX_覆盖率_{name_part}_{url_hash}{ts}.csv"
    return f"IX_覆盖率_{name_part}_{url_hash}{ts}.csv"


def generate_ix_coverage_from_car_csv(
    car_csv_file: str,
    *,
    output_dir: Optional[str] = None,
) -> List[str]:
    """
    根据 car_*.csv 生成 IX 覆盖率文件（按页面URL拆分，每个URL一份）。

    Args:
        car_csv_file: 车辆汇总 car 文件路径
        output_dir: 输出目录（不传则与 car 文件同目录）

    Returns:
        生成的覆盖率文件路径列表；若无有效数据则返回空列表
    """
    if not car_csv_file or not os.path.exists(car_csv_file):
        raise FileNotFoundError(f"找不到 car 文件: {car_csv_file}")

    df = pd.read_csv(car_csv_file, encoding="utf-8-sig")
    if df.empty:
        return []

    required_cols = ["客源地", "取车点", "ABP", "LOR", "supplier_ids", "p_date", "d_date"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"car 文件缺少必要列: {missing}")

    df["ABP"] = pd.to_numeric(df["ABP"], errors="coerce")
    df["LOR"] = pd.to_numeric(df["LOR"], errors="coerce")

    # IX判定：supplier_ids 中包含 1516
    supplier_text = df["supplier_ids"].fillna("").astype(str)
    df["_是否IX"] = supplier_text.str.contains(_IX_TOKEN_RE, regex=True)

    # 先按页面URL拆分：每个URL输出一个文件（相同URL不同日期对在同一文件中）
    url_col = "页面URL" if "页面URL" in df.columns else None
    if url_col is None:
        # 兼容旧car文件：无法按URL拆分时，退化为全量生成一个文件
        df["_页面URL"] = ""
        url_col = "_页面URL"

    url_series = df[url_col].fillna("").astype(str).str.strip()
    df["_页面URL"] = url_series

    output_dir = output_dir or _默认输出目录(car_csv_file)
    os.makedirs(output_dir, exist_ok=True)

    _, ts = _解析car文件名(car_csv_file)

    output_files: List[str] = []
    url_grouped = df.groupby("_页面URL", dropna=False)

    for page_url, url_df in url_grouped:
        if url_df.empty:
            continue

        poi_name = ""
        poi_id = ""
        if "地点" in url_df.columns:
            poi_name = str(url_df["地点"].dropna().iloc[0]) if not url_df["地点"].dropna().empty else ""
        if "poi_id" in url_df.columns:
            poi_id = str(url_df["poi_id"].dropna().iloc[0]) if not url_df["poi_id"].dropna().empty else ""

        out_name = _生成覆盖率文件名(ts=ts, poi_name=poi_name, poi_id=poi_id, page_url=page_url)
        out_path = os.path.join(output_dir, out_name)

        # 在同一URL文件内，仍按(客源地, 取车点)分组输出（与劣势分析一致，便于纵向拼接）
        grouped = url_df.groupby(["客源地", "取车点"], dropna=False)

        all_results = []
        for (customer_country, pickup_location), group_df in grouped:
            if group_df.empty:
                continue

            # 取该组存在的 ABP-LOR 组合（按 ABP、LOR 升序）
            pairs_df = (
                group_df[["ABP", "LOR"]]
                .dropna()
                .drop_duplicates(subset=["ABP", "LOR"], keep="first")
                .sort_values(by=["ABP", "LOR"], ascending=True)
            )
            if pairs_df.empty:
                continue

            date_row = {}
            total_row = {}
            ix_row = {}
            coverage_row = {}

            for _, pair in pairs_df.iterrows():
                abp_val = int(pair["ABP"])
                lor_val = int(pair["LOR"])
                col_name = f"ABP={abp_val}_LOR={lor_val}"

                subset = group_df[(group_df["ABP"] == abp_val) & (group_df["LOR"] == lor_val)]
                total_cars = int(len(subset))
                ix_cars = int(subset["_是否IX"].sum())

                total_row[col_name] = total_cars
                ix_row[col_name] = ix_cars

                if total_cars <= 0:
                    coverage_row[col_name] = "0.00%"
                    date_row[col_name] = ""
                    continue

                coverage_rate = (ix_cars / total_cars) * 100
                coverage_row[col_name] = f"{coverage_rate:.2f}%"

                p_date = subset["p_date"].iloc[0] if "p_date" in subset.columns else ""
                d_date = subset["d_date"].iloc[0] if "d_date" in subset.columns else ""
                p_md = _格式化月日(p_date)
                d_md = _格式化月日(d_date)
                date_row[col_name] = f"p:{p_md},d:{d_md}" if (p_md or d_md) else ""

            result_df = pd.DataFrame(
                [total_row, ix_row, coverage_row],
                index=["车辆总数", "IX车辆数", "IX——覆盖率"],
            )

            date_df = pd.DataFrame([date_row], index=["日期信息"])
            result_df = pd.concat([date_df, result_df], ignore_index=False)

            # 固定列：客源地、取车点（便于多组纵向拼接）
            result_df.insert(0, "客源地", customer_country)
            result_df.insert(1, "取车点", pickup_location)

            all_results.append(result_df)

        if not all_results:
            continue

        final_result = pd.concat(all_results, ignore_index=False)

        # 输出与劣势分析一致：添加“置顶表头”与列分组
        output_df = 为输出添加分组置顶表头(final_result)
        output_df.to_csv(out_path, encoding="utf-8-sig", index=True)
        output_files.append(out_path)

    return output_files


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python -m analyzers.ix_coverage_analyzer <car_*.csv> [output_dir]")
        raise SystemExit(2)

    input_file = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) >= 3 else None
    results = generate_ix_coverage_from_car_csv(input_file, output_dir=out_dir)
    if results:
        for p in results:
            print(f"IX覆盖率文件已生成: {p}")
    else:
        print("未生成IX覆盖率文件：输入数据为空或缺少有效ABP/LOR组合。")
