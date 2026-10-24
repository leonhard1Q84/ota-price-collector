# data_filter.py
import pandas as pd
import numpy as np
import re
from datetime import datetime


def clean_price(price):
    """清理价格列：移除逗号，转换为数值类型"""
    if pd.isna(price):
        return np.nan
    # 如果是字符串，移除逗号、空格等非数字字符
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
    """处理数据过滤逻辑：使用新规则（9条）"""
    try:
        print(f"\n{'=' * 80}")
        print(f"开始执行数据过滤程序 - 输入文件: {input_file}")
        print(f"使用规则：9条分类规则")
        print(f"{'=' * 80}")

        # 1. 读取原始 CSV 文件
        df = pd.read_csv(input_file)

        # 查看所有列名
        print("数据列名:", df.columns.tolist())
        print("\n前5行数据:")
        print(df.head())

        # 清理价格列
        df['价格（/天）'] = df['价格（/天）'].apply(clean_price)

        # 确保必要字段的数据类型正确
        df['座位'] = pd.to_numeric(df['座位'], errors='coerce')

        # 统一字符串处理（去除空格，不区分大小写）
        for col in ['车型', '动力类型', '品牌型号', '供应商品牌', '客源地', '取车点', 'ABP', 'LOR']:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

        # 初始化结果容器
        all_results = []

        # 获取所有唯一的客源地
        if '客源地' in df.columns:
            country_list = df['客源地'].drop_duplicates()
        else:
            country_list = pd.Series(['默认'])

        print(f"\n开始处理 {len(country_list)} 个客源地...")

        # 遍历每个客源地
        for country in country_list:
            print(f"\n正在处理: 客源地={country}")

            # 筛选当前客源地
            subset = df[df['客源地'] == country].copy()
            print(f"  原始数据量: {len(subset)} 条")

            if subset.empty:
                print(f"  警告: 客源地={country} 无有效数据")
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

                        # 在每个ABP-LOR分类内应用9个规则
                        # ===== 规则 1：座位=4 =====
                        rule1_name = '座位=4'
                        rule1 = abp_lor_subset[
                            (abp_lor_subset['座位'] == 4)
                        ].copy()
                        if not rule1.empty:
                            rule1['规则分类'] = rule1_name
                        result1 = get_top_n_unique(rule1, 4)

                        # ===== 规则 2：座位=5且动力类型为非混合动力 =====
                        rule2_name = '座位=5且动力类型为非混合动力'
                        rule2 = abp_lor_subset[
                            (abp_lor_subset['座位'] == 5) &
                            (~abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False))
                            ].copy()
                        if not rule2.empty:
                            rule2['规则分类'] = rule2_name
                        result2 = get_top_n_unique(rule2, 4)

                        # ===== 规则 3：座位=5且动力类型为混合动力 =====
                        rule3_name = '座位=5且动力类型为混合动力'
                        rule3 = abp_lor_subset[
                            (abp_lor_subset['座位'] == 5) &
                            (abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False))
                        ].copy()
                        if not rule3.empty:
                            rule3['规则分类'] = rule3_name
                        result3 = get_top_n_unique(rule3, 4)

                        # ===== 规则 4：品牌名称含有prius =====
                        rule4_name = '品牌名称含有prius'
                        rule4 = abp_lor_subset[
                            abp_lor_subset['品牌型号'].str.contains('prius', case=False, na=False)
                        ].copy()
                        if not rule4.empty:
                            rule4['规则分类'] = rule4_name
                        result4 = get_top_n_unique(rule4, 4)

                        # ===== 规则 5：品牌名称含有freed =====
                        rule5_name = '品牌名称含有freed'
                        rule5 = abp_lor_subset[
                            abp_lor_subset['品牌型号'].str.contains('freed', case=False, na=False)
                        ].copy()
                        if not rule5.empty:
                            rule5['规则分类'] = rule5_name
                        result5 = get_top_n_unique(rule5, 4)

                        # ===== 规则 6：品牌名称含有sienta =====
                        rule6_name = '品牌名称含有sienta'
                        rule6 = abp_lor_subset[
                            abp_lor_subset['品牌型号'].str.contains('sienta', case=False, na=False)
                        ].copy()
                        if not rule6.empty:
                            rule6['规则分类'] = rule6_name
                        result6 = get_top_n_unique(rule6, 4)

                        # ===== 规则 7：座位=7，动力类型为混合动力 =====
                        rule7_name = '座位=7且动力类型为混合动力'
                        rule7 = abp_lor_subset[
                            (abp_lor_subset['座位'] == 7) &
                            (abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False))
                        ].copy()
                        if not rule7.empty:
                            rule7['规则分类'] = rule7_name
                        result7 = get_top_n_unique(rule7, 4)

                        # ===== 规则 8：座位=7且品牌名称不含有sienta或freed =====
                        rule8_name = '座位=7且品牌名称不含有sienta或freed'
                        rule8 = abp_lor_subset[
                            (abp_lor_subset['座位'] == 7) &
                            (~abp_lor_subset['品牌型号'].str.contains('freed|sienta', case=False, na=False, regex=True))
                            ].copy()
                        if not rule8.empty:
                            rule8['规则分类'] = rule8_name
                        result8 = get_top_n_unique(rule8, 4)

                        # ===== 规则 9：品牌型号包含alphard或vellfire =====
                        rule9_name = '品牌型号包含alphard或vellfire'
                        rule9 = abp_lor_subset[
                            abp_lor_subset['品牌型号'].str.contains('alphard|vellfire', case=False, na=False, regex=True)
                        ].copy()
                        if not rule9.empty:
                            rule9['规则分类'] = rule9_name
                        result9 = get_top_n_unique(rule9, 4)

                        # 合并当前ABP-LOR分类的所有规则结果
                        current_result = pd.concat(
                            [result1, result2, result3, result4, result5,
                             result6, result7, result8, result9],
                            ignore_index=True
                        )

                        # 添加组合信息到结果中
                        if not current_result.empty:
                            current_result['客源地'] = country
                            current_result['ABP'] = abp_val
                            current_result['LOR'] = lor_val
                            all_results.append(current_result)

                            print(f"  └─ 筛选出{len(current_result)}条数据")
                            print(f"     规则1: {len(result1)}条, 规则2: {len(result2)}条, 规则3: {len(result3)}条")
                            print(f"     规则4: {len(result4)}条, 规则5: {len(result5)}条, 规则6: {len(result6)}条")
                            print(f"     规则7: {len(result7)}条, 规则8: {len(result8)}条, 规则9: {len(result9)}条")
                        else:
                            print(f"  └─ 未筛选出任何数据")

            if abp_lor_count == 0:
                print(f"  警告: 未找到有效的ABP-LOR组合")

        # 合并所有结果
        if all_results:
            final_result = pd.concat(all_results, ignore_index=True)

            # 确保规则分类列无缺失
            final_result['规则分类'] = final_result['规则分类'].fillna('未分类')

            # 生成带时间戳的输出文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f'FUK_filtered_data_{timestamp}.csv'

            # 保存结果（UTF-8 with BOM）
            final_result.to_csv(output_file, index=False, encoding='utf-8-sig')

            print(f"\n 处理完成！共处理 {len(country_list)} 个客源地")
            print(f"共保留 {len(final_result)} 条数据，已保存到 {output_file}")

            # 输出统计信息
            print("\n各客源地结果统计:")
            country_summary = final_result.groupby(['客源地']).size().reset_index(name='数量')
            print(country_summary.to_string(index=False))

            print("\n各规则结果统计:")
            rule_summary = final_result.groupby(['规则分类']).size().reset_index(name='数量')
            rule_summary = rule_summary.sort_values('数量', ascending=False)
            print(rule_summary.to_string(index=False))

            # 打印规则说明
            print(f"\n{'=' * 80}")
            print("使用的9条规则说明:")
            print("1. 座位=4")
            print("2. 座位=5且动力类型为非混合动力")
            print("3. 座位=5且动力类型为混合动力")
            print("4. 品牌名称含有prius")
            print("5. 品牌名称含有freed")
            print("6. 品牌名称含有sienta")
            print("7. 座位=7且动力类型为混合动力")
            print("8. 座位=7且品牌名称不含有sienta或freed")
            print("9. 品牌型号包含alphard或vellfire")
            print(f"{'=' * 80}")

            return output_file
        else:
            print(" 未筛选出任何数据，请检查输入数据或规则条件。")
            return None

    except Exception as e:
        import traceback
        print(f" 数据过滤程序出错: {e}")
        traceback.print_exc()
        return None
