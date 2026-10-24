# data_filter.py
import pandas as pd
import numpy as np
import re
from datetime import datetime

def format_date(date_str):
    """格式化日期，只显示月/日"""
    if pd.isna(date_str) or not date_str:
        return ""

    try:
        if isinstance(date_str, str):
            date_part = date_str.split()[0] if ' ' in date_str else date_str
            date_obj = pd.to_datetime(date_part)
            return f"{date_obj.month}/{date_obj.day}"
        elif hasattr(date_str, 'month') and hasattr(date_str, 'day'):
            return f"{date_str.month}/{date_str.day}"
        else:
            return str(date_str)[:10]
    except:
        return str(date_str)[:10]


def clean_price(price):
    """清理价格列：移除逗号，转换为数值类型"""
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
    """辅助函数：按价格排序 + 按价格和供应商品牌去重 + 取前n条"""
    if data.empty:
        return data
    data_with_price = data[data['价格（/天）'].notna()].copy()
    if data_with_price.empty:
        return data_with_price
    sorted_data = data_with_price.sort_values(by='价格（/天）')
    deduped = sorted_data.drop_duplicates(subset=['价格（/天）', '供应商品牌'], keep='first')
    return deduped.head(n)


def get_min_non_ix_record(data, rule_name, abp_val, lor_val):
    """获取非IX供应商的最低价格记录"""
    if data.empty:
        return None

    non_ix_data = data[~data['供应商品牌'].str.lower().str.contains('ix rental', na=False)].copy()

    if non_ix_data.empty:
        return None

    sorted_data = non_ix_data.sort_values(by='价格（/天）')
    min_record = sorted_data.iloc[0].to_dict()

    result = {
        '规则': rule_name,
        'ABP': abp_val,
        'LOR': lor_val,
        '供应商品牌': min_record.get('供应商品牌', ''),
        '品牌型号': min_record.get('品牌型号', ''),
        '车型': min_record.get('车型', ''),
        '价格（/天）': min_record.get('价格（/天）', np.nan)
    }

    return result


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

        for col in ['车型', '动力类型', '品牌型号', '供应商品牌', '客源地', '取车点', 'ABP', 'LOR', 'p_datetime', 'd_datetime']:
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

        for idx, row in country_pickup_combinations.iterrows():
            country = row['客源地']
            pickup = row['取车点']

            print(f"\n正在处理: 客源地={country}, 取车点={pickup}")

            subset = df[(df['客源地'] == country) & (df['取车点'] == pickup)].copy()
            print(f"  原始数据量: {len(subset)} 条")

            if subset.empty:
                print(f"  警告: 客源地={country}, 取车点={pickup} 无数据或过滤后无数据")
                continue

            subset_abp_values = subset['ABP'].dropna().unique()
            subset_lor_values = subset['LOR'].dropna().unique()

            print(f"  ABP值: {subset_abp_values.tolist()}")
            print(f"  LOR值: {subset_lor_values.tolist()}")

            abp_lor_count = 0
            for abp_val in subset_abp_values:
                for lor_val in subset_lor_values:
                    abp_lor_subset = subset[(subset['ABP'] == abp_val) & (subset['LOR'] == lor_val)].copy()

                    if not abp_lor_subset.empty:
                        abp_lor_count += 1
                        print(
                            f"\n  └─ 处理第{abp_lor_count}个ABP-LOR组合: ABP={abp_val}, LOR={lor_val}, 数据量: {len(abp_lor_subset)}条")

                        country_pickup_key = f"{country}_{pickup}"
                        abp_lor_key = f"{abp_val}_{lor_val}"

                        if country_pickup_key not in min_price_matrix:
                            min_price_matrix[country_pickup_key] = {}
                            datetime_matrix[country_pickup_key] = {}

                        if abp_lor_key not in min_price_matrix[country_pickup_key]:
                            min_price_matrix[country_pickup_key][abp_lor_key] = {}

                        first_record = abp_lor_subset.iloc[0]
                        p_datetime_val = first_record.get('p_datetime', '')
                        d_datetime_val = first_record.get('d_datetime', '')

                        p_datetime_formatted = format_date(p_datetime_val)
                        d_datetime_formatted = format_date(d_datetime_val)

                        datetime_matrix[country_pickup_key][abp_lor_key] = {
                            'p_datetime': p_datetime_formatted,
                            'd_datetime': d_datetime_formatted
                        }

                        # ===== 规则 1：其他供应商全平台最低价（IX Rental车型包含n_box/spacia/tanto） =====
                        rule1_name = '其他供应商全平台最低价'
                        rule1 = abp_lor_subset[
                            (abp_lor_subset['供应商品牌'].str.lower() == 'ix rental') &
                            (abp_lor_subset['车型'].str.lower().str.contains('n_box|spacia|tanto', case=False, na=False, regex=True))
                        ].copy()
                        result1 = get_top_n_unique(rule1, 4)
                        if not result1.empty:
                            result1['规则分类'] = rule1_name
                            min_record = get_min_non_ix_record(rule1, rule1_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)
                                min_price_matrix[country_pickup_key][abp_lor_key][rule1_name] = min_record['价格（/天）']

                        # ===== 规则 2：其他供应商5座最低价（IX Rental车型包含aqua/fit/yaris） =====
                        rule2_name = '其他供应商5座最低价'
                        rule2 = abp_lor_subset[
                            (abp_lor_subset['供应商品牌'].str.lower() == 'ix rental') &
                            (abp_lor_subset['车型'].str.lower().str.contains('aqua|fit|yaris', case=False, na=False, regex=True))
                        ].copy()
                        result2 = get_top_n_unique(rule2, 4)
                        if not result2.empty:
                            result2['规则分类'] = rule2_name
                            min_record = get_min_non_ix_record(rule2, rule2_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)
                                min_price_matrix[country_pickup_key][abp_lor_key][rule2_name] = min_record['价格（/天）']

                        # ===== 规则 3：其他供应商SUV最低价（IX Rental车型包含SUV/越野车） =====
                        rule3_name = '其他供应商SUV最低价'
                        rule3 = abp_lor_subset[
                            (abp_lor_subset['供应商品牌'].str.lower() == 'ix rental') &
                            (abp_lor_subset['车型'].str.lower().str.contains('suv|越野车', case=False, na=False, regex=True))
                        ].copy()
                        result3 = get_top_n_unique(rule3, 4)
                        if not result3.empty:
                            result3['规则分类'] = rule3_name
                            min_record = get_min_non_ix_record(rule3, rule3_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)
                                min_price_matrix[country_pickup_key][abp_lor_key][rule3_name] = min_record['价格（/天）']

                        # ===== 规则 4：其他供应商7-8座最低价（IX Rental座位=7/8） =====
                        rule4_name = '其他供应商7-8座最低价'
                        rule4 = abp_lor_subset[
                            (abp_lor_subset['供应商品牌'].str.lower() == 'ix rental') &
                            (abp_lor_subset['座位'].astype(str).str.contains('7|8', na=False, regex=True))
                        ].copy()
                        result4 = get_top_n_unique(rule4, 4)
                        if not result4.empty:
                            result4['规则分类'] = rule4_name
                            min_record = get_min_non_ix_record(rule4, rule4_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)
                                min_price_matrix[country_pickup_key][abp_lor_key][rule4_name] = min_record['价格（/天）']

                        current_result = pd.concat(
                            [result1, result2, result3, result4],
                            ignore_index=True
                        )

                        if not current_result.empty:
                            current_result['客源地'] = country
                            current_result['取车点'] = pickup
                            current_result['ABP'] = abp_val
                            current_result['LOR'] = lor_val
                            all_results.append(current_result)

                            print(f"  └─ 筛选出{len(current_result)}条数据")
                            print(f"     规则1: {len(result1)}条, 规则2: {len(result2)}条, 规则3: {len(result3)}条, 规则4: {len(result4)}条")
                        else:
                            print(f"  └─ 未筛选出任何数据")

            if abp_lor_count == 0:
                print(f"  警告: 未找到有效的ABP-LOR组合")

        if all_results:
            final_result = pd.concat(all_results, ignore_index=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f'JDZ_filtered_data_{timestamp}.csv'
            final_result.to_csv(output_file, index=False, encoding='utf-8-sig')
            print(f"\n处理完成！共处理 {len(country_pickup_combinations)} 个组合")
            print(f"共保留 {len(final_result)} 条数据，已保存到 {output_file}")

            if min_price_matrix:
                # 行顺序：使用新的4个规则名
                original_rule_names = [
                    '其他供应商全平台最低价',
                    '其他供应商5座最低价',
                    '其他供应商SUV最低价',
                    '其他供应商7-8座最低价'
                ]

                # 收集所有 ABP_LOR 组合（保持首次出现顺序）
                all_abp_lor_keys = []
                for cp_key in min_price_matrix:
                    for abp_lor_key in min_price_matrix[cp_key]:
                        if abp_lor_key not in all_abp_lor_keys:
                            all_abp_lor_keys.append(abp_lor_key)

                # 列名格式：ABP=xxx_LOR=yyy
                col_names = [f"ABP={k.split('_')[0]}_LOR={k.split('_')[1]}" for k in all_abp_lor_keys]
                abp_lor_key_to_col = dict(zip(all_abp_lor_keys, col_names))

                all_formatted_results = []

                for country_pickup_key in min_price_matrix:
                    if '_' in country_pickup_key:
                        parts = country_pickup_key.split('_', 1)
                        country = parts[0]
                        pickup = parts[1]
                    else:
                        country = "未知客源地"
                        pickup = "未知取车点"

                    # 初始化每行数据（按原始规则名顺序）
                    result_dict = {rule: {} for rule in original_rule_names}
                    date_info = {}

                    for abp_lor_key in all_abp_lor_keys:
                        col_name = abp_lor_key_to_col[abp_lor_key]

                        # 日期信息
                        dt = datetime_matrix.get(country_pickup_key, {}).get(abp_lor_key, {})
                        p_date = dt.get('p_datetime', '')
                        d_date = dt.get('d_datetime', '')
                        date_info[col_name] = f"p:{p_date},d:{d_date}" if p_date or d_date else ""

                        # 规则价格（仅限原始4个规则）
                        rule_prices = min_price_matrix[country_pickup_key].get(abp_lor_key, {})
                        for rule_name in original_rule_names:
                            price = rule_prices.get(rule_name, np.nan)
                            result_dict[rule_name][col_name] = price

                    # 转为 DataFrame
                    df_part = pd.DataFrame(result_dict).T
                    df_part = df_part.reindex(original_rule_names)

                    # 插入日期信息行（最顶部）
                    date_row = pd.DataFrame([date_info], index=['日期信息'])
                    df_part = pd.concat([date_row, df_part])

                    # 插入客源地、取车点
                    df_part.insert(0, '客源地', country)
                    df_part.insert(1, '取车点', pickup)

                    all_formatted_results.append(df_part)

                # 合并所有组合
                final_matrix_df = pd.concat(all_formatted_results, axis=0)

                min_output_file = f'JDZ_min_{timestamp}.csv'
                final_matrix_df.to_csv(min_output_file, index=True, encoding='utf-8-sig')

                print(f"\n已生成 JDZ_min 文件: {min_output_file}")
                print(f"   行结构: 日期信息 + 4条原始规则名")
                print(f"   列结构: 客源地, 取车点, ABP=xxx_LOR=yyy...")
                print(f"   注意: 未包含 '劣势指数' 和 'BM%'（因无IX数据无法计算）")

                return output_file, min_output_file
            else:
                print("未生成非IX最低价格矩阵（无有效数据）")
                return output_file, None
        else:
            print("没有筛选出任何数据，请检查原始数据和筛选条件。")
            return None, None
    except Exception as e:
        print(f"数据过滤程序出错: {e}")
        import traceback
        traceback.print_traceback()
        return None, None