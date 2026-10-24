# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
from datetime import datetime

from analyzers.output_header_utils import 为输出添加分组置顶表头


class DisadvantageAnalyzer:
    """劣势分析器（固定4条比价逻辑）"""

    def __init__(self):
        # 规则将在分析时动态生成，基于存在的ABP/LOR组合
        self.rules = []

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
        """根据存在的ABP/LOR组合创建规则（固定4条比价逻辑）"""
        rules = []

        for _, (abp, lor) in abp_lor_pairs.iterrows():
            abp_val = int(abp)
            lor_val = int(lor)

            # 规则1：其他供应商全平台最低价（IX Rental车型包含n_box/spacia/tanto vs 其他供应商全部）
            rules.append({
                'name': f'其他供应商全平台最低价 (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 1,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['供应商品牌'].str.lower() == 'ix rental') &
                    (df['车型'].str.lower().str.contains('n_box|spacia|tanto', case=False, na=False, regex=True)) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['供应商品牌'].str.lower() != 'ix rental') &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                )
            })

            # 规则2：其他供应商5座最低价（IX Rental车型包含aqua/fit/yaris vs 其他供应商座位=5）
            rules.append({
                'name': f'其他供应商5座最低价 (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 2,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['供应商品牌'].str.lower() == 'ix rental') &
                    (df['车型'].str.lower().str.contains('aqua|fit|yaris', case=False, na=False, regex=True)) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['供应商品牌'].str.lower() != 'ix rental') &
                    (df['座位'].astype(str).str.contains('5', na=False)) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                )
            })

            # 规则3：其他供应商SUV最低价（IX Rental车型包含SUV vs 其他供应商车型包含SUV）
            rules.append({
                'name': f'其他供应商SUV最低价 (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 3,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['供应商品牌'].str.lower() == 'ix rental') &
                    (df['车型'].str.lower().str.contains('越野车suv', case=False, na=False, regex=True)) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['供应商品牌'].str.lower() != 'ix rental') &
                    (df['车型'].str.lower().str.contains('越野车suv', case=False, na=False, regex=True)) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                )
            })

            # 规则4：其他供应商7-8座最低价（IX Rental座位=7/8 vs 其他供应商座位=7/8）
            rules.append({
                'name': f'其他供应商7-8座最低价 (ABP={abp_val}, LOR={lor_val})',
                'abp': abp_val,
                'lor': lor_val,
                'rule_type': 4,
                'ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['供应商品牌'].str.lower() == 'ix rental') &
                    (df['座位'].astype(str).str.contains('7|8', na=False, regex=True)) &
                    (df['ABP'] == a) &
                    (df['LOR'] == l)
                ),
                'non_ix_condition': lambda df, a=abp_val, l=lor_val: (
                    (df['供应商品牌'].str.lower() != 'ix rental') &
                    (df['座位'].astype(str).str.contains('7|8', na=False, regex=True)) &
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
        print(f"  IX RENTAL数量: {(df['供应商品牌'].str.lower() == 'ix rental').sum()}")
        print(f"  非IX RENTAL数量: {(df['供应商品牌'].str.lower() != 'ix rental').sum()}")

        # 获取所有存在的 (ABP, LOR) 组合，严格按原始数据中首次出现的顺序
        if has_date_columns:
            # 保留原始顺序：先去重（keep='first'），再提取
            temp_df = df[['ABP', 'LOR', 'p_datetime', 'd_datetime']].dropna(subset=['ABP', 'LOR'])
            # 使用 drop_duplicates 保持首次出现顺序
            abp_lor_info = temp_df.drop_duplicates(subset=['ABP', 'LOR'], keep='first').reset_index(drop=True)
            abp_lor_pairs = abp_lor_info[['ABP', 'LOR']].copy()
        else:
            temp_df = df[['ABP', 'LOR']].dropna()
            abp_lor_pairs = temp_df.drop_duplicates(keep='first').reset_index(drop=True)

        print(f"  存在的 ABP-LOR 组合数: {len(abp_lor_pairs)}")

        # 根据ABP/LOR组合创建规则（使用新的固定4条逻辑）
        self.rules = self.create_rules(abp_lor_pairs)
        print(f"  生成的规则总数: {len(self.rules)}")

        # 分析每个规则
        results = {}
        # 存储每个ABP-LOR组合的日期信息
        date_info = {}

        for rule in self.rules:
            rule_name = rule['name']
            value = self.analyze_rule(df, rule)

            # 根据规则类型和ABP/LOR组合组织结果
            col_name = f'ABP={rule["abp"]}_LOR={rule["lor"]}'
            row_name = self.get_row_name(rule['rule_type'])

            if col_name not in results:
                results[col_name] = {}

            results[col_name][row_name] = value

            # 存储日期信息（如果有）
            if col_name not in date_info and has_date_columns:
                # 查找对应的日期信息
                date_info_row = abp_lor_info[(abp_lor_info['ABP'] == rule["abp"]) &
                                             (abp_lor_info['LOR'] == rule["lor"])]
                if not date_info_row.empty:
                    p_date = date_info_row['p_datetime'].iloc[0]
                    d_date = date_info_row['d_datetime'].iloc[0]
                    # 格式化日期，只显示月/日
                    p_date_formatted = self.format_date(p_date)
                    d_date_formatted = self.format_date(d_date)
                    date_info[col_name] = f"p:{p_date_formatted},d:{d_date_formatted}"
                else:
                    date_info[col_name] = ""

        # 转换结果格式
        result_df = pd.DataFrame(results)

        # 重新排列行顺序（更新为新的4条规则）
        row_order = [
            '其他供应商全平台最低价',
            '其他供应商5座最低价',
            '其他供应商SUV最低价',
            '其他供应商7-8座最低价'
        ]

        # 只保留存在的行
        existing_rows = [row for row in row_order if row in result_df.index]
        result_df = result_df.loc[existing_rows]

        if result_df.empty:
            print(f"  警告：所有数据均为 NaN，结果为空。")
            return pd.DataFrame()

        # 计算劣势指数（每列） - 更新为1/规则数
        disadvantage_index_row = {}
        for col in result_df.columns:
            values = result_df[col]
            score = 0.0
            count = 0
            # 计算非空值的数量
            for val in values:
                if pd.isna(val):
                    continue
                count += 1
                if val > 0:
                    # 每正值计1/规则数（规则数=行数）
                    score += 1 / len(existing_rows)
                # val <= 0 时不加分
            if count == 0:
                disadvantage_index_row[col] = np.nan
            else:
                disadvantage_index_row[col] = round(score, 3)

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

        # 创建日期信息行（作为第一行）- 这是新增的部分
        if date_info:  # 只有有日期信息时才添加日期行
            date_row = {}
            for col in result_df.columns:
                if col in date_info:
                    date_row[col] = date_info[col]
                else:
                    date_row[col] = ''

            # 将日期行插入到DataFrame的最前面
            date_df = pd.DataFrame([date_row], index=['日期信息'])
            result_df = pd.concat([date_df, result_df])

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
        if rule_type == 1:
            return '其他供应商全平台最低价'
        elif rule_type == 2:
            return '其他供应商5座最低价'
        elif rule_type == 3:
            return '其他供应商SUV最低价'
        elif rule_type == 4:
            return '其他供应商7-8座最低价'
        else:
            return f'规则类型{rule_type}'


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
                            if pd.isna(val):
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
                                    print(f"{val:<15.3f}", end="")  # 更新为3位小数
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
        output_file = f'JDZ_BM_{timestamp}.csv'
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