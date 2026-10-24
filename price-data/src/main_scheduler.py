#!/usr/bin/env python3
import schedule
import time
import subprocess
import sys
import os

# === 配置 ===
SCRIPT_PATH = os.environ.get(
    "PRICE_DATA_CONTROLLER_SCRIPT",
    os.path.join(os.path.dirname(__file__), "main_controller.py"),
)
# ===========

def run_script():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 正在执行 {SCRIPT_PATH}")
    try:
        # 使用当前 Python 解释器执行目标脚本
        result = subprocess.run(
            [sys.executable, SCRIPT_PATH],
            cwd=os.path.dirname(SCRIPT_PATH),  # 确保工作目录正确
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 执行成功")
        else:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 执行失败:\n{result.stderr}")
    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 异常: {e}")

def main():
    # 设置每天执行时间
    schedule.every().day.at("11:15").do(run_script)

    print("调度器已启动，等待执行时间...")

    while True:
        schedule.run_pending()
        time.sleep(60)  # 每分钟检查一次


if __name__ == "__main__":
    main()
