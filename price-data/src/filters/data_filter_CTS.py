# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import re
from datetime import datetime


RULE_SMALL_FAMILY = '5座非混合动力'
RULE_SUV = '5座越野车SUV'
RULE_BUSINESS = '商务车7座，品牌型号不包含alphard或vellfire'
RULE_ALPHARD_VELLFIRE = '品牌型号包含alphard或vellfire'
RULE_ALPHARD_8 = '座位=8，品牌型号包含alphard'
RULE_NAMES = [RULE_SMALL_FAMILY, RULE_SUV, RULE_BUSINESS, RULE_ALPHARD_VELLFIRE, RULE_ALPHARD_8]

CTS_SUV_TYPES = r'越野车SUV|SUV'
CTS_BUSINESS_TYPES = r'商务车'
CTS_ALPHARD_VELLFIRE_MODELS = r'alphard|vellfire'
CTS_ALPHARD_MODELS = r'alphard'


def is_ix_supplier(series):
    return series.astype(str).str.strip().str.lower().eq('ix rental')


def format_date(date_str):
    if pd.isna(date_str) or not date_str:
        return ''
    try:
        if isinstance(date_str, str):
            date_part = date_str.split()[0] if ' ' in date_str else date_str
            date_obj = pd.to_datetime(date_part)
            return f'{date_obj.month}/{date_obj.day}'
        if hasattr(date_str, 'month') and hasattr(date_str, 'day'):
            return f'{date_str.month}/{date_str.day}'
        return str(date_str)[:10]
    except Exception:
        return str(date_str)[:10]


def clean_price(price):
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
    if data.empty:
        return data
    data_with_price = data[data['价格（/天）'].notna()].copy()
    if data_with_price.empty:
        return data_with_price
    sorted_data = data_with_price.sort_values(by='价格（/天）')
    deduped = sorted_data.drop_duplicates(subset=['价格（/天）', '供应商品牌'], keep='first')
    return deduped.head(n)


def get_min_non_ix_record(data, rule_name, abp_val, lor_val):
    if data.empty:
        return None

    non_ix_data = data[~is_ix_supplier(data['供应商品牌'])].copy()
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
        '价格（/天）': min_record.get('价格（/天）', np.nan)
    }


def build_rule_frames(abp_lor_subset):
    rule1 = abp_lor_subset[
        (abp_lor_subset['座位'] == 5) &
        (~abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False))
    ].copy()

    rule2 = abp_lor_subset[
        (abp_lor_subset['座位'] == 5) &
        (abp_lor_subset['车型'].astype(str).str.contains(CTS_SUV_TYPES, case=False, na=False))
    ].copy()

    rule3 = abp_lor_subset[
        (abp_lor_subset['座位'] == 7) &
        (abp_lor_subset['车型'].astype(str).str.contains(CTS_BUSINESS_TYPES, case=False, na=False)) &
        (~abp_lor_subset['品牌型号'].astype(str).str.contains(CTS_ALPHARD_VELLFIRE_MODELS, case=False, na=False))
    ].copy()

    rule4 = abp_lor_subset[
        abp_lor_subset['品牌型号'].astype(str).str.contains(CTS_ALPHARD_VELLFIRE_MODELS, case=False, na=False)
    ].copy()

    rule5 = abp_lor_subset[
        (abp_lor_subset['座位'] == 8) &
        (abp_lor_subset['品牌型号'].astype(str).str.contains(CTS_ALPHARD_MODELS, case=False, na=False))
    ].copy()

    return {
        RULE_SMALL_FAMILY: rule1,
        RULE_SUV: rule2,
        RULE_BUSINESS: rule3,
        RULE_ALPHARD_VELLFIRE: rule4,
        RULE_ALPHARD_8: rule5,
    }


def process_data_filter(input_file):
    try:
        print(f"\n{'=' * 80}")
        print(f'开始执行数据过滤程序 - 输入文件: {input_file}')
        print(f"{'=' * 80}")

        df = pd.read_csv(input_file)
        df['价格（/天）'] = df['价格（/天）'].apply(clean_price)
        df['座位'] = pd.to_numeric(df['座位'], errors='coerce')

        for col in ['车型', '动力类型', '品牌型号', '供应商品牌', '客源地', '取车点', 'ABP', 'LOR']:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

        all_results = []
        min_non_ix_records = []
        min_price_matrix = {}
        datetime_info = {}

        if '客源地' in df.columns and '取车点' in df.columns:
            country_pickup_combinations = df[['客源地', '取车点']].drop_duplicates()
        else:
            country_pickup_combinations = pd.DataFrame({'客源地': ['默认'], '取车点': ['默认']})

        for _, row in country_pickup_combinations.iterrows():
            country = row['客源地']
            pickup = row['取车点']
            subset = df[(df['客源地'] == country) & (df['取车点'] == pickup)].copy()
            if subset.empty:
                continue

            subset['ABP'] = pd.to_numeric(subset['ABP'], errors='coerce')
            subset['LOR'] = pd.to_numeric(subset['LOR'], errors='coerce')

            for abp_val in subset['ABP'].dropna().unique():
                for lor_val in subset['LOR'].dropna().unique():
                    abp_lor_subset = subset[(subset['ABP'] == abp_val) & (subset['LOR'] == lor_val)].copy()
                    if abp_lor_subset.empty:
                        continue

                    country_pickup_key = f'{country}_{pickup}'
                    abp_lor_key = f'{int(abp_val)}_{int(lor_val)}'
                    min_price_matrix.setdefault(country_pickup_key, {})
                    datetime_info.setdefault(country_pickup_key, {})
                    min_price_matrix[country_pickup_key].setdefault(abp_lor_key, {})

                    if abp_lor_key not in datetime_info[country_pickup_key]:
                        datetime_info[country_pickup_key][abp_lor_key] = {
                            'p_datetime': format_date(abp_lor_subset.iloc[0].get('p_datetime', '')),
                            'd_datetime': format_date(abp_lor_subset.iloc[0].get('d_datetime', '')),
                        }

                    current_frames = []
                    for rule_name, rule_df in build_rule_frames(abp_lor_subset).items():
                        result = get_top_n_unique(rule_df, 4)
                        if not result.empty:
                            result['规则分类'] = rule_name
                            current_frames.append(result)

                        min_record = get_min_non_ix_record(rule_df, rule_name, int(abp_val), int(lor_val))
                        if min_record:
                            min_non_ix_records.append(min_record)
                            min_price_matrix[country_pickup_key][abp_lor_key][rule_name] = min_record['价格（/天）']

                    if current_frames:
                        current_result = pd.concat(current_frames, ignore_index=True)
                        current_result['客源地'] = country
                        current_result['取车点'] = pickup
                        current_result['ABP'] = int(abp_val)
                        current_result['LOR'] = int(lor_val)
                        all_results.append(current_result)

        if not all_results:
            print('没有筛选出任何数据，请检查原始数据和筛选条件。')
            return None, None

        final_result = pd.concat(all_results, ignore_index=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f'CTS_filtered_data_{timestamp}.csv'
        final_result.to_csv(output_file, index=False, encoding='utf-8-sig')

        min_output_file = None
        if min_price_matrix:
            all_abp_lor_keys = sorted({
                abp_lor_key
                for country_pickup_key in min_price_matrix
                for abp_lor_key in min_price_matrix[country_pickup_key]
            })

            matrix_rows = []
            for country_pickup_key in sorted(min_price_matrix.keys()):
                if '_' in country_pickup_key:
                    country, pickup = country_pickup_key.split('_', 1)
                else:
                    country, pickup = country_pickup_key, ''

                for rule_name in RULE_NAMES:
                    row_data = {
                        '客源地': country,
                        '取车点': pickup,
                        '规则': rule_name,
                    }
                    for abp_lor_key in all_abp_lor_keys:
                        row_data[abp_lor_key] = min_price_matrix[country_pickup_key].get(abp_lor_key, {}).get(rule_name, np.nan)
                    matrix_rows.append(row_data)

                date_row_data = {
                    '客源地': country,
                    '取车点': pickup,
                    '规则': '日期',
                }
                for abp_lor_key in all_abp_lor_keys:
                    info = datetime_info.get(country_pickup_key, {}).get(abp_lor_key, {})
                    p_datetime_val = info.get('p_datetime', '')
                    d_datetime_val = info.get('d_datetime', '')
                    if p_datetime_val and d_datetime_val:
                        date_row_data[abp_lor_key] = f'{p_datetime_val} / {d_datetime_val}'
                    else:
                        date_row_data[abp_lor_key] = p_datetime_val or d_datetime_val or np.nan
                matrix_rows.append(date_row_data)

            matrix_df = pd.DataFrame(matrix_rows)
            base_cols = ['客源地', '取车点', '规则']
            other_cols = sorted([col for col in matrix_df.columns if col not in base_cols])
            matrix_df = matrix_df[base_cols + other_cols]

            min_output_file = f'CTS_min_{timestamp}.csv'
            matrix_df.to_csv(min_output_file, index=False, encoding='utf-8-sig')

        print(f'处理完成，已保存到: {output_file}')
        return output_file, min_output_file
    except Exception as exc:
        print(f'数据过滤程序出错: {exc}')
        import traceback
        traceback.print_exc()
        return None, None
