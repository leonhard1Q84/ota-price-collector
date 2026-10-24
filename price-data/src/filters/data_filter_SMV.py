# data_filter.py
import re
from datetime import datetime

import numpy as np
import pandas as pd


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
    """清理价格列：移除逗号，转换为数值类型"""
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
    """按价格排序 + 按价格和供应商品牌去重 + 取前n条"""
    if data.empty:
        return data
    data_with_price = data[data["价格（/天）"].notna()].copy()
    if data_with_price.empty:
        return data_with_price
    sorted_data = data_with_price.sort_values(by="价格（/天）")
    deduped = sorted_data.drop_duplicates(subset=["价格（/天）", "供应商品牌"], keep="first")
    return deduped.head(n)


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


def format_rule_log(rule_name, result_count, ix_reference_year, fallback_used):
    fallback_text = "是" if fallback_used else "否"
    ref_text = ix_reference_year if ix_reference_year is not None else "无"
    return f"{rule_name}:{result_count}(IX参考年={ref_text}, 非IX回退座位={fallback_text})"


def process_data_filter(input_file):
    """处理 SMV 数据过滤逻辑"""
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
        df["ABP"] = pd.to_numeric(df["ABP"], errors="coerce")
        df["LOR"] = pd.to_numeric(df["LOR"], errors="coerce")

        for col in ["车型", "动力类型", "品牌型号", "供应商品牌", "客源地", "取车点"]:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

        all_results = []

        if "客源地" in df.columns and "取车点" in df.columns:
            country_pickup_combinations = df[["客源地", "取车点"]].drop_duplicates()
        else:
            country_pickup_combinations = pd.DataFrame({"客源地": ["默认"], "取车点": ["默认"]})

        print(f"\n开始处理 {len(country_pickup_combinations)} 个组合...")

        for _, row in country_pickup_combinations.iterrows():
            country = row["客源地"]
            pickup = row["取车点"]
            print(f"\n正在处理: 客源地={country}, 取车点={pickup}")

            subset = df[(df["客源地"] == country) & (df["取车点"] == pickup)].copy()
            print(f"  原始数据量: {len(subset)} 条")

            if subset.empty:
                print(f"  警告: 客源地={country}, 取车点={pickup} 无数据")
                continue

            subset_abp_values = subset["ABP"].dropna().unique()
            subset_lor_values = subset["LOR"].dropna().unique()

            print(f"  ABP值: {subset_abp_values.tolist()}")
            print(f"  LOR值: {subset_lor_values.tolist()}")

            abp_lor_count = 0
            for abp_val in subset_abp_values:
                for lor_val in subset_lor_values:
                    abp_lor_subset = subset[
                        (subset["ABP"] == abp_val) & (subset["LOR"] == lor_val)
                    ].copy()

                    if abp_lor_subset.empty:
                        continue

                    abp_lor_count += 1
                    print(
                        f"\n  └─ 处理第{abp_lor_count}个ABP-LOR组合: "
                        f"ABP={abp_val}, LOR={lor_val}, 数据量: {len(abp_lor_subset)}条"
                    )

                    current_rule_results = []
                    rule_logs = []

                    for rule in RULE_DEFINITIONS:
                        ix_subset, non_ix_subset, ix_reference_year, fallback_used = build_rule_candidate_frames(
                            abp_lor_subset, rule
                        )
                        rule_candidates = pd.concat([ix_subset, non_ix_subset], ignore_index=True)
                        result = get_top_n_unique(rule_candidates, 4)

                        if not result.empty:
                            result["规则分类"] = rule["name"]
                            current_rule_results.append(result)

                        rule_logs.append(
                            format_rule_log(rule["name"], len(result), ix_reference_year, fallback_used)
                        )

                    if current_rule_results:
                        current_result = pd.concat(current_rule_results, ignore_index=True)
                        current_result["客源地"] = country
                        current_result["取车点"] = pickup
                        current_result["ABP"] = abp_val
                        current_result["LOR"] = lor_val
                        all_results.append(current_result)

                        print(f"  └─ 筛选出{len(current_result)}条数据")
                        print("     规则结果:", " | ".join(rule_logs))
                    else:
                        print("  └─ 未筛选出任何数据")

            if abp_lor_count == 0:
                print("  警告: 未找到有效的ABP-LOR组合")

        if all_results:
            final_result = pd.concat(all_results, ignore_index=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"SMV_filtered_data_{timestamp}.csv"
            final_result.to_csv(output_file, index=False, encoding="utf-8-sig")

            print(f"\n✅ 处理完成！共处理 {len(country_pickup_combinations)} 个组合")
            print(f"✅ 共保留 {len(final_result)} 条数据，已保存到 {output_file}")

            print("\n各组合结果统计:")
            result_summary = final_result.groupby(["客源地", "取车点"]).size().reset_index(name="数量")
            print(result_summary.to_string(index=False))

            print("\n各规则结果统计（前20个）:")
            rule_summary = final_result.groupby(["规则分类"]).size().reset_index(name="数量")
            rule_summary = rule_summary.sort_values("数量", ascending=False)
            print(rule_summary.head(20).to_string(index=False))

            print("\nABP和LOR组合统计:")
            abp_lor_summary = final_result.groupby(["ABP", "LOR"]).size().reset_index(name="数量")
            abp_lor_summary = abp_lor_summary.sort_values("数量", ascending=False)
            print(abp_lor_summary.to_string(index=False))

            return output_file

        print("❌ 没有筛选出任何数据，请检查原始数据和筛选条件。")
        return None

    except Exception as e:
        print(f"数据过滤程序出错: {e}")
        import traceback

        traceback.print_exc()
        return None
