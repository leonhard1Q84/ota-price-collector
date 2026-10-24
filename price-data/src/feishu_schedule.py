import argparse
import ast
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, urlunparse

from feishu_sync import FeishuClient, FeishuError, parse_wiki_token


DEFAULT_CODES = [
    "BKS",
    "HKT",
    "NGO",
    "OKA",
    "SMV",
    "FUK",
    "GTZY",
    "GXCZ",
    "JDM",
    "KHH",
    "NRT",
    "QCQ",
    "TPE",
    "CTS"
]

FIELD_TYPE_TEXT = 1
FIELD_TYPE_CHECKBOX = 7
STATE_VERSION = 7
STATUS_IDLE = "空闲"
STATUS_QUEUED = "等待中"
STATUS_RUNNING = "运行中"
STATUS_FAILED = "失败"
RESULT_SUCCESS = "成功"
RESULT_FAILED = "失败"
COMPENSATED_RESULT_TEXT = "已中断"
COMPENSATED_ERROR_TEXT = "检测到上次调度异常退出，已自动补偿为已中断"
SIGNAL_STOP_ERROR_TEXT = "调度器收到中断信号，任务已停止"

STATE_LOCK = threading.RLock()
ACTIVE_CONTEXT: Optional["SchedulerRuntimeContext"] = None
INTERRUPTED_TASK_ERRORS: Dict[str, str] = {}
STATUS_SYNC_RETRY_DELAY_SECONDS = 60
STATUS_SYNC_PHASES = {"queued", "running", "finished", "failed"}
FEISHU_CLIENT_CACHE_LOCK = threading.RLock()
FEISHU_CLIENT_CACHE: Dict[Tuple[str, str], "FeishuBitableClient"] = {}


def is_invalid_access_token_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return "99991663" in text or "invalid access token" in text


def send_scheduler_alert(subject: str, detail: str) -> bool:
    """将调度器异常发送到独立告警群；通知失败不影响任务清理。"""

    try:
        import feishu_notifier

        message = "\n".join(
            [
                f"price-data 调度异常：{subject}",
                f"发生时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
                f"详情：{str(detail or '未知错误').strip()[:1200]}",
            ]
        )
        return bool(feishu_notifier.发送告警(message))
    except Exception:
        return False


@dataclass
class RecordTask:
    record_id: str
    code: str
    run_time: str
    fields: Dict[str, Any]

    @property
    def task_id(self) -> str:
        return self.record_id

    @property
    def task_label(self) -> str:
        return self.code


@dataclass
class SchedulerRuntimeContext:
    config: Dict[str, Any]
    meta: Dict[str, Any]
    runtime_paths: Dict[str, Path]
    lock_payload: Dict[str, Any]


class ScheduleConfigError(RuntimeError):
    pass


def load_schedule_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    default_path = Path(__file__).with_name("feishu_schedule_config.json")
    example_path = Path(__file__).with_name("feishu_schedule_config.example.json")
    path = Path(config_path) if config_path else default_path
    if not path.exists() and not config_path:
        path = example_path
    if not path.exists():
        raise FileNotFoundError(f"找不到调度配置文件: {path}")

    with path.open("r", encoding="utf-8") as file:
        config = json.load(file)

    project_root = Path(__file__).resolve().parents[1]
    config["_config_path"] = str(path)
    config["_project_root"] = str(project_root)
    config["app_id"] = resolve_env_value(config.get("app_id")) or os.getenv("FEISHU_APP_ID", "")
    config["app_secret"] = resolve_env_value(config.get("app_secret")) or os.getenv("FEISHU_APP_SECRET", "")
    config.setdefault("poll_interval_seconds", 30)
    config.setdefault("max_concurrency", 2)
    config.setdefault("active_tick_scan_interval_seconds", 10)
    config.setdefault("projects", {})
    config.setdefault("project_candidates", {})
    config.setdefault("fields", {})
    config.setdefault("base_name", "PriceData URL 调度")
    config.setdefault("table_name", "每日任务")
    normalized_projects: Dict[str, str] = {}
    for code, root in dict(config.get("projects") or {}).items():
        code_text = str(code).strip().upper()
        project_root_text = normalize_project_root(root)
        if code_text and project_root_text:
            normalized_projects[code_text] = project_root_text
    config["projects"] = normalized_projects

    raw_candidate_map = dict(config.get("project_candidates") or {})
    normalized_candidate_map: Dict[str, List[str]] = {}
    all_project_roots: List[str] = []
    for code, preferred_root in normalized_projects.items():
        raw_candidates = raw_candidate_map.get(code)
        if isinstance(raw_candidates, (list, tuple, set)):
            candidate_roots = unique_project_roots([preferred_root, *list(raw_candidates)])
        else:
            candidate_roots = unique_project_roots([preferred_root, raw_candidates])
        if not candidate_roots and preferred_root:
            candidate_roots = [preferred_root]
        normalized_candidate_map[code] = candidate_roots
        for candidate_root in candidate_roots:
            if candidate_root not in all_project_roots:
                all_project_roots.append(candidate_root)
    config["project_candidates"] = normalized_candidate_map
    config["all_project_roots"] = all_project_roots

    runtime_dir = config.get("data_root", "src\\feishu_schedule_runtime")
    runtime_path = Path(runtime_dir)
    if not runtime_path.is_absolute():
        runtime_path = project_root / runtime_path
    config["runtime_dir"] = str(runtime_path)
    return config


def resolve_env_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.startswith("${") and text.endswith("}"):
        return os.getenv(text[2:-1], "")
    return os.path.expandvars(text)


def ensure_runtime_paths(config: Dict[str, Any]) -> Dict[str, Path]:
    runtime_dir = Path(config["runtime_dir"])
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return {
        "runtime_dir": runtime_dir,
        "app_state": runtime_dir / "schedule_state.json",
        "table_meta": runtime_dir / "table_meta.json",
        "tick_lock": runtime_dir / "tick.lock",
        "runner_log": runtime_dir / "schedule.log",
    }


def parse_state_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def normalize_pending_status_updates(value: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for raw_task_id, item in value.items():
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or raw_task_id).strip()
        record_id = str(item.get("record_id", "")).strip()
        code = str(item.get("code", "")).strip().upper()
        phase = str(item.get("phase", "")).strip().lower()
        fields_update = item.get("fields_update")
        if not task_id or not record_id or not code or phase not in STATUS_SYNC_PHASES or not isinstance(fields_update, dict):
            continue
        try:
            retry_count = max(0, int(item.get("retry_count", 0)))
        except (TypeError, ValueError):
            retry_count = 0
        normalized[task_id] = {
            "task_id": task_id,
            "record_id": record_id,
            "code": code,
            "phase": phase,
            "fields_update": dict(fields_update),
            "retry_count": retry_count,
            "next_retry_at": str(item.get("next_retry_at", "")).strip(),
            "last_error": str(item.get("last_error", "")).strip()[:1000],
            "updated_at": str(item.get("updated_at", "")).strip(),
        }
    return normalized


def _extract_generate_config_pick_set(generate_config_path: Path) -> Set[str]:
    source = generate_config_path.read_text(encoding="utf-8")
    marker = "poi_configs = {"
    marker_index = source.find(marker)
    if marker_index < 0:
        raise ScheduleConfigError(f"未在 {generate_config_path} 中找到 poi_configs 定义")

    brace_start = source.find("{", marker_index)
    if brace_start < 0:
        raise ScheduleConfigError(f"未在 {generate_config_path} 中解析到 poi_configs 起始位置")

    depth = 0
    brace_end = -1
    for index in range(brace_start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                brace_end = index + 1
                break
    if brace_end < 0:
        raise ScheduleConfigError(f"未在 {generate_config_path} 中解析到完整 poi_configs")

    payload = ast.literal_eval(source[brace_start:brace_end])
    if not isinstance(payload, dict):
        raise ScheduleConfigError(f"{generate_config_path} 中的 poi_configs 不是字典")
    return {str(key).strip().upper() for key in payload.keys() if str(key).strip()}


def _extract_urls_json_pick_set(urls_json_path: Path) -> Set[str]:
    with urls_json_path.open("r", encoding="utf-8-sig") as file:
        payload = json.load(file)
    if not isinstance(payload, list):
        raise ScheduleConfigError(f"{urls_json_path} 不是有效的 URL 列表")
    return {
        str(item.get("pick", "")).strip().upper()
        for item in payload
        if isinstance(item, dict) and str(item.get("pick", "")).strip()
    }


def _project_support_snapshot(project_root: str) -> Dict[str, Any]:
    root_path = Path(project_root)
    src_dir = root_path / "src"
    urls_path = src_dir / "urls" / "urls.json"
    generate_config_path = src_dir / "generate_config.py"
    if not urls_path.exists():
        raise ScheduleConfigError(f"未找到 URLs 文件: {urls_path}")
    if not generate_config_path.exists():
        raise ScheduleConfigError(f"未找到 generate_config.py: {generate_config_path}")
    return {
        "project_root": str(root_path),
        "urls_path": str(urls_path),
        "generate_config_path": str(generate_config_path),
        "urls_picks": _extract_urls_json_pick_set(urls_path),
        "generate_config_picks": _extract_generate_config_pick_set(generate_config_path),
    }


def validate_project_support_consistency(config: Dict[str, Any], runtime_paths: Dict[str, Path]) -> None:
    project_roots = unique_project_roots(list(config.get("all_project_roots") or []))
    if len(project_roots) < 2:
        return

    scheduled_codes = {
        str(code).strip().upper()
        for code in list((config.get("projects") or {}).keys()) + list((config.get("project_candidates") or {}).keys())
        if str(code).strip()
    }
    snapshots = [_project_support_snapshot(project_root) for project_root in project_roots]
    baseline = snapshots[0]
    problems: List[str] = []

    for snapshot in snapshots:
        urls_picks = set(snapshot["urls_picks"]) & scheduled_codes
        generate_picks = set(snapshot["generate_config_picks"]) & scheduled_codes
        snapshot["scheduled_urls_picks"] = urls_picks
        snapshot["scheduled_generate_config_picks"] = generate_picks
        if urls_picks != generate_picks:
            missing_in_urls = sorted(generate_picks - urls_picks)
            missing_in_generate = sorted(urls_picks - generate_picks)
            if missing_in_urls:
                problems.append(
                    f"{snapshot['project_root']} 的 urls.json 缺少代码: {', '.join(missing_in_urls)}"
                )
            if missing_in_generate:
                problems.append(
                    f"{snapshot['project_root']} 的 generate_config.py 缺少代码: {', '.join(missing_in_generate)}"
                )

    for snapshot in snapshots[1:]:
        baseline_urls = set(baseline["scheduled_urls_picks"])
        snapshot_urls = set(snapshot["scheduled_urls_picks"])
        baseline_generate = set(baseline["scheduled_generate_config_picks"])
        snapshot_generate = set(snapshot["scheduled_generate_config_picks"])

        missing_urls = sorted(baseline_urls - snapshot_urls)
        extra_urls = sorted(snapshot_urls - baseline_urls)
        if missing_urls:
            problems.append(
                f"{snapshot['project_root']} 的 urls.json 相比 {baseline['project_root']} 缺少代码: {', '.join(missing_urls)}"
            )
        if extra_urls:
            problems.append(
                f"{snapshot['project_root']} 的 urls.json 相比 {baseline['project_root']} 多出代码: {', '.join(extra_urls)}"
            )

        missing_generate = sorted(baseline_generate - snapshot_generate)
        extra_generate = sorted(snapshot_generate - baseline_generate)
        if missing_generate:
            problems.append(
                f"{snapshot['project_root']} 的 generate_config.py 相比 {baseline['project_root']} 缺少代码: {', '.join(missing_generate)}"
            )
        if extra_generate:
            problems.append(
                f"{snapshot['project_root']} 的 generate_config.py 相比 {baseline['project_root']} 多出代码: {', '.join(extra_generate)}"
            )

    if problems:
        for problem in problems:
            log_line(runtime_paths, f"[配置一致性检查失败] {problem}")
        raise ScheduleConfigError("两个项目的代码支持范围不一致，已阻止调度启动；详情见 schedule.log")

    log_line(
        runtime_paths,
        f"[配置一致性检查通过] 已确认 {len(project_roots)} 个项目的 urls.json 与 generate_config.py 支持代码集合一致",
    )


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def normalize_project_root(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return os.path.normcase(os.path.abspath(os.path.expandvars(text)))


def _list_matching_files(directory: Path, patterns: List[str]) -> List[Path]:
    matches: List[Path] = []
    if not directory.exists():
        return matches
    for pattern in patterns:
        matches.extend(path for path in directory.glob(pattern) if path.is_file())
    return matches


def collect_task_output_artifacts(project_root: Path, code: str, run_date: datetime) -> Dict[str, List[Path]]:
    date_text = run_date.strftime("%Y%m%d")
    upper_code = str(code or "").strip().upper()
    raw_dir = project_root / "src" / "data" / date_text
    processed_dir = project_root / "src" / "price_data" / date_text

    raw_files = _list_matching_files(raw_dir, [f"output_{upper_code}_*.csv"])
    processed_files = _list_matching_files(
        processed_dir,
        [
            f"{upper_code}_filtered_data_*.csv",
            f"{upper_code}_BM*.csv",
            f"{upper_code}_BM_*.csv",
            f"{upper_code}_min_*.csv",
            f"{upper_code}_*.xlsx",
        ],
    )
    return {"raw": raw_files, "processed": processed_files}


def filter_new_artifacts(paths: List[Path], started_at: datetime) -> List[Path]:
    started_ts = started_at.timestamp()
    new_paths: List[Path] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size <= 0:
            continue
        if stat.st_mtime >= started_ts:
            new_paths.append(path)
    return new_paths


def verify_task_output_artifacts(project_root: Path, code: str, started_at: datetime) -> Tuple[bool, str]:
    artifacts = collect_task_output_artifacts(project_root, code, started_at)
    new_raw_files = filter_new_artifacts(artifacts["raw"], started_at)
    if new_raw_files:
        return True, ""

    new_processed_files = filter_new_artifacts(artifacts["processed"], started_at)
    raw_dir = project_root / "src" / "data" / started_at.strftime("%Y%m%d")
    processed_dir = project_root / "src" / "price_data" / started_at.strftime("%Y%m%d")
    processed_note = ""
    if new_processed_files:
        processed_note = f"; found processed outputs only in {processed_dir}"
    return False, (
        f"main_controller exited 0 but no new output_{str(code or '').strip().upper()}_*.csv "
        f"was produced in {raw_dir}{processed_note}"
    )


def unique_project_roots(values: List[Any]) -> List[str]:
    normalized: List[str] = []
    for value in values:
        project_root = normalize_project_root(value)
        if project_root and project_root not in normalized:
            normalized.append(project_root)
    return normalized


def get_preferred_project_root(config: Dict[str, Any], code: str) -> str:
    return normalize_project_root((config.get("projects") or {}).get(code))


def get_candidate_project_roots(config: Dict[str, Any], code: str) -> List[str]:
    preferred_root = get_preferred_project_root(config, code)
    raw_candidates = list((config.get("project_candidates") or {}).get(code) or [])
    candidate_roots = unique_project_roots([preferred_root, *raw_candidates])
    if not candidate_roots and preferred_root:
        candidate_roots = [preferred_root]
    return candidate_roots


def load_state(runtime_paths: Dict[str, Path]) -> Dict[str, Any]:
    data = load_json_file(
        runtime_paths["app_state"],
        {
            "version": STATE_VERSION,
            "running_tasks": {},
            "queued_tasks": {},
            "pending_status_updates": {},
            "last_due_scan_at": "",
        },
    )
    if not isinstance(data, dict):
        data = {
            "version": STATE_VERSION,
            "running_tasks": {},
            "queued_tasks": {},
            "pending_status_updates": {},
            "last_due_scan_at": "",
        }
    data["version"] = STATE_VERSION
    running_tasks = data.get("running_tasks")
    if not isinstance(running_tasks, dict):
        running_tasks = {}
    queued_tasks = data.get("queued_tasks")
    if not isinstance(queued_tasks, dict):
        queued_tasks = {}
    pending_status_updates = normalize_pending_status_updates(data.get("pending_status_updates"))
    last_due_scan_at = str(data.get("last_due_scan_at", "")).strip()
    data["running_tasks"] = running_tasks
    data["queued_tasks"] = queued_tasks
    data["pending_status_updates"] = pending_status_updates
    data["last_due_scan_at"] = last_due_scan_at
    return data


def save_state(runtime_paths: Dict[str, Path], state: Dict[str, Any]) -> None:
    cleaned = {
        "version": STATE_VERSION,
        "running_tasks": dict(state.get("running_tasks") or {}),
        "queued_tasks": dict(state.get("queued_tasks") or {}),
        "pending_status_updates": normalize_pending_status_updates(state.get("pending_status_updates")),
        "last_due_scan_at": str(state.get("last_due_scan_at", "")).strip(),
    }
    save_json_file(runtime_paths["app_state"], cleaned)


def mutate_state(runtime_paths: Dict[str, Path], mutator) -> Dict[str, Any]:
    with STATE_LOCK:
        state = load_state(runtime_paths)
        mutator(state)
        save_state(runtime_paths, state)
        return state


def get_running_task_codes(runtime_paths: Dict[str, Path]) -> Set[str]:
    with STATE_LOCK:
        state = load_state(runtime_paths)
        return set((state.get("running_tasks") or {}).keys())


def get_active_task_codes(runtime_paths: Dict[str, Path]) -> Set[str]:
    with STATE_LOCK:
        state = load_state(runtime_paths)
        return set((state.get("running_tasks") or {}).keys()) | set((state.get("queued_tasks") or {}).keys())


def get_queued_task_codes(runtime_paths: Dict[str, Path]) -> Set[str]:
    with STATE_LOCK:
        state = load_state(runtime_paths)
        return set((state.get("queued_tasks") or {}).keys())


def load_last_due_scan_at(runtime_paths: Dict[str, Path]) -> Optional[datetime]:
    with STATE_LOCK:
        state = load_state(runtime_paths)
        value = str(state.get("last_due_scan_at", "")).strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def save_last_due_scan_at(runtime_paths: Dict[str, Path], scanned_at: datetime) -> None:
    def _mutator(state: Dict[str, Any]) -> None:
        state["last_due_scan_at"] = scanned_at.strftime("%Y-%m-%d %H:%M:%S")

    mutate_state(runtime_paths, _mutator)


def get_running_project_roots(runtime_paths: Dict[str, Path]) -> Set[str]:
    with STATE_LOCK:
        state = load_state(runtime_paths)
        running_tasks = dict(state.get("running_tasks") or {})
    project_roots: Set[str] = set()
    for task_state in running_tasks.values():
        project_root = normalize_project_root(task_state.get("project_root"))
        if project_root:
            project_roots.add(project_root)
    return project_roots


def sync_lock_codes_from_state(runtime_paths: Dict[str, Path]) -> None:
    update_lock_codes(runtime_paths["tick_lock"], sorted(get_active_task_codes(runtime_paths)))


def set_running_task(
    runtime_paths: Dict[str, Path],
    task: RecordTask,
    project_root: Path,
    owner_pid: int,
    started_at: datetime,
    child_pid: Optional[int],
    phase: str,
    scheduled_run_time: Optional[str] = None,
) -> None:
    def _mutator(state: Dict[str, Any]) -> None:
        running_tasks = state.setdefault("running_tasks", {})
        running_tasks[task.task_id] = {
            "record_id": task.record_id,
            "code": task.code,
            "project_root": normalize_project_root(project_root),
            "started_at": started_at.strftime("%Y-%m-%d %H:%M:%S"),
            "owner_pid": owner_pid,
            "child_pid": child_pid,
            "phase": phase,
            "run_date": started_at.strftime("%Y-%m-%d"),
            "status": "running",
            "scheduled_run_time": scheduled_run_time or task.run_time,
        }

    mutate_state(runtime_paths, _mutator)


def remove_running_task(runtime_paths: Dict[str, Path], task_id: str) -> None:
    def _mutator(state: Dict[str, Any]) -> None:
        running_tasks = state.setdefault("running_tasks", {})
        running_tasks.pop(task_id, None)

    mutate_state(runtime_paths, _mutator)


def set_queued_task(
    runtime_paths: Dict[str, Path],
    task: RecordTask,
    preferred_project_root: str,
    candidate_project_roots: List[str],
    owner_pid: int,
    queued_at: datetime,
    *,
    scheduled_run_time: Optional[str] = None,
    admitted_at: Optional[datetime] = None,
    preserve_existing: bool = False,
) -> bool:
    created = False

    def _mutator(state: Dict[str, Any]) -> None:
        nonlocal created
        queued_tasks = state.setdefault("queued_tasks", {})
        existing = queued_tasks.get(task.task_id)
        if not isinstance(existing, dict):
            existing = {}
            created = True
        existing_candidates = existing.get("candidate_project_roots")
        if preserve_existing and isinstance(existing_candidates, list):
            candidate_roots = unique_project_roots(existing_candidates)
        else:
            candidate_roots = unique_project_roots(candidate_project_roots)
        preferred_root = (
            normalize_project_root(existing.get("preferred_project_root"))
            if preserve_existing and existing.get("preferred_project_root")
            else normalize_project_root(preferred_project_root)
        )
        queued_tasks[task.task_id] = {
            "record_id": task.record_id,
            "code": task.code,
            "preferred_project_root": preferred_root,
            "candidate_project_roots": candidate_roots,
            "queued_at": (
                str(existing.get("queued_at", "")).strip()
                if preserve_existing and existing.get("queued_at")
                else queued_at.strftime("%Y-%m-%d %H:%M:%S")
            ),
            "owner_pid": owner_pid,
            "run_date": (
                str(existing.get("run_date", "")).strip()
                if preserve_existing and existing.get("run_date")
                else queued_at.strftime("%Y-%m-%d")
            ),
            "status": "queued",
            "scheduled_run_time": (
                str(existing.get("scheduled_run_time", "")).strip()
                if preserve_existing and existing.get("scheduled_run_time")
                else (scheduled_run_time or task.run_time)
            ),
            "admitted_at": (
                str(existing.get("admitted_at", "")).strip()
                if preserve_existing and existing.get("admitted_at")
                else (admitted_at or queued_at).strftime("%Y-%m-%d %H:%M:%S")
            ),
        }

    mutate_state(runtime_paths, _mutator)
    return created


def remove_queued_task(runtime_paths: Dict[str, Path], task_id: str) -> None:
    def _mutator(state: Dict[str, Any]) -> None:
        queued_tasks = state.setdefault("queued_tasks", {})
        queued_tasks.pop(task_id, None)

    mutate_state(runtime_paths, _mutator)


def update_running_task_phase(
    runtime_paths: Dict[str, Path],
    task_id: str,
    *,
    child_pid: Optional[int] = None,
    phase: Optional[str] = None,
) -> None:
    def _mutator(state: Dict[str, Any]) -> None:
        task_state = (state.setdefault("running_tasks", {})).get(task_id)
        if not task_state:
            return
        if child_pid is not None:
            task_state["child_pid"] = child_pid
        if phase is not None:
            task_state["phase"] = phase

    mutate_state(runtime_paths, _mutator)


def set_running_task_status_sync(
    runtime_paths: Dict[str, Path],
    task_id: str,
    *,
    pending: bool,
    error_text: str = "",
    phase: Optional[str] = None,
) -> None:
    def _mutator(state: Dict[str, Any]) -> None:
        task_state = (state.setdefault("running_tasks", {})).get(task_id)
        if not task_state:
            return
        if pending:
            task_state["status_sync_pending"] = True
            task_state["last_status_sync_error"] = str(error_text or "").strip()[:1000]
            task_state["last_status_sync_phase"] = str(phase or "").strip().lower()
            return
        task_state.pop("status_sync_pending", None)
        task_state.pop("last_status_sync_error", None)
        task_state.pop("last_status_sync_phase", None)

    mutate_state(runtime_paths, _mutator)


def clear_pending_status_update(runtime_paths: Dict[str, Path], task_id: str) -> None:
    def _mutator(state: Dict[str, Any]) -> None:
        pending_updates = state.setdefault("pending_status_updates", {})
        pending_updates.pop(task_id, None)
        task_state = (state.setdefault("running_tasks", {})).get(task_id)
        if not task_state:
            return
        task_state.pop("status_sync_pending", None)
        task_state.pop("last_status_sync_error", None)
        task_state.pop("last_status_sync_phase", None)

    mutate_state(runtime_paths, _mutator)


def queue_pending_status_update(
    runtime_paths: Dict[str, Path],
    *,
    task_id: str,
    record_id: str,
    code: str,
    phase: str,
    fields_update: Dict[str, Any],
    error_text: str,
    retry_count: int = 1,
    next_retry_at: Optional[datetime] = None,
) -> None:
    normalized_phase = str(phase or "").strip().lower()
    if normalized_phase not in STATUS_SYNC_PHASES:
        raise ValueError(f"Unsupported status sync phase: {phase}")
    now = datetime.now()
    due_at = next_retry_at or (now + timedelta(seconds=STATUS_SYNC_RETRY_DELAY_SECONDS))

    def _mutator(state: Dict[str, Any]) -> None:
        pending_updates = state.setdefault("pending_status_updates", {})
        pending_updates[task_id] = {
            "task_id": task_id,
            "record_id": record_id,
            "code": str(code or "").strip().upper(),
            "phase": normalized_phase,
            "fields_update": dict(fields_update),
            "retry_count": max(0, int(retry_count)),
            "next_retry_at": due_at.strftime("%Y-%m-%d %H:%M:%S"),
            "last_error": str(error_text or "").strip()[:1000],
            "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        task_state = (state.setdefault("running_tasks", {})).get(task_id)
        if not task_state:
            return
        task_state["status_sync_pending"] = True
        task_state["last_status_sync_error"] = str(error_text or "").strip()[:1000]
        task_state["last_status_sync_phase"] = normalized_phase

    mutate_state(runtime_paths, _mutator)


def write_lock(lock_path: Path, payload: Dict[str, Any]) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    finally:
        os.close(fd)


def read_lock_payload(lock_path: Path) -> Optional[Dict[str, Any]]:
    if not lock_path.exists():
        return None
    raw_text = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not raw_text:
        return None
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        if raw_text.isdigit():
            return {"pid": int(raw_text), "legacy": True}
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def update_lock_codes(lock_path: Path, codes: List[str]) -> None:
    with STATE_LOCK:
        payload = read_lock_payload(lock_path)
        if not payload:
            return
        payload["codes"] = list(dict.fromkeys(codes))
        tmp_path = lock_path.with_suffix(lock_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        tmp_path.replace(lock_path)


def process_exists(pid: Any) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid_int}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=10,
        )
    except Exception:
        return False
    output = (result.stdout or "").strip()
    if not output or "没有运行的任务" in output or "No tasks are running" in output:
        return False
    return str(pid_int) in output


def log_line(runtime_paths: Dict[str, Path], message: str) -> None:
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    runtime_paths["runtime_dir"].mkdir(parents=True, exist_ok=True)
    with runtime_paths["runner_log"].open("a", encoding="utf-8") as file:
        file.write(f"[{now_text}] {message}\n")


def mark_task_interrupted(code: str, error_text: str) -> None:
    with STATE_LOCK:
        INTERRUPTED_TASK_ERRORS[code] = error_text


def get_interrupted_error(code: str) -> Optional[str]:
    with STATE_LOCK:
        return INTERRUPTED_TASK_ERRORS.get(code)


def clear_interrupted_error(code: str) -> None:
    with STATE_LOCK:
        INTERRUPTED_TASK_ERRORS.pop(code, None)


def terminate_process_tree(pid: Any) -> bool:
    if not process_exists(pid):
        return True
    try:
        subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=20,
        )
    except Exception:
        return False
    time.sleep(0.5)
    return not process_exists(pid)


class FeishuBitableClient:
    def __init__(self, app_id: str, app_secret: str) -> None:
        self.client = FeishuClient(app_id, app_secret)
        self._lock = threading.RLock()

    def get_wiki_node(self, wiki_ref: str) -> Dict[str, Any]:
        wiki_ref_text = str(wiki_ref or "").strip()
        if not wiki_ref_text:
            raise ValueError("wiki_url 不能为空")
        if "://" in wiki_ref_text or "/wiki/" in wiki_ref_text:
            wiki_token = parse_wiki_token(wiki_ref_text)
        else:
            wiki_token = wiki_ref_text
        with self._lock:
            return self.client.get_wiki_node(wiki_token)

    def request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        with self._lock:
            return self.client.request(method, path, **kwargs)

    def refresh_token(self) -> None:
        with self._lock:
            self.client.token = self.client._get_tenant_access_token()

    def paginate(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        item_key: str = "items",
    ) -> List[Dict[str, Any]]:
        with self._lock:
            return self.client.paginate(method, path, params=params, item_key=item_key)


def create_base(client: FeishuBitableClient, name: str, folder_token: Optional[str] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {"name": name}
    if folder_token:
        body["folder_token"] = folder_token
    return client.request("POST", "/bitable/v1/apps", body=body)


def list_tables(client: FeishuBitableClient, app_token: str) -> List[Dict[str, Any]]:
    return client.paginate("GET", f"/bitable/v1/apps/{app_token}/tables", params={}, item_key="items")


def create_table(client: FeishuBitableClient, app_token: str, name: str) -> Dict[str, Any]:
    return client.request("POST", f"/bitable/v1/apps/{app_token}/tables", body={"table": {"name": name}})


def list_fields(client: FeishuBitableClient, app_token: str, table_id: str) -> List[Dict[str, Any]]:
    return client.paginate("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", params={}, item_key="items")


def create_field(client: FeishuBitableClient, app_token: str, table_id: str, field_name: str, field_type: int) -> Dict[str, Any]:
    payload = {"field_name": field_name, "type": field_type}
    return client.request("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", body=payload)


def list_records(client: FeishuBitableClient, app_token: str, table_id: str) -> List[Dict[str, Any]]:
    return client.paginate("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records", params={}, item_key="items")


def create_record(client: FeishuBitableClient, app_token: str, table_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    return client.request("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records", body={"fields": fields})


def update_record(client: FeishuBitableClient, app_token: str, table_id: str, record_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    return client.request("PUT", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}", body={"fields": fields})


def get_feishu_bitable_client(config: Dict[str, Any]) -> FeishuBitableClient:
    app_id = str(config.get("app_id", "")).strip()
    app_secret = str(config.get("app_secret", "")).strip()
    cache_key = (app_id, app_secret)
    with FEISHU_CLIENT_CACHE_LOCK:
        client = FEISHU_CLIENT_CACHE.get(cache_key)
        if client is None:
            client = FeishuBitableClient(app_id, app_secret)
            FEISHU_CLIENT_CACHE[cache_key] = client
        return client


def field_type_for_name(logical_name: str) -> int:
    if logical_name == "enabled":
        return FIELD_TYPE_CHECKBOX
    return FIELD_TYPE_TEXT


def required_schedule_field_names(fields: Dict[str, str]) -> Dict[str, str]:
    required_keys = (
        "code",
        "enabled",
        "run_time",
        "status",
        "last_run_date",
        "last_start_time",
        "last_end_time",
        "last_result",
        "error_message",
    )
    return {key: fields[key] for key in required_keys if fields.get(key)}


def normalize_wiki_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    normalized_path = parsed.path.rstrip("/") or parsed.path
    normalized = parsed._replace(query="", fragment="", path=normalized_path)
    return urlunparse(normalized)


def build_table_meta_source(config: Dict[str, Any]) -> Dict[str, str]:
    return {
        "wiki_url": normalize_wiki_url(config.get("wiki_url")),
        "table_name": str(config.get("table_name", "")).strip(),
        "app_token": str(config.get("app_token", "")).strip(),
        "table_id": str(config.get("table_id", "")).strip(),
    }


def resolve_existing_table_id(
    client: FeishuBitableClient,
    app_token: str,
    preferred_table_name: str,
) -> str:
    tables = list_tables(client, app_token)
    preferred_name = str(preferred_table_name or "").strip()
    if preferred_name:
        for item in tables:
            if str(item.get("name", "")).strip() == preferred_name and item.get("table_id"):
                return str(item["table_id"])
    if tables:
        first_table_id = str((tables[0] or {}).get("table_id", "")).strip()
        if first_table_id:
            return first_table_id
    if preferred_name:
        raise ScheduleConfigError(
            f"在现有飞书多维表 app_token={app_token} 中未找到工作表 {preferred_table_name}"
        )
    raise ScheduleConfigError(f"现有飞书多维表 app_token={app_token} 中没有可用工作表")


def resolve_schedule_table_meta(config: Dict[str, Any]) -> Dict[str, str]:
    client = get_feishu_bitable_client(config)
    explicit_app_token = parse_table_text(config.get("app_token"))
    explicit_table_id = parse_table_text(config.get("table_id"))
    preferred_table_name = str(config.get("table_name", "")).strip()

    if explicit_app_token:
        return {
            "app_token": explicit_app_token,
            "table_id": explicit_table_id or resolve_existing_table_id(client, explicit_app_token, preferred_table_name),
        }

    wiki_url = normalize_wiki_url(config.get("wiki_url"))
    if wiki_url:
        try:
            node = client.get_wiki_node(wiki_url)
        except Exception as exc:
            raise ScheduleConfigError(f"无法解析现有飞书调度文档：{wiki_url}，{exc}") from exc
        obj_type = str((node or {}).get("obj_type", "")).strip().lower()
        obj_token = parse_table_text((node or {}).get("obj_token"))
        if obj_type == "bitable" and obj_token:
            return {
                "app_token": obj_token,
                "table_id": explicit_table_id or resolve_existing_table_id(client, obj_token, preferred_table_name),
            }
        raise ScheduleConfigError(f"飞书链接未指向可用的多维表：{wiki_url}")

    raise ScheduleConfigError("未配置现有飞书调度文档，请提供 wiki_url 或 app_token/table_id")


def ensure_schedule_table(config: Dict[str, Any], runtime_paths: Dict[str, Path]) -> Dict[str, Any]:
    cached = load_json_file(runtime_paths["table_meta"], {})
    source = build_table_meta_source(config)
    client = get_feishu_bitable_client(config)
    if cached.get("app_token") and cached.get("table_id") and cached.get("source") == source:
        app_token = str(cached.get("app_token", "")).strip()
        table_id = str(cached.get("table_id", "")).strip()
    else:
        resolved_meta = resolve_schedule_table_meta(config)
        app_token = resolved_meta["app_token"]
        table_id = resolved_meta["table_id"]

    fields = config["fields"]
    required_keys = (
        "code",
        "enabled",
        "run_time",
        "status",
        "last_run_date",
        "last_start_time",
        "last_end_time",
        "last_result",
        "error_message",
    )
    missing_config_keys = [key for key in required_keys if not fields.get(key)]
    if missing_config_keys:
        raise ScheduleConfigError(
            f"调度配置缺少必需字段映射: {', '.join(missing_config_keys)}"
        )
    required_fields = required_schedule_field_names(fields)
    existing_fields = {item.get("field_name"): item for item in list_fields(client, app_token, table_id)}
    missing_table_fields = [field_name for field_name in required_fields.values() if field_name not in existing_fields]
    if missing_table_fields:
        raise ScheduleConfigError(
            f"现有飞书调度表缺少必需字段: {', '.join(missing_table_fields)}"
        )

    meta = {"app_token": app_token, "table_id": table_id, "source": source}
    save_json_file(runtime_paths["table_meta"], meta)
    return meta


def truthy_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "是", "启用"}


def parse_run_time(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = normalize_time_text(str(value).strip())
    if not text:
        return None
    try:
        return datetime.strptime(text, "%H:%M").strftime("%H:%M")
    except ValueError:
        return None


def parse_table_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        for item in value:
            text = parse_table_text(item)
            if text:
                return text
        return ""
    if isinstance(value, dict):
        for key in ("text", "name", "link", "url", "value"):
            text = parse_table_text(value.get(key))
            if text:
                return text
        return ""
    return str(value).strip()


def parse_schedule_datetime_text(value: Any, *, default_time: str = "10:00") -> Optional[str]:
    text = parse_table_text(value)
    if not text:
        return None
    normalized = (
        text.replace("/", "-")
        .replace("T", " ")
        .replace("：", ":")
        .replace("上午", " ")
        .replace("下午", " ")
    ).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(normalized, fmt)
            return parsed.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    try:
        parsed = datetime.strptime(normalized, "%Y-%m-%d")
        hour_text, minute_text = default_time.split(":", 1)
        parsed = parsed.replace(hour=int(hour_text), minute=int(minute_text))
        return parsed.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


def normalize_time_text(text: str) -> str:
    translation = str.maketrans("０１２３４５６７８９：", "0123456789:")
    return text.translate(translation)


def build_record_task(
    config: Dict[str, Any],
    fields: Dict[str, str],
    item: Dict[str, Any],
) -> Optional[RecordTask]:
    item_fields = dict(item.get("fields") or {})
    code = parse_table_text(item_fields.get(fields["code"])).upper()
    if not code or code not in config["projects"]:
        return None
    run_time = parse_run_time(item_fields.get(fields["run_time"]))
    if not run_time:
        return None
    return RecordTask(
        record_id=item["record_id"],
        code=code,
        run_time=run_time,
        fields=item_fields,
    )


def scheduled_datetime_for_day(day: datetime, run_time: str) -> datetime:
    parsed = datetime.strptime(run_time, "%H:%M")
    return day.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)


def task_sort_key(task: RecordTask) -> Tuple[str, str, str]:
    return (task.run_time, task.code, task.record_id)


def normalize_time_text(text: str) -> str:
    translation = {
        ord("０"): "0",
        ord("１"): "1",
        ord("２"): "2",
        ord("３"): "3",
        ord("４"): "4",
        ord("５"): "5",
        ord("６"): "6",
        ord("７"): "7",
        ord("８"): "8",
        ord("９"): "9",
        ord("："): ":",
        ord("﹕"): ":",
        ord("∶"): ":",
        ord("꞉"): ":",
    }
    return text.translate(translation)


def apply_remote_task_status_update(
    config: Dict[str, Any],
    meta: Dict[str, Any],
    record_id: str,
    fields_update: Dict[str, Any],
) -> None:
    client = get_feishu_bitable_client(config)
    try:
        update_record(client, meta["app_token"], meta["table_id"], record_id, dict(fields_update))
    except Exception as exc:
        if not is_invalid_access_token_error(exc):
            raise
        client.refresh_token()
        update_record(client, meta["app_token"], meta["table_id"], record_id, dict(fields_update))


def update_task_status(
    config: Dict[str, Any],
    meta: Dict[str, Any],
    record_id: str,
    fields_update: Dict[str, Any],
    *,
    required: bool = False,
    runtime_paths: Optional[Dict[str, Path]] = None,
    task_id: Optional[str] = None,
    code: Optional[str] = None,
    phase: Optional[str] = None,
) -> bool:
    try:
        apply_remote_task_status_update(config, meta, record_id, fields_update)
    except Exception as exc:
        if required:
            raise
        if not runtime_paths or not task_id or not code or not phase:
            raise
        error_text = str(exc).strip() or exc.__class__.__name__
        queue_pending_status_update(
            runtime_paths,
            task_id=task_id,
            record_id=record_id,
            code=code,
            phase=phase,
            fields_update=dict(fields_update),
            error_text=error_text,
        )
        log_line(runtime_paths, f"{code} 飞书状态更新失败，已加入补写队列: phase={phase} error={error_text}")
        return False
    if runtime_paths and task_id:
        clear_pending_status_update(runtime_paths, task_id)
    return True


def flush_pending_status_updates(config: Dict[str, Any], meta: Dict[str, Any], runtime_paths: Dict[str, Path]) -> None:
    with STATE_LOCK:
        state = load_state(runtime_paths)
        pending_updates = dict(state.get("pending_status_updates") or {})
    if not pending_updates:
        return

    now = datetime.now()
    due_updates: List[Tuple[datetime, str, Dict[str, Any]]] = []
    for task_id, item in pending_updates.items():
        retry_at = parse_state_datetime(item.get("next_retry_at")) or now
        if retry_at > now:
            continue
        due_updates.append((retry_at, task_id, item))

    for _, task_id, item in sorted(due_updates, key=lambda value: (value[0], value[2].get("code", ""), value[1])):
        code = str(item.get("code", "UNKNOWN")).strip().upper() or "UNKNOWN"
        phase = str(item.get("phase", "")).strip().lower()
        try:
            apply_remote_task_status_update(config, meta, str(item.get("record_id", "")).strip(), dict(item.get("fields_update") or {}))
        except Exception as exc:
            error_text = str(exc).strip() or exc.__class__.__name__
            retry_count = int(item.get("retry_count", 0)) + 1
            next_retry_at = datetime.now() + timedelta(seconds=STATUS_SYNC_RETRY_DELAY_SECONDS)
            queue_pending_status_update(
                runtime_paths,
                task_id=task_id,
                record_id=str(item.get("record_id", "")).strip(),
                code=code,
                phase=phase,
                fields_update=dict(item.get("fields_update") or {}),
                error_text=error_text,
                retry_count=retry_count,
                next_retry_at=next_retry_at,
            )
            log_line(runtime_paths, f"{code} 飞书状态补写失败，将于 {STATUS_SYNC_RETRY_DELAY_SECONDS} 秒后重试: phase={phase} error={error_text}")
            continue
        clear_pending_status_update(runtime_paths, task_id)
        log_line(runtime_paths, f"{code} 飞书状态补写成功: phase={phase}")


def build_compensation_fields(
    fields: Dict[str, str],
    end_time: datetime,
    error_text: str,
    *,
    start_time_text: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        fields["status"]: STATUS_FAILED,
        fields["last_end_time"]: end_time.strftime("%Y-%m-%d %H:%M:%S"),
        fields["last_result"]: COMPENSATED_RESULT_TEXT,
        fields["error_message"]: error_text,
    }
    if start_time_text:
        payload[fields["last_start_time"]] = start_time_text
    return payload


def build_idle_status_fields(fields: Dict[str, str]) -> Dict[str, Any]:
    return {fields["status"]: STATUS_IDLE}


def fetch_due_tasks(
    config: Dict[str, Any],
    meta: Dict[str, Any],
    runtime_paths: Dict[str, Path],
    *,
    window_start: datetime,
    window_end: datetime,
) -> List[RecordTask]:
    client = get_feishu_bitable_client(config)
    records = list_records(client, meta["app_token"], meta["table_id"])
    fields = config["fields"]
    today = window_end.strftime("%Y-%m-%d")
    active_task_ids = get_active_task_codes(runtime_paths)
    tasks: List[RecordTask] = []

    for item in records:
        task = build_record_task(config, fields, item)
        if not task:
            continue
        if task.task_id in active_task_ids:
            continue
        item_fields = item.get("fields", {})
        if not truthy_enabled(item_fields.get(fields["enabled"])):
            continue
        scheduled_at = scheduled_datetime_for_day(window_end, task.run_time)
        if scheduled_at <= window_start or scheduled_at > window_end:
            continue
        last_run_date = str(item_fields.get(fields["last_run_date"], "")).strip()
        last_result = str(item_fields.get(fields["last_result"], "")).strip()
        if last_run_date == today and last_result == RESULT_SUCCESS:
            continue
        tasks.append(task)
    tasks.sort(key=task_sort_key)
    return tasks


def compensate_running_task(
    config: Dict[str, Any],
    meta: Dict[str, Any],
    runtime_paths: Dict[str, Path],
    task_state: Dict[str, Any],
    error_text: str,
) -> None:
    fields = config["fields"]
    end_time = datetime.now()
    record_id = str(task_state.get("record_id", "")).strip()
    if not record_id:
        return
    start_time_text = str(task_state.get("started_at", "")).strip() or None
    try:
        update_task_status(
            config,
            meta,
            record_id,
            build_compensation_fields(fields, end_time, error_text, start_time_text=start_time_text),
        )
    except Exception as exc:
        log_line(runtime_paths, f"{task_state.get('code', 'UNKNOWN')} 补偿状态回写失败: {exc}")
        raise


def restore_queued_task_to_idle(
    config: Dict[str, Any],
    meta: Dict[str, Any],
    runtime_paths: Dict[str, Path],
    task_state: Dict[str, Any],
) -> None:
    fields = config["fields"]
    record_id = str(task_state.get("record_id", "")).strip()
    if not record_id:
        return
    try:
        update_task_status(config, meta, record_id, build_idle_status_fields(fields))
    except Exception as exc:
        log_line(runtime_paths, f"{task_state.get('code', 'UNKNOWN')} 等待状态恢复失败: {exc}")
        raise


def load_record_field_map(
    config: Dict[str, Any],
    meta: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    client = get_feishu_bitable_client(config)
    records = list_records(client, meta["app_token"], meta["table_id"])
    return {
        str(item.get("record_id", "")).strip(): dict(item.get("fields") or {})
        for item in records
        if str(item.get("record_id", "")).strip()
    }


def should_keep_queued_task(
    fields: Dict[str, str],
    task_state: Dict[str, Any],
    record_fields: Dict[str, Any],
    today_text: str,
) -> bool:
    run_date = str(task_state.get("run_date", "")).strip()
    if run_date != today_text:
        return False
    last_run_date = str(record_fields.get(fields["last_run_date"], "")).strip()
    last_result = str(record_fields.get(fields["last_result"], "")).strip()
    return not (last_run_date == today_text and last_result == RESULT_SUCCESS)


def recover_running_tasks(config: Dict[str, Any], meta: Dict[str, Any], runtime_paths: Dict[str, Path]) -> None:
    with STATE_LOCK:
        state = load_state(runtime_paths)
        running_tasks = dict(state.get("running_tasks") or {})
        queued_tasks = dict(state.get("queued_tasks") or {})
    if not running_tasks and not queued_tasks:
        return

    changed = False
    for code, task_state in list(running_tasks.items()):
        owner_pid = task_state.get("owner_pid")
        child_pid = task_state.get("child_pid")
        owner_alive = process_exists(owner_pid)
        child_alive = process_exists(child_pid)

        if owner_alive:
            continue

        if child_alive:
            log_line(runtime_paths, f"{code} 检测到孤儿子进程仍在运行，暂不重跑")
            continue

        log_line(runtime_paths, f"{code} 检测到残留运行记录，回写为已中断")
        compensate_running_task(config, meta, runtime_paths, task_state, COMPENSATED_ERROR_TEXT)
        running_tasks.pop(code, None)
        changed = True

    for code, task_state in list(queued_tasks.items()):
        owner_pid = task_state.get("owner_pid")
        if process_exists(owner_pid):
            continue
        log_line(runtime_paths, f"{code} 检测到残留等待记录，恢复为空闲")
        restore_queued_task_to_idle(config, meta, runtime_paths, task_state)
        queued_tasks.pop(code, None)
        changed = True

    if changed:
        with STATE_LOCK:
            latest_state = load_state(runtime_paths)
            latest_state["running_tasks"] = running_tasks
            latest_state["queued_tasks"] = queued_tasks
            save_state(runtime_paths, latest_state)


def recover_running_tasks(config: Dict[str, Any], meta: Dict[str, Any], runtime_paths: Dict[str, Path]) -> None:
    with STATE_LOCK:
        state = load_state(runtime_paths)
        running_tasks = dict(state.get("running_tasks") or {})
        queued_tasks = dict(state.get("queued_tasks") or {})
    if not running_tasks and not queued_tasks:
        return

    changed = False
    fields = config["fields"]
    today_text = datetime.now().strftime("%Y-%m-%d")

    for code, task_state in list(running_tasks.items()):
        owner_pid = task_state.get("owner_pid")
        child_pid = task_state.get("child_pid")
        owner_alive = process_exists(owner_pid)
        child_alive = process_exists(child_pid)

        if owner_alive:
            continue

        if child_alive:
            log_line(runtime_paths, f"{code} 妫€娴嬪埌瀛ゅ効瀛愯繘绋嬩粛鍦ㄨ繍琛岋紝鏆備笉閲嶈窇")
            continue

        log_line(runtime_paths, f"{code} 妫€娴嬪埌娈嬬暀杩愯璁板綍锛屽洖鍐欎负宸蹭腑鏂?")
        compensate_running_task(config, meta, runtime_paths, task_state, COMPENSATED_ERROR_TEXT)
        running_tasks.pop(code, None)
        changed = True

    queued_record_map: Optional[Dict[str, Dict[str, Any]]] = None
    for code, task_state in list(queued_tasks.items()):
        owner_pid = task_state.get("owner_pid")
        if process_exists(owner_pid):
            continue
        if queued_record_map is None:
            queued_record_map = load_record_field_map(config, meta)
        record_id = str(task_state.get("record_id", "")).strip()
        record_fields = queued_record_map.get(record_id, {})
        if should_keep_queued_task(fields, task_state, record_fields, today_text):
            log_line(runtime_paths, f"{code} 妫€娴嬪埌鍚屾棩娈嬬暀绛夊緟璁板綍锛屼繚鐣欏緟涓嬩竴娆?tick 缁х画鎺掗槦")
            continue
        log_line(runtime_paths, f"{code} 妫€娴嬪埌娈嬬暀绛夊緟璁板綍锛屾仮澶嶄负绌洪棽")
        restore_queued_task_to_idle(config, meta, runtime_paths, task_state)
        queued_tasks.pop(code, None)
        changed = True

    if changed:
        with STATE_LOCK:
            latest_state = load_state(runtime_paths)
            latest_state["running_tasks"] = running_tasks
            latest_state["queued_tasks"] = queued_tasks
            save_state(runtime_paths, latest_state)


def cleanup_stale_lock(config: Dict[str, Any], meta: Dict[str, Any], runtime_paths: Dict[str, Path]) -> None:
    lock_path = runtime_paths["tick_lock"]
    if not lock_path.exists():
        return

    payload = read_lock_payload(lock_path)
    pid = payload.get("pid") if payload else None
    if payload and process_exists(pid):
        return

    reason = "检测到旧格式锁文件，准备恢复" if payload and payload.get("legacy") else "检测到陈旧锁文件，准备恢复"
    if not payload:
        reason = "检测到损坏锁文件，准备恢复"
    log_line(runtime_paths, reason)
    recover_running_tasks(config, meta, runtime_paths)
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass
    log_line(runtime_paths, "陈旧锁文件已清理")


def acquire_lock(runtime_paths: Dict[str, Path]) -> Tuple[bool, Dict[str, Any]]:
    lock_payload = {
        "pid": os.getpid(),
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "tick",
        "codes": [],
    }
    try:
        write_lock(runtime_paths["tick_lock"], lock_payload)
    except FileExistsError:
        existing = read_lock_payload(runtime_paths["tick_lock"]) or {}
        return False, existing
    return True, lock_payload


def release_lock(runtime_paths: Dict[str, Path]) -> None:
    lock_path = runtime_paths["tick_lock"]
    try:
        if lock_path.exists():
            lock_path.unlink()
    except FileNotFoundError:
        pass


def register_context(context: Optional[SchedulerRuntimeContext]) -> None:
    global ACTIVE_CONTEXT
    with STATE_LOCK:
        ACTIVE_CONTEXT = context


def compensate_active_tasks_for_signal(signal_name: str) -> None:
    with STATE_LOCK:
        context = ACTIVE_CONTEXT
    if not context:
        return

    config = context.config
    meta = context.meta
    runtime_paths = context.runtime_paths
    with STATE_LOCK:
        state = load_state(runtime_paths)
        running_tasks = dict(state.get("running_tasks") or {})
        queued_tasks = dict(state.get("queued_tasks") or {})

    owner_pid = int(context.lock_payload.get("pid") or os.getpid())
    for code, task_state in running_tasks.items():
        if int(task_state.get("owner_pid") or 0) != owner_pid:
            continue
        mark_task_interrupted(code, SIGNAL_STOP_ERROR_TEXT)
        child_pid = task_state.get("child_pid")
        if child_pid and not terminate_process_tree(child_pid):
            log_line(runtime_paths, f"{code} 收到 {signal_name} 后未能终止子进程 {child_pid}，保留运行记录")
            continue
        try:
            compensate_running_task(config, meta, runtime_paths, task_state, SIGNAL_STOP_ERROR_TEXT)
            log_line(runtime_paths, f"{code} 收到 {signal_name} 后已回写中断状态")
            remove_running_task(runtime_paths, code)
        except Exception:
            continue

    for code, task_state in queued_tasks.items():
        if int(task_state.get("owner_pid") or 0) != owner_pid:
            continue
        try:
            restore_queued_task_to_idle(config, meta, runtime_paths, task_state)
            log_line(runtime_paths, f"{code} 收到 {signal_name} 后已恢复为空闲")
            remove_queued_task(runtime_paths, code)
        except Exception:
            continue

    release_lock(runtime_paths)


def install_signal_handlers() -> None:
    def _handler(signum, _frame) -> None:
        signal_name = getattr(signal.Signals(signum), "name", str(signum))
        compensate_active_tasks_for_signal(signal_name)
        raise KeyboardInterrupt()

    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handler)


def run_single_code(
    config: Dict[str, Any],
    meta: Dict[str, Any],
    runtime_paths: Dict[str, Path],
    task: RecordTask,
    project_root_value: str,
) -> Tuple[str, bool, str]:
    fields = config["fields"]
    project_root_text = normalize_project_root(project_root_value)
    if not project_root_text:
        raise ScheduleConfigError(f"{task.code} 缺少可执行项目目录")
    project_root = Path(project_root_text)
    python_exe = sys.executable
    start_time = datetime.now()
    child_process: Optional[subprocess.Popen[str]] = None
    sync_process: Optional[subprocess.Popen[str]] = None

    try:
        remove_queued_task(runtime_paths, task.task_id)
        update_task_status(
            config,
            meta,
            task.record_id,
            {
                fields["status"]: STATUS_RUNNING,
                fields["last_start_time"]: start_time.strftime("%Y-%m-%d %H:%M:%S"),
                fields["last_end_time"]: "",
                fields["last_result"]: "",
                fields["error_message"]: "",
            },
        )

        set_running_task(
            runtime_paths,
            task,
            project_root,
            os.getpid(),
            start_time,
            child_pid=None,
            phase="main_controller",
            scheduled_run_time=task.run_time,
        )

        interrupted_error = get_interrupted_error(task.task_id)
        if interrupted_error:
            end_time = datetime.now()
            update_task_status(
                config,
                meta,
                task.record_id,
                build_compensation_fields(
                    fields,
                    end_time,
                    interrupted_error,
                    start_time_text=start_time.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            return task.code, False, interrupted_error

        command = [
            python_exe,
            str(project_root / "src" / "main_controller.py"),
            "--picks",
            task.code,
        ]
        child_process = subprocess.Popen(
            command,
            cwd=str(project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        update_running_task_phase(runtime_paths, task.task_id, child_pid=child_process.pid, phase="main_controller")
        stdout_text, stderr_text = child_process.communicate()

        sync_stdout = ""
        sync_stderr = ""
        sync_returncode = 0
        if child_process.returncode == 0:
            interrupted_error = get_interrupted_error(task.task_id)
            if interrupted_error:
                end_time = datetime.now()
                update_task_status(
                    config,
                    meta,
                    task.record_id,
                    build_compensation_fields(
                        fields,
                        end_time,
                        interrupted_error,
                        start_time_text=start_time.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
                return task.code, False, interrupted_error
            sync_command = [python_exe, str(Path(__file__).resolve().with_name("feishu_sync.py"))]
            sync_process = subprocess.Popen(
                sync_command,
                cwd=str(Path(__file__).resolve().parents[1]),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            update_running_task_phase(runtime_paths, task.task_id, child_pid=sync_process.pid, phase="feishu_sync")
            sync_stdout, sync_stderr = sync_process.communicate()
            sync_returncode = sync_process.returncode

        end_time = datetime.now()
        success = child_process.returncode == 0 and sync_returncode == 0
        error_text = ""
        if child_process.returncode != 0:
            error_text = (stderr_text or stdout_text or f"main_controller exit={child_process.returncode}").strip()
        elif sync_returncode != 0:
            error_text = (sync_stderr or sync_stdout or f"feishu_sync exit={sync_returncode}").strip()
        elif success:
            artifact_ok, artifact_error = verify_task_output_artifacts(project_root, task.code, start_time)
            if not artifact_ok:
                success = False
                error_text = artifact_error
                log_line(runtime_paths, f"{task.code} 执行产物校验失败: {artifact_error}")
        error_text = error_text[:1000]

        interrupted_error = get_interrupted_error(task.task_id)
        if interrupted_error:
            update_task_status(
                config,
                meta,
                task.record_id,
                build_compensation_fields(
                    fields,
                    end_time,
                    interrupted_error,
                    start_time_text=start_time.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            return task.code, False, interrupted_error

        update_task_status(
            config,
            meta,
            task.record_id,
            {
                fields["status"]: STATUS_IDLE if success else STATUS_FAILED,
                fields["last_run_date"]: end_time.strftime("%Y-%m-%d"),
                fields["last_start_time"]: start_time.strftime("%Y-%m-%d %H:%M:%S"),
                fields["last_end_time"]: end_time.strftime("%Y-%m-%d %H:%M:%S"),
                fields["last_result"]: RESULT_SUCCESS if success else RESULT_FAILED,
                fields["error_message"]: error_text,
            },
        )
        return task.code, success, error_text
    except Exception as exc:
        end_time = datetime.now()
        error_text = get_interrupted_error(task.task_id) or str(exc).strip() or exc.__class__.__name__
        error_text = error_text[:1000]
        try:
            if get_interrupted_error(task.task_id):
                update_task_status(
                    config,
                    meta,
                    task.record_id,
                    build_compensation_fields(
                        fields,
                        end_time,
                        error_text,
                        start_time_text=start_time.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
            else:
                update_task_status(
                    config,
                    meta,
                    task.record_id,
                    {
                        fields["status"]: STATUS_FAILED,
                        fields["last_run_date"]: end_time.strftime("%Y-%m-%d"),
                        fields["last_start_time"]: start_time.strftime("%Y-%m-%d %H:%M:%S"),
                        fields["last_end_time"]: end_time.strftime("%Y-%m-%d %H:%M:%S"),
                        fields["last_result"]: RESULT_FAILED,
                        fields["error_message"]: error_text,
                    },
                )
        except Exception as status_exc:
            log_line(runtime_paths, f"{task.code} 启动阶段状态回写失败: {status_exc}")
        return task.code, False, error_text
    finally:
        remove_running_task(runtime_paths, task.task_id)
        clear_interrupted_error(task.task_id)


def run_tick(config_path: Optional[str] = None) -> int:
    config = load_schedule_config(config_path)
    runtime_paths = ensure_runtime_paths(config)
    validate_project_support_consistency(config, runtime_paths)
    meta = ensure_schedule_table(config, runtime_paths)

    cleanup_stale_lock(config, meta, runtime_paths)
    recover_running_tasks(config, meta, runtime_paths)

    acquired, lock_payload = acquire_lock(runtime_paths)
    if not acquired:
        log_line(runtime_paths, "上一次 tick 仍在运行，跳过本次执行")
        return 0

    context = SchedulerRuntimeContext(
        config=config,
        meta=meta,
        runtime_paths=runtime_paths,
        lock_payload=lock_payload,
    )
    register_context(context)

    try:
        tasks = fetch_due_tasks(config, meta, runtime_paths)
        if not tasks:
            log_line(runtime_paths, "没有到点且待执行的代码")
            return 0

        max_concurrency = max(1, int(config.get("max_concurrency", 2)))
        log_line(runtime_paths, f"本轮准备执行 {len(tasks)} 个代码，并发上限 {max_concurrency}")
        owner_pid = int(lock_payload.get("pid") or os.getpid())
        blocked_projects = get_running_project_roots(runtime_paths)
        project_queues: Dict[str, List[RecordTask]] = {}
        project_order: List[str] = []

        for task in tasks:
            project_root = normalize_project_root(config["projects"][task.code])
            if project_root in blocked_projects:
                log_line(runtime_paths, f"{task.code} 所属项目仍被残留运行任务占用，本轮暂不调度")
                continue
            if project_root not in project_queues:
                project_queues[project_root] = []
                project_order.append(project_root)
            project_queues[project_root].append(task)

        if not project_queues:
            log_line(runtime_paths, "当前到点任务所属项目均被占用，本轮暂不调度")
            return 0

        for project_root, project_tasks in project_queues.items():
            if len(project_tasks) <= 1:
                continue
            for queued_task in project_tasks[1:]:
                update_task_status(
                    config,
                    meta,
                    queued_task.record_id,
                    {config["fields"]["status"]: STATUS_QUEUED},
                )
                set_queued_task(
                    runtime_paths,
                    queued_task,
                    Path(project_root),
                    owner_pid,
                    datetime.now(),
                )
                log_line(runtime_paths, f"{queued_task.code} 已进入等待队列")

        running: Dict[Any, Tuple[str, RecordTask]] = {}
        running_projects: Set[str] = set()

        with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
            while any(project_queues.get(project_root) for project_root in project_order) or running:
                started_this_round = False
                while len(running) < max_concurrency:
                    next_project_root = None
                    for project_root in project_order:
                        if project_root in running_projects:
                            continue
                        if project_queues.get(project_root):
                            next_project_root = project_root
                            break
                    if not next_project_root:
                        break

                    task = project_queues[next_project_root].pop(0)
                    future = executor.submit(run_single_code, config, meta, runtime_paths, task)
                    running[future] = (next_project_root, task)
                    running_projects.add(next_project_root)
                    sync_lock_codes_from_state(runtime_paths)
                    log_line(runtime_paths, f"开始执行 {task.code}")
                    started_this_round = True

                if not running:
                    if not started_this_round:
                        break
                    continue

                done, _ = wait(list(running.keys()), return_when=FIRST_COMPLETED)
                for future in done:
                    project_root, task = running.pop(future)
                    running_projects.discard(project_root)
                    try:
                        code, success, error_text = future.result()
                        if success:
                            log_line(runtime_paths, f"{code} 执行成功并已同步飞书文档")
                        else:
                            log_line(runtime_paths, f"{code} 执行失败: {error_text}")
                    except Exception as exc:
                        log_line(runtime_paths, f"{task.code} 执行异常: {exc}")
                    sync_lock_codes_from_state(runtime_paths)
        return 0
    finally:
        register_context(None)
        release_lock(runtime_paths)


def load_resumable_queued_tasks(
    config: Dict[str, Any],
    meta: Dict[str, Any],
    runtime_paths: Dict[str, Path],
    owner_pid: int,
    today_text: str,
) -> List[Tuple[str, RecordTask]]:
    with STATE_LOCK:
        state = load_state(runtime_paths)
        queued_tasks = dict(state.get("queued_tasks") or {})

    fields = config["fields"]
    record_field_map: Optional[Dict[str, Dict[str, Any]]] = None
    resumable: List[Tuple[str, RecordTask]] = []
    adopted_at = datetime.now()
    stale_task_ids: List[str] = []
    for task_id, task_state in queued_tasks.items():
        task_code = str(task_state.get("code", "")).strip().upper()
        if not task_code or task_code not in config["projects"]:
            continue
        if str(task_state.get("run_date", "")).strip() != today_text:
            continue
        record_id = str(task_state.get("record_id", "")).strip()
        run_time = parse_run_time(task_state.get("scheduled_run_time"))
        if not run_time and record_id:
            if record_field_map is None:
                record_field_map = load_record_field_map(config, meta)
            record_fields = record_field_map.get(record_id, {})
            run_time = parse_run_time(record_fields.get(fields["run_time"]))
        if not record_id or not run_time:
            stale_task_ids.append(task_id)
            if record_id:
                try:
                    restore_queued_task_to_idle(config, meta, runtime_paths, task_state)
                except Exception as exc:
                    log_line(runtime_paths, f"{task_code or task_id} 等待队列任务恢复失败: {exc}")
                else:
                    log_line(runtime_paths, f"{task_code or task_id} 缺少必要字段，已从等待状态恢复为空闲")
            continue
        preferred_root = normalize_project_root(task_state.get("preferred_project_root")) or get_preferred_project_root(config, task_code)
        candidate_roots = unique_project_roots(task_state.get("candidate_project_roots") or get_candidate_project_roots(config, task_code))
        if not candidate_roots and preferred_root:
            candidate_roots = [preferred_root]
        task = RecordTask(
            record_id=record_id,
            code=task_code,
            run_time=run_time,
            fields={},
        )
        resumable.append((preferred_root, task))
        set_queued_task(
            runtime_paths,
            task,
            preferred_root,
            candidate_roots,
            owner_pid,
            adopted_at,
            scheduled_run_time=run_time,
            admitted_at=adopted_at,
            preserve_existing=True,
        )

    if stale_task_ids:
        def _mutator(state: Dict[str, Any]) -> None:
            queued_state = state.setdefault("queued_tasks", {})
            for stale_task_id in stale_task_ids:
                queued_state.pop(stale_task_id, None)

        mutate_state(runtime_paths, _mutator)

    resumable.sort(key=lambda item: task_sort_key(item[1]))
    return resumable


def compute_startable_codes(
    project_queues: Dict[str, List[RecordTask]],
    project_order: List[str],
    occupied_projects: Set[str],
    running_count: int,
    max_concurrency: int,
) -> Set[str]:
    startable_codes: Set[str] = set()
    available_slots = max(0, max_concurrency - running_count)
    if available_slots <= 0:
        return startable_codes

    reserved_projects: Set[str] = set()
    for project_root in project_order:
        if available_slots <= 0:
            break
        if project_root in occupied_projects or project_root in reserved_projects:
            continue
        queue = project_queues.get(project_root) or []
        if not queue:
            continue
        startable_codes.add(queue[0].code)
        reserved_projects.add(project_root)
        available_slots -= 1
    return startable_codes


def sync_waiting_tasks(
    config: Dict[str, Any],
    meta: Dict[str, Any],
    runtime_paths: Dict[str, Path],
    owner_pid: int,
    project_queues: Dict[str, List[RecordTask]],
    project_order: List[str],
    occupied_projects: Set[str],
    running_count: int,
    max_concurrency: int,
) -> None:
    startable_codes = compute_startable_codes(
        project_queues,
        project_order,
        occupied_projects,
        running_count,
        max_concurrency,
    )
    queued_codes = get_queued_task_codes(runtime_paths)
    queued_at = datetime.now()

    for project_root in project_order:
        queue = project_queues.get(project_root) or []
        queue.sort(key=task_sort_key)
        for task in queue:
            if task.code in startable_codes or task.code in queued_codes:
                continue
            update_task_status(
                config,
                meta,
                task.record_id,
                {config["fields"]["status"]: STATUS_QUEUED},
            )
            set_queued_task(
                runtime_paths,
                task,
                Path(project_root),
                owner_pid,
                queued_at,
                scheduled_run_time=task.run_time,
                admitted_at=queued_at,
            )
            queued_codes.add(task.code)
            log_line(runtime_paths, f"{task.code} 宸茶繘鍏ョ瓑寰呴槦鍒?")


def enqueue_tasks(
    config: Dict[str, Any],
    project_queues: Dict[str, List[RecordTask]],
    project_order: List[str],
    tasks: List[RecordTask],
) -> None:
    for task in tasks:
        project_root = normalize_project_root(config["projects"][task.code])
        if not project_root:
            continue
        if project_root not in project_queues:
            project_queues[project_root] = []
            project_order.append(project_root)
        project_queues[project_root].append(task)
        project_queues[project_root].sort(key=task_sort_key)


def compensate_active_tasks_for_signal(signal_name: str) -> None:
    with STATE_LOCK:
        context = ACTIVE_CONTEXT
    if not context:
        return

    config = context.config
    meta = context.meta
    runtime_paths = context.runtime_paths
    with STATE_LOCK:
        state = load_state(runtime_paths)
        running_tasks = dict(state.get("running_tasks") or {})
        queued_tasks = dict(state.get("queued_tasks") or {})

    owner_pid = int(context.lock_payload.get("pid") or os.getpid())
    for code, task_state in running_tasks.items():
        if int(task_state.get("owner_pid") or 0) != owner_pid:
            continue
        mark_task_interrupted(code, SIGNAL_STOP_ERROR_TEXT)
        child_pid = task_state.get("child_pid")
        if child_pid and not terminate_process_tree(child_pid):
            log_line(runtime_paths, f"{code} 鏀跺埌 {signal_name} 鍚庢湭鑳界粓姝㈠瓙杩涚▼ {child_pid}锛屼繚鐣欒繍琛岃褰?")
            continue
        try:
            compensate_running_task(config, meta, runtime_paths, task_state, SIGNAL_STOP_ERROR_TEXT)
            log_line(runtime_paths, f"{code} 鏀跺埌 {signal_name} 鍚庡凡鍥炲啓涓柇鐘舵€?")
            remove_running_task(runtime_paths, code)
        except Exception:
            continue

    for code, task_state in queued_tasks.items():
        if int(task_state.get("owner_pid") or 0) != owner_pid:
            continue
        log_line(runtime_paths, f"{code} 鏀跺埌 {signal_name} 鍚庝繚鐣欑瓑寰呴槦鍒楋紝寰呬笅娆?tick 缁х画鎵ц")

    release_lock(runtime_paths)


def install_signal_handlers() -> None:
    def _handler(signum, _frame) -> None:
        signal_name = getattr(signal.Signals(signum), "name", str(signum))
        compensate_active_tasks_for_signal(signal_name)
        raise KeyboardInterrupt()

    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handler)


def run_tick(config_path: Optional[str] = None) -> int:
    config = load_schedule_config(config_path)
    runtime_paths = ensure_runtime_paths(config)
    meta = ensure_schedule_table(config, runtime_paths)

    cleanup_stale_lock(config, meta, runtime_paths)
    recover_running_tasks(config, meta, runtime_paths)

    acquired, lock_payload = acquire_lock(runtime_paths)
    if not acquired:
        log_line(runtime_paths, "涓婁竴娆?tick 浠嶅湪杩愯锛岃烦杩囨湰娆℃墽琛?")
        return 0

    context = SchedulerRuntimeContext(
        config=config,
        meta=meta,
        runtime_paths=runtime_paths,
        lock_payload=lock_payload,
    )
    register_context(context)

    try:
        max_concurrency = max(1, int(config.get("max_concurrency", 2)))
        scan_interval = max(1, int(config.get("active_tick_scan_interval_seconds", 10)))
        owner_pid = int(lock_payload.get("pid") or os.getpid())
        blocked_projects = get_running_project_roots(runtime_paths)
        project_queues: Dict[str, List[RecordTask]] = {}
        project_order: List[str] = []

        tick_started_at = datetime.now()
        tick_minute_floor = tick_started_at.replace(second=0, microsecond=0)
        saved_window_start = load_last_due_scan_at(runtime_paths)
        initial_window_start = saved_window_start or (tick_minute_floor - timedelta(seconds=1))
        if initial_window_start > tick_started_at:
            initial_window_start = tick_minute_floor - timedelta(seconds=1)
        last_due_scan_at = tick_started_at
        today_text = tick_started_at.strftime("%Y-%m-%d")

        resumable = load_resumable_queued_tasks(config, meta, runtime_paths, owner_pid, today_text)
        if resumable:
            enqueue_tasks(config, project_queues, project_order, [task for _, task in resumable])

        initial_tasks = fetch_due_tasks(
            config,
            meta,
            runtime_paths,
            window_start=initial_window_start,
            window_end=tick_started_at,
        )
        if initial_tasks:
            enqueue_tasks(config, project_queues, project_order, initial_tasks)

        if not project_queues:
            log_line(runtime_paths, "娌℃湁鍒扮偣涓斿緟鎵ц鐨勪唬鐮?")
            return 0

        log_line(
            runtime_paths,
            f"鏈疆鍑嗗鎵ц {sum(len(tasks) for tasks in project_queues.values())} 涓唬鐮侊紝骞跺彂涓婇檺 {max_concurrency}",
        )

        running: Dict[Any, Tuple[str, RecordTask]] = {}
        running_projects: Set[str] = set()

        with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
            while any(project_queues.get(project_root) for project_root in project_order) or running:
                started_this_round = False
                occupied_projects = set(blocked_projects) | set(running_projects)
                sync_waiting_tasks(
                    config,
                    meta,
                    runtime_paths,
                    owner_pid,
                    project_queues,
                    project_order,
                    occupied_projects,
                    len(running),
                    max_concurrency,
                )

                while len(running) < max_concurrency:
                    next_project_root = None
                    for project_root in project_order:
                        if project_root in occupied_projects:
                            continue
                        if project_queues.get(project_root):
                            next_project_root = project_root
                            break
                    if not next_project_root:
                        break

                    task = project_queues[next_project_root].pop(0)
                    future = executor.submit(run_single_code, config, meta, runtime_paths, task)
                    running[future] = (next_project_root, task)
                    running_projects.add(next_project_root)
                    occupied_projects.add(next_project_root)
                    sync_lock_codes_from_state(runtime_paths)
                    log_line(runtime_paths, f"寮€濮嬫墽琛?{task.code}")
                    started_this_round = True

                if not running:
                    if not started_this_round:
                        break
                    continue

                done, _ = wait(
                    list(running.keys()),
                    timeout=scan_interval,
                    return_when=FIRST_COMPLETED,
                )

                if done:
                    for future in done:
                        project_root, task = running.pop(future)
                        running_projects.discard(project_root)
                        try:
                            code, success, error_text = future.result()
                            if success:
                                log_line(runtime_paths, f"{code} 鎵ц鎴愬姛骞跺凡鍚屾椋炰功鏂囨。")
                            else:
                                log_line(runtime_paths, f"{code} 鎵ц澶辫触: {error_text}")
                        except Exception as exc:
                            log_line(runtime_paths, f"{task.code} 鎵ц寮傚父: {exc}")

                    resumed = load_resumable_queued_tasks(config, meta, runtime_paths, owner_pid, today_text)
                    if resumed:
                        merge_pending_tasks(pending_tasks, [task for _, task in resumed])

                scan_end = datetime.now()
                new_tasks = fetch_due_tasks(
                    config,
                    meta,
                    runtime_paths,
                    window_start=last_due_scan_at,
                    window_end=scan_end,
                )
                if new_tasks:
                    enqueue_tasks(config, project_queues, project_order, new_tasks)
                last_due_scan_at = scan_end
                sync_lock_codes_from_state(runtime_paths)
        return 0
    finally:
        register_context(None)
        release_lock(runtime_paths)


def merge_pending_tasks(pending_tasks: List[RecordTask], tasks: List[RecordTask]) -> None:
    existing_task_ids = {task.task_id for task in pending_tasks}
    for task in tasks:
        if task.task_id in existing_task_ids:
            continue
        pending_tasks.append(task)
        existing_task_ids.add(task.task_id)
    pending_tasks.sort(key=task_sort_key)


def choose_project_for_task(
    config: Dict[str, Any],
    task: RecordTask,
    occupied_projects: Set[str],
    reserved_projects: Set[str],
    *,
    preferred_only: bool,
) -> Optional[str]:
    preferred_root = get_preferred_project_root(config, task.code)
    candidate_roots = get_candidate_project_roots(config, task.code)
    if preferred_only:
        if not preferred_root:
            return None
        if preferred_root in occupied_projects or preferred_root in reserved_projects:
            return None
        return preferred_root

    for project_root in candidate_roots:
        if project_root in occupied_projects or project_root in reserved_projects:
            continue
        return project_root
    return None


def plan_task_assignments(
    config: Dict[str, Any],
    pending_tasks: List[RecordTask],
    occupied_projects: Set[str],
    running_count: int,
    max_concurrency: int,
) -> Dict[str, str]:
    available_slots = max(0, max_concurrency - running_count)
    if available_slots <= 0:
        return {}

    assignments: Dict[str, str] = {}
    reserved_projects: Set[str] = set()

    for preferred_only in (True, False):
        if len(assignments) >= available_slots:
            break
        for task in pending_tasks:
            if task.task_id in assignments:
                continue
            project_root = choose_project_for_task(
                config,
                task,
                occupied_projects,
                reserved_projects,
                preferred_only=preferred_only,
            )
            if not project_root:
                continue
            assignments[task.task_id] = project_root
            reserved_projects.add(project_root)
            if len(assignments) >= available_slots:
                break

    return assignments


def sync_waiting_tasks(
    config: Dict[str, Any],
    meta: Dict[str, Any],
    runtime_paths: Dict[str, Path],
    owner_pid: int,
    pending_tasks: List[RecordTask],
    occupied_projects: Set[str],
    running_count: int,
    max_concurrency: int,
) -> None:
    assignments = plan_task_assignments(
        config,
        pending_tasks,
        occupied_projects,
        running_count,
        max_concurrency,
    )
    queued_task_ids = get_queued_task_codes(runtime_paths)
    queued_at = datetime.now()

    for task in pending_tasks:
        if task.task_id in assignments or task.task_id in queued_task_ids:
            continue
        update_task_status(
            config,
            meta,
            task.record_id,
            {config["fields"]["status"]: STATUS_QUEUED},
        )
        set_queued_task(
            runtime_paths,
            task,
            get_preferred_project_root(config, task.code),
            get_candidate_project_roots(config, task.code),
            owner_pid,
            queued_at,
            scheduled_run_time=task.run_time,
            admitted_at=queued_at,
        )
        queued_task_ids.add(task.task_id)
        log_line(runtime_paths, f"{task.code} 已进入等待队列")


def compensate_active_tasks_for_signal(signal_name: str) -> None:
    with STATE_LOCK:
        context = ACTIVE_CONTEXT
    if not context:
        return

    config = context.config
    meta = context.meta
    runtime_paths = context.runtime_paths
    with STATE_LOCK:
        state = load_state(runtime_paths)
        running_tasks = dict(state.get("running_tasks") or {})
        queued_tasks = dict(state.get("queued_tasks") or {})

    owner_pid = int(context.lock_payload.get("pid") or os.getpid())
    for code, task_state in running_tasks.items():
        if int(task_state.get("owner_pid") or 0) != owner_pid:
            continue
        mark_task_interrupted(code, SIGNAL_STOP_ERROR_TEXT)
        child_pid = task_state.get("child_pid")
        if child_pid and not terminate_process_tree(child_pid):
            log_line(runtime_paths, f"{code} 收到 {signal_name} 后未能终止子进程 {child_pid}")
            continue
        try:
            compensate_running_task(config, meta, runtime_paths, task_state, SIGNAL_STOP_ERROR_TEXT)
            log_line(runtime_paths, f"{code} 收到 {signal_name} 后已回写中断状态")
            remove_running_task(runtime_paths, code)
        except Exception:
            continue

    for code, task_state in queued_tasks.items():
        if int(task_state.get("owner_pid") or 0) != owner_pid:
            continue
        try:
            restore_queued_task_to_idle(config, meta, runtime_paths, task_state)
            log_line(runtime_paths, f"{code} 收到 {signal_name} 后已恢复为空闲")
            remove_queued_task(runtime_paths, code)
        except Exception:
            continue

    release_lock(runtime_paths)


def install_signal_handlers() -> None:
    def _handler(signum, _frame) -> None:
        signal_name = getattr(signal.Signals(signum), "name", str(signum))
        compensate_active_tasks_for_signal(signal_name)
        raise KeyboardInterrupt()

    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handler)


def run_tick(config_path: Optional[str] = None) -> int:
    config = load_schedule_config(config_path)
    runtime_paths = ensure_runtime_paths(config)
    meta = ensure_schedule_table(config, runtime_paths)

    cleanup_stale_lock(config, meta, runtime_paths)
    recover_running_tasks(config, meta, runtime_paths)

    acquired, lock_payload = acquire_lock(runtime_paths)
    if not acquired:
        log_line(runtime_paths, "上一轮 tick 仍在运行，跳过本次执行")
        return 0

    context = SchedulerRuntimeContext(
        config=config,
        meta=meta,
        runtime_paths=runtime_paths,
        lock_payload=lock_payload,
    )
    register_context(context)

    try:
        max_concurrency = max(1, int(config.get("max_concurrency", 2)))
        scan_interval = max(1, int(config.get("active_tick_scan_interval_seconds", 10)))
        owner_pid = int(lock_payload.get("pid") or os.getpid())
        pending_tasks: List[RecordTask] = []

        tick_started_at = datetime.now()
        tick_minute_floor = tick_started_at.replace(second=0, microsecond=0)
        saved_window_start = load_last_due_scan_at(runtime_paths)
        initial_window_start = saved_window_start or (tick_minute_floor - timedelta(seconds=1))
        if initial_window_start > tick_started_at:
            initial_window_start = tick_minute_floor - timedelta(seconds=1)
        last_due_scan_at = tick_started_at
        today_text = tick_started_at.strftime("%Y-%m-%d")

        resumable = load_resumable_queued_tasks(config, meta, runtime_paths, owner_pid, today_text)
        if resumable:
            merge_pending_tasks(pending_tasks, [task for _, task in resumable])

        initial_tasks = fetch_due_tasks(
            config,
            meta,
            runtime_paths,
            window_start=initial_window_start,
            window_end=tick_started_at,
        )
        if initial_tasks:
            merge_pending_tasks(pending_tasks, initial_tasks)

        if not pending_tasks:
            save_last_due_scan_at(runtime_paths, tick_started_at)
            log_line(runtime_paths, "没有到点且待执行的代码")
            return 0

        log_line(runtime_paths, f"本轮准备调度 {len(pending_tasks)} 个代码，全局并发上限 {max_concurrency}")

        running: Dict[Any, Tuple[str, RecordTask]] = {}
        running_projects: Set[str] = set()

        with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
            while pending_tasks or running:
                started_this_round = False
                occupied_projects = get_running_project_roots(runtime_paths) | set(running_projects)
                sync_waiting_tasks(
                    config,
                    meta,
                    runtime_paths,
                    owner_pid,
                    pending_tasks,
                    occupied_projects,
                    len(running),
                    max_concurrency,
                )

                assignments = plan_task_assignments(
                    config,
                    pending_tasks,
                    occupied_projects,
                    len(running),
                    max_concurrency,
                )

                while len(running) < max_concurrency and assignments:
                    launched = False
                    for task in list(pending_tasks):
                        project_root = assignments.get(task.task_id)
                        if not project_root:
                            continue
                        future = executor.submit(
                            run_single_code,
                            config,
                            meta,
                            runtime_paths,
                            task,
                            project_root,
                        )
                        running[future] = (project_root, task)
                        running_projects.add(project_root)
                        occupied_projects.add(project_root)
                        pending_tasks.remove(task)
                        sync_lock_codes_from_state(runtime_paths)
                        log_line(runtime_paths, f"开始执行 {task.code}，项目 {project_root}")
                        started_this_round = True
                        launched = True
                        if len(running) >= max_concurrency:
                            break
                    if not launched:
                        break
                    assignments = plan_task_assignments(
                        config,
                        pending_tasks,
                        occupied_projects,
                        len(running),
                        max_concurrency,
                    )

                if not running:
                    if not started_this_round:
                        break
                    continue

                done, _ = wait(
                    list(running.keys()),
                    timeout=scan_interval,
                    return_when=FIRST_COMPLETED,
                )

                if done:
                    for future in done:
                        project_root, task = running.pop(future)
                        running_projects.discard(project_root)
                        try:
                            code, success, error_text = future.result()
                            if success:
                                log_line(runtime_paths, f"{code} 执行成功并已同步飞书文档")
                            else:
                                log_line(runtime_paths, f"{code} 执行失败: {error_text}")
                        except Exception as exc:
                            log_line(runtime_paths, f"{task.code} 执行异常: {exc}")

                scan_end = datetime.now()
                new_tasks = fetch_due_tasks(
                    config,
                    meta,
                    runtime_paths,
                    window_start=last_due_scan_at,
                    window_end=scan_end,
                )
                if new_tasks:
                    merge_pending_tasks(pending_tasks, new_tasks)
                last_due_scan_at = scan_end
                save_last_due_scan_at(runtime_paths, last_due_scan_at)
                sync_lock_codes_from_state(runtime_paths)
        return 0
    finally:
        register_context(None)
        release_lock(runtime_paths)


def compensate_running_task(
    config: Dict[str, Any],
    meta: Dict[str, Any],
    runtime_paths: Dict[str, Path],
    task_state: Dict[str, Any],
    error_text: str,
) -> None:
    code = str(task_state.get("code", "UNKNOWN")).strip().upper() or "UNKNOWN"
    record_id = str(task_state.get("record_id", "")).strip()
    if not record_id:
        return
    start_time_text = str(task_state.get("started_at", "")).strip() or None
    update_task_status(
        config,
        meta,
        record_id,
        build_compensation_fields(config["fields"], datetime.now(), error_text, start_time_text=start_time_text),
        required=False,
        runtime_paths=runtime_paths,
        task_id=record_id,
        code=code,
        phase="failed",
    )


def restore_queued_task_to_idle(
    config: Dict[str, Any],
    meta: Dict[str, Any],
    runtime_paths: Dict[str, Path],
    task_state: Dict[str, Any],
) -> None:
    code = str(task_state.get("code", "UNKNOWN")).strip().upper() or "UNKNOWN"
    record_id = str(task_state.get("record_id", "")).strip()
    if not record_id:
        return
    update_task_status(
        config,
        meta,
        record_id,
        build_idle_status_fields(config["fields"]),
        required=False,
        runtime_paths=runtime_paths,
        task_id=record_id,
        code=code,
        phase="queued",
    )


def sync_waiting_tasks(
    config: Dict[str, Any],
    meta: Dict[str, Any],
    runtime_paths: Dict[str, Path],
    owner_pid: int,
    pending_tasks: List[RecordTask],
    occupied_projects: Set[str],
    running_count: int,
    max_concurrency: int,
) -> None:
    assignments = plan_task_assignments(
        config,
        pending_tasks,
        occupied_projects,
        running_count,
        max_concurrency,
    )
    queued_task_ids = get_queued_task_codes(runtime_paths)
    queued_at = datetime.now()

    for task in pending_tasks:
        if task.task_id in assignments or task.task_id in queued_task_ids:
            continue
        set_queued_task(
            runtime_paths,
            task,
            get_preferred_project_root(config, task.code),
            get_candidate_project_roots(config, task.code),
            owner_pid,
            queued_at,
            scheduled_run_time=task.run_time,
            admitted_at=queued_at,
        )
        update_task_status(
            config,
            meta,
            task.record_id,
            {config["fields"]["status"]: STATUS_QUEUED},
            required=False,
            runtime_paths=runtime_paths,
            task_id=task.task_id,
            code=task.code,
            phase="queued",
        )
        queued_task_ids.add(task.task_id)
        log_line(runtime_paths, f"{task.code} 宸茶繘鍏ョ瓑寰呴槦鍒?")


def run_single_code(
    config: Dict[str, Any],
    meta: Dict[str, Any],
    runtime_paths: Dict[str, Path],
    task: RecordTask,
    project_root_value: str,
) -> Tuple[str, bool, str]:
    fields = config["fields"]
    project_root_text = normalize_project_root(project_root_value)
    if not project_root_text:
        raise ScheduleConfigError(f"{task.code} 缂哄皯鍙墽琛岄」鐩洰褰?")
    project_root = Path(project_root_text)
    python_exe = sys.executable
    start_time = datetime.now()
    child_process: Optional[subprocess.Popen[str]] = None
    sync_process: Optional[subprocess.Popen[str]] = None

    try:
        remove_queued_task(runtime_paths, task.task_id)
        set_running_task(
            runtime_paths,
            task,
            project_root,
            os.getpid(),
            start_time,
            child_pid=None,
            phase="main_controller",
            scheduled_run_time=task.run_time,
        )

        running_status_written = update_task_status(
            config,
            meta,
            task.record_id,
            {
                fields["status"]: STATUS_RUNNING,
                fields["last_start_time"]: start_time.strftime("%Y-%m-%d %H:%M:%S"),
                fields["last_end_time"]: "",
                fields["last_result"]: "",
                fields["error_message"]: "",
            },
            required=False,
            runtime_paths=runtime_paths,
            task_id=task.task_id,
            code=task.code,
            phase="running",
        )
        if not running_status_written:
            log_line(runtime_paths, f"{task.code} 鍚姩鍓嶉涔︾姸鎬佸洖鍐欏け璐ワ紝鎸夐檷绾х瓥鐣ョ户缁惎鍔?")

        interrupted_error = get_interrupted_error(task.task_id)
        if interrupted_error:
            update_task_status(
                config,
                meta,
                task.record_id,
                build_compensation_fields(
                    fields,
                    datetime.now(),
                    interrupted_error,
                    start_time_text=start_time.strftime("%Y-%m-%d %H:%M:%S"),
                ),
                required=False,
                runtime_paths=runtime_paths,
                task_id=task.task_id,
                code=task.code,
                phase="failed",
            )
            flush_pending_status_updates(config, meta, runtime_paths)
            return task.code, False, interrupted_error

        command = [
            python_exe,
            str(project_root / "src" / "main_controller.py"),
            "--picks",
            task.code,
        ]
        child_process = subprocess.Popen(
            command,
            cwd=str(project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        update_running_task_phase(runtime_paths, task.task_id, child_pid=child_process.pid, phase="main_controller")
        stdout_text, stderr_text = child_process.communicate()

        sync_stdout = ""
        sync_stderr = ""
        sync_returncode = 0
        if child_process.returncode == 0:
            interrupted_error = get_interrupted_error(task.task_id)
            if interrupted_error:
                update_task_status(
                    config,
                    meta,
                    task.record_id,
                    build_compensation_fields(
                        fields,
                        datetime.now(),
                        interrupted_error,
                        start_time_text=start_time.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                    required=False,
                    runtime_paths=runtime_paths,
                    task_id=task.task_id,
                    code=task.code,
                    phase="failed",
                )
                flush_pending_status_updates(config, meta, runtime_paths)
                return task.code, False, interrupted_error
            sync_command = [python_exe, str(Path(__file__).resolve().with_name("feishu_sync.py"))]
            sync_process = subprocess.Popen(
                sync_command,
                cwd=str(Path(__file__).resolve().parents[1]),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            update_running_task_phase(runtime_paths, task.task_id, child_pid=sync_process.pid, phase="feishu_sync")
            sync_stdout, sync_stderr = sync_process.communicate()
            sync_returncode = sync_process.returncode

        end_time = datetime.now()
        success = child_process.returncode == 0 and sync_returncode == 0
        error_text = ""
        if child_process.returncode != 0:
            error_text = (stderr_text or stdout_text or f"main_controller exit={child_process.returncode}").strip()
        elif sync_returncode != 0:
            error_text = (sync_stderr or sync_stdout or f"feishu_sync exit={sync_returncode}").strip()
        error_text = error_text[:1000]

        interrupted_error = get_interrupted_error(task.task_id)
        if interrupted_error:
            update_task_status(
                config,
                meta,
                task.record_id,
                build_compensation_fields(
                    fields,
                    end_time,
                    interrupted_error,
                    start_time_text=start_time.strftime("%Y-%m-%d %H:%M:%S"),
                ),
                required=False,
                runtime_paths=runtime_paths,
                task_id=task.task_id,
                code=task.code,
                phase="failed",
            )
            flush_pending_status_updates(config, meta, runtime_paths)
            return task.code, False, interrupted_error

        final_phase = "finished" if success else "failed"
        final_status_written = update_task_status(
            config,
            meta,
            task.record_id,
            {
                fields["status"]: STATUS_IDLE if success else STATUS_FAILED,
                fields["last_run_date"]: end_time.strftime("%Y-%m-%d"),
                fields["last_start_time"]: start_time.strftime("%Y-%m-%d %H:%M:%S"),
                fields["last_end_time"]: end_time.strftime("%Y-%m-%d %H:%M:%S"),
                fields["last_result"]: RESULT_SUCCESS if success else RESULT_FAILED,
                fields["error_message"]: error_text,
            },
            required=False,
            runtime_paths=runtime_paths,
            task_id=task.task_id,
            code=task.code,
            phase=final_phase,
        )
        if not final_status_written:
            if success:
                log_line(runtime_paths, f"{task.code} 涓氬姟鎵ц鎴愬姛锛屼絾椋炰功鐘舵€佸緟琛ュ啓")
            else:
                log_line(runtime_paths, f"{task.code} 涓氬姟鎵ц澶辫触锛屼絾椋炰功鐘舵€佸緟琛ュ啓")
        flush_pending_status_updates(config, meta, runtime_paths)
        return task.code, success, error_text
    except Exception as exc:
        end_time = datetime.now()
        error_text = get_interrupted_error(task.task_id) or str(exc).strip() or exc.__class__.__name__
        error_text = error_text[:1000]
        try:
            update_task_status(
                config,
                meta,
                task.record_id,
                build_compensation_fields(
                    fields,
                    end_time,
                    error_text,
                    start_time_text=start_time.strftime("%Y-%m-%d %H:%M:%S"),
                ),
                required=False,
                runtime_paths=runtime_paths,
                task_id=task.task_id,
                code=task.code,
                phase="failed",
            )
            flush_pending_status_updates(config, meta, runtime_paths)
        except Exception as status_exc:
            log_line(runtime_paths, f"{task.code} 鍚姩闃舵鐘舵€佸洖鍐欏け璐? {status_exc}")
        return task.code, False, error_text
    finally:
        remove_running_task(runtime_paths, task.task_id)
        clear_interrupted_error(task.task_id)


def run_tick(config_path: Optional[str] = None) -> int:
    config = load_schedule_config(config_path)
    runtime_paths = ensure_runtime_paths(config)
    meta = ensure_schedule_table(config, runtime_paths)

    cleanup_stale_lock(config, meta, runtime_paths)
    recover_running_tasks(config, meta, runtime_paths)

    acquired, lock_payload = acquire_lock(runtime_paths)
    if not acquired:
        log_line(runtime_paths, "涓婁竴杞?tick 浠嶅湪杩愯锛岃烦杩囨湰娆℃墽琛?")
        return 0

    context = SchedulerRuntimeContext(
        config=config,
        meta=meta,
        runtime_paths=runtime_paths,
        lock_payload=lock_payload,
    )
    register_context(context)

    try:
        max_concurrency = max(1, int(config.get("max_concurrency", 2)))
        scan_interval = max(1, int(config.get("active_tick_scan_interval_seconds", 10)))
        owner_pid = int(lock_payload.get("pid") or os.getpid())
        pending_tasks: List[RecordTask] = []

        flush_pending_status_updates(config, meta, runtime_paths)

        tick_started_at = datetime.now()
        tick_minute_floor = tick_started_at.replace(second=0, microsecond=0)
        saved_window_start = load_last_due_scan_at(runtime_paths)
        initial_window_start = saved_window_start or (tick_minute_floor - timedelta(seconds=1))
        if initial_window_start > tick_started_at:
            initial_window_start = tick_minute_floor - timedelta(seconds=1)
        last_due_scan_at = tick_started_at
        today_text = tick_started_at.strftime("%Y-%m-%d")

        resumable = load_resumable_queued_tasks(config, meta, runtime_paths, owner_pid, today_text)
        if resumable:
            merge_pending_tasks(pending_tasks, [task for _, task in resumable])

        initial_tasks = fetch_due_tasks(
            config,
            meta,
            runtime_paths,
            window_start=initial_window_start,
            window_end=tick_started_at,
        )
        if initial_tasks:
            merge_pending_tasks(pending_tasks, initial_tasks)

        if not pending_tasks:
            save_last_due_scan_at(runtime_paths, tick_started_at)
            log_line(runtime_paths, "娌℃湁鍒扮偣涓斿緟鎵ц鐨勪唬鐮?")
            return 0

        log_line(runtime_paths, f"鏈疆鍑嗗璋冨害 {len(pending_tasks)} 涓唬鐮侊紝鍏ㄥ眬骞跺彂涓婇檺 {max_concurrency}")

        running: Dict[Any, Tuple[str, RecordTask]] = {}
        running_projects: Set[str] = set()

        with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
            while pending_tasks or running:
                started_this_round = False
                occupied_projects = get_running_project_roots(runtime_paths) | set(running_projects)
                flush_pending_status_updates(config, meta, runtime_paths)
                sync_waiting_tasks(
                    config,
                    meta,
                    runtime_paths,
                    owner_pid,
                    pending_tasks,
                    occupied_projects,
                    len(running),
                    max_concurrency,
                )

                assignments = plan_task_assignments(
                    config,
                    pending_tasks,
                    occupied_projects,
                    len(running),
                    max_concurrency,
                )

                while len(running) < max_concurrency and assignments:
                    launched = False
                    for task in list(pending_tasks):
                        project_root = assignments.get(task.task_id)
                        if not project_root:
                            continue
                        future = executor.submit(
                            run_single_code,
                            config,
                            meta,
                            runtime_paths,
                            task,
                            project_root,
                        )
                        running[future] = (project_root, task)
                        running_projects.add(project_root)
                        occupied_projects.add(project_root)
                        pending_tasks.remove(task)
                        sync_lock_codes_from_state(runtime_paths)
                        log_line(runtime_paths, f"寮€濮嬫墽琛?{task.code}锛岄」鐩?{project_root}")
                        started_this_round = True
                        launched = True
                        if len(running) >= max_concurrency:
                            break
                    if not launched:
                        break
                    assignments = plan_task_assignments(
                        config,
                        pending_tasks,
                        occupied_projects,
                        len(running),
                        max_concurrency,
                    )

                if not running:
                    if not started_this_round:
                        break
                    continue

                done, _ = wait(
                    list(running.keys()),
                    timeout=scan_interval,
                    return_when=FIRST_COMPLETED,
                )

                if done:
                    for future in done:
                        project_root, task = running.pop(future)
                        running_projects.discard(project_root)
                        try:
                            code, success, error_text = future.result()
                            if success:
                                log_line(runtime_paths, f"{code} 鎵ц鎴愬姛骞跺凡鍚屾椋炰功鏂囨。")
                            else:
                                log_line(runtime_paths, f"{code} 鎵ц澶辫触: {error_text}")
                                if send_scheduler_alert(f"任务 {code} 失败", error_text):
                                    log_line(runtime_paths, f"{code} 失败告警已发送到自动化程序通知群")
                        except Exception as exc:
                            log_line(runtime_paths, f"{task.code} 鎵ц寮傚父: {exc}")
                            if send_scheduler_alert(f"任务 {task.code} 异常", str(exc)):
                                log_line(runtime_paths, f"{task.code} 异常告警已发送到自动化程序通知群")

                resumed = load_resumable_queued_tasks(
                    config,
                    meta,
                    runtime_paths,
                    owner_pid,
                    today_text,
                )
                if resumed:
                    merge_pending_tasks(pending_tasks, [task for _, task in resumed])
                flush_pending_status_updates(config, meta, runtime_paths)

                scan_end = datetime.now()
                new_tasks = fetch_due_tasks(
                    config,
                    meta,
                    runtime_paths,
                    window_start=last_due_scan_at,
                    window_end=scan_end,
                )
                if new_tasks:
                    merge_pending_tasks(pending_tasks, new_tasks)
                last_due_scan_at = scan_end
                save_last_due_scan_at(runtime_paths, last_due_scan_at)
                sync_lock_codes_from_state(runtime_paths)
        return 0
    finally:
        register_context(None)
        release_lock(runtime_paths)


def run_watch(config_path: Optional[str] = None) -> int:
    config = load_schedule_config(config_path)
    interval = max(10, int(config.get("poll_interval_seconds", 30)))
    while True:
        try:
            run_tick(config_path)
        except Exception as exc:
            runtime_paths = ensure_runtime_paths(config)
            log_line(runtime_paths, f"调度循环异常: {exc}")
            if send_scheduler_alert("调度循环异常", str(exc)):
                log_line(runtime_paths, "调度循环异常告警已发送到自动化程序通知群")
        time.sleep(interval)


def normalize_time_text(text: str) -> str:
    translation = {
        0xFF10: "0",
        0xFF11: "1",
        0xFF12: "2",
        0xFF13: "3",
        0xFF14: "4",
        0xFF15: "5",
        0xFF16: "6",
        0xFF17: "7",
        0xFF18: "8",
        0xFF19: "9",
        0xFF1A: ":",
        0xFE55: ":",
        0x2236: ":",
        0xA789: ":",
    }
    return text.translate(translation)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="飞书单表调度器")
    parser.add_argument("--config", default=None, help="调度配置文件路径")
    parser.add_argument("--init-only", action="store_true", help="只初始化飞书多维表和默认记录")
    parser.add_argument("--tick", action="store_true", help="执行一次调度检查")
    parser.add_argument("--watch", action="store_true", help="持续轮询")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    install_signal_handlers()
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_schedule_config(args.config)
    runtime_paths = ensure_runtime_paths(config)
    meta = ensure_schedule_table(config, runtime_paths)
    print(f"飞书调度表已就绪: app_token={meta['app_token']} table_id={meta['table_id']}")

    if args.init_only:
        return 0
    if args.watch:
        return run_watch(args.config)
    return run_tick(args.config)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FeishuError as exc:
        print(f"飞书 API 失败: {exc}", file=sys.stderr)
        if send_scheduler_alert("飞书 API 失败", str(exc)):
            print("飞书 API 失败告警已发送到自动化程序通知群", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)
