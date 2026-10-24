# -*- coding: utf-8 -*-
"""
IX Rental 评分汇总工具。
"""
import csv
import json
import os
import re
import urllib.parse
from datetime import datetime
from typing import Dict, Iterable, List, Tuple

from logger import log_success, log_warning
from path_utils import ensure_dir, get_data_dir, get_price_data_dir


IX_SUPPLIER_NAME = "IX RENTAL"
SUMMARY_DOC_CODE = "BKS"
SUMMARY_FILENAME_PREFIX = "评分汇总"
LEGACY_KLOOK_DATA_ROOT = r"F:\klook\src\data"
LEGACY_PRICE_DATA_ROOT = r"C:\Users\42047\PycharmProjects\price_data\src\data"
LEGACY_KLOOK_PRICE_ROOT = r"F:\klook\src\price_data"
LEGACY_PRICE_ROOT = r"C:\Users\42047\PycharmProjects\price_data\src\price_data"

KLOOK_DATA_ROOT = os.environ.get("KLOOK_DATA_ROOT", LEGACY_KLOOK_DATA_ROOT)
PRICE_DATA_ROOT = os.environ.get("PRICE_DATA_ROOT", get_data_dir())
MERGED_ROOT = os.environ.get("PRICE_DATA_MERGED_ROOT", get_price_data_dir())
SCAN_DATA_ROOTS = [KLOOK_DATA_ROOT, PRICE_DATA_ROOT, LEGACY_PRICE_DATA_ROOT]
LEGACY_PRICE_ROOTS = [LEGACY_KLOOK_PRICE_ROOT, LEGACY_PRICE_ROOT, MERGED_ROOT]

RAW_OUTPUT_RE = re.compile(r"^output_(?P<code>[A-Z0-9]+)_(?P<stamp>\d{8}_\d{6})\.csv$", re.IGNORECASE)
LEGACY_IX_RE = re.compile(r"^[A-Z0-9]+_IX_project_summary_(?P<date>\d{8})_000000\.csv$", re.IGNORECASE)
LEGACY_MERGED_IX_RE = re.compile(r"^BKS_IX_(?P<date>\d{8})_000000\.csv$", re.IGNORECASE)
SUMMARY_RE = re.compile(r"^评分汇总_?(?P<date>\d{8})\.csv$")

RAW_PICK_FIELD = "取车点"
RAW_SUPPLIER_FIELD = "供应商品牌"
RAW_RATING_FIELD = "评分"

FIELD_PICK = "pick"
FIELD_RATING = "评分"

SUMMARY_HEADERS = [
    FIELD_PICK,
    FIELD_RATING,
]


def _safe_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_supplier_name(value: str) -> str:
    return " ".join(_safe_text(value).upper().split())


def _summary_key(row: Dict[str, str]) -> Tuple[str, str]:
    pick = _safe_text(row.get(FIELD_PICK))
    return pick, pick


def _summary_filename(date_str: str) -> str:
    return f"{SUMMARY_FILENAME_PREFIX}{date_str}.csv"


def _load_url_metadata_map(csv_file: str) -> Dict[str, Dict[str, str]]:
    src_dir = os.path.dirname(os.path.dirname(os.path.dirname(csv_file)))
    urls_file = os.path.join(src_dir, "urls", "urls.json")
    if not os.path.isfile(urls_file):
        return {}

    try:
        with open(urls_file, "r", encoding="utf-8") as f:
            items = json.load(f)
    except Exception as exc:
        log_warning(f"读取 URL 元数据失败，继续使用 CSV 自身字段: {exc}")
        return {}

    metadata_map: Dict[str, Dict[str, str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        url = _safe_text(item.get("url"))
        if not url:
            continue
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        display_pick = _safe_text((params.get("pick") or [""])[0])
        if not display_pick or display_pick in metadata_map:
            continue
        metadata_map[display_pick] = {
            FIELD_PICK: _safe_text(item.get("poi_name")) or display_pick,
        }
    return metadata_map


def _detect_project_name(csv_file: str) -> str:
    normalized = os.path.normcase(os.path.abspath(csv_file))
    if normalized.startswith(os.path.normcase(os.path.abspath(KLOOK_DATA_ROOT))):
        return "klook"
    return "price_data"


def _extract_summary_rows(csv_file: str, code: str, generated_at: str) -> List[Dict[str, str]]:
    extracted: Dict[Tuple[str, str], Dict[str, str]] = {}
    metadata_map = _load_url_metadata_map(csv_file)
    project_name = _detect_project_name(csv_file)

    with open(csv_file, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for raw_row in reader:
            pick_value = _safe_text(raw_row.get(RAW_PICK_FIELD))
            if not pick_value:
                continue

            metadata = metadata_map.get(pick_value, {})
            supplier_name = _normalize_supplier_name(raw_row.get(RAW_SUPPLIER_FIELD))
            rating = _safe_text(raw_row.get(RAW_RATING_FIELD))

            key = (pick_value, pick_value)
            if key not in extracted:
                extracted[key] = {
                    FIELD_PICK: pick_value,
                    FIELD_RATING: "",
                }

            if supplier_name == IX_SUPPLIER_NAME:
                if not extracted[key][FIELD_RATING] and rating:
                    extracted[key][FIELD_RATING] = rating

    return list(extracted.values())


def _scan_raw_output_files(date_str: str, extra_roots: Iterable[str]) -> List[str]:
    roots = list(extra_roots) + list(SCAN_DATA_ROOTS)
    found: List[Tuple[float, str]] = []
    seen = set()

    for root in roots:
        date_dir = os.path.join(root, date_str)
        if not os.path.isdir(date_dir):
            continue
        for name in os.listdir(date_dir):
            if not name.lower().endswith(".csv"):
                continue
            if not RAW_OUTPUT_RE.match(name):
                continue
            full_path = os.path.join(date_dir, name)
            key = os.path.normcase(os.path.abspath(full_path))
            if key in seen:
                continue
            seen.add(key)
            try:
                mtime = os.path.getmtime(full_path)
            except OSError:
                continue
            found.append((mtime, full_path))

    found.sort(key=lambda item: (item[0], item[1].lower()))
    return [path for _mtime, path in found]


def _cleanup_legacy_files(date_str: str, merged_path: str) -> None:
    keep_path = os.path.normcase(os.path.abspath(merged_path))
    for root in LEGACY_PRICE_ROOTS:
        date_dir = os.path.join(root, date_str)
        if not os.path.isdir(date_dir):
            continue
        for name in os.listdir(date_dir):
            full_path = os.path.join(date_dir, name)
            normalized = os.path.normcase(os.path.abspath(full_path))
            if normalized == keep_path:
                continue
            if SUMMARY_RE.match(name):
                try:
                    os.remove(full_path)
                except OSError as exc:
                    log_warning(f"删除旧评分汇总失败: {full_path} - {exc}")
                continue
            if LEGACY_IX_RE.match(name) or LEGACY_MERGED_IX_RE.match(name):
                try:
                    os.remove(full_path)
                except OSError as exc:
                    log_warning(f"删除旧 IX 汇总文件失败: {full_path} - {exc}")


def rebuild_daily_rating_summary(date_str: str, extra_roots: Iterable[str] = ()) -> str:
    merged_rows: Dict[Tuple[str, str], Dict[str, str]] = {}
    generated_at = datetime.now().isoformat(timespec="seconds")

    for file_path in _scan_raw_output_files(date_str, extra_roots):
        match = RAW_OUTPUT_RE.match(os.path.basename(file_path))
        if not match:
            continue
        code = match.group("code").upper()
        for row in _extract_summary_rows(file_path, code, generated_at):
            merged_rows[_summary_key(row)] = row

    final_rows = sorted(
        merged_rows.values(),
        key=lambda row: (
            _safe_text(row.get(FIELD_PICK)),
        ),
    )

    merged_date_dir = ensure_dir(os.path.join(MERGED_ROOT, date_str))
    merged_path = os.path.join(merged_date_dir, _summary_filename(date_str))
    _cleanup_legacy_files(date_str, merged_path)

    with open(merged_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_HEADERS)
        writer.writeheader()
        writer.writerows(final_rows)

    if final_rows:
        log_success(f"每日评分汇总已重建: {merged_path}")
    else:
        log_warning(f"未发现 {date_str} 的原始数据，仍已生成空白评分汇总: {merged_path}")
    return merged_path
