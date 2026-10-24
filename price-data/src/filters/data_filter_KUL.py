# data_filter.py
import pandas as pd
import numpy as np
import re
from datetime import datetime


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


def process_data_filter(input_file):
    """处理数据过滤逻辑（使用新规则1-6，保留ABP/LOR分组逻辑）"""
    try:
        print(f"\n{'=' * 80}")
        print(f"开始执行数据过滤程序 - 输入文件: {input_file}")
        print(f"{'=' * 80}")

        df = pd.read_csv(input_file)

        print("数据列名:", df.columns.tolist())
        print("\n前5行数据:")
        print(df.head())

        # 数据清洗
        df['价格（/天）'] = df['价格（/天）'].apply(clean_price)
        df['座位'] = pd.to_numeric(df['座位'], errors='coerce')

        for col in ['车型', '动力类型', '品牌型号', '供应商品牌', '客源地', '取车点', 'ABP', 'LOR']:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

        all_results = []

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
                print(f"  警告: 客源地={country}, 取车点={pickup} 无数据")
                continue

            # 获取当前子集中的唯一ABP和LOR值
            subset_abp_values = subset['ABP'].dropna().unique()
            subset_lor_values = subset['LOR'].dropna().unique()

            print(f"  ABP值: {subset_abp_values.tolist()}")
            print(f"  LOR值: {subset_lor_values.tolist()}")

            abp_lor_count = 0
            for abp_val in subset_abp_values:
                for lor_val in subset_lor_values:
                    abp_lor_subset = subset[
                        (subset['ABP'] == abp_val) & (subset['LOR'] == lor_val)
                    ].copy()

                    if abp_lor_subset.empty:
                        continue

                    abp_lor_count += 1
                    print(
                        f"\n  └─ 处理第{abp_lor_count}个ABP-LOR组合: ABP={abp_val}, LOR={lor_val}, 数据量: {len(abp_lor_subset)}条"
                    )

                    # ===== 新规则1：座位=5，价格最低的四条数据 =====
                    rule_1 = abp_lor_subset[
                        (abp_lor_subset['座位'] == 5)
                    ].copy()
                    result_1 = get_top_n_unique(rule_1, 4)
                    if not result_1.empty:
                        result_1['规则分类'] = '5座车最低价'

                    # ===== 新规则2：品牌型号为X70、CRV、tiguan、CX5价格最低的四条数据 =====
                    # 创建品牌型号列表（不区分大小写）
                    target_brands = ['x70', 'cr-v', 'tiguan', 'cx-5']
                    rule_2 = abp_lor_subset[
                        abp_lor_subset['品牌型号'].apply(
                            lambda x: any(brand in str(x).lower() for brand in target_brands)
                        )
                    ].copy()
                    result_2 = get_top_n_unique(rule_2, 4)
                    if not result_2.empty:
                        result_2['规则分类'] = '指定SUV最低价（X70/CRV/tiguan/CX5）'

                    # ===== 新规则3：座位=5，品牌型号为passat价格最低的四条数据 =====
                    rule_3 = abp_lor_subset[
                        (abp_lor_subset['座位'] == 5) &
                        (abp_lor_subset['品牌型号'].str.contains('passat', case=False, na=False))
                    ].copy()
                    result_3 = get_top_n_unique(rule_3, 4)
                    if not result_3.empty:
                        result_3['规则分类'] = '5座Passat最低价'

                    # ===== 新规则4：座位=7，车型为越野车SUV价格最低的四条数据 =====
                    rule_4 = abp_lor_subset[
                        (abp_lor_subset['座位'] == 7) &
                        (abp_lor_subset['车型'].str.contains('越野车SUV', case=False, na=False))
                    ].copy()
                    result_4 = get_top_n_unique(rule_4, 4)
                    if not result_4.empty:
                        result_4['规则分类'] = '7座越野车SUV最低价'

                    # ===== 新规则5：座位=7，品牌型号为vellfire/alphard价格最低的四条数据 =====
                    rule_5 = abp_lor_subset[
                        (abp_lor_subset['座位'] == 7) &
                        (abp_lor_subset['品牌型号'].str.contains('vellfire|alphard', case=False, na=False))
                    ].copy()
                    result_5 = get_top_n_unique(rule_5, 4)
                    if not result_5.empty:
                        result_5['规则分类'] = '7座Vellfire/Alphard最低价'

                    # ===== 新规则6：座位=10，价格最低的四条数据 =====
                    rule_6 = abp_lor_subset[
                        (abp_lor_subset['座位'] == 10)
                    ].copy()
                    result_6 = get_top_n_unique(rule_6, 4)
                    if not result_6.empty:
                        result_6['规则分类'] = '10座车最低价'

                    # 合并结果
                    current_result = pd.concat(
                        [result_1, result_2, result_3, result_4, result_5, result_6],
                        ignore_index=True
                    )

                    if not current_result.empty:
                        current_result['客源地'] = country
                        current_result['取车点'] = pickup
                        current_result['ABP'] = abp_val
                        current_result['LOR'] = lor_val
                        all_results.append(current_result)

                        print(f"  └─ 筛选出{len(current_result)}条数据")
                        print(f"     规则1:{len(result_1)}, 规则2:{len(result_2)}, 规则3:{len(result_3)}")
                        print(f"     规则4:{len(result_4)}, 规则5:{len(result_5)}, 规则6:{len(result_6)}")
                    else:
                        print(f"  └─ 未筛选出任何数据")

            if abp_lor_count == 0:
                print(f"  警告: 未找到有效的ABP-LOR组合")

        # 合并所有结果
        if all_results:
            final_result = pd.concat(all_results, ignore_index=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f'KUL_filtered_data_{timestamp}.csv'
            final_result.to_csv(output_file, index=False, encoding='utf-8-sig')

            print(f"\n✅ 处理完成！共处理 {len(country_pickup_combinations)} 个组合")
            print(f"✅ 共保留 {len(final_result)} 条数据，已保存到 {output_file}")

            # 打印统计信息
            print("\n各组合结果统计:")
            result_summary = final_result.groupby(['客源地', '取车点']).size().reset_index(name='数量')
            print(result_summary.to_string(index=False))

            print("\n各规则结果统计:")
            rule_summary = final_result.groupby(['规则分类']).size().reset_index(name='数量')
            rule_summary = rule_summary.sort_values('数量', ascending=False)
            print(rule_summary.to_string(index=False))

            print("\nABP和LOR组合统计:")
            abp_lor_summary = final_result.groupby(['ABP', 'LOR']).size().reset_index(name='数量')
            abp_lor_summary = abp_lor_summary.sort_values('数量', ascending=False)
            print(abp_lor_summary.to_string(index=False))

            return output_file
        else:
            print("❌ 没有筛选出任何数据，请检查原始数据和筛选条件。")
            return None

    except Exception as e:
        print(f"数据过滤程序出错: {e}")
        import traceback
        traceback.print_exc()
        return None