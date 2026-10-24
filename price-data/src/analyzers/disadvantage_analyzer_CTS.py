# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
from datetime import datetime

from .output_header_utils import 为输出添加分组置顶表头


RULE_SMALL_FAMILY = '5座非混合动力'
RULE_SUV = '5座越野车SUV'
RULE_BUSINESS = '商务车7座，品牌型号不包含alphard或vellfire'
RULE_ALPHARD_VELLFIRE = '品牌型号包含alphard或vellfire'
RULE_ALPHARD_8 = '座位=8，品牌型号包含alphard'
ROW_ORDER = [RULE_SMALL_FAMILY, RULE_SUV, RULE_BUSINESS, RULE_ALPHARD_VELLFIRE, RULE_ALPHARD_8]

CTS_SUV_TYPES = r'越野车SUV|SUV'
CTS_BUSINESS_TYPES = r'商务车'
CTS_ALPHARD_VELLFIRE_MODELS = r'alphard|vellfire'
CTS_ALPHARD_MODELS = r'alphard'


def is_ix_supplier(series):
    return series.astype(str).str.strip().str.lower().eq('ix rental')


class DisadvantageAnalyzer:
    """CTS disadvantage analyzer grouped by ABP/LOR."""

    def __init__(self):
        self.rules = []

    def create_rules(self, abp_lor_pairs):
        rules = []

        for _, (abp, lor) in abp_lor_pairs.iterrows():
            abp_val = int(abp)
            lor_val = int(lor)

            rules.append({
                'name': f'{RULE_SMALL_FAMILY} (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 1,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['座位'] == 5) &
                    (~df['动力类型'].str.contains('混合动力', case=False, na=False)) &
                    is_ix_supplier(df['供应商品牌']) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['座位'] == 5) &
                    (~df['动力类型'].str.contains('混合动力', case=False, na=False)) &
                    (~is_ix_supplier(df['供应商品牌'])) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                )
            })

            rules.append({
                'name': f'{RULE_SUV} (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 2,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['座位'] == 5) &
                    (df['车型'].astype(str).str.contains(CTS_SUV_TYPES, case=False, na=False)) &
                    is_ix_supplier(df['供应商品牌']) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['座位'] == 5) &
                    (df['车型'].astype(str).str.contains(CTS_SUV_TYPES, case=False, na=False)) &
                    (~is_ix_supplier(df['供应商品牌'])) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                )
            })

            rules.append({
                'name': f'{RULE_BUSINESS} (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 3,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['座位'] == 7) &
                    (df['车型'].astype(str).str.contains(CTS_BUSINESS_TYPES, case=False, na=False)) &
                    (~df['品牌型号'].astype(str).str.contains(CTS_ALPHARD_VELLFIRE_MODELS, case=False, na=False)) &
                    is_ix_supplier(df['供应商品牌']) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['座位'] == 7) &
                    (df['车型'].astype(str).str.contains(CTS_BUSINESS_TYPES, case=False, na=False)) &
                    (~df['品牌型号'].astype(str).str.contains(CTS_ALPHARD_VELLFIRE_MODELS, case=False, na=False)) &
                    (~is_ix_supplier(df['供应商品牌'])) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                )
            })

            rules.append({
                'name': f'{RULE_ALPHARD_VELLFIRE} (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 4,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['品牌型号'].astype(str).str.contains(CTS_ALPHARD_VELLFIRE_MODELS, case=False, na=False)) &
                    is_ix_supplier(df['供应商品牌']) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['品牌型号'].astype(str).str.contains(CTS_ALPHARD_VELLFIRE_MODELS, case=False, na=False)) &
                    (~is_ix_supplier(df['供应商品牌'])) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                )
            })

            rules.append({
                'name': f'{RULE_ALPHARD_8} (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 5,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['座位'] == 8) &
                    (df['品牌型号'].astype(str).str.contains(CTS_ALPHARD_MODELS, case=False, na=False)) &
                    is_ix_supplier(df['供应商品牌']) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['座位'] == 8) &
                    (df['品牌型号'].astype(str).str.contains(CTS_ALPHARD_MODELS, case=False, na=False)) &
                    (~is_ix_supplier(df['供应商品牌'])) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                )
            })

        return rules

    def clean_price(self, price):
        if pd.isna(price):
            return np.nan
        if isinstance(price, str):
            price_str = str(price).replace('"', '').replace(',', '').strip()
            if not price_str:
                return np.nan
            try:
                return float(price_str)
            except ValueError:
                return np.nan
        return float(price)

    def analyze_rule(self, df, rule):
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
            raise ValueError('输入数据必须是DataFrame或文件路径')

        df['价格（/天）'] = df['价格（/天）'].apply(self.clean_price)
        df['座位'] = pd.to_numeric(df['座位'], errors='coerce')
        df['ABP'] = pd.to_numeric(df['ABP'], errors='coerce')
        df['LOR'] = pd.to_numeric(df['LOR'], errors='coerce')

        for col in ['品牌型号', '车型', '动力类型', '供应商品牌']:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

        if pickup_location is None:
            pickup_location = df['取车点'].iloc[0] if not df.empty and '取车点' in df.columns else '未知取车点'

        ix_mask = is_ix_supplier(df['供应商品牌'])
        print(f'数据统计信息 - 取车点: {pickup_location}')
        print(f'  总数据量: {len(df)}')
        print(f'  IX RENTAL数量: {ix_mask.sum()}')
        print(f'  非IX RENTAL数量: {(~ix_mask).sum()}')

        abp_lor_pairs = df[['ABP', 'LOR']].dropna().drop_duplicates(keep='first').reset_index(drop=True)
        self.rules = self.create_rules(abp_lor_pairs)

        results = {}
        for rule in self.rules:
            col_name = f'ABP={rule["abp"]}_LOR={rule["lor"]}'
            row_name = self.get_row_name(rule['rule_type'])
            results.setdefault(col_name, {})[row_name] = self.analyze_rule(df, rule)

        if not results:
            return pd.DataFrame()

        result_df = pd.DataFrame(results).reindex(ROW_ORDER)
        if result_df.empty or result_df.isna().all().all():
            return pd.DataFrame()

        disadvantage_index_row = {}
        for col in result_df.columns:
            score = 0.0
            count = 0
            for val in result_df[col]:
                if pd.isna(val):
                    continue
                count += 1
                if val > 0:
                    score += 1 / len(ROW_ORDER)
            disadvantage_index_row[col] = np.nan if count == 0 else round(score, 2)

        result_df.loc['劣势指数'] = pd.Series(disadvantage_index_row)

        bm_percentage_row = {}
        for col in result_df.columns:
            idx_val = result_df.loc['劣势指数', col]
            bm_percentage_row[col] = np.nan if pd.isna(idx_val) else f'{(1 - idx_val) * 100:.1f}%'
        result_df.loc['BM%'] = pd.Series(bm_percentage_row)

        customer_country = '未知客源地'
        if '客源地' in df.columns and not df['客源地'].isna().all():
            customer_country = df['客源地'].iloc[0] if not df.empty else customer_country

        result_df.insert(0, '客源地', customer_country)
        result_df.insert(1, '取车点', pickup_location)

        date_row = {}
        for col in result_df.columns:
            if 'ABP=' in col and 'LOR=' in col:
                try:
                    abp_str, lor_str = col.split('_', 1)
                    abp_value = int(abp_str.split('=')[1])
                    lor_value = int(lor_str.split('=')[1])
                    pickup_date = datetime.now() + pd.Timedelta(days=abp_value)
                    return_date = pickup_date + pd.Timedelta(days=lor_value)
                    date_row[col] = f'{pickup_date:%m/%d}-{return_date:%m/%d}'
                except (ValueError, IndexError, KeyError):
                    date_row[col] = ''
            else:
                date_row[col] = ''

        date_series = pd.Series(date_row, name='日期')
        result_df = pd.concat([pd.DataFrame([date_series]), result_df], ignore_index=False)
        result_df.index = ['日期'] + list(result_df.index[1:])
        return result_df

    def get_row_name(self, rule_type):
        if rule_type == 1:
            return RULE_SMALL_FAMILY
        if rule_type == 2:
            return RULE_SUV
        if rule_type == 3:
            return RULE_BUSINESS
        if rule_type == 4:
            return RULE_ALPHARD_VELLFIRE
        if rule_type == 5:
            return RULE_ALPHARD_8
        return f'规则类型{rule_type}'


def process_disadvantage_analysis(input_file):
    try:
        print(f"\n{'=' * 80}")
        print(f'开始执行劣势分析程序 - 输入文件: {input_file}')
        print(f"{'=' * 80}")

        df = pd.read_csv(input_file, encoding='utf-8')

        if '客源地' in df.columns and not df['客源地'].isna().all():
            grouped = df.groupby(['客源地', '取车点'])
        else:
            df['客源地'] = '未知客源地'
            grouped = df.groupby(['客源地', '取车点'])

        all_results = []
        for (_, pickup_location), group_df in grouped:
            analyzer = DisadvantageAnalyzer()
            result = analyzer.analyze(group_df, pickup_location)
            if not result.empty:
                all_results.append(result)

        if not all_results:
            print('没有生成有效结果。')
            return None

        final_result = pd.concat(all_results, ignore_index=False)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f'CTS_BM_{timestamp}.csv'
        output_df = 为输出添加分组置顶表头(final_result)
        output_df.to_csv(output_file, encoding='utf-8-sig', index=True)

        print(f'分析结果已保存到: {output_file}')
        return output_file
    except Exception as exc:
        print(f'劣势分析程序出错: {exc}')
        import traceback
        traceback.print_exc()
        return None
