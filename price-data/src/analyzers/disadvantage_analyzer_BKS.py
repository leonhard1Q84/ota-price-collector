# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os

from .output_header_utils import 为输出添加分组置顶表头


class DisadvantageAnalyzer:
    """劣势分析器（动态ABP+LOR值，每个ABP/LOR组合为独立规则）"""

    def __init__(self):
        # 规则将在分析时动态生成，基于存在的ABP/LOR组合
        self.rules = []
        self.total_rules_per_column = 6  # 每个ABP/LOR组合的规则总数（6条规则）

    def format_date(self, date_str):
        """格式化日期，只显示月/日"""
        if pd.isna(date_str) or not date_str:
            return ""

        try:
            # 尝试解析多种日期格式
            if isinstance(date_str, str):
                # 移除时间部分（如果有的话）
                date_part = date_str.split()[0] if ' ' in date_str else date_str
                # 尝试解析日期
                date_obj = pd.to_datetime(date_part)
                return f"{date_obj.month}/{date_obj.day}"
            elif hasattr(date_str, 'month') and hasattr(date_str, 'day'):
                # 如果是datetime对象
                return f"{date_str.month}/{date_str.day}"
            else:
                # 其他情况返回原始字符串的一部分
                return str(date_str)[:10]
        except:
            # 如果解析失败，返回原始字符串（截断）
            return str(date_str)[:10]

    def create_rules(self, abp_lor_pairs):
        """根据存在的ABP/LOR组合创建规则"""
        rules = []

        for _, (abp, lor) in abp_lor_pairs.iterrows():
            abp_val = int(abp)
            lor_val = int(lor)

            # 规则1：全部数据
            rules.append({
                'name': f'全部数据 (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 1,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                        (df['供应商品牌'] == 'IX RENTAL') &
                        (df['品牌型号'].str.contains('move|conte|roox|dayz|n-box|wagon|tanto', case=False, na=False)) &
                        (df['座位'].astype(str).str.contains('4', na=False)) &
                        (df['ABP'] == a) &
                        (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                        (df['供应商品牌'] != 'IX RENTAL') &
                        (df['ABP'] == a) &
                        (df['LOR'] == l)
                )
            })
            # 规则2：座位=5，动力类型不是混合动力
            rules.append({
                'name': f'5座非混合动力 (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 3,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                        (df['座位'].astype(str).str.contains('5', na=False)) &
                        (df['品牌型号'].str.contains('vitz|aqua|fit|yaris|note|cube|freed|solio|airwave|demio', case=False, na=False)) &
                        (~df['动力类型'].str.contains('混合动力', case=False, na=False)) &
                        (df['供应商品牌'] == 'IX RENTAL') &
                        (df['ABP'] == a) &
                        (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                        (df['座位'].astype(str).str.contains('5', na=False)) &
                        (df['车型'].str.contains('迷你|小型家庭用车|中型车', case=False, na=False, regex=True)) &
                        (~df['动力类型'].str.contains('混合动力', case=False, na=False)) &
                        (df['供应商品牌'] != 'IX RENTAL') &
                        (df['ABP'] == a) &
                        (df['LOR'] == l)
                )
            })

            # 规则3：座位=5，动力类型为混合动力
            rules.append({
                'name': f'5座混合动力 (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 5,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                        (df['座位'].astype(str).str.contains('5', na=False)) &
                        (df['车型'].str.contains('迷你|小型家庭用车|中型车', case=False, na=False, regex=True)) &
                        (df['品牌型号'].str.contains('note e-power|insight|freed|fit|prius|shuttle|sai', case=False, na=False)) &
                        (df['动力类型'].str.contains('混合动力', case=False, na=False)) &
                        (df['供应商品牌'] == 'IX RENTAL') &
                        (df['ABP'] == a) &
                        (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                        (df['座位'].astype(str).str.contains('5', na=False)) &
                        (df['车型'].str.contains('迷你|小型家庭用车|中型车', case=False, na=False, regex=True)) &
                        (df['动力类型'].str.contains('混合动力', case=False, na=False)) &
                        (df['供应商品牌'] != 'IX RENTAL') &
                        (df['ABP'] == a) &
                        (df['LOR'] == l)
                )
            })

            # 规则4：6-8座最低价
            rules.append({
                'name': f'6-8座最低价 (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 7,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                        (df['座位'].astype(str).str.contains('6|7|8', na=False)) &
                        (df['供应商品牌'] == 'IX RENTAL') &
                        (df['ABP'] == a) &
                        (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                        (df['座位'].astype(str).str.contains('6|7|8', na=False)) &
                        (df['供应商品牌'] != 'IX RENTAL') &
                        (df['ABP'] == a) &
                        (df['LOR'] == l)
                )
            })

            # 规则5：越野车SUV
            rules.append({
                'name': f'越野车SUV (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 8,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                        (df['座位'].astype(str).str.contains('5', na=False)) &
                        (df['车型'].str.contains('越野车SUV', case=False, na=False)) &
                        (df['品牌型号'].str.contains('cr-v|raize|x-trail|cx-5|vezel', case=False, na=False)) &
                        (df['供应商品牌'] == 'IX RENTAL') &
                        (df['ABP'] == a) &
                        (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                        (df['座位'].astype(str).str.contains('5', na=False)) &
                        (df['车型'].str.contains('越野车SUV', case=False, na=False)) &
                        (df['供应商品牌'] != 'IX RENTAL') &
                        (df['ABP'] == a) &
                        (df['LOR'] == l)
                )
            })

            # 规则6：MPV7-8座
            rules.append({
                'name': f'MPV7-8座 (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 11,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                        (df['品牌型号'].str.contains('isis|stepwagon|voxy|delica|vellfire|alphard|serena', case=False, na=False)) &
                        (df['供应商品牌'] == 'IX RENTAL') &
                        (df['ABP'] == a) &
                        (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                        (df['座位'].astype(str).str.contains('7|8', na=False)) &
                        (df['供应商品牌'] != 'IX RENTAL') &
                        (df['ABP'] == a) &
                        (df['LOR'] == l)
                )
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
            except:
                return np.nan
        return float(price)

    def analyze_rule(self, df, rule):
        """分析单个规则"""
        ix_mask = rule['ix_condition'](df)
        non_ix_mask = rule['non_ix_condition'](df)

        ix_df = df[ix_mask]
        non_ix_df = df[non_ix_mask]

        if ix_df.empty or non_ix_df.empty:
            return np.nan

        ix_min = ix_df['价格（/天）'].min()
        non_ix_min = non_ix_df['价格（/天）'].min()
        price_diff = ix_min - non_ix_min
        return round(price_diff, 2)

    def analyze(self, input_data, pickup_location=None):
        if isinstance(input_data, str):
            df = pd.read_csv(input_data, encoding='utf-8')
        elif isinstance(input_data, pd.DataFrame):
            df = input_data.copy()
        else:
            raise ValueError("输入数据必须是DataFrame或文件路径")

        # 数据清洗
        df['价格（/天）'] = df['价格（/天）'].apply(self.clean_price)
        df['座位'] = df['座位'].astype(str).str.strip()
        df['品牌型号'] = df['品牌型号'].astype(str).str.strip()
        df['车型'] = df['车型'].astype(str).str.strip()
        df['动力类型'] = df['动力类型'].astype(str).str.strip()

        # 确保 ABP 和 LOR 是数值类型
        df['ABP'] = pd.to_numeric(df['ABP'], errors='coerce')
        df['LOR'] = pd.to_numeric(df['LOR'], errors='coerce')

        # 检查是否有日期列
        has_date_columns = 'p_datetime' in df.columns and 'd_datetime' in df.columns

        # 使用传入的取车点或从数据中获取
        if pickup_location is None:
            pickup_location = df['取车点'].iloc[0] if not df.empty else "未知取车点"

        print(f"数据统计信息 - 取车点: {pickup_location}:")
        print(f"  总数据量: {len(df)}")
        print(f"  IX RENTAL数量: {(df['供应商品牌'] == 'IX RENTAL').sum()}")
        print(f"  非IX RENTAL数量: {(df['供应商品牌'] != 'IX RENTAL').sum()}")

        # === 核心修改：严格按原始 CSV 行顺序生成 ABP-LOR 组合（保序！）===
        # 步骤1：提取 ABP/LOR 并保留原始行顺序
        abp_lor_raw = df[['ABP', 'LOR']].dropna().reset_index(drop=False)
        # 步骤2：按首次出现去重，保持原始顺序（关键！）
        abp_lor_pairs = abp_lor_raw.drop_duplicates(subset=['ABP', 'LOR'], keep='first')[['ABP', 'LOR']].reset_index(
            drop=True)

        # 同步构建 abp_lor_info（用于日期），同样保序
        abp_lor_info = None
        if has_date_columns:
            date_raw = df[['ABP', 'LOR', 'p_datetime', 'd_datetime']].dropna().reset_index(drop=False)
            abp_lor_info = date_raw.drop_duplicates(subset=['ABP', 'LOR'], keep='first').reset_index(drop=True)

        print(f"  存在的 ABP-LOR 组合数: {len(abp_lor_pairs)}")

        # 根据ABP/LOR组合创建规则
        self.rules = self.create_rules(abp_lor_pairs)
        print(f"  生成的规则总数: {len(self.rules)}")

        # 分析每个规则
        results = {}
        date_info = {}

        for rule in self.rules:
            rule_name = rule['name']
            value = self.analyze_rule(df, rule)

            col_name = f'ABP={rule["abp"]}_LOR={rule["lor"]}'
            row_name = self.get_row_name(rule['rule_type'])

            if col_name not in results:
                results[col_name] = {}

            results[col_name][row_name] = value

            # 构建日期信息（保序）
            if col_name not in date_info and has_date_columns and abp_lor_info is not None:
                # 在保序的 abp_lor_info 中查找
                mask = (abp_lor_info['ABP'] == rule["abp"]) & (abp_lor_info['LOR'] == rule["lor"])
                matched_rows = abp_lor_info[mask]
                if not matched_rows.empty:
                    p_date = matched_rows['p_datetime'].iloc[0]
                    d_date = matched_rows['d_datetime'].iloc[0]
                    # 格式化日期，只显示月/日
                    p_date_formatted = self.format_date(p_date)
                    d_date_formatted = self.format_date(d_date)
                    date_info[col_name] = f"p:{p_date_formatted},d:{d_date_formatted}"
                else:
                    date_info[col_name] = ""

        # 转换结果格式
        result_df = pd.DataFrame(results)
        row_order = [
            '全部数据',
            '5座非混合动力',
            '5座混合动力',
            '6-8座最低价',
            '越野车SUV',
            'MPV7-8座'
        ]

        existing_rows = [row for row in row_order if row in result_df.index]
        result_df = result_df.loc[existing_rows]

        if result_df.empty:
            print(f"  警告：所有数据均为 NaN，结果为空。")
            return pd.DataFrame()

        # 计算劣势指数（每列）- 修改：使用1/规则数
        disadvantage_index_row = {}
        for col in result_df.columns:
            values = result_df[col]
            score = 0.0
            count = 0
            # 计算该列中非NaN值的数量
            non_na_count = values.notna().sum()

            for val in values:
                if pd.isna(val):
                    continue
                count += 1
                # 如果价格差异为正（IX价格高于非IX价格），则添加分值
                if val > 0:
                    # 使用1/规则数作为每个规则的分值（现在是6个规则，所以是1/6≈0.1667）
                    score += 1.0 / self.total_rules_per_column
            if count == 0:
                disadvantage_index_row[col] = np.nan
            else:
                disadvantage_index_row[col] = round(score, 4)  # 保留4位小数以显示精度

        result_df.loc['劣势指数'] = pd.Series(disadvantage_index_row)

        # 计算 BM%
        bm_percentage_row = {}
        for col in result_df.columns:
            idx_val = result_df.loc['劣势指数', col]
            if pd.isna(idx_val):
                bm_percentage_row[col] = np.nan
            else:
                bm_percentage = (1 - idx_val) * 100
                bm_percentage_row[col] = f"{bm_percentage:.1f}%"

        result_df.loc['BM%'] = pd.Series(bm_percentage_row)

        # 插入日期信息行（作为第一行）
        if date_info:
            date_row = {col: date_info.get(col, '') for col in result_df.columns}
            date_df = pd.DataFrame([date_row], index=['日期信息'])
            result_df = pd.concat([date_df, result_df], ignore_index=False)

        # 插入客源地和取车点
        if '客源地' in df.columns and not df['客源地'].isna().all():
            customer_country = df['客源地'].iloc[0] if not df.empty else "未知客源地"
            result_df.insert(0, '客源地', customer_country)
        else:
            result_df.insert(0, '客源地', "未知客源地")

        result_df.insert(1, '取车点', pickup_location)

        return result_df

    def get_row_name(self, rule_type):
        """根据规则类型获取行名"""
        row_names = {
            1: '全部数据',
            3: '5座非混合动力',
            5: '5座混合动力',
            7: '6-8座最低价',
            8: '越野车SUV',
            11: 'MPV7-8座'
        }
        return row_names.get(rule_type, f'规则类型{rule_type}')


def process_disadvantage_analysis(input_file):
    """处理劣势分析逻辑，按客源地和取车点组合处理"""
    try:
        print(f"\n{'=' * 80}")
        print(f"开始执行劣势分析程序 - 输入文件: {input_file}")
        print(f"{'=' * 80}")

        # 读取原始数据
        df = pd.read_csv(input_file, encoding='utf-8')

        # 检查是否有客源地列
        has_customer_country = '客源地' in df.columns and not df['客源地'].isna().all()

        # 按客源地和取车点分组
        if has_customer_country:
            # 有客源地信息，按客源地和取车点分组
            grouped = df.groupby(['客源地', '取车点'])
            print(f"数据将按客源地和取车点组合处理，共发现 {len(grouped)} 个组合")
        else:
            # 没有客源地信息，只按取车点分组
            df['客源地'] = '未知客源地'
            grouped = df.groupby(['客源地', '取车点'])
            print(f"数据将按取车点处理，共发现 {len(grouped)} 个取车点")

        all_results = []

        # 对每个组合进行分析
        for (customer_country, pickup_location), group_df in grouped:
            print(f"\n处理组合: 客源地={customer_country}, 取车点={pickup_location}")
            print(f"  数据量: {len(group_df)} 行")

            analyzer = DisadvantageAnalyzer()
            result = analyzer.analyze(group_df, pickup_location)

            if not result.empty:
                all_results.append(result)

                # 打印当前组合的结果
                print(f"\n  组合结果:")
                print("  " + "-" * 100)

                # 简化打印，只显示关键信息
                print(f"  {'规则':<35}", end="")
                for col in result.columns[2:]:
                    if len(col) > 15:
                        print(f"{col[:12]:<13}...", end="")
                    else:
                        print(f"{col:<15}", end="")
                print()
                print("  " + "-" * 100)

                for idx, row in result.iterrows():
                    # 日期信息行用不同的格式显示
                    if idx == '日期信息':
                        print(f"  {'日期信息':<35}", end="")
                        for col in result.columns[2:]:
                            val = row[col]
                            if pd.isna(val) or val == '':
                                print(f"{'':<15}", end="")
                            else:
                                print(f"{str(val)[:15]:<15}", end="")
                        print()
                    else:
                        print(f"  {idx:<35}", end="")
                        for col in result.columns[2:]:
                            val = row[col]
                            if pd.isna(val):
                                print(f"{'NaN':<15}", end="")
                            elif isinstance(val, (int, float)):
                                if idx == '劣势指数':
                                    print(f"{val:<15.4f}", end="")  # 劣势指数显示4位小数
                                else:
                                    print(f"{val:<15.2f}", end="")
                            elif isinstance(val, str) and '%' in val:
                                print(f"{val:<15}", end="")
                            else:
                                print(f"{val:<15}", end="")
                        print()
                print()

        if not all_results:
            print("\n没有生成有效结果。")
            return None

        # 合并所有结果
        final_result = pd.concat(all_results, ignore_index=False)

        # 保存结果
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f'BKS_BM_{timestamp}.csv'
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
