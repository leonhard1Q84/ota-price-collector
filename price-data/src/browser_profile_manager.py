# -*- coding: utf-8 -*-
"""Browser profile lifecycle helpers for the Klook crawler."""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from logger import log_info, log_warning
from path_utils import (
    ensure_dir,
    get_userdata_archive_dir,
    get_userdata_base_dir,
    get_userdata_dir,
    get_userdata_runtime_dir,
)


class BrowserProfileLock:
    """跨进程独占持久 Profile；锁文件位于 Profile 同级目录。"""

    def __init__(self, profile_dir: str):
        profile_path = Path(profile_dir)
        self.profile_dir = str(profile_path)
        self.lock_path = profile_path.parent / f".{profile_path.name}.klook-profile.lock"
        self._file = None

    def acquire(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.lock_path.open("a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write("\0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            lock_file.close()
            raise RuntimeError(
                f"浏览器 Profile 正被另一个任务使用: {self.profile_dir}"
            ) from exc

        lock_file.seek(0)
        lock_file.truncate()
        json.dump(
            {
                "pid": os.getpid(),
                "acquired_at": datetime.now().isoformat(),
                "profile_dir": self.profile_dir,
            },
            lock_file,
            ensure_ascii=False,
        )
        lock_file.flush()
        self._file = lock_file
        return self

    def release(self):
        lock_file = self._file
        if lock_file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()
            self._file = None


def get_browser_profile_path(browser_config=None) -> str:
    config = browser_config if isinstance(browser_config, dict) else {}
    profile_mode = str(config.get("profile_mode") or "base_plus_runtime")
    if profile_mode == "base_plus_runtime":
        return get_userdata_runtime_dir()
    return get_userdata_dir()


def acquire_browser_profile_lock(browser_config=None) -> BrowserProfileLock:
    return BrowserProfileLock(get_browser_profile_path(browser_config)).acquire()


def _preferences_path(profile_dir: str) -> Path:
    return Path(profile_dir) / "Default" / "Preferences"


def _read_preferences(profile_dir: str):
    path = _preferences_path(profile_dir)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except Exception as exc:
        log_warning(f"读取浏览器 Preferences 失败: {exc}")
        return None


def _write_preferences(profile_dir: str, preferences) -> None:
    path = _preferences_path(profile_dir)
    if not path.exists() or not isinstance(preferences, dict):
        return
    with path.open("w", encoding="utf-8") as file:
        json.dump(preferences, file, ensure_ascii=False, separators=(",", ":"))


def _profile_exit_type(profile_dir: str) -> str:
    preferences = _read_preferences(profile_dir)
    if not isinstance(preferences, dict):
        return ""
    return str(((preferences.get("profile") or {}).get("exit_type") or "")).strip()


def _clear_crash_flags(profile_dir: str) -> None:
    preferences = _read_preferences(profile_dir)
    if not isinstance(preferences, dict):
        return
    profile = preferences.setdefault("profile", {})
    if isinstance(profile, dict):
        profile["exit_type"] = "Normal"
        profile["exited_cleanly"] = True
    session = preferences.setdefault("session", {})
    if isinstance(session, dict):
        session["restore_on_startup"] = 5
        session["startup_urls"] = []
    _write_preferences(profile_dir, preferences)


def _copy_profile(src: str, dst: str) -> None:
    dst_path = Path(dst)
    if dst_path.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("Singleton*", "Crashpad", "BrowserMetrics*"))
    _clear_crash_flags(dst)


def _archive_runtime(runtime_dir: str) -> None:
    runtime_path = Path(runtime_dir)
    if not runtime_path.exists():
        return
    archive_root = Path(ensure_dir(get_userdata_archive_dir()))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = archive_root / f"userdata_runtime_{timestamp}"
    try:
        shutil.move(str(runtime_path), str(archive_path))
        log_warning(f"已归档异常浏览器运行资料: {archive_path}")
    except Exception as exc:
        log_warning(f"归档异常浏览器运行资料失败，保留原目录并停止重建: {exc}")
        raise


def ensure_browser_profile(browser_config=None, force_rebuild_runtime=False) -> str:
    config = browser_config if isinstance(browser_config, dict) else {}
    profile_mode = str(config.get("profile_mode") or "base_plus_runtime")
    rebuild_on_crash = bool(config.get("rebuild_runtime_on_crash", False))

    legacy_dir = get_userdata_dir()
    if profile_mode != "base_plus_runtime":
        ensure_dir(legacy_dir)
        if force_rebuild_runtime:
            log_warning("收到强制重建浏览器资料请求，当前为 legacy profile，仅修复崩溃标记")
        _clear_crash_flags(legacy_dir)
        return legacy_dir

    base_dir = get_userdata_base_dir()
    runtime_dir = get_userdata_runtime_dir()

    if not os.path.exists(base_dir):
        if os.path.exists(legacy_dir):
            log_info(f"初始化浏览器基础资料目录: {base_dir}")
            _copy_profile(legacy_dir, base_dir)
        else:
            ensure_dir(base_dir)

    runtime_exists = os.path.exists(runtime_dir)
    if runtime_exists and force_rebuild_runtime:
        log_warning("已禁用自动重建 runtime profile；继续保留并复用现有资料目录")
    runtime_crashed = runtime_exists and _profile_exit_type(runtime_dir).lower() == "crashed"
    if runtime_crashed:
        log_warning("检测到浏览器运行资料上次异常退出")

    if runtime_exists and runtime_crashed and rebuild_on_crash:
        log_warning("rebuild_runtime_on_crash 已忽略；仅修复退出标记并保留 Profile")

    if not runtime_exists:
        if os.path.exists(base_dir) and os.listdir(base_dir):
            log_info(f"从基础资料目录创建浏览器运行资料: {runtime_dir}")
            _copy_profile(base_dir, runtime_dir)
        else:
            ensure_dir(runtime_dir)

    _clear_crash_flags(runtime_dir)
    return runtime_dir
