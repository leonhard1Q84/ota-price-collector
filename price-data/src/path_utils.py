# -*- coding: utf-8 -*-
"""
路径工具模块

说明：
- Windows 计划任务（任务计划程序）运行时，默认“起始于/工作目录”经常不是项目目录，
  相对路径（如 data、logs、urls/urls.json、config.json）会解析到 C:\\Windows\\System32 等位置，
  从而出现“找不到目录”或“无权限创建目录”等问题。
- 本模块统一将关键读写路径解析到 src 目录下，避免依赖当前工作目录。
"""

import os


_SRC_DIR = os.path.dirname(os.path.abspath(__file__))


def get_src_dir() -> str:
    """获取 src 目录的绝对路径。"""
    return _SRC_DIR


def resolve_in_src(relative_path: str) -> str:
    """将相对路径解析为 src 目录下的绝对路径。"""
    return os.path.normpath(os.path.join(_SRC_DIR, relative_path))


def resolve_env_or_src(env_name: str, relative_path: str) -> str:
    """Resolve an optional path override, falling back to the src directory."""
    value = os.environ.get(env_name, "").strip()
    if not value:
        return resolve_in_src(relative_path)
    value = os.path.expanduser(os.path.expandvars(value))
    if not os.path.isabs(value):
        value = os.path.join(_SRC_DIR, value)
    return os.path.normpath(value)


def resolve_existing_path(path: str) -> str:
    """
    解析文件路径：
    - 若传入路径已存在（绝对或相对当前工作目录），返回其绝对路径；
    - 否则返回 src 目录下的对应路径（即使文件尚不存在，也用于给出稳定落点）。
    """
    if not path:
        return path

    if os.path.isabs(path) and os.path.exists(path):
        return path

    if os.path.exists(path):
        return os.path.abspath(path)

    return resolve_in_src(path)


def ensure_dir(dir_path: str) -> str:
    """确保目录存在（不存在则创建），并返回目录路径。"""
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)
    return dir_path


def get_data_dir() -> str:
    """获取 CSV 输出目录（src/data）。"""
    return resolve_in_src("data")


def get_logs_dir() -> str:
    """获取日志目录（src/logs）。"""
    return resolve_in_src("logs")


def get_userdata_dir() -> str:
    """获取浏览器用户数据目录（src/userdata）。"""
    return resolve_env_or_src("KLOOK_USERDATA_DIR", "userdata")


def get_price_data_dir() -> str:
    """获取后处理输出目录（src/price_data）。"""
    return resolve_in_src("price_data")


def get_userdata_base_dir() -> str:
    """Return the stable browser profile template directory."""
    return resolve_env_or_src("KLOOK_USERDATA_BASE_DIR", "userdata_base")


def get_userdata_runtime_dir() -> str:
    """Return the disposable browser runtime profile directory."""
    return resolve_env_or_src("KLOOK_USERDATA_RUNTIME_DIR", "userdata_runtime")


def get_userdata_archive_dir() -> str:
    """Return the directory used to archive unhealthy runtime profiles."""
    return resolve_env_or_src("KLOOK_USERDATA_ARCHIVE_DIR", "userdata_archive")


def get_crawler_config_path() -> str:
    """获取运行信息输出文件路径（src/crawler_config.json）。"""
    return resolve_in_src("crawler_config.json")


def get_task_state_path() -> str:
    """获取任务状态持久化文件路径（src/task_state.json）。"""
    return resolve_in_src("task_state.json")


def get_screenshots_dir() -> str:
    """获取截图输出目录（src/logs/screenshots）。"""
    return resolve_in_src(os.path.join("logs", "screenshots"))
