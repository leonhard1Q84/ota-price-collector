# data_filter.py
import re
from datetime import datetime

import numpy as np
import pandas as pd


RULE_NAMES = [
    '全平台最低价',
    '5座价格',
    '6座价格',
    'SUV4座价格',
    '7座价格',
    'alphard&vellfire价格',
]


def format_date(date_str):
    """格式化日期，只显示月/日"""
    if pd.isna(date_str) or not date_str:
        return ""

    try:
        if isinstance(date_str, str):
            date_part = date_str.split()[0] if ' ' in date_str else date_str
            date_obj = pd.to_datetime(date_part)
            return f"{date_obj.month}/{date_obj.day}"
        if hasattr(date_str, 'month') and hasattr(date_str, 'day'):
            return f"{date_str.month}/{date_str.day}"
        return str(date_str)[:10]
    except Exception:
        return str(date_str)[:10]


def clean_price(price):
    """清理价格列：移除非数字字符后转为数值类型"""
    if pd.isna(price):
        return np.nan
    if isinstance(price, str):
        price_str = re.sub(r'[^\d.]', '', price)
        try:
            return float(price_str)
        except ValueError:
            return np.nan
    return float(price)


def get_top_n_unique(data, n=4):
    """按价格排序，并按价格+供应商品牌去重后取前 n 条"""
    if data.empty:
        return data

    data_with_price = data[data['价格（/天）'].notna()].copy()
    if data_with_price.empty:
        return data_with_price

    sorted_data = data_with_price.sort_values(by='价格（/天）')
    deduped = sorted_data.drop_duplicates(subset=['价格（/天）', '供应商品牌'], keep='first')
    return deduped.head(n)


def is_ix_supplier(df):
    return df['供应商品牌'].astype(str).str.contains('ix rental', case=False, na=False)


def get_min_non_ix_record(data, rule_name, abp_val, lor_val):
    """获取非 IX 供应商的最低价记录"""
    if data.empty:
        return None

    non_ix_data = data[~is_ix_supplier(data)].copy()
    if non_ix_data.empty:
        return None

    min_record = non_ix_data.sort_values(by='价格（/天）').iloc[0].to_dict()
    return {
        '规则': rule_name,
        'ABP': abp_val,
        'LOR': lor_val,
        '供应商品牌': min_record.get('供应商品牌', ''),
        '品牌型号': min_record.get('品牌型号', ''),
        '车型': min_record.get('车型', ''),
        '价格（/天）': min_record.get('价格（/天）', np.nan),
    }


def build_rule_datasets(abp_lor_subset):
    ix_mask = is_ix_supplier(abp_lor_subset)

    return {
        '全平台最低价': abp_lor_subset[
            (~ix_mask) | (ix_mask & (abp_lor_subset['座位'] == 4))
        ].copy(),
        '5座价格': abp_lor_subset[abp_lor_subset['座位'] == 5].copy(),
        '6座价格': abp_lor_subset[abp_lor_subset['座位'] == 6].copy(),
        'SUV4座价格': abp_lor_subset[
            abp_lor_subset['车型'].str.contains('越野车SUV', case=False, na=False)
            & (abp_lor_subset['座位'] == 4)
        ].copy(),
        '7座价格': abp_lor_subset[
            (abp_lor_subset['座位'] >= 7)
            & (~abp_lor_subset['品牌型号'].str.contains('alphard|vellfire', case=False, na=False))
        ].copy(),
        'alphard&vellfire价格': abp_lor_subset[
            abp_lor_subset['品牌型号'].str.contains('alphard|vellfire', case=False, na=False)
        ].copy(),
    }


def process_data_filter(input_file):
    """处理数据过滤逻辑"""
    try:
        print(f"\n{'=' * 80}")
        print(f"开始执行数据过滤程序 - 输入文件: {input_file}")
        print(f"{'=' * 80}")

        df = pd.read_csv(input_file)

        print("数据列名:", df.columns.tolist())
        print("\n前5行数据:")
        print(df.head())

        df['价格（/天）'] = df['价格（/天）'].apply(clean_price)
        df['座位'] = pd.to_numeric(df['座位'], errors='coerce')
        df['ABP'] = pd.to_numeric(df['ABP'], errors='coerce')
        df['LOR'] = pd.to_numeric(df['LOR'], errors='coerce')

        for col in ['车型', '动力类型', '品牌型号', '供应商品牌', '客源地', '取车点', 'p_datetime', 'd_datetime']:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

        all_results = []
        min_non_ix_records = []
        min_price_matrix = {}
        datetime_matrix = {}

        if '客源地' in df.columns and '取车点' in df.columns:
            country_pickup_combinations = df[['客源地', '取车点']].drop_duplicates()
        else:
            country_pickup_combinations = pd.DataFrame({'客源地': ['默认'], '取车点': ['默认']})

        print(f"\n开始处理 {len(country_pickup_combinations)} 个组合...")

        for _, row in country_pickup_combinations.iterrows():
            country = row['客源地']
            pickup = row['取车点']

            print(f"\n正在处理: 客源地={country}, 取车点={pickup}")

            subset = df[(df['客源地'] == country) & (df['取车点'] == pickup)].copy()
            print(f"  原始数据量: {len(subset)} 条")

            if subset.empty:
                print(f"  警告: 客源地={country}, 取车点={pickup} 无数据或过滤后无数据")
                continue

            subset_abp_values = sorted(int(value) for value in subset['ABP'].dropna().unique())
            subset_lor_values = sorted(int(value) for value in subset['LOR'].dropna().unique())

            print(f"  ABP值: {subset_abp_values}")
            print(f"  LOR值: {subset_lor_values}")

            abp_lor_count = 0
            for abp_val in subset_abp_values:
                for lor_val in subset_lor_values:
                    abp_lor_subset = subset[(subset['ABP'] == abp_val) & (subset['LOR'] == lor_val)].copy()

                    if abp_lor_subset.empty:
                        continue

                    abp_lor_count += 1
                    country_pickup_key = f"{country}_{pickup}"
                    abp_lor_key = f"{abp_val}_{lor_val}"

                    if country_pickup_key not in min_price_matrix:
                        min_price_matrix[country_pickup_key] = {}
                        datetime_matrix[country_pickup_key] = {}

                    if abp_lor_key not in min_price_matrix[country_pickup_key]:
                        min_price_matrix[country_pickup_key][abp_lor_key] = {}

                    first_record = abp_lor_subset.iloc[0]
                    datetime_matrix[country_pickup_key][abp_lor_key] = {
                        'p_datetime': format_date(first_record.get('p_datetime', '')),
                        'd_datetime': format_date(first_record.get('d_datetime', '')),
                    }

                    rule_datasets = build_rule_datasets(abp_lor_subset)
                    rule_results = {}

                    for rule_name in RULE_NAMES:
                        rule_df = rule_datasets[rule_name]
                        result_df = get_top_n_unique(rule_df, 4)
                        if not result_df.empty:
                            result_df['规则分类'] = rule_name
                            min_record = get_min_non_ix_record(rule_df, rule_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)
                                min_price_matrix[country_pickup_key][abp_lor_key][rule_name] = min_record['价格（/天）']
                        rule_results[rule_name] = result_df

                    current_result = pd.concat(
                        [rule_results[rule_name] for rule_name in RULE_NAMES],
                        ignore_index=True,
                    )

                    if not current_result.empty:
                        current_result['客源地'] = country
                        current_result['取车点'] = pickup
                        current_result['ABP'] = abp_val
                        current_result['LOR'] = lor_val
                        all_results.append(current_result)

                        counts = [len(rule_results[rule_name]) for rule_name in RULE_NAMES]
                        print(f"  -> 筛选出 {len(current_result)} 条数据")
                        print(
                            "     "
                            f"规则1: {counts[0]}条 规则2: {counts[1]}条 规则3: {counts[2]}条 "
                            f"规则4: {counts[3]}条 规则5: {counts[4]}条 规则6: {counts[5]}条"
                        )
                    else:
                        print("  -> 未筛选出任何数据")

            if abp_lor_count == 0:
                print("  警告: 未找到有效的 ABP-LOR 组合")

        if not all_results:
            print("没有筛选出任何数据，请检查原始数据和筛选条件。")
            return None, None

        final_result = pd.concat(all_results, ignore_index=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f'OKA_filtered_data_{timestamp}.csv'
        final_result.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"\n处理完成！共处理 {len(country_pickup_combinations)} 个组合")
        print(f"共保留 {len(final_result)} 条数据，已保存到 {output_file}")

        if not min_price_matrix:
            print("未生成非IX最低价矩阵（无有效数据）")
            return output_file, None

        all_abp_lor_keys = []
        for country_pickup_key in min_price_matrix:
            for abp_lor_key in min_price_matrix[country_pickup_key]:
                if abp_lor_key not in all_abp_lor_keys:
                    all_abp_lor_keys.append(abp_lor_key)

        col_names = [f"ABP={key.split('_')[0]}_LOR={key.split('_')[1]}" for key in all_abp_lor_keys]
        abp_lor_key_to_col = dict(zip(all_abp_lor_keys, col_names))

        all_formatted_results = []
        for country_pickup_key in min_price_matrix:
            if '_' in country_pickup_key:
                country, pickup = country_pickup_key.split('_', 1)
            else:
                country = "未知客源地"
                pickup = "未知取车点"

            result_dict = {rule_name: {} for rule_name in RULE_NAMES}
            date_info = {}

            for abp_lor_key in all_abp_lor_keys:
                col_name = abp_lor_key_to_col[abp_lor_key]
                dt = datetime_matrix.get(country_pickup_key, {}).get(abp_lor_key, {})
                p_date = dt.get('p_datetime', '')
                d_date = dt.get('d_datetime', '')
                date_info[col_name] = f"p:{p_date},d:{d_date}" if p_date or d_date else ""

                rule_prices = min_price_matrix[country_pickup_key].get(abp_lor_key, {})
                for rule_name in RULE_NAMES:
                    result_dict[rule_name][col_name] = rule_prices.get(rule_name, np.nan)

            df_part = pd.DataFrame(result_dict).T.reindex(RULE_NAMES)
            date_row = pd.DataFrame([date_info], index=['日期信息'])
            df_part = pd.concat([date_row, df_part])
            df_part.insert(0, '客源地', country)
            df_part.insert(1, '取车点', pickup)
            all_formatted_results.append(df_part)

        final_matrix_df = pd.concat(all_formatted_results, axis=0)
        min_output_file = f'OKA_min_{timestamp}.csv'
        final_matrix_df.to_csv(min_output_file, index=True, encoding='utf-8-sig')

        print(f"\n已生成 OKA_min 文件: {min_output_file}")
        print("   行结构: 日期信息 + 6条新 OKA 规则名")
        print("   列结构: 客源地, 取车点, ABP=xxx_LOR=yyy...")
        print("   注意: 未包含 '劣势指数' 和 'BM%'（因该文件仅保留非IX最低价）")

        return output_file, min_output_file
    except Exception as e:
        print(f"数据过滤程序出错: {e}")
        import traceback
        traceback.print_exc()
        return None, None
