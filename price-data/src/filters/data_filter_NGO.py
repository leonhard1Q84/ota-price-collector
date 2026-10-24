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


def contains_brand_model(series, pattern):
    """品牌型号大小写不敏感匹配"""
    return series.astype(str).str.contains(pattern, case=False, na=False)


def process_data_filter(input_file):
    """处理数据过滤逻辑：按照ABP、LOR、客源地、取车点四个维度分组，按 NGO 7 条规则取价格最低的4条"""
    try:
        print(f"\n{'=' * 80}")
        print(f"开始执行数据过滤程序 - 输入文件: {input_file}")
        print(f"{'=' * 80}")

        # 1. 读取原始 CSV 文件
        df = pd.read_csv(input_file)

        # 查看所有列名
        print("数据列名:", df.columns.tolist())
        print("\n前5行数据:")
        print(df.head().to_string())

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

            # ===== NGO 7 条规则：每条规则取价格最低的 4 条数据 =====
            brand_model = subset['品牌型号'].astype(str)
            not_alphard_or_vellfire = ~contains_brand_model(brand_model, r'alphard|vellfire')

            # 规则 A: 5座车
            rule_a = subset[subset['座位'] == 5].copy()
            if not rule_a.empty:
                rule_a['规则分类'] = '5座车'
                result_a = get_top_n_unique(rule_a, 4)
            else:
                result_a = pd.DataFrame()
            print(f"  规则A(5座)匹配: {len(rule_a)} 条 → 保留 {len(result_a)} 条")

            # 规则 B: 品牌型号包含prius
            rule_b = subset[contains_brand_model(brand_model, 'prius')].copy()
            if not rule_b.empty:
                rule_b['规则分类'] = '品牌型号包含prius'
                result_b = get_top_n_unique(rule_b, 4)
            else:
                result_b = pd.DataFrame()
            print(f"  规则B(品牌型号包含prius)匹配: {len(rule_b)} 条 → 保留 {len(result_b)} 条")

            # 规则 C: 6座车
            rule_c = subset[subset['座位'] == 6].copy()
            if not rule_c.empty:
                rule_c['规则分类'] = '6座车'
                result_c = get_top_n_unique(rule_c, 4)
            else:
                result_c = pd.DataFrame()
            print(f"  规则C(6座)匹配: {len(rule_c)} 条 → 保留 {len(result_c)} 条")

            # 规则 D: 7座车、品牌型号不包含alphard或vellfire
            rule_d = subset[(subset['座位'] == 7) & not_alphard_or_vellfire].copy()
            if not rule_d.empty:
                rule_d['规则分类'] = '7座车、品牌型号不包含alphard或vellfire'
                result_d = get_top_n_unique(rule_d, 4)
            else:
                result_d = pd.DataFrame()
            print(f"  规则D(7座且不含alphard/vellfire)匹配: {len(rule_d)} 条 → 保留 {len(result_d)} 条")

            # 规则 E: 品牌型号包含alphard
            rule_e = subset[contains_brand_model(brand_model, 'alphard')].copy()
            if not rule_e.empty:
                rule_e['规则分类'] = '品牌型号包含alphard'
                result_e = get_top_n_unique(rule_e, 4)
            else:
                result_e = pd.DataFrame()
            print(f"  规则E(品牌型号包含alphard)匹配: {len(rule_e)} 条 → 保留 {len(result_e)} 条")

            # 规则 F: 品牌型号包含vellfire
            rule_f = subset[contains_brand_model(brand_model, 'vellfire')].copy()
            if not rule_f.empty:
                rule_f['规则分类'] = '品牌型号包含vellfire'
                result_f = get_top_n_unique(rule_f, 4)
            else:
                result_f = pd.DataFrame()
            print(f"  规则F(品牌型号包含vellfire)匹配: {len(rule_f)} 条 → 保留 {len(result_f)} 条")

            # 规则 G: 8座车、品牌型号不包含alphard或vellfire
            rule_g = subset[(subset['座位'] == 8) & not_alphard_or_vellfire].copy()
            if not rule_g.empty:
                rule_g['规则分类'] = '8座车、品牌型号不包含alphard或vellfire'
                result_g = get_top_n_unique(rule_g, 4)
            else:
                result_g = pd.DataFrame()
            print(f"  规则G(8座且不含alphard/vellfire)匹配: {len(rule_g)} 条 → 保留 {len(result_g)} 条")

            # 合并当前组合结果
            current_result = pd.concat(
                [result_a, result_b, result_c, result_d, result_e, result_f, result_g],
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
            output_file = f'NGO_filtered_data_{timestamp}.csv'

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
