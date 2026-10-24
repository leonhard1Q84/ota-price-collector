# -*- coding: utf-8 -*-
"""
日志模块
提供日志记录功能，同时输出到控制台和日志文件
"""
import os
from datetime import datetime
from path_utils import get_logs_dir, ensure_dir

# 日志文件句柄（全局变量）
_log_file = None
_log_filename = None

def init_logger():
    """初始化日志系统"""
    global _log_file, _log_filename
    
    # 确保logs目录存在
    log_dir = ensure_dir(get_logs_dir())
    
    # 生成日志文件名（带时间戳）
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _log_filename = os.path.join(log_dir, f"spider_{timestamp}.log")
    
    # 打开日志文件（追加模式，UTF-8编码）
    _log_file = open(_log_filename, "a", encoding="utf-8")
    
    return _log_filename

def close_logger():
    """关闭日志文件"""
    global _log_file
    if _log_file:
        _log_file.close()
        _log_file = None

def log(message, level="INFO"):
    """
    日志函数：同时输出到控制台（带时间前缀）和日志文件
    
    Args:
        message: 日志消息
        level: 日志级别（INFO, WARNING, ERROR, SUCCESS）
    """
    global _log_file
    
    # 获取当前时间
    now = datetime.now()
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    # 格式化消息（控制台输出带时间前缀）
    console_message = f"[{time_str}] {message}"
    
    # 格式化消息（日志文件输出带级别）
    log_message = f"[{time_str}] [{level}] {message}"
    
    # 输出到控制台
    print(console_message)
    
    # 输出到日志文件
    if _log_file:
        try:
            _log_file.write(log_message + "\n")
            _log_file.flush()  # 立即刷新到文件
        except Exception as e:
            # 如果写入日志文件失败，只输出到控制台
            print(f"[{time_str}] [ERROR] 写入日志文件失败: {e}")

def log_info(message):
    """信息日志"""
    log(message, "INFO")

def log_warning(message):
    """警告日志"""
    log(message, "WARNING")

def log_error(message):
    """错误日志"""
    log(message, "ERROR")

def log_success(message):
    """成功日志"""
    log(message, "SUCCESS")

