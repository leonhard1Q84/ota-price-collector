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


def format_date(date_str):
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


def get_min_non_ix_record(data, rule_name, abp_val, lor_val):
    """获取非IX供应商的最低价格记录"""
    if data.empty:
        return None

    # 排除IX RENTAL（不区分大小写）
    non_ix_data = data[~data['供应商品牌'].str.lower().str.contains('ix rental')].copy()

    if non_ix_data.empty:
        return None

    # 按价格升序排序，取第一条（最低价格）
    sorted_data = non_ix_data.sort_values(by='价格（/天）')
    min_record = sorted_data.iloc[0].to_dict()

    # 只保留需要的7个字段
    result = {
        '规则': rule_name,
        'ABP': abp_val,
        'LOR': lor_val,
        '供应商品牌': min_record.get('供应商品牌', ''),
        '品牌型号': min_record.get('品牌型号', ''),
        '车型': min_record.get('车型', ''),
        '价格（/天）': min_record.get('价格（/天）', np.nan)
    }

    return result


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
        # 初始化一个空列表来存储非IX最低价格记录
        min_non_ix_records = []

        # 初始化一个字典来存储非IX最低价格矩阵数据
        # 结构: {客源地_取车点: {ABP_LOR: {规则: 价格}}}
        min_price_matrix = {}

        # 初始化一个字典来存储每个ABP_LOR组合的日期时间信息
        # 结构: {客源地_取车点: {ABP_LOR: {'p_datetime': 值, 'd_datetime': 值}}}
        datetime_info = {}

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

            # 创建列表来保持ABP-LOR组合的顺序
            abp_lor_sequence = []

            for abp_val in subset_abp_values:
                for lor_val in subset_lor_values:
                    # 筛选当前ABP和LOR组合的数据
                    abp_lor_subset = subset[(subset['ABP'] == abp_val) & (subset['LOR'] == lor_val)].copy()

                    if not abp_lor_subset.empty:
                        abp_lor_count += 1
                        abp_lor_sequence.append((abp_val, lor_val))

                        # 创建组合键：客源地_取车点
                        country_pickup_key = f"{country}_{pickup}"
                        # 创建ABP_LOR键（格式：ABP=XX_LOR=XX，与代码1相同）
                        abp_lor_key = f"ABP={abp_val}_LOR={lor_val}"

                        # 初始化数据结构
                        if country_pickup_key not in min_price_matrix:
                            min_price_matrix[country_pickup_key] = {}
                            datetime_info[country_pickup_key] = {}

                        if abp_lor_key not in min_price_matrix[country_pickup_key]:
                            min_price_matrix[country_pickup_key][abp_lor_key] = {}

                        # 获取该ABP_LOR组合的p_datetime和d_datetime（取第一条记录）
                        if abp_lor_key not in datetime_info[country_pickup_key]:
                            # 从原始数据中获取日期时间信息
                            # 注意：这里假设每个ABP_LOR组合下的p_datetime和d_datetime都相同
                            date_cols = ['p_datetime', 'd_datetime']
                            for date_col in date_cols:
                                if date_col in abp_lor_subset.columns:
                                    # 取第一条记录的日期时间
                                    first_date_val = abp_lor_subset.iloc[0].get(date_col, '')
                                    if pd.isna(first_date_val):
                                        first_date_val = ''
                                    # 格式化日期，只显示月/日
                                    first_date_val_formatted = format_date(first_date_val)
                                    # 初始化日期时间字典
                                    if abp_lor_key not in datetime_info[country_pickup_key]:
                                        datetime_info[country_pickup_key][abp_lor_key] = {}
                                    datetime_info[country_pickup_key][abp_lor_key][date_col] = str(
                                        first_date_val_formatted)

                        # ===== 规则1：座位=4 =====
                        rule1_name = '全部数据'  # 对应代码1的规则1
                        # 排除IX RENTAL，获取最低价格
                        rule1_non_ix = abp_lor_subset[
                            (abp_lor_subset['座位'] == 4) &
                            (~abp_lor_subset['供应商品牌'].str.contains('IX RENTAL', case=False, na=False))
                            ].copy()

                        # 同时获取前4条数据用于BKS_filtered_data文件
                        rule1_all = abp_lor_subset[abp_lor_subset['座位'] == 4].copy()
                        result1 = get_top_n_unique(rule1_all, 4)

                        if not result1.empty:
                            result1['规则分类'] = rule1_name

                        if not rule1_non_ix.empty:
                            # 获取最低价格记录
                            min_price_record = rule1_non_ix.loc[rule1_non_ix['价格（/天）'].idxmin()]
                            min_price_matrix[country_pickup_key][abp_lor_key][rule1_name] = min_price_record[
                                '价格（/天）']

                            # 获取非IX最低价格记录用于min_non_ix_records
                            min_record = get_min_non_ix_record(rule1_all, rule1_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)

                        # ===== 规则2：品牌型号包含wagon或者tanto =====
                        rule2_name = 'wagon或tanto车型'
                        # 排除IX RENTAL，获取最低价格
                        rule2_non_ix = abp_lor_subset[
                            ((abp_lor_subset['品牌型号'].str.contains('wagon', case=False, na=False)) |
                             (abp_lor_subset['品牌型号'].str.contains('tanto', case=False, na=False))) &
                            (~abp_lor_subset['供应商品牌'].str.contains('IX RENTAL', case=False, na=False))
                            ].copy()

                        # 同时获取前4条数据用于BKS_filtered_data文件
                        rule2_all = abp_lor_subset[
                            (abp_lor_subset['品牌型号'].str.contains('wagon', case=False, na=False)) |
                            (abp_lor_subset['品牌型号'].str.contains('tanto', case=False, na=False))
                            ].copy()
                        result2 = get_top_n_unique(rule2_all, 4)

                        if not result2.empty:
                            result2['规则分类'] = rule2_name

                        if not rule2_non_ix.empty:
                            min_price_record = rule2_non_ix.loc[rule2_non_ix['价格（/天）'].idxmin()]
                            min_price_matrix[country_pickup_key][abp_lor_key][rule2_name] = min_price_record[
                                '价格（/天）']

                            min_record = get_min_non_ix_record(rule2_all, rule2_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)

                        # ===== 规则3：座位=5，动力类型不是混合动力 =====
                        rule3_name = '5座非混合动力'
                        # 排除IX RENTAL，获取最低价格
                        rule3_non_ix = abp_lor_subset[
                            (abp_lor_subset['座位'] == 5) &
                            (~abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False)) &
                            (~abp_lor_subset['供应商品牌'].str.contains('IX RENTAL', case=False, na=False))
                            ].copy()

                        # 同时获取前4条数据用于BKS_filtered_data文件
                        rule3_all = abp_lor_subset[
                            (abp_lor_subset['座位'] == 5) &
                            (~abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False))
                            ].copy()
                        result3 = get_top_n_unique(rule3_all, 4)

                        if not result3.empty:
                            result3['规则分类'] = rule3_name

                        if not rule3_non_ix.empty:
                            min_price_record = rule3_non_ix.loc[rule3_non_ix['价格（/天）'].idxmin()]
                            min_price_matrix[country_pickup_key][abp_lor_key][rule3_name] = min_price_record[
                                '价格（/天）']

                            min_record = get_min_non_ix_record(rule3_all, rule3_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)

                        # ===== 规则4：品牌型号包含solio或者airwave或者demio =====
                        rule4_name = 'solio/airwave/demio车型'
                        # 排除IX RENTAL，获取最低价格
                        rule4_non_ix = abp_lor_subset[
                            ((abp_lor_subset['品牌型号'].str.contains('solio', case=False, na=False)) |
                             (abp_lor_subset['品牌型号'].str.contains('airwave', case=False, na=False)) |
                             (abp_lor_subset['品牌型号'].str.contains('demio', case=False, na=False))) &
                            (~abp_lor_subset['供应商品牌'].str.contains('IX RENTAL', case=False, na=False))
                            ].copy()

                        # 同时获取前4条数据用于BKS_filtered_data文件
                        rule4_all = abp_lor_subset[
                            (abp_lor_subset['品牌型号'].str.contains('solio', case=False, na=False)) |
                            (abp_lor_subset['品牌型号'].str.contains('airwave', case=False, na=False)) |
                            (abp_lor_subset['品牌型号'].str.contains('demio', case=False, na=False))
                            ].copy()
                        result4 = get_top_n_unique(rule4_all, 4)

                        if not result4.empty:
                            result4['规则分类'] = rule4_name

                        if not rule4_non_ix.empty:
                            min_price_record = rule4_non_ix.loc[rule4_non_ix['价格（/天）'].idxmin()]
                            min_price_matrix[country_pickup_key][abp_lor_key][rule4_name] = min_price_record[
                                '价格（/天）']

                            min_record = get_min_non_ix_record(rule4_all, rule4_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)

                        # ===== 规则5：座位=5，动力类型为混合动力 =====
                        rule5_name = '5座混合动力'
                        # 排除IX RENTAL，获取最低价格
                        rule5_non_ix = abp_lor_subset[
                            (abp_lor_subset['座位'] == 5) &
                            (abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False)) &
                            (~abp_lor_subset['供应商品牌'].str.contains('IX RENTAL', case=False, na=False))
                            ].copy()

                        # 同时获取前4条数据用于BKS_filtered_data文件
                        rule5_all = abp_lor_subset[
                            (abp_lor_subset['座位'] == 5) &
                            (abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False))
                            ].copy()
                        result5 = get_top_n_unique(rule5_all, 4)

                        if not result5.empty:
                            result5['规则分类'] = rule5_name

                        if not rule5_non_ix.empty:
                            min_price_record = rule5_non_ix.loc[rule5_non_ix['价格（/天）'].idxmin()]
                            min_price_matrix[country_pickup_key][abp_lor_key][rule5_name] = min_price_record[
                                '价格（/天）']

                            min_record = get_min_non_ix_record(rule5_all, rule5_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)

                        # ===== 规则6：品牌型号包含prius或者shuttle或者sai，动力类型为混合动力 =====
                        rule6_name = 'prius/shuttle/sai混合动力'
                        # 排除IX RENTAL，获取最低价格
                        rule6_non_ix = abp_lor_subset[
                            ((abp_lor_subset['品牌型号'].str.contains('prius', case=False, na=False)) |
                             (abp_lor_subset['品牌型号'].str.contains('shuttle', case=False, na=False)) |
                             (abp_lor_subset['品牌型号'].str.contains('sai', case=False, na=False))) &
                            (abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False)) &
                            (~abp_lor_subset['供应商品牌'].str.contains('IX RENTAL', case=False, na=False))
                            ].copy()

                        # 同时获取前4条数据用于BKS_filtered_data文件
                        rule6_all = abp_lor_subset[
                            ((abp_lor_subset['品牌型号'].str.contains('prius', case=False, na=False)) |
                             (abp_lor_subset['品牌型号'].str.contains('shuttle', case=False, na=False)) |
                             (abp_lor_subset['品牌型号'].str.contains('sai', case=False, na=False))) &
                            (abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False))
                            ].copy()
                        result6 = get_top_n_unique(rule6_all, 4)

                        if not result6.empty:
                            result6['规则分类'] = rule6_name

                        if not rule6_non_ix.empty:
                            min_price_record = rule6_non_ix.loc[rule6_non_ix['价格（/天）'].idxmin()]
                            min_price_matrix[country_pickup_key][abp_lor_key][rule6_name] = min_price_record[
                                '价格（/天）']

                            min_record = get_min_non_ix_record(rule6_all, rule6_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)

                        # ===== 规则7：动力类型为混合动力，座位=6-8 =====
                        rule7_name = '6-8座'
                        # 排除IX RENTAL，获取最低价格
                        rule7_non_ix = abp_lor_subset[
                            (abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False)) &
                            (abp_lor_subset['座位'].astype(str).str.contains('6|7|8', na=False)) &
                            (~abp_lor_subset['供应商品牌'].str.contains('IX RENTAL', case=False, na=False))
                            ].copy()

                        # 同时获取前4条数据用于BKS_filtered_data文件
                        rule7_all = abp_lor_subset[
                            (abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False)) &
                            (abp_lor_subset['座位'].astype(str).str.contains('6|7|8', na=False))
                            ].copy()
                        result7 = get_top_n_unique(rule7_all, 4)

                        if not result7.empty:
                            result7['规则分类'] = rule7_name

                        if not rule7_non_ix.empty:
                            min_price_record = rule7_non_ix.loc[rule7_non_ix['价格（/天）'].idxmin()]
                            min_price_matrix[country_pickup_key][abp_lor_key][rule7_name] = min_price_record[
                                '价格（/天）']

                            min_record = get_min_non_ix_record(rule7_all, rule7_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)

                        # ===== 规则8：车型为越野车SUV =====
                        rule8_name = '越野车SUV'
                        # 排除IX RENTAL，获取最低价格
                        rule8_non_ix = abp_lor_subset[
                            (abp_lor_subset['座位'] == 5) &
                            (abp_lor_subset['车型'].str.contains('越野车SUV', case=False, na=False)) &
                            (~abp_lor_subset['供应商品牌'].str.contains('IX RENTAL', case=False, na=False))
                            ].copy()

                        # 同时获取前4条数据用于BKS_filtered_data文件
                        rule8_all = abp_lor_subset[
                            (abp_lor_subset['座位'] == 5) &
                            (abp_lor_subset['车型'].str.contains('越野车SUV', case=False, na=False))].copy()
                        result8 = get_top_n_unique(rule8_all, 4)

                        if not result8.empty:
                            result8['规则分类'] = rule8_name

                        if not rule8_non_ix.empty:
                            min_price_record = rule8_non_ix.loc[rule8_non_ix['价格（/天）'].idxmin()]
                            min_price_matrix[country_pickup_key][abp_lor_key][rule8_name] = min_price_record[
                                '价格（/天）']

                            min_record = get_min_non_ix_record(rule8_all, rule8_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)

                        # ===== 规则9：品牌型号含有vezel，动力类型为混合动力 =====
                        rule9_name = 'vezel混合动力'
                        # 排除IX RENTAL，获取最低价格
                        rule9_non_ix = abp_lor_subset[
                            (abp_lor_subset['品牌型号'].str.contains('vezel', case=False, na=False)) &
                            (abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False)) &
                            (~abp_lor_subset['供应商品牌'].str.contains('IX RENTAL', case=False, na=False))
                            ].copy()

                        # 同时获取前4条数据用于BKS_filtered_data文件
                        rule9_all = abp_lor_subset[
                            (abp_lor_subset['品牌型号'].str.contains('vezel', case=False, na=False)) &
                            (abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False))
                            ].copy()
                        result9 = get_top_n_unique(rule9_all, 4)

                        if not result9.empty:
                            result9['规则分类'] = rule9_name

                        if not rule9_non_ix.empty:
                            min_price_record = rule9_non_ix.loc[rule9_non_ix['价格（/天）'].idxmin()]
                            min_price_matrix[country_pickup_key][abp_lor_key][rule9_name] = min_price_record[
                                '价格（/天）']

                            min_record = get_min_non_ix_record(rule9_all, rule9_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)

                        # ===== 规则10：品牌型号含有tank或者spade =====
                        rule10_name = 'tank或spade车型'
                        # 排除IX RENTAL，获取最低价格
                        rule10_non_ix = abp_lor_subset[
                            ((abp_lor_subset['品牌型号'].str.contains('tank', case=False, na=False)) |
                             (abp_lor_subset['品牌型号'].str.contains('spade', case=False, na=False))) &
                            (~abp_lor_subset['供应商品牌'].str.contains('IX RENTAL', case=False, na=False))
                            ].copy()

                        # 同时获取前4条数据用于BKS_filtered_data文件
                        rule10_all = abp_lor_subset[
                            (abp_lor_subset['品牌型号'].str.contains('tank', case=False, na=False)) |
                            (abp_lor_subset['品牌型号'].str.contains('spade', case=False, na=False))
                            ].copy()
                        result10 = get_top_n_unique(rule10_all, 4)

                        if not result10.empty:
                            result10['规则分类'] = rule10_name

                        if not rule10_non_ix.empty:
                            min_price_record = rule10_non_ix.loc[rule10_non_ix['价格（/天）'].idxmin()]
                            min_price_matrix[country_pickup_key][abp_lor_key][rule10_name] = min_price_record[
                                '价格（/天）']

                            min_record = get_min_non_ix_record(rule10_all, rule10_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)

                        # ===== 规则11：品牌型号含有isis或stepwagon或者voxy或者delica =====
                        rule11_name = 'isis/stepwagon/voxy/delica车型'
                        # 排除IX RENTAL，获取最低价格
                        rule11_non_ix = abp_lor_subset[
                            ((abp_lor_subset['品牌型号'].str.contains('isis', case=False, na=False)) |
                             (abp_lor_subset['品牌型号'].str.contains('stepwagon', case=False, na=False)) |
                             (abp_lor_subset['品牌型号'].str.contains('voxy', case=False, na=False)) |
                             (abp_lor_subset['品牌型号'].str.contains('delica', case=False, na=False))) &
                            (~abp_lor_subset['供应商品牌'].str.contains('IX RENTAL', case=False, na=False))
                            ].copy()

                        # 同时获取前4条数据用于BKS_filtered_data文件
                        rule11_all = abp_lor_subset[
                            (abp_lor_subset['品牌型号'].str.contains('isis', case=False, na=False)) |
                            (abp_lor_subset['品牌型号'].str.contains('stepwagon', case=False, na=False)) |
                            (abp_lor_subset['品牌型号'].str.contains('voxy', case=False, na=False)) |
                            (abp_lor_subset['品牌型号'].str.contains('delica', case=False, na=False))
                            ].copy()
                        result11 = get_top_n_unique(rule11_all, 4)

                        if not result11.empty:
                            result11['规则分类'] = rule11_name

                        if not rule11_non_ix.empty:
                            min_price_record = rule11_non_ix.loc[rule11_non_ix['价格（/天）'].idxmin()]
                            min_price_matrix[country_pickup_key][abp_lor_key][rule11_name] = min_price_record[
                                '价格（/天）']

                            min_record = get_min_non_ix_record(rule11_all, rule11_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)

                        # ===== 规则12：品牌型号含有vellfire，座位=7 =====
                        rule12_name = '7座vellfire车型'
                        # 排除IX RENTAL，获取最低价格
                        rule12_non_ix = abp_lor_subset[
                            (abp_lor_subset['品牌型号'].str.contains('vellfire', case=False, na=False)) &
                            (abp_lor_subset['座位'] == 7) &
                            (~abp_lor_subset['供应商品牌'].str.contains('IX RENTAL', case=False, na=False))
                            ].copy()

                        # 同时获取前4条数据用于BKS_filtered_data文件
                        rule12_all = abp_lor_subset[
                            (abp_lor_subset['品牌型号'].str.contains('vellfire', case=False, na=False)) &
                            (abp_lor_subset['座位'] == 7)
                            ].copy()
                        result12 = get_top_n_unique(rule12_all, 4)

                        if not result12.empty:
                            result12['规则分类'] = rule12_name

                        if not rule12_non_ix.empty:
                            min_price_record = rule12_non_ix.loc[rule12_non_ix['价格（/天）'].idxmin()]
                            min_price_matrix[country_pickup_key][abp_lor_key][rule12_name] = min_price_record[
                                '价格（/天）']

                            min_record = get_min_non_ix_record(rule12_all, rule12_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)

                        # ===== 规则13：品牌型号含有serena，座位=8，动力类型为混合动力 =====
                        rule13_name = '8座serena混合动力'
                        # 排除IX RENTAL，获取最低价格
                        rule13_non_ix = abp_lor_subset[
                            (abp_lor_subset['品牌型号'].str.contains('serena', case=False, na=False)) &
                            (abp_lor_subset['座位'] == 8) &
                            (abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False)) &
                            (~abp_lor_subset['供应商品牌'].str.contains('IX RENTAL', case=False, na=False))
                            ].copy()

                        # 同时获取前4条数据用于BKS_filtered_data文件
                        rule13_all = abp_lor_subset[
                            (abp_lor_subset['品牌型号'].str.contains('serena', case=False, na=False)) &
                            (abp_lor_subset['座位'] == 8) &
                            (abp_lor_subset['动力类型'].str.contains('混合动力', case=False, na=False))
                            ].copy()
                        result13 = get_top_n_unique(rule13_all, 4)

                        if not result13.empty:
                            result13['规则分类'] = rule13_name

                        if not rule13_non_ix.empty:
                            min_price_record = rule13_non_ix.loc[rule13_non_ix['价格（/天）'].idxmin()]
                            min_price_matrix[country_pickup_key][abp_lor_key][rule13_name] = min_price_record[
                                '价格（/天）']

                            min_record = get_min_non_ix_record(rule13_all, rule13_name, abp_val, lor_val)
                            if min_record:
                                min_non_ix_records.append(min_record)

                        # 合并当前ABP-LOR分类的所有规则结果
                        current_result = pd.concat(
                            [result1, result2, result3, result4, result5, result6, result7,
                             result8, result9, result10, result11, result12, result13],
                            ignore_index=True
                        )

                        # 添加组合信息到结果中
                        if not current_result.empty:
                            current_result['客源地'] = country
                            current_result['取车点'] = pickup
                            current_result['ABP'] = abp_val
                            current_result['LOR'] = lor_val
                            all_results.append(current_result)

            if abp_lor_count == 0:
                print(f"  警告: 未找到有效的ABP-LOR组合")

        # 合并所有组合的结果
        if all_results:
            final_result = pd.concat(all_results, ignore_index=True)

            # 生成输出文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f'BKS_filtered_data_{timestamp}.csv'

            # 保存到新 CSV 文件（UTF-8 with BOM，避免 Excel 中文乱码）
            final_result.to_csv(output_file, index=False, encoding='utf-8-sig')

            print(f"\n处理完成！共处理 {len(country_pickup_combinations)} 个组合")
            print(f"共保留 {len(final_result)} 条数据，已保存到 {output_file}")

            # 创建非IX最低价格矩阵，格式与代码1相同
            if min_price_matrix:
                # 定义所有规则名称（与代码1的行顺序保持一致）
                all_rule_names = [
                    '全部数据',
                    '5座非混合动力',
                    '5座混合动力',
                    '6-8座',
                    '越野车SUV',
                    'isis/stepwagon/voxy/delica车型',
                    '7座vellfire车型',
                    'wagon或tanto车型',
                    'solio/airwave/demio车型',
                    'prius/shuttle/sai混合动力',
                    'vezel混合动力',
                    'tank或spade车型',
                    '8座serena混合动力'
                ]

                # 收集所有ABP_LOR组合，保持原始顺序
                all_abp_lor_keys = []
                # 遍历所有组合，按照原始顺序收集ABP_LOR键
                for country_pickup_key in min_price_matrix:
                    # 获取该组合下所有的ABP_LOR键
                    for abp_lor_key in min_price_matrix[country_pickup_key]:
                        if abp_lor_key not in all_abp_lor_keys:
                            all_abp_lor_keys.append(abp_lor_key)

                # 为每个客源地_取车点组合创建结果DataFrame
                all_matrix_dfs = []

                for country_pickup_key in min_price_matrix:
                    # 拆分客源地和取车点
                    if '_' in country_pickup_key:
                        parts = country_pickup_key.split('_', 1)
                        country = parts[0]
                        pickup = parts[1] if len(parts) > 1 else ''
                    else:
                        country = country_pickup_key
                        pickup = ''

                    # 创建空的DataFrame来存储当前组合的结果
                    result_dict = {}

                    # 1. 添加日期信息行（作为第一行） - 格式：p:月/日,d:月/日
                    date_info_row = {}
                    for abp_lor_key in all_abp_lor_keys:
                        if (country_pickup_key in datetime_info and
                                abp_lor_key in datetime_info[country_pickup_key]):
                            p_datetime_val = datetime_info[country_pickup_key][abp_lor_key].get('p_datetime', '')
                            d_datetime_val = datetime_info[country_pickup_key][abp_lor_key].get('d_datetime', '')
                            date_info_row[abp_lor_key] = f"p:{p_datetime_val},d:{d_datetime_val}"
                        else:
                            date_info_row[abp_lor_key] = ''

                    result_dict['日期信息'] = date_info_row

                    # 2. 添加各规则行（只包含非IX最低价格）
                    for rule_name in all_rule_names:
                        rule_row = {}
                        for abp_lor_key in all_abp_lor_keys:
                            if (abp_lor_key in min_price_matrix[country_pickup_key] and
                                    rule_name in min_price_matrix[country_pickup_key][abp_lor_key]):
                                price = min_price_matrix[country_pickup_key][abp_lor_key][rule_name]
                                rule_row[abp_lor_key] = round(price, 2) if pd.notna(price) else np.nan
                            else:
                                rule_row[abp_lor_key] = np.nan
                        result_dict[rule_name] = rule_row

                    # 将字典转换为DataFrame
                    result_df = pd.DataFrame(result_dict).T  # 转置，使规则名作为行索引

                    # 添加客源地和取车点列（插入到最前面）
                    result_df.insert(0, '客源地', country)
                    result_df.insert(1, '取车点', pickup)

                    # 确保行顺序与代码1完全一致（只包含日期信息行和13个规则行）
                    required_rows = ['日期信息'] + all_rule_names
                    result_df = result_df.reindex(required_rows)

                    all_matrix_dfs.append(result_df)

                # 合并所有组合的DataFrame
                if all_matrix_dfs:
                    final_matrix_df = pd.concat(all_matrix_dfs)

                    # 生成矩阵输出文件名
                    min_output_file = f'BKS_min_{timestamp}.csv'

                    # 保存矩阵到CSV文件
                    final_matrix_df.to_csv(min_output_file, encoding='utf-8-sig')

                    # 打印示例
                    print(f"\n示例输出格式:")
                    print(f"  行索引: 日期信息, {', '.join(all_rule_names[:3])}...")
                    print(f"  列: 客源地, 取车点, {all_abp_lor_keys[0] if all_abp_lor_keys else 'ABP=XX_LOR=XX'}, ...")

                    return output_file, min_output_file

        else:
            print("没有筛选出任何非IX最低价格数据，请检查原始数据和筛选条件。")
            return None, None

    except Exception as e:
        print(f"数据过滤程序出错: {e}")
        import traceback
        traceback.print_exc()
        return None, None
