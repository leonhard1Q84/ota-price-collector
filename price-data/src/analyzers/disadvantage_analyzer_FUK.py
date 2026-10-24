# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
from datetime import datetime
import traceback

from .output_header_utils import 为输出添加分组置顶表头


class DisadvantageAnalyzer:
    """劣势分析器（动态ABP+LOR值，仅显示存在的组合）"""

    def __init__(self):
        # 定义新规则 - 不区分大小写匹配（共9条）
        self.rules = [
            {
                'name': '座位=4',
                'condition': lambda df: (df['座位'] == 4)
            },
            {
                'name': '座位=5且动力类型为非混合动力',
                'condition': lambda df: (df['座位'] == 5) &
                                        (~df['动力类型'].str.contains('混合动力', case=False, na=False))
            },
            {
                'name': '座位=5且动力类型为混合动力',
                'condition': lambda df: (df['座位'] == 5) &
                                        (df['动力类型'].str.contains('混合动力', case=False, na=False))
            },
            {
                'name': '品牌名称含有prius',
                'condition': lambda df: df['品牌型号'].str.contains('prius', case=False, na=False)
            },
            {
                'name': '品牌名称含有freed',
                'condition': lambda df: df['品牌型号'].str.contains('freed', case=False, na=False)
            },
            {
                'name': '品牌名称含有sienta',
                'condition': lambda df: df['品牌型号'].str.contains('sienta', case=False, na=False)
            },
            {
                'name': '座位=7且动力类型为混合动力',
                'condition': lambda df: (df['座位'] == 7) &
                                        (df['动力类型'].str.contains('混合动力', case=False, na=False))
            },
            {
                'name': '座位=7且品牌名称不含有sienta或freed',
                'condition': lambda df: (df['座位'] == 7) &
                                        (~df['品牌型号'].str.contains('freed|sienta', case=False, na=False, regex=True))
            },
            {
                'name': '品牌型号包含alphard或vellfire',
                'condition': lambda df: df['品牌型号'].str.contains('alphard|vellfire', case=False, na=False,
                                                                    regex=True)
            }
        ]

    def format_date(self, date_str):
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
            except:
                return np.nan
        return float(price)

    def analyze_cell(self, df, rule_mask, abp_val, lor_val):
        """分析单个 (ABP, LOR) 单元格"""
        basic_mask = (
                rule_mask &
                (df['ABP'] == abp_val) &
                (df['LOR'] == lor_val)
        )

        ix_df = df[basic_mask & (df['供应商品牌'] == 'IX RENTAL')]
        non_ix_df = df[basic_mask & (df['供应商品牌'] != 'IX RENTAL')]

        if ix_df.empty or non_ix_df.empty:
            return np.nan

        ix_min = ix_df['价格（/天）'].min()
        non_ix_min = non_ix_df['价格（/天）'].min()
        price_diff = ix_min - non_ix_min
        return round(price_diff, 2)

    def analyze(self, input_data):
        if isinstance(input_data, str):
            df = pd.read_csv(input_data, encoding='utf-8')
        elif isinstance(input_data, pd.DataFrame):
            df = input_data.copy()
        else:
            raise ValueError("输入数据必须是DataFrame或文件路径")

        # 数据清洗
        df['价格（/天）'] = df['价格（/天）'].apply(self.clean_price)
        df['座位'] = pd.to_numeric(df['座位'], errors='coerce')
        df['ABP'] = pd.to_numeric(df['ABP'], errors='coerce')
        df['LOR'] = pd.to_numeric(df['LOR'], errors='coerce')

        has_date_columns = 'p_datetime' in df.columns and 'd_datetime' in df.columns
        customer_country = (
            str(df['客源地'].dropna().iloc[0]).strip()
            if '客源地' in df.columns and not df['客源地'].dropna().empty
            else "未知客源地"
        )
        pickup_location = (
            str(df['取车点'].dropna().iloc[0]).strip()
            if '取车点' in df.columns and not df['取车点'].dropna().empty
            else "福冈机场"
        )

        print(f"数据统计信息 - 客源地: {customer_country}, 取车点: {pickup_location}:")
        print(f"总数据量: {len(df)}")
        print(f"IX RENTAL数量: {(df['供应商品牌'] == 'IX RENTAL').sum()}")
        print(f"非IX RENTAL数量: {(df['供应商品牌'] != 'IX RENTAL').sum()}")

        # 按原始顺序提取 ABP-LOR 组合
        abp_lor_raw = df[['ABP', 'LOR']].dropna().reset_index(drop=False)
        abp_lor_pairs = abp_lor_raw.drop_duplicates(subset=['ABP', 'LOR'], keep='first')[['ABP', 'LOR']].reset_index(drop=True)

        if abp_lor_pairs.empty:
            print("警告：未找到有效的ABP-LOR组合。")
            return pd.DataFrame()

        print(f"存在的 ABP-LOR 组合数: {len(abp_lor_pairs)}")

        abp_lor_info = None
        if has_date_columns:
            date_raw = df[['ABP', 'LOR', 'p_datetime', 'd_datetime']].dropna().reset_index(drop=False)
            abp_lor_info = date_raw.drop_duplicates(subset=['ABP', 'LOR'], keep='first').reset_index(drop=True)

        date_info = {}
        if has_date_columns and abp_lor_info is not None:
            for _, row in abp_lor_info.iterrows():
                abp_val = int(row['ABP'])
                lor_val = int(row['LOR'])
                col_name = f'ABP={abp_val}_LOR={lor_val}'
                p_date = self.format_date(row['p_datetime'])
                d_date = self.format_date(row['d_datetime'])
                date_info[col_name] = f"p:{p_date},d:{d_date}"

        # 分析每个规则
        results = {}
        for rule in self.rules:
            rule_name = rule['name']
            rule_mask = rule['condition'](df)
            row_data = {}
            for _, row in abp_lor_pairs.iterrows():
                abp_val = int(row['ABP'])
                lor_val = int(row['LOR'])
                col_name = f'ABP={abp_val}_LOR={lor_val}'
                value = self.analyze_cell(df, rule_mask, abp_val, lor_val)
                row_data[col_name] = value
            results[rule_name] = row_data

        if not results:
            print("警告：未生成任何有效结果。")
            return pd.DataFrame()

        result_df = pd.DataFrame(results).T

        if result_df.empty:
            print("警告：结果为空，跳过输出。")
            return pd.DataFrame()

        # ✅ 新劣势指数计算逻辑：正差值数量 / 总规则数
        total_rules = len(self.rules)
        disadvantage_index_row = {}
        for col in result_df.columns:
            vals = result_df[col]
            positive_count = 0
            valid_count = 0
            for v in vals:
                if pd.isna(v):
                    continue
                valid_count += 1
                if v > 0:
                    positive_count += 1

            if valid_count == 0:
                disadvantage_index_row[col] = np.nan
            else:
                disadvantage_index = positive_count / total_rules
                disadvantage_index_row[col] = round(disadvantage_index, 2)

        result_df.loc['劣势指数'] = pd.Series(disadvantage_index_row)

        # 计算 BM%
        bm_row = {}
        for col in result_df.columns:
            idx = result_df.loc['劣势指数', col]
            if pd.isna(idx):
                bm_row[col] = np.nan
            else:
                bm = (1 - idx) * 100
                bm_row[col] = f"{bm:.1f}%"

        result_df.loc['BM%'] = pd.Series(bm_row)

        # 插入日期信息行（作为第一行）
        if date_info:
            date_row = {col: date_info.get(col, '') for col in result_df.columns}
            date_df = pd.DataFrame([date_row], index=['日期信息'])
            result_df = pd.concat([date_df, result_df], ignore_index=False)

        result_df.insert(0, '客源地', customer_country)
        result_df.insert(1, '取车点', pickup_location)

        return result_df


def process_disadvantage_analysis(input_file):
    """处理劣势分析逻辑（兼容命令行）"""
    try:
        print(f"\n{'=' * 80}")
        print(f"开始执行劣势分析程序 - 输入文件: {input_file}")
        print(f"{'=' * 80}")

        analyzer = DisadvantageAnalyzer()
        result = analyzer.analyze(input_file)

        if result.empty:
            print("结果为空，跳过输出。")
            return None

        print("\n" + "=" * 120)
        print("数据比较结果表格（动态ABP+LOR值）")
        print("=" * 120)

        # 打印表格
        print(f"{'规则':<40}", end="")
        for col in result.columns[2:]:
            display_col = (col[:18] + "...") if len(col) > 20 else col
            print(f"{display_col:<20}", end="")
        print()
        print("-" * 120)

        for idx, row in result.iterrows():
            print(f"{idx:<40}", end="")
            for col in result.columns[2:]:
                val = row[col]
                if pd.isna(val):
                    if idx == '日期信息':
                        print(f"{'':<20}", end="")
                    else:
                        print(f"{'NaN':<20}", end="")
                elif isinstance(val, float):
                    print(f"{val:<20.2f}", end="")
                else:
                    print(f"{str(val):<20}", end="")
            print()

        # ✅ 更新说明文字
        print("\n说明:")
        print("1. 数值：IX RENTAL最低价 - 非IX RENTAL最低价")
        print(f"2. 劣势指数：(价格差 > 0 的规则数) / {len(analyzer.rules)}（总规则数）")
        print("3. BM% = (1 - 劣势指数) × 100%")
        print("4. 仅显示数据中存在的 ABP-LOR 组合列")
        print("5. 日期信息行显示格式：p:{月/日},d:{月/日}")
        print("6. 当前规则（9条）：")
        for i, rule in enumerate(analyzer.rules, 1):
            print(f"   {i}. {rule['name']}")
        print("-" * 120)

        # 保存结果
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f'FUK_BM{timestamp}.csv'
        output_df = 为输出添加分组置顶表头(result)
        output_df.to_csv(output_file, encoding='utf-8-sig', index=True)
        print(f"\n结果已保存到: {output_file}")
        print("=" * 120)

        return output_file

    except Exception as e:
        print(f"劣势分析程序出错: {e}")
        traceback.print_exc()
        return None
