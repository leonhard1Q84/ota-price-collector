# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
from datetime import datetime
import re

from .output_header_utils import 为输出添加分组置顶表头


class DisadvantageAnalyzer:
    """劣势分析器（动态ABP+LOR值，仅显示存在的组合，支持复杂规则与取车点过滤）"""

    def __init__(self):
        # 定义规则 - 不区分大小写匹配
        self.rules = [
            {
                'name': '座位=4',
                'condition': self.seat_four_condition
            },
            {
                'name': '座位=5且动力类型为非混合动力',
                'condition': lambda df: (df['座位'].astype(str).str.contains('5', na=False)) &
                                        (~df['动力类型'].str.contains('混合动力', case=False, na=False))
            },
            {
                'name': '座位=5且动力类型为混合动力',
                'condition': lambda df: (df['座位'].astype(str).str.contains('5', na=False)) &
                                        (df['动力类型'].str.contains('混合动力', case=False, na=False))
            },
            {
                'name': '品牌名称含有prius',
                'condition': lambda df: df['品牌型号'].str.contains('prius', case=False, na=False)
            },
            {
                'name': '车型为越野车SUV（Harrier/CX-5/X-Trail/CH-R/Levorg）',
                'condition': self.suv_condition
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
                'name': '座位=7且品牌名称不含有sienta或freed',
                'condition': lambda df: (df['座位'] == 7) &
                                        (~df['品牌型号'].str.contains('sienta|freed', case=False, na=False, regex=True))
            },
            {
                'name': '品牌型号包含alphard或vellfire',
                'condition': lambda df: df['品牌型号'].str.contains('alphard|vellfire', case=False, na=False,
                                                                    regex=True)
            }
        ]

    def seat_four_condition(self, df):
        return (
            (df['座位'] == 4) &
            (df['车型'].isin(['迷你', '小型家庭用车', '中型车']))
        )

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

    def suv_condition(self, df):
        """
        越野车SUV的修正条件：
        - 车型必须包含"越野车SUV"
        - 品牌型号必须包含 Harrier、CX-5、X-Trail、CH-R 或 Levorg（不区分大小写）
        - 所有符合条件的车辆都纳入该规则（不再因存在Harrier而排除其他）
        """
        suv_mask = df['车型'].str.contains('越野车SUV', case=False, na=False)
        model_mask = df['品牌型号'].str.contains(
            r'Harrier|CX-5|X-Trail|CH-R|Levorg',
            case=False,
            na=False,
            regex=True
        )
        return suv_mask & model_mask

    def luxury_condition(self, df):
        """
        豪华车条件：
        1. 优先选择品牌型号包含LS500的车辆
        2. 如果没有LS500，则选择车型为高级车的车辆
        3. 两种情况的车辆都会参与比较（取并集）
        """
        # 优先匹配 LS500
        ls500_mask = df['品牌型号'].str.contains('LS500', case=False, na=False)

        # 其次匹配高级车型
        luxury_mask = df['车型'].str.contains('高级车', case=False, na=False)

        # 合并两个条件（取并集）
        return ls500_mask | luxury_mask

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

    def clean_distance(self, distance):
        """清理距离值（针对距站点位置）"""
        if pd.isna(distance):
            return np.nan

        if isinstance(distance, str):
            distance_str = str(distance)
            distance_str = re.sub(r'[^\d.约]', '', distance_str)
            if distance_str.startswith('约'):
                distance_str = distance_str[1:]
            if not distance_str:
                return np.nan
            try:
                return float(distance_str)
            except:
                numbers = re.findall(r'\d+\.?\d*', distance_str)
                if numbers:
                    return float(numbers[0])
                return np.nan

        try:
            return float(distance)
        except:
            return np.nan

    def analyze_cell(self, df, rule_mask, abp_val, lor_val):
        """分析单个 (ABP, LOR) 单元格"""
        mask = (
                rule_mask &
                (df['ABP'] == abp_val) &
                (df['LOR'] == lor_val)
        )
        filtered_df = df[mask]

        if filtered_df.empty:
            return np.nan

        ix_df = filtered_df[filtered_df['供应商品牌'] == 'IX RENTAL']
        non_ix_df = filtered_df[filtered_df['供应商品牌'] != 'IX RENTAL']

        if ix_df.empty or non_ix_df.empty:
            return 0

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

        # 清理距离列
        distance_col = None
        for col in df.columns:
            if '距站点位置' in col or '距离' in col or '距' in col:
                distance_col = col
                break

        if distance_col:
            df['距站点位置_数值'] = df[distance_col].apply(self.clean_distance)
        else:
            df['距站点位置_数值'] = np.nan

        # 检查是否有日期列
        has_date_columns = 'p_datetime' in df.columns and 'd_datetime' in df.columns

        print("数据统计信息:")
        print(f"总数据量: {len(df)}")
        print(f"IX RENTAL数量: {(df['供应商品牌'] == 'IX RENTAL').sum()}")
        print(f"非IX RENTAL数量: {(df['供应商品牌'] != 'IX RENTAL').sum()}")

        # 按取车点分组处理
        all_results = []

        for pickup_location in df['取车点'].dropna().unique():
            loc_df = df[df['取车点'] == pickup_location].copy()
            customer_country = "未知客源地"
            if '客源地' in loc_df.columns:
                country_values = loc_df['客源地'].dropna().astype(str).str.strip()
                country_values = country_values[country_values != '']
                if not country_values.empty:
                    customer_country = country_values.iloc[0]

            # 特殊处理：东京国际机场+(HND) - 包含"机场内"或"步行到达"
            if '东京国际机场+(HND)' in str(pickup_location):
                before = len(loc_df)

                tag_columns = [col for col in loc_df.columns if '标签' in col or 'tag' in col or '备注' in col]
                if tag_columns:
                    tag_column = tag_columns[0]
                    print(f"东京国际机场: 检测到标签列 '{tag_column}'")

                    airport_mask = loc_df[tag_column].astype(str).str.contains(
                        '机场内|步行到达', case=False, na=False, regex=True
                    )
                    loc_df = loc_df[airport_mask]

                    after = len(loc_df)
                    print(f"东京国际机场: 从 {before} 条筛选出 {after} 条（包含'机场内'或'步行到达'标签）")
                else:
                    print(f"东京国际机场: 未找到标签列，无法进行过滤")
                    continue

                if after == 0:
                    continue

            if loc_df.empty:
                continue

            # === 严格按原始 CSV 行顺序生成 ABP-LOR 列顺序 ===
            abp_lor_raw = loc_df[['ABP', 'LOR']].dropna().reset_index(drop=False)
            abp_lor_pairs = abp_lor_raw.drop_duplicates(subset=['ABP', 'LOR'], keep='first')[
                ['ABP', 'LOR']].reset_index(drop=True)

            if abp_lor_pairs.empty:
                continue

            abp_lor_info = None
            if has_date_columns:
                date_raw = loc_df[['ABP', 'LOR', 'p_datetime', 'd_datetime']].dropna().reset_index(drop=False)
                abp_lor_info = date_raw.drop_duplicates(subset=['ABP', 'LOR'], keep='first').reset_index(drop=True)

            date_info = {}
            if has_date_columns and abp_lor_info is not None:
                for _, row in abp_lor_info.iterrows():
                    abp_val = int(row['ABP'])
                    lor_val = int(row['LOR'])
                    col_name = f'ABP={abp_val}_LOR={lor_val}'
                    p_date = row['p_datetime']
                    d_date = row['d_datetime']
                    p_date_formatted = self.format_date(p_date)
                    d_date_formatted = self.format_date(d_date)
                    date_info[col_name] = f"p:{p_date_formatted},d:{d_date_formatted}"

            # 分析每条规则
            results = {}
            for rule in self.rules:
                rule_name = rule['name']
                rule_mask = rule['condition'](loc_df)
                row_data = {}
                for _, row in abp_lor_pairs.iterrows():
                    abp_val = int(row['ABP'])
                    lor_val = int(row['LOR'])
                    col_name = f'ABP={abp_val}_LOR={lor_val}'
                    value = self.analyze_cell(loc_df, rule_mask, abp_val, lor_val)
                    row_data[col_name] = value
                results[rule_name] = row_data

            if not results:
                continue

            result_df = pd.DataFrame(results).T

            if result_df.empty:
                continue

            # 计算劣势指数（10条规则，每正值计0.1）
            disadvantage_index_row = {}
            for col in result_df.columns:
                vals = result_df[col]
                score = sum((1 / len(self.rules)) for v in vals if pd.notna(v) and v > 0)
                disadvantage_index_row[col] = round(score, 3)

            result_df.loc['劣势指数'] = pd.Series(disadvantage_index_row)

            # 计算 BM%
            bm_row = {}
            for col in result_df.columns:
                idx = result_df.loc['劣势指数', col]
                bm = (1 - idx) * 100
                bm_row[col] = f"{bm:.1f}%"

            result_df.loc['BM%'] = pd.Series(bm_row)

            # 插入日期信息行
            if date_info:
                date_row = {col: date_info.get(col, '') for col in result_df.columns}
                date_df = pd.DataFrame([date_row], index=['日期信息'])
                result_df = pd.concat([date_df, result_df])

            result_df.insert(0, '客源地', customer_country)
            result_df.insert(1, '取车点', pickup_location)
            all_results.append(result_df)

        if not all_results:
            print("警告：未生成任何有效结果。")
            return pd.DataFrame()

        final_df = pd.concat(all_results, axis=0)
        return final_df


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

        current_loc = None
        for idx, row in result.iterrows():
            loc = row['取车点']
            if loc != current_loc:
                if current_loc is not None:
                    print("-" * 120)
                print(f"取车点: {loc}")
                print("-" * 120)
                cols = result.columns
                print(f"{'规则':<25}", end="")
                for c in cols[2:]:
                    print(f"{c:<18}", end="")
                print()
                print("-" * 120)
                current_loc = loc

            if idx == '日期信息':
                print(f"{idx:<25}", end="")
                for c in result.columns[2:]:
                    val = row[c]
                    if pd.isna(val) or val == '':
                        print(f"{'':<18}", end="")
                    else:
                        print(f"{str(val):<18}", end="")
                print()
            else:
                print(f"{idx:<25}", end="")
                for c in result.columns[2:]:
                    val = row[c]
                    if pd.isna(val):
                        print(f"{'NaN':<18}", end="")
                    elif isinstance(val, float):
                        if idx == '劣势指数':
                            print(f"{val:<18.3f}", end="")
                        elif idx in [rule['name'] for rule in analyzer.rules]:
                            print(f"{val:<18.2f}", end="")
                        else:
                            print(f"{str(val):<18}", end="")
                    else:
                        print(f"{str(val):<18}", end="")
                print()

        print("\n说明:")
        print("1. 数值：IX RENTAL最低价 - 非IX RENTAL最低价")
        print(f"2. 劣势指数：每列中，每个正值计1/{len(analyzer.rules)}（≈{1 / len(analyzer.rules):.3f}），共{len(analyzer.rules)}条规则，满分为1.0")
        print("3. BM% = (1 - 劣势指数) × 100%")
        print("4. 仅显示数据中存在的 ABP-LOR 组合列")
        print("5. 东京国际机场仅使用标签中包含'机场内'或'步行到达'的数据")
        print("6. SUV规则：包含 Harrier、CX-5、X-Trail、CH-R、Levorg 的越野车SUV（全部参与比较）")
        print("7. 第一行显示每个ABP-LOR组合对应的日期信息（格式：月/日）")
        print("8. 第8条规则：座位=7且品牌型号不含sienta或freed")
        print("9. 第9条规则：品牌型号包含alphard或vellfire")
        print("-" * 120)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f'NRT_BM{timestamp}.csv'
        output_df = 为输出添加分组置顶表头(result)
        output_df.to_csv(output_file, encoding='utf-8-sig', index=True)
        print(f"\n结果已保存到: {output_file}")
        print("=" * 120)

        return output_file

    except Exception as e:
        print(f"劣势分析程序出错: {e}")
        import traceback
        traceback.print_exc()
        return None
