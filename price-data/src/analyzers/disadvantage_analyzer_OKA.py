# -*- coding: utf-8 -*-
from datetime import datetime

import numpy as np
import pandas as pd

from analyzers.output_header_utils import 为输出添加分组置顶表头


RULE_NAMES = [
    '全平台最低价',
    '5座价格',
    '6座价格',
    'SUV4座价格',
    '7座价格',
    'alphard&vellfire价格',
]


def is_ix_supplier(df):
    return df['供应商品牌'].astype(str).str.contains('ix rental', case=False, na=False)


def seat_equals(df, seat_value):
    return df['座位'] == seat_value


def seat_at_least(df, seat_value):
    return df['座位'] >= seat_value


def excludes_alphard_vellfire(df):
    return ~df['品牌型号'].str.contains('alphard|vellfire', case=False, na=False)


def suv4_mask(df):
    return df['车型'].str.contains('越野车SUV', case=False, na=False) & seat_equals(df, 4)


def alphard_vellfire_mask(df):
    return df['品牌型号'].str.contains('alphard|vellfire', case=False, na=False)


class DisadvantageAnalyzer:
    """劣势分析器（动态 ABP + LOR）"""

    def __init__(self):
        self.rules = []

    def format_date(self, date_str):
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

    def create_rules(self, abp_lor_pairs):
        """根据存在的 ABP/LOR 组合创建规则"""
        rules = []

        for _, (abp, lor) in abp_lor_pairs.iterrows():
            abp_val = int(abp)
            lor_val = int(lor)

            rules.append({
                'name': f'全平台最低价 (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 1,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                    is_ix_supplier(df) &
                    seat_equals(df, 4) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (~is_ix_supplier(df)) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
            })

            rules.append({
                'name': f'5座价格 (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 2,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                    is_ix_supplier(df) &
                    seat_equals(df, 5) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (~is_ix_supplier(df)) &
                    seat_equals(df, 5) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
            })

            rules.append({
                'name': f'6座价格 (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 3,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                    is_ix_supplier(df) &
                    seat_equals(df, 6) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (~is_ix_supplier(df)) &
                    seat_equals(df, 6) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
            })

            rules.append({
                'name': f'SUV4座价格 (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 4,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                    is_ix_supplier(df) &
                    suv4_mask(df) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (~is_ix_supplier(df)) &
                    suv4_mask(df) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
            })

            rules.append({
                'name': f'7座价格 (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 5,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                    is_ix_supplier(df) &
                    seat_at_least(df, 7) &
                    excludes_alphard_vellfire(df) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (~is_ix_supplier(df)) &
                    seat_at_least(df, 7) &
                    excludes_alphard_vellfire(df) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
            })

            rules.append({
                'name': f'alphard&vellfire价格 (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 6,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                    is_ix_supplier(df) &
                    alphard_vellfire_mask(df) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (~is_ix_supplier(df)) &
                    alphard_vellfire_mask(df) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
            })

        return rules

    def clean_price(self, price):
        """清理价格"""
        if pd.isna(price):
            return np.nan
        if isinstance(price, str):
            price_str = str(price).replace('"', '').replace(',', '').strip()
            if not price_str:
                return np.nan
            try:
                return float(price_str)
            except Exception:
                return np.nan
        return float(price)

    def analyze_rule(self, df, rule):
        """分析单个规则"""
        ix_df = df[rule['ix_condition'](df)]
        non_ix_df = df[rule['non_ix_condition'](df)]

        if ix_df.empty or non_ix_df.empty:
            return np.nan

        ix_min = ix_df['价格（/天）'].min()
        non_ix_min = non_ix_df['价格（/天）'].min()
        return round(ix_min - non_ix_min, 2)

    def analyze(self, input_data, pickup_location=None):
        if isinstance(input_data, str):
            df = pd.read_csv(input_data, encoding='utf-8')
        elif isinstance(input_data, pd.DataFrame):
            df = input_data.copy()
        else:
            raise ValueError("输入数据必须是 DataFrame 或文件路径")

        df['价格（/天）'] = df['价格（/天）'].apply(self.clean_price)
        df['座位'] = pd.to_numeric(df['座位'], errors='coerce')
        df['品牌型号'] = df['品牌型号'].astype(str).str.strip()
        df['车型'] = df['车型'].astype(str).str.strip()
        df['动力类型'] = df['动力类型'].astype(str).str.strip()
        df['供应商品牌'] = df['供应商品牌'].astype(str).str.strip()
        df['ABP'] = pd.to_numeric(df['ABP'], errors='coerce')
        df['LOR'] = pd.to_numeric(df['LOR'], errors='coerce')

        has_date_columns = 'p_datetime' in df.columns and 'd_datetime' in df.columns

        if pickup_location is None:
            pickup_location = df['取车点'].iloc[0] if not df.empty else "未知取车点"

        print(f"数据统计信息 - 取车点: {pickup_location}:")
        print(f"  总数据量: {len(df)}")
        print(f"  IX RENTAL数量: {is_ix_supplier(df).sum()}")
        print(f"  非IX RENTAL数量: {(~is_ix_supplier(df)).sum()}")

        if has_date_columns:
            temp_df = df[['ABP', 'LOR', 'p_datetime', 'd_datetime']].dropna(subset=['ABP', 'LOR'])
            abp_lor_info = temp_df.drop_duplicates(subset=['ABP', 'LOR'], keep='first').reset_index(drop=True)
            abp_lor_pairs = abp_lor_info[['ABP', 'LOR']].copy()
        else:
            temp_df = df[['ABP', 'LOR']].dropna()
            abp_lor_pairs = temp_df.drop_duplicates(keep='first').reset_index(drop=True)
            abp_lor_info = None

        print(f"  存在的 ABP-LOR 组合数: {len(abp_lor_pairs)}")

        self.rules = self.create_rules(abp_lor_pairs)
        print(f"  生成的规则总数: {len(self.rules)}")

        results = {}
        date_info = {}

        for rule in self.rules:
            value = self.analyze_rule(df, rule)
            col_name = f'ABP={rule["abp"]}_LOR={rule["lor"]}'
            row_name = self.get_row_name(rule['rule_type'])

            results.setdefault(col_name, {})
            results[col_name][row_name] = value

            if col_name not in date_info and has_date_columns and abp_lor_info is not None:
                date_info_row = abp_lor_info[
                    (abp_lor_info['ABP'] == rule['abp']) &
                    (abp_lor_info['LOR'] == rule['lor'])
                ]
                if not date_info_row.empty:
                    p_date = self.format_date(date_info_row['p_datetime'].iloc[0])
                    d_date = self.format_date(date_info_row['d_datetime'].iloc[0])
                    date_info[col_name] = f"p:{p_date},d:{d_date}"
                else:
                    date_info[col_name] = ""

        result_df = pd.DataFrame(results)
        existing_rows = [row for row in RULE_NAMES if row in result_df.index]
        result_df = result_df.loc[existing_rows]

        if result_df.empty:
            print("  警告：所有数据均为 NaN，结果为空。")
            return pd.DataFrame()

        disadvantage_index_row = {}
        for col in result_df.columns:
            values = result_df[col]
            score = 0.0
            count = 0
            for val in values:
                if pd.isna(val):
                    continue
                count += 1
                if val > 0:
                    score += 1 / len(existing_rows)
            disadvantage_index_row[col] = np.nan if count == 0 else round(score, 3)

        result_df.loc['劣势指数'] = pd.Series(disadvantage_index_row)

        bm_percentage_row = {}
        for col in result_df.columns:
            idx_val = result_df.loc['劣势指数', col]
            bm_percentage_row[col] = np.nan if pd.isna(idx_val) else f"{(1 - idx_val) * 100:.1f}%"
        result_df.loc['BM%'] = pd.Series(bm_percentage_row)

        if date_info:
            date_row = {col: date_info.get(col, '') for col in result_df.columns}
            result_df = pd.concat([pd.DataFrame([date_row], index=['日期信息']), result_df])

        if '客源地' in df.columns and not df['客源地'].isna().all():
            customer_country = df['客源地'].iloc[0] if not df.empty else "未知客源地"
            result_df.insert(0, '客源地', customer_country)
        else:
            result_df.insert(0, '客源地', "未知客源地")

        result_df.insert(1, '取车点', pickup_location)
        return result_df

    def get_row_name(self, rule_type):
        """根据规则类型获取行名"""
        mapping = {
            1: '全平台最低价',
            2: '5座价格',
            3: '6座价格',
            4: 'SUV4座价格',
            5: '7座价格',
            6: 'alphard&vellfire价格',
        }
        return mapping.get(rule_type, f'规则类型{rule_type}')


def process_disadvantage_analysis(input_file):
    """处理劣势分析逻辑，按客源地和取车点组合处理"""
    try:
        print(f"\n{'=' * 80}")
        print(f"开始执行劣势分析程序 - 输入文件: {input_file}")
        print(f"{'=' * 80}")

        df = pd.read_csv(input_file, encoding='utf-8')
        has_customer_country = '客源地' in df.columns and not df['客源地'].isna().all()

        if has_customer_country:
            grouped = df.groupby(['客源地', '取车点'])
            print(f"数据将按客源地和取车点组合处理，共发现 {len(grouped)} 个组合")
        else:
            df['客源地'] = '未知客源地'
            grouped = df.groupby(['客源地', '取车点'])
            print(f"数据将按取车点处理，共发现 {len(grouped)} 个取车点")

        all_results = []

        for (customer_country, pickup_location), group_df in grouped:
            print(f"\n处理组合: 客源地={customer_country}, 取车点={pickup_location}")
            print(f"  数据量: {len(group_df)} 行")

            analyzer = DisadvantageAnalyzer()
            result = analyzer.analyze(group_df, pickup_location)

            if result.empty:
                continue

            all_results.append(result)

            print(f"\n  组合结果:")
            print("  " + "-" * 100)
            print(f"  {'规则':<35}", end="")
            for col in result.columns[2:]:
                if len(col) > 15:
                    print(f"{col[:12]:<13}...", end="")
                else:
                    print(f"{col:<15}", end="")
            print()
            print("  " + "-" * 100)

            for idx, row in result.iterrows():
                if idx == '日期信息':
                    print(f"  {'日期信息':<35}", end="")
                    for col in result.columns[2:]:
                        val = row[col]
                        print(f"{'' if pd.isna(val) else str(val)[:15]:<15}", end="")
                    print()
                    continue

                print(f"  {idx:<35}", end="")
                for col in result.columns[2:]:
                    val = row[col]
                    if pd.isna(val):
                        print(f"{'NaN':<15}", end="")
                    elif isinstance(val, (int, float)):
                        if idx == '劣势指数':
                            print(f"{val:<15.3f}", end="")
                        else:
                            print(f"{val:<15.2f}", end="")
                    else:
                        print(f"{val:<15}", end="")
                print()
            print()

        if not all_results:
            print("\n没有生成有效结果。")
            return None

        final_result = pd.concat(all_results, ignore_index=False)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f'OKA_BM_{timestamp}.csv'
        output_df = 为输出添加分组置顶表头(final_result)
        output_df.to_csv(output_file, encoding='utf-8-sig', index=True)

        print(f"\n{'=' * 120}")
        print("处理完成!")
        print(f"所有组合的分析结果已保存到: {output_file}")
        print(f"总组合数: {len(all_results)}")
        print(f"最终结果行数: {len(final_result)}")
        print("=" * 120)

        return output_file
    except Exception as e:
        print(f"劣势分析程序出错: {e}")
        import traceback
        traceback.print_exc()
        return None
