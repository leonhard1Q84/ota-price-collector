# -*- coding: utf-8 -*-
import re
from datetime import datetime

import numpy as np
import pandas as pd

from .output_header_utils import 为输出添加分组置顶表头


RULE_DEFINITIONS = [
    {"name": "4座车价格最低", "seat": 4, "keyword": None},
    {"name": "4座车，品牌型号包含myvi", "seat": 4, "keyword": "myvi"},
    {"name": "5座车，品牌型号包含vios", "seat": 5, "keyword": "vios"},
    {"name": "5座车，品牌型号包含saga", "seat": 5, "keyword": "saga"},
    {"name": "5座车，品牌型号包含persona", "seat": 5, "keyword": "persona"},
    {"name": "5座车，品牌型号包含taft", "seat": 5, "keyword": "taft"},
    {"name": "5座车价格最低", "seat": 5, "keyword": None},
    {"name": "5座车，品牌型号包含myvi", "seat": 5, "keyword": "myvi"},
    {"name": "7座车，品牌型号包含avanza", "seat": 7, "keyword": "avanza"},
    {"name": "7座车，品牌型号包含alza", "seat": 7, "keyword": "alza"},
    {"name": "7座车，品牌型号包含vellfire", "seat": 7, "keyword": "vellfire"},
    {"name": "7座车，品牌型号包含innova", "seat": 7, "keyword": "innova"},
    {"name": "14座车，品牌型号包含urvan", "seat": 14, "keyword": "urvan"},
]

MODEL_YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
IX_SUPPLIER = "IX RENTAL"


def clean_price(price):
    if pd.isna(price):
        return np.nan
    if isinstance(price, str):
        price_str = str(price).replace('"', "").replace(",", "").strip()
        if not price_str:
            return np.nan
        try:
            return float(price_str)
        except ValueError:
            return np.nan
    return float(price)


def extract_model_years(brand_model):
    if pd.isna(brand_model):
        return []
    return [int(match.group(0)) for match in MODEL_YEAR_PATTERN.finditer(str(brand_model))]


def get_candidate_year(brand_model, is_ix):
    years = extract_model_years(brand_model)
    if not years:
        return None
    return min(years) if is_ix else max(years)


def get_ix_reference_year(ix_df):
    if ix_df.empty:
        return None

    priced_ix_df = ix_df[ix_df["价格（/天）"].notna()].sort_values(by="价格（/天）")
    reference_source = priced_ix_df if not priced_ix_df.empty else ix_df
    reference_brand_model = reference_source.iloc[0]["品牌型号"]
    years = extract_model_years(reference_brand_model)
    return min(years) if years else None


def apply_year_window(df, ix_reference_year, is_ix):
    if df.empty or ix_reference_year is None:
        return df.copy()

    min_year = ix_reference_year - 4
    working_df = df.copy()
    working_df["_candidate_year"] = working_df["品牌型号"].apply(lambda text: get_candidate_year(text, is_ix))
    working_df = working_df[
        working_df["_candidate_year"].notna()
        & working_df["_candidate_year"].between(min_year, ix_reference_year)
    ].copy()
    return working_df.drop(columns=["_candidate_year"])


def build_rule_candidate_frames(df, rule):
    seat_subset = df[df["座位"] == rule["seat"]].copy()
    ix_base = seat_subset[seat_subset["供应商品牌"] == IX_SUPPLIER].copy()
    non_ix_base = seat_subset[seat_subset["供应商品牌"] != IX_SUPPLIER].copy()

    keyword = rule["keyword"]
    fallback_used = False

    if keyword:
        ix_subset = ix_base[ix_base["品牌型号"].str.contains(keyword, case=False, na=False)].copy()
        preferred_non_ix_subset = non_ix_base[
            non_ix_base["品牌型号"].str.contains(keyword, case=False, na=False)
        ].copy()
        if preferred_non_ix_subset.empty:
            non_ix_subset = non_ix_base.copy()
            fallback_used = not non_ix_base.empty
        else:
            non_ix_subset = preferred_non_ix_subset
    else:
        ix_subset = ix_base
        non_ix_subset = non_ix_base

    ix_reference_year = get_ix_reference_year(ix_subset)
    ix_subset = apply_year_window(ix_subset, ix_reference_year, is_ix=True)
    non_ix_subset = apply_year_window(non_ix_subset, ix_reference_year, is_ix=False)

    return ix_subset, non_ix_subset, ix_reference_year, fallback_used


class DisadvantageAnalyzer:
    """劣势分析器（动态ABP+LOR值，仅显示存在的组合）"""

    def __init__(self):
        self.rules = RULE_DEFINITIONS

    def get_group_label(self, abp_val, p_date_str=None, d_date_str=None):
        """根据ABP值返回对应的分组名称（无节假日）"""
        if abp_val < 7:
            return "1-7组"
        if 7 <= abp_val <= 15:
            return "8-15组"
        if 16 <= abp_val <= 30:
            return "15-30组"
        if 31 <= abp_val <= 60:
            return "30-60组"
        return "其他组"

    def format_date(self, date_str):
        if pd.isna(date_str) or not date_str:
            return ""
        try:
            if isinstance(date_str, str):
                date_part = date_str.split()[0]
                date_obj = pd.to_datetime(date_part)
                return f"{date_obj.month}/{date_obj.day}"
            if hasattr(date_str, "month") and hasattr(date_str, "day"):
                return f"{date_str.month}/{date_str.day}"
            return str(date_str)[:10]
        except Exception:
            return str(date_str)[:10]

    def analyze_cell(self, df, rule, abp_val, lor_val):
        subset = df[(df["ABP"] == abp_val) & (df["LOR"] == lor_val)].copy()
        if subset.empty:
            return np.nan

        ix_subset, non_ix_subset, _, _ = build_rule_candidate_frames(subset, rule)
        if ix_subset.empty and non_ix_subset.empty:
            return np.nan
        if ix_subset.empty or non_ix_subset.empty:
            return 0

        ix_min = ix_subset["价格（/天）"].min()
        non_ix_min = non_ix_subset["价格（/天）"].min()
        return round(ix_min - non_ix_min, 2)

    def analyze(self, input_data):
        if isinstance(input_data, str):
            df = pd.read_csv(input_data, encoding="utf-8")
        elif isinstance(input_data, pd.DataFrame):
            df = input_data.copy()
        else:
            raise ValueError("输入数据必须是DataFrame或文件路径")

        df["价格（/天）"] = df["价格（/天）"].apply(clean_price)
        df["座位"] = pd.to_numeric(df["座位"], errors="coerce")
        df["ABP"] = pd.to_numeric(df["ABP"], errors="coerce")
        df["LOR"] = pd.to_numeric(df["LOR"], errors="coerce")

        for col in ["车型", "动力类型", "品牌型号", "供应商品牌", "客源地", "取车点"]:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

        has_date_columns = ("p_datetime" in df.columns) and ("d_datetime" in df.columns)
        pickup_location = df["取车点"].iloc[0] if not df.empty else "未知取车点"
        customer_country = "未知客源地"
        if "客源地" in df.columns:
            country_values = df["客源地"].dropna().astype(str).str.strip()
            country_values = country_values[country_values != ""]
            if not country_values.empty:
                customer_country = country_values.iloc[0]

        print("数据统计信息:")
        print(f"总数据量: {len(df)}")
        print(f"IX RENTAL数量: {(df['供应商品牌'] == IX_SUPPLIER).sum()}")
        print(f"非IX RENTAL数量: {(df['供应商品牌'] != IX_SUPPLIER).sum()}")

        seen_pairs = set()
        abp_lor_pairs = []
        date_info_list = []
        group_label_list = []

        for _, row in df.iterrows():
            if pd.notna(row["ABP"]) and pd.notna(row["LOR"]):
                abp_val = float(row["ABP"])
                lor_val = float(row["LOR"])
                pair = (abp_val, lor_val)
                if pair in seen_pairs:
                    continue

                seen_pairs.add(pair)
                abp_lor_pairs.append(pair)

                p_dt = row["p_datetime"] if has_date_columns else ""
                d_dt = row["d_datetime"] if has_date_columns else ""
                date_info = f"p:{self.format_date(p_dt)},d:{self.format_date(d_dt)}" if has_date_columns else ""
                group_label = self.get_group_label(abp_val, p_dt, d_dt)

                date_info_list.append(date_info)
                group_label_list.append(group_label)

        print(f"存在的 ABP-LOR 组合数: {len(abp_lor_pairs)}")

        col_names = []
        group_dict = {}
        date_dict = {}
        for i, (abp_val, lor_val) in enumerate(abp_lor_pairs):
            col_name = f"ABP={int(abp_val)}_LOR={int(lor_val)}"
            col_names.append(col_name)
            group_dict[col_name] = group_label_list[i]
            date_dict[col_name] = date_info_list[i]

        results = {}
        for rule in self.rules:
            ix_subset, non_ix_subset, ix_reference_year, fallback_used = build_rule_candidate_frames(df, rule)
            ref_text = ix_reference_year if ix_reference_year is not None else "无"
            print(
                f"规则: {rule['name']}, IX数量: {len(ix_subset)}, 非IX数量: {len(non_ix_subset)}, "
                f"IX参考年: {ref_text}, 非IX回退座位: {'是' if fallback_used else '否'}"
            )

            row_data = {}
            for col_name in col_names:
                parts = col_name.split("_")
                abp_val = int(parts[0].split("=")[1])
                lor_val = int(parts[1].split("=")[1])
                row_data[col_name] = self.analyze_cell(df, rule, abp_val, lor_val)

            results[rule["name"]] = row_data

        result_df = pd.DataFrame(results).T
        result_df = result_df.reindex(index=[rule["name"] for rule in self.rules], columns=col_names)

        if result_df.empty:
            print("警告：所有列均为 NaN，结果为空。")
            result_df = pd.DataFrame(index=[rule["name"] for rule in self.rules])

        disadvantage_index_row = {}
        total_rules = len(self.rules)
        for col in result_df.columns:
            valid_values = result_df[col].dropna()
            if valid_values.empty:
                disadvantage_index_row[col] = np.nan
                continue

            positive_count = (valid_values > 0).sum()
            disadvantage_index_row[col] = round(positive_count / total_rules, 4)

        result_df.loc["劣势指数"] = pd.Series(disadvantage_index_row)

        bm_percentage_row = {}
        for col in result_df.columns:
            idx_val = result_df.loc["劣势指数", col]
            if pd.isna(idx_val):
                bm_percentage_row[col] = np.nan
            else:
                bm_percentage_row[col] = f"{(1 - idx_val) * 100:.1f}%"
        result_df.loc["BM%"] = pd.Series(bm_percentage_row)

        group_row = {col: group_dict.get(col, "") for col in result_df.columns}
        date_row = {col: date_dict.get(col, "") for col in result_df.columns}
        top_rows = pd.DataFrame([group_row, date_row], index=["组别", "日期信息"])
        result_df = pd.concat([top_rows, result_df])

        result_df.insert(0, "客源地", customer_country)
        result_df.insert(1, "取车点", pickup_location)
        return result_df


def process_disadvantage_analysis(input_file):
    try:
        print(f"\n{'=' * 80}")
        print(f"开始执行劣势分析程序 - 输入文件: {input_file}")
        print(f"{'=' * 80}")

        analyzer = DisadvantageAnalyzer()
        result = analyzer.analyze(input_file)

        print("\n" + "=" * 120)
        print("数据比较结果表格（动态ABP+LOR值）")
        print("=" * 120)
        print("取车点:", result["取车点"].iloc[0] if "取车点" in result.columns else result.iloc[0, 0])
        print("-" * 120)

        col_width = 15
        print(f"{'规则':<20}", end="")
        for col in result.columns[2:]:
            display_col = col[: col_width - 3] + "..." if len(col) > col_width else col
            print(f"{display_col:<{col_width}}", end="")
        print()
        print("-" * 120)

        for idx, row in result.iterrows():
            print(f"{idx:<20}", end="")
            for col in result.columns[2:]:
                val = row[col]
                if pd.isna(val) or val == "":
                    print(f"{'':<{col_width}}", end="")
                elif isinstance(val, (int, float)):
                    print(f"{val:<{col_width}.2f}", end="")
                else:
                    print(f"{str(val)[:col_width]:<{col_width}}", end="")
            print()

        print("\n说明:")
        print("1. 数值：IX RENTAL最低价 - 非IX RENTAL最低价")
        print("2. 品牌规则优先比较同品牌；同座位无同品牌时，非IX回退到同座位比较")
        print("3. 年限窗口按IX车型年份向前4年过滤，例如2023只比较2019-2023")
        print("4. 劣势指数：13条规则等权，正值规则数 ÷ 13")
        print("5. BM% = (1 - 劣势指数) × 100%")
        print("6. 仅显示存在的 ABP-LOR 组合")
        print("-" * 120)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"SMV_BM{timestamp}.csv"
        output_df = 为输出添加分组置顶表头(result)
        output_df.to_csv(output_file, encoding="utf-8-sig", index=True)

        print(f"\n结果已保存到: {output_file}")
        print("=" * 120)
        return output_file

    except Exception as e:
        print(f"劣势分析程序出错: {e}")
        import traceback

        traceback.print_exc()
        return None
