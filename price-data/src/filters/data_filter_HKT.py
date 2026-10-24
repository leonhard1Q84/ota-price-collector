# -*- coding: utf-8 -*-
import os
import re
from datetime import datetime

import numpy as np
import pandas as pd


HKT_RULES = [
    ("品牌型号含有vios", "vios"),
    ("品牌型号含有yaris", "yaris"),
    ("品牌型号含有ativ", "ativ"),
    ("品牌型号含有city", "city"),
]


def clean_price(price):
    """清理价格列：移除逗号和杂质，转换为数值。"""
    if pd.isna(price):
        return np.nan
    if isinstance(price, str):
        price_str = re.sub(r"[^\d.]", "", price)
        try:
            return float(price_str)
        except ValueError:
            return np.nan
    return float(price)


def get_top_n_unique(data, n=4):
    """按价格升序，按价格+供应商品牌去重，保留前 n 条。"""
    if data.empty:
        return data
    data_with_price = data[data["价格（/天）"].notna()].copy()
    if data_with_price.empty:
        return data_with_price
    sorted_data = data_with_price.sort_values(by="价格（/天）")
    deduped = sorted_data.drop_duplicates(subset=["价格（/天）", "供应商品牌"], keep="first")
    return deduped.head(n)


def _normalize_string_columns(df, columns):
    for col in columns:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()


def _iter_group_keys(df):
    if "客源地" in df.columns and "取车点" in df.columns:
        return df[["客源地", "取车点"]].drop_duplicates().itertuples(index=False, name=None)
    return [("默认", "默认")]


def _iter_abp_lor_pairs(df):
    if "ABP" not in df.columns or "LOR" not in df.columns:
        return []
    return df[["ABP", "LOR"]].dropna().drop_duplicates().itertuples(index=False, name=None)


def process_data_filter(input_file, output_dir=None):
    """按 HKT 劣势分析器同源规则筛选明细数据。"""
    try:
        print(f"\n{'=' * 80}")
        print(f"开始执行数据过滤程序 - 输入文件: {input_file}")
        print(f"{'=' * 80}")

        df = pd.read_csv(input_file)

        print("数据列名:", df.columns.tolist())
        print("\n前5行数据:")
        print(df.head())

        df["价格（/天）"] = df["价格（/天）"].apply(clean_price)
        df["座位"] = pd.to_numeric(df["座位"], errors="coerce")

        _normalize_string_columns(
            df,
            ["车型", "动力类型", "品牌型号", "供应商品牌", "客源地", "取车点", "ABP", "LOR"],
        )

        all_results = []
        group_keys = list(_iter_group_keys(df))
        print(f"\n开始处理 {len(group_keys)} 个组合...")

        for country, pickup in group_keys:
            print(f"\n正在处理: 客源地={country}, 取车点={pickup}")
            subset = df[(df["客源地"] == country) & (df["取车点"] == pickup)].copy()
            print(f"  原始数据量: {len(subset)} 条")

            if subset.empty:
                print(f"  警告: 客源地={country}, 取车点={pickup} 无数据")
                continue

            abp_lor_pairs = list(_iter_abp_lor_pairs(subset))
            print(f"  ABP/LOR组合数: {len(abp_lor_pairs)}")

            if not abp_lor_pairs:
                print("  警告: 未找到有效的ABP-LOR组合")
                continue

            for index, (abp_val, lor_val) in enumerate(abp_lor_pairs, start=1):
                abp_lor_subset = subset[(subset["ABP"] == abp_val) & (subset["LOR"] == lor_val)].copy()
                if abp_lor_subset.empty:
                    continue

                print(
                    f"\n  └─ 处理第{index}个ABP-LOR组合: "
                    f"ABP={abp_val}, LOR={lor_val}, 数据量: {len(abp_lor_subset)}条"
                )

                current_results = []
                for rule_name, keyword in HKT_RULES:
                    rule_df = abp_lor_subset[
                        abp_lor_subset["品牌型号"].str.contains(keyword, case=False, na=False, regex=False)
                    ].copy()
                    rule_result = get_top_n_unique(rule_df, 4)
                    if not rule_result.empty:
                        rule_result["规则分类"] = rule_name
                        current_results.append(rule_result)
                    print(f"     {rule_name}: {len(rule_result)}条")

                if not current_results:
                    print("  └─ 未筛选出任何数据")
                    continue

                current_result = pd.concat(current_results, ignore_index=True)
                current_result["客源地"] = country
                current_result["取车点"] = pickup
                current_result["ABP"] = abp_val
                current_result["LOR"] = lor_val
                all_results.append(current_result)
                print(f"  └─ 筛选出 {len(current_result)} 条数据")

        if not all_results:
            print("没有筛选出任何数据，请检查原始数据和规则。")
            return None

        final_result = pd.concat(all_results, ignore_index=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_name = f"HKT_filtered_data_{timestamp}.csv"

        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, output_name)
        else:
            output_path = output_name

        final_result.to_csv(output_path, index=False, encoding="utf-8-sig")

        print(f"\n处理完成！共处理 {len(group_keys)} 个组合")
        print(f"共保留 {len(final_result)} 条数据，已保存到 {output_path}")
        return output_path
    except Exception as exc:
        print(f"数据过滤程序出错: {exc}")
        import traceback

        traceback.print_exc()
        return None
