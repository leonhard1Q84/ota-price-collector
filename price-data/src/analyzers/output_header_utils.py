# -*- coding: utf-8 -*-
# 统一输出文件“置顶表头”与列分组逻辑（仅影响导出，不影响终端打印）。
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional, Sequence, Tuple

import pandas as pd


@dataclass(frozen=True)
class 节日日期范围:
    """用于按月/日判断节日区间（忽略年份）。"""

    名称: str
    开始: date
    结束: date

    def 包含(self, d: date) -> bool:
        return self.开始 <= d <= self.结束


_ABP_RE = re.compile(r"ABP=(\d+)")
# 不再需要日期解析相关正则和节日逻辑，但保留以兼容后续扩展（可选删除）
_ISO_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_MD_DATE_RE = re.compile(r"(\d{1,2})[/.](\d{1,2})")

_参考年 = 2026  # 保留但不再使用

# 删除节日范围定义（或注释掉）
# _春节范围 = 节日日期范围("春节组", date(_参考年, 2, 13), date(_参考年, 2, 24))
# _樱花节范围 = 节日日期范围("樱花节组", date(_参考年, 3, 30), date(_参考年, 4, 6))

_日期行候选: Tuple[str, ...] = ("日期信息", "日期")
_需要移除的旧分组行: Tuple[str, ...] = ("组别", "分组信息")

_默认固定列: Tuple[str, ...] = ("客源地", "取车点", "分组序号", "分组标题")


def _取原始列名(col) -> str:
    if isinstance(col, tuple) and len(col) > 0:
        return str(col[-1])
    return str(col)


def _提取_abp(col_name: str) -> Optional[int]:
    match = _ABP_RE.search(col_name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


# 以下函数不再被调用，可删除（但为最小改动保留）
def _提取月日列表(value) -> Sequence[date]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if hasattr(value, "month") and hasattr(value, "day"):
        try:
            return [date(_参考年, int(value.month), int(value.day))]
        except Exception:
            return []
    text = str(value)
    if not text or text.lower() in ("nan", "none", "null"):
        return []
    results = []
    for _, m, d in _ISO_DATE_RE.findall(text):
        try:
            results.append(date(_参考年, int(m), int(d)))
        except Exception:
            continue
    for m, d in _MD_DATE_RE.findall(text):
        try:
            results.append(date(_参考年, int(m), int(d)))
        except Exception:
            continue
    return results


# 删除节日判断函数
# def _判断节日组(候选日期: Iterable[date]) -> Optional[str]:
#     候选日期列表 = list(候选日期)
#     if any(_春节范围.包含(d) for d in 候选日期列表):
#         return _春节范围.名称
#     if any(_樱花节范围.包含(d) for d in 候选日期列表):
#         return _樱花节范围.名称
#     return None


def _判断_abp组(abp: Optional[int]) -> str:
    """根据ABP值返回对应的分组名称"""
    if abp is None:
        return ""
    if abp < 7:
        return "1-7组"
    elif 7 <= abp <= 15:
        return "8-15组"
    elif 16 <= abp <= 30:
        return "15-30组"
    elif 31 <= abp <= 60:
        return "30-60组"
    else:
        return "其他组"

def 为输出添加分组置顶表头(
    df: pd.DataFrame,
    *,
    固定列: Sequence[str] = _默认固定列,
    日期行候选: Sequence[str] = _日期行候选,
) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    out = df.copy()

    # 避免历史实现把“组别/分组信息”作为数据行导致重复展示
    for idx_name in _需要移除的旧分组行:
        if idx_name in out.index:
            out = out.drop(index=idx_name)

    固定列集合 = set(固定列)

    # 判断是否存在 ABP-LOR 列
    原始列名列表 = [_取原始列名(c) for c in out.columns]
    if not any(("ABP=" in name and "LOR=" in name) for name in 原始列名列表):
        return out

    # 注意：虽然保留了日期行块读取逻辑，但不再用于分组（可优化删除）
    日期行块 = []
    for row_name in 日期行候选:
        if row_name in out.index:
            日期行块.append(out.loc[out.index == row_name])

    新列 = []
    for col, 原始列名 in zip(out.columns, 原始列名列表):
        if 原始列名 in 固定列集合:
            新列.append(("", 原始列名))
            continue

        if "ABP=" not in 原始列名 or "LOR=" not in 原始列名:
            新列.append(("", 原始列名))
            continue

        abp = _提取_abp(原始列名)

        # 节日判断已移除，直接使用 ABP 分组
        组别 = _判断_abp组(abp)
        新列.append((组别, 原始列名))

    out.columns = pd.MultiIndex.from_tuples(新列)
    return out