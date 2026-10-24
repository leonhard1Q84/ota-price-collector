import pandas as pd
import numpy as np
import re
from datetime import datetime


def clean_price(price):
    """清理价格列：移除逗号，转换为数值类型"""
    if pd.isna(price):
        return np.nan
    # 如果是字符串，移除逗号、空格、货币符号等
    if isinstance(price, str):
        # 移除逗号、空格、货币符号等
        price_str = re.sub(r'[^\d.]', '', price)
        try:
            return float(price_str)
        except ValueError:
            return np.nan
    # 如果已经是数字，直接返回
    return float(price)


def get_top_n_unique(data, n=4):
    """辅助函数：按价格排序 + 按价格和供应商品牌去重 + 取前n条"""
    if data.empty:
        return data
    # 先过滤掉价格为空的数据
    data_with_price = data[data['价格（/天）'].notna()].copy()
    if data_with_price.empty:
        return data_with_price
    # 按价格升序排序
    sorted_data = data_with_price.sort_values(by='价格（/天）')
    # 按价格和供应商品牌去重（只有当价格和供应商品牌都相同时才去重）
    deduped = sorted_data.drop_duplicates(subset=['价格（/天）', '供应商品牌'], keep='first')
    # 取前n条
    return deduped.head(n)


def process_data_filter(input_file):
    """处理数据过滤逻辑"""
    try:
        print(f"\n{'=' * 80}")
        print(f"开始执行数据过滤程序 - 输入文件: {input_file}")
        print(f"{'=' * 80}")

        # 1. 读取原始 CSV 文件
        df = pd.read_csv(input_file)

        # 查看所有列名
        print("数据列名:", df.columns.tolist())
        print("\n前5行数据:")
        print(df.head())

        # 应用价格清理函数
        df['价格（/天）'] = df['价格（/天）'].apply(clean_price)

        # 确保必要字段的数据类型正确
        df['座位'] = pd.to_numeric(df['座位'], errors='coerce')

        # 统一字符串处理（去除空格，便于匹配）
        for col in ['车型', '动力类型', '品牌型号', '供应商品牌', '客源地', '取车点', 'ABP', 'LOR']:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

        # 初始化一个空的DataFrame来存储所有结果
        all_results = []

        # 获取所有唯一的客源地和取车点组合
        if '客源地' in df.columns and '取车点' in df.columns:
            country_pickup_combinations = df[['客源地', '取车点']].drop_duplicates()
        else:
            # 如果没有这些列，创建一个默认组合
            country_pickup_combinations = pd.DataFrame({'客源地': ['默认'], '取车点': ['默认']})

        print(f"\n开始处理 {len(country_pickup_combinations)} 个组合...")

        # 遍历每个客源地和取车点的组合
        for idx, row in country_pickup_combinations.iterrows():
            country = row['客源地']
            pickup = row['取车点']

            print(f"\n正在处理: 客源地={country}, 取车点={pickup}")

            # 筛选当前组合的数据
            subset = df[(df['客源地'] == country) & (df['取车点'] == pickup)].copy()
            print(f"  原始数据量: {len(subset)} 条")

            # 如果数据为空，跳过
            if subset.empty:
                print(f"  警告: 客源地={country}, 取车点={pickup} 无数据或过滤后无数据")
                continue

            # 获取当前子集中的唯一ABP和LOR值
            subset_abp_values = subset['ABP'].dropna().unique()
            subset_lor_values = subset['LOR'].dropna().unique()

            print(f"  ABP值: {subset_abp_values.tolist()}")
            print(f"  LOR值: {subset_lor_values.tolist()}")

            # 按照ABP和LOR的组合进行分类处理
            abp_lor_count = 0
            for abp_val in subset_abp_values:
                for lor_val in subset_lor_values:
                    # 筛选当前ABP和LOR组合的数据
                    abp_lor_subset = subset[(subset['ABP'] == abp_val) & (subset['LOR'] == lor_val)].copy()

                    if not abp_lor_subset.empty:
                        abp_lor_count += 1
                        print(
                            f"\n  └─ 处理第{abp_lor_count}个ABP-LOR组合: ABP={abp_val}, LOR={lor_val}, 数据量: {len(abp_lor_subset)}条")

                        # ===== 新规则 1：座位=5，品牌型号不含有altis =====
                        rule1_name = '座位5_不含Altis'
                        rule1_mask = (
                                (abp_lor_subset['座位'] == 5) &
                                (~abp_lor_subset['品牌型号'].str.contains('altis', case=False, na=True))
                        )
                        rule1 = abp_lor_subset[rule1_mask].copy()
                        result1 = get_top_n_unique(rule1, 4)
                        if not result1.empty:
                            result1['规则分类'] = rule1_name

                        # ===== 新规则 2：座位=5，品牌型号含有altis =====
                        rule2_name = '座位5_含Altis'
                        rule2_mask = (
                                (abp_lor_subset['座位'] == 5) &
                                (abp_lor_subset['品牌型号'].str.contains('altis', case=False, na=False))
                        )
                        rule2 = abp_lor_subset[rule2_mask].copy()
                        result2 = get_top_n_unique(rule2, 4)
                        if not result2.empty:
                            result2['规则分类'] = rule2_name

                        # ===== 新规则 3：座位=7，车型为商务车 =====
                        rule3_name = '座位7_商务车'
                        rule3_mask = (
                                (abp_lor_subset['座位'] == 7) &
                                (abp_lor_subset['车型'].str.contains('商务车', case=False, na=False))
                        )
                        rule3 = abp_lor_subset[rule3_mask].copy()
                        result3 = get_top_n_unique(rule3, 4)
                        if not result3.empty:
                            result3['规则分类'] = rule3_name

                        # ===== 新规则 4：越野车SUV，品牌型号含cross或hrv或urx =====
                        rule4_name = '越野车_Cross_HRV_URX'
                        rule4_mask = (
                                (abp_lor_subset['车型'].str.contains('越野车SUV', case=False, na=False)) &
                                (abp_lor_subset['品牌型号'].str.contains('cross|hrv|urx', case=False, na=False))
                        )
                        rule4 = abp_lor_subset[rule4_mask].copy()
                        result4 = get_top_n_unique(rule4, 4)
                        if not result4.empty:
                            result4['规则分类'] = rule4_name

                        # ===== 新规则 5：车型为商务车或者旅行车，座位=9 =====
                        rule5_name = '座位9_商务或旅行车'
                        rule5_mask = (
                                (abp_lor_subset['座位'] == 9) &
                                (abp_lor_subset['车型'].str.contains('商务车|旅行车', case=False, na=False))
                        )
                        rule5 = abp_lor_subset[rule5_mask].copy()
                        result5 = get_top_n_unique(rule5, 4)
                        if not result5.empty:
                            result5['规则分类'] = rule5_name

                        # 合并当前ABP-LOR分类的所有规则结果
                        current_result = pd.concat(
                            [result1, result2, result3, result4, result5],
                            ignore_index=True
                        )

                        # 添加组合信息到结果中
                        if not current_result.empty:
                            current_result['客源地'] = country
                            current_result['取车点'] = pickup
                            current_result['ABP'] = abp_val
                            current_result['LOR'] = lor_val
                            all_results.append(current_result)

                            print(f"  └─ 筛选出{len(current_result)}条数据")
                            print(f"     规则1: {len(result1)}条, 规则2: {len(result2)}条, 规则3: {len(result3)}条")
                            print(f"     规则4: {len(result4)}条, 规则5: {len(result5)}条")
                        else:
                            print(f"  └─ 未筛选出任何数据")

            if abp_lor_count == 0:
                print(f"  警告: 未找到有效的ABP-LOR组合")

        # 合并所有组合的结果
        if all_results:
            final_result = pd.concat(all_results, ignore_index=True)

            # 生成输出文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f'GXCZ_filtered_data_{timestamp}.csv'

            # 保存到新 CSV 文件（UTF-8 with BOM，避免 Excel 中文乱码）
            final_result.to_csv(output_file, index=False, encoding='utf-8-sig')

            print(f"\n 处理完成！共处理 {len(country_pickup_combinations)} 个组合")
            print(f" 共保留 {len(final_result)} 条数据，已保存到 {output_file}")

            return output_file
        else:
            print(" 没有筛选出任何数据，请检查原始数据和筛选条件。")
            return None
    except Exception as e:
        print(f"数据过滤程序出错: {e}")
        import traceback
        traceback.print_exc()
        return None