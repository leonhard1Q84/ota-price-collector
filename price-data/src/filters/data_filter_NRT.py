import pandas as pd
import numpy as np
import re
from datetime import datetime
import sys
import os
def setup_encoding():
    """设置控制台编码以避免编码错误"""
    if sys.platform.startswith('win'):
        try:
            # 尝试设置控制台编码为UTF-8
            os.system('chcp 65001 > nul')
            # 修改标准输出编码
            if hasattr(sys.stdout, 'buffer'):
                sys.stdout = open(sys.stdout.buffer.fileno(), 'w',
                                  encoding='utf-8', errors='ignore')
        except:
            pass


def clean_distance(distance):
    """清理距离列：提取数字并统一转为米"""
    if pd.isna(distance):
        return np.nan
    if isinstance(distance, str):
        distance = distance.strip()
        if not distance:
            return np.nan
        match = re.search(r'(\d+(?:\.\d+)?)', distance)
        if match:
            num = float(match.group(1))
            lower_dist = distance.lower()
            if 'km' in lower_dist or '公里' in lower_dist:
                num *= 1000
            # 默认单位为米
            return num
        else:
            return np.nan
    try:
        return float(distance)
    except (ValueError, TypeError):
        return np.nan


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
    """处理数据过滤逻辑：按照ABP、LOR、客源地、取车点四个维度分组，使用新规则（10条）"""
    try:
        # 在Windows上设置控制台编码
        if sys.platform.startswith('win'):
            try:
                os.system('chcp 65001 > nul')
            except:
                pass

        print(f"\n{'=' * 80}")
        print(f"开始执行数据过滤程序 - 输入文件: {input_file}")
        print(f"{'=' * 80}")

        # 1. 读取原始 CSV 文件
        df = pd.read_csv(input_file)

        # 查看所有列名
        print("数据列名:", df.columns.tolist())
        print("\n前5行数据:")
        print(df.head())

        # 检查必要的列是否存在
        required_columns = ['ABP', 'LOR', '客源地', '取车点']
        for col in required_columns:
            if col not in df.columns:
                print(f"[错误] 缺少必要的列: '{col}'")
                print("请确保输入文件包含以下列: ABP, LOR, 客源地, 取车点")
                return None

        # 清理价格列
        df['价格（/天）'] = df['价格（/天）'].apply(clean_price)

        # 确保必要字段的数据类型正确
        df['座位'] = pd.to_numeric(df['座位'], errors='coerce')

        # 尝试识别并清理距离列
        distance_columns = [col for col in df.columns if '距' in col or '距离' in col or 'Distance' in col]
        if distance_columns:
            distance_col = distance_columns[0]
            print(f"检测到距离列: '{distance_col}'")
            df['距站点位置'] = df[distance_col].apply(clean_distance)
        else:
            print("[警告] 未找到距离相关列，'距站点位置' 将设为 NaN")
            df['距站点位置'] = np.nan

        # 统一字符串处理（去除空格）
        string_columns = ['ABP', 'LOR', '客源地', '取车点', '车型', '动力类型', '品牌型号', '供应商品牌']
        for col in string_columns:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

        # 初始化结果容器
        all_results = []

        # 获取所有唯一的ABP、LOR、客源地、取车点组合
        grouping_combinations = df[['ABP', 'LOR', '客源地', '取车点']].drop_duplicates()

        print(f"\n开始处理 {len(grouping_combinations)} 个组合...")
        print(f"分组维度: ABP, LOR, 客源地, 取车点")

        # 遍历每个组合
        for idx, row in grouping_combinations.iterrows():
            abp = row['ABP']
            lor = row['LOR']
            country = row['客源地']
            pickup = row['取车点']

            print(f"\n正在处理: ABP={abp}, LOR={lor}, 客源地={country}, 取车点={pickup}")

            # 筛选当前组合
            subset = df[
                (df['ABP'] == abp) &
                (df['LOR'] == lor) &
                (df['客源地'] == country) &
                (df['取车点'] == pickup)
                ].copy()
            print(f"  原始数据量: {len(subset)} 条")

            if subset.empty:
                print(f"  [警告] ABP={abp}, LOR={lor}, 客源地={country}, 取车点={pickup} 无有效数据")
                continue

            # ===== 规则 1：座位=4 =====
            rule1 = subset[
                (subset['座位'] == 4) &
                (subset['车型'].isin(['迷你', '小型家庭用车', '中型车']))
                ].copy()
            if not rule1.empty:
                rule1['规则分类'] = '座位=4'
            result1 = get_top_n_unique(rule1, 4)
            print(f"  规则1匹配: {len(rule1)} 条 → 保留 {len(result1)} 条")

            # ===== 规则 2：座位=5且动力类型为非混合动力 =====
            rule2 = subset[
                (subset['座位'] == 5) &
                (~subset['动力类型'].str.contains('混合动力', case=False, na=False))
            ].copy()
            if not rule2.empty:
                rule2['规则分类'] = '座位=5且动力类型为非混合动力'
            result2 = get_top_n_unique(rule2, 4)
            print(f"  规则2匹配: {len(rule2)} 条 → 保留 {len(result2)} 条")

            # ===== 规则 3：座位=5且动力类型为混合动力 =====
            rule3 = subset[
                (subset['座位'] == 5) &
                (subset['动力类型'].str.contains('混合动力', case=False, na=False))
            ].copy()
            if not rule3.empty:
                rule3['规则分类'] = '座位=5且动力类型为混合动力'
            result3 = get_top_n_unique(rule3, 4)
            print(f"  规则3匹配: {len(rule3)} 条 → 保留 {len(result3)} 条")

            # ===== 规则 4：品牌名称含有prius =====
            rule4 = subset[
                subset['品牌型号'].str.contains('prius', case=False, na=False)
            ].copy()
            if not rule4.empty:
                rule4['规则分类'] = '品牌名称含有prius'
            result4 = get_top_n_unique(rule4, 4)
            print(f"  规则4匹配: {len(rule4)} 条 → 保留 {len(result4)} 条")

            # ===== 规则 5：车型为越野车SUV（Harrier/CX-5/X-Trail/CH-R/Levorg）=====
            rule5 = subset[
                (subset['车型'].str.contains('越野车SUV', case=False, na=False)) &
                (subset['品牌型号'].str.contains('Harrier|CX-5|X-Trail|CH-R|Levorg', case=False, na=False, regex=True))
            ].copy()
            if not rule5.empty:
                rule5['规则分类'] = '车型为越野车SUV（Harrier/CX-5/X-Trail/CH-R/Levorg）'
            result5 = get_top_n_unique(rule5, 4)
            print(f"  规则5匹配: {len(rule5)} 条 → 保留 {len(result5)} 条")

            # ===== 规则 6：品牌名称含有freed =====
            rule6 = subset[
                subset['品牌型号'].str.contains('freed', case=False, na=False)
            ].copy()
            if not rule6.empty:
                rule6['规则分类'] = '品牌名称含有freed'
            result6 = get_top_n_unique(rule6, 4)
            print(f"  规则6匹配: {len(rule6)} 条 → 保留 {len(result6)} 条")

            # ===== 规则 7：品牌名称含有sienta =====
            rule7 = subset[
                subset['品牌型号'].str.contains('sienta', case=False, na=False)
            ].copy()
            if not rule7.empty:
                rule7['规则分类'] = '品牌名称含有sienta'
            result7 = get_top_n_unique(rule7, 4)
            print(f"  规则7匹配: {len(rule7)} 条 → 保留 {len(result7)} 条")

            # ===== 规则 8：座位=7且品牌名称不含有sienta或freed =====
            rule8 = subset[
                (subset['座位'] == 7) &
                (~subset['品牌型号'].str.contains('sienta|freed', case=False, na=False, regex=True))
            ].copy()
            if not rule8.empty:
                rule8['规则分类'] = '座位=7且品牌名称不含有sienta或freed'
            result8 = get_top_n_unique(rule8, 4)
            print(f"  规则8匹配: {len(rule8)} 条 → 保留 {len(result8)} 条")

            # ===== 规则 9：品牌型号包含alphard或vellfire =====
            rule9 = subset[
                subset['品牌型号'].str.contains('alphard|vellfire', case=False, na=False, regex=True)
            ].copy()
            if not rule9.empty:
                rule9['规则分类'] = '品牌型号包含alphard或vellfire'
            result9 = get_top_n_unique(rule9, 4)
            print(f"  规则9匹配: {len(rule9)} 条 → 保留 {len(result9)} 条")

            # 合并当前组合结果
            current_result = pd.concat(
                [result1, result2, result3, result4, result5, result6, result7, result8, result9],
                ignore_index=True
            )

            if not current_result.empty:
                # 确保分组列在结果中
                current_result['ABP'] = abp
                current_result['LOR'] = lor
                current_result['客源地'] = country
                current_result['取车点'] = pickup

                all_results.append(current_result)
                print(f"  [完成] 本组合共筛选出 {len(current_result)} 条数据")
            else:
                print(f"  [警告] 本组合未筛选出任何数据")

        # 合并所有结果
        if all_results:
            final_result = pd.concat(all_results, ignore_index=True)

            # 确保规则分类列无缺失
            final_result['规则分类'] = final_result['规则分类'].fillna('未分类')

            # 生成带时间戳的输出文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f'NRT_filtered_data_{timestamp}.csv'

            # 保存结果（UTF-8 with BOM）
            final_result.to_csv(output_file, index=False, encoding='utf-8-sig')

            print(f"\n[完成] 处理完成！共处理 {len(grouping_combinations)} 个组合")
            print(f"[完成] 共保留 {len(final_result)} 条数据，已保存到 {output_file}")

            # 输出统计信息
            print("\n各组合结果统计:")
            combo_summary = final_result.groupby(['ABP', 'LOR', '客源地', '取车点']).size().reset_index(name='数量')
            print(combo_summary.to_string(index=False))

            print("\n各规则结果统计:")
            rule_summary = final_result.groupby(['规则分类']).size().reset_index(name='数量')
            rule_summary = rule_summary.sort_values('数量', ascending=False)
            print(rule_summary.to_string(index=False))

            return output_file
        else:
            print("[错误] 未筛选出任何数据，请检查输入数据或规则条件。")
            return None

    except Exception as e:
        import traceback
        print(f"[错误] 数据过滤程序出错: {e}")
        traceback.print_exc()
        return None
