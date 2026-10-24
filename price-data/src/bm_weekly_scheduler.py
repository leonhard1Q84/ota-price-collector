# -*- coding: utf-8 -*-
"""Klook BM 周均值定时调度入口。

默认每周五 10:00 执行一次 BM 周均值汇总，并写入飞书报表。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import schedule


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = Path(__file__).resolve().parent
REPORT_SCRIPT = SRC_DIR / "bm_weekly_report.py"


def build_report_command(args: argparse.Namespace) -> List[str]:
    cmd = [sys.executable, str(REPORT_SCRIPT), "--write-feishu"]
    if args.config:
        cmd.extend(["--config", args.config])
    if args.run_date:
        cmd.extend(["--run-date", args.run_date])
    if args.week_start:
        cmd.extend(["--week-start", args.week_start])
    if args.week_end:
        cmd.extend(["--week-end", args.week_end])
    if args.week_label:
        cmd.extend(["--week-label", args.week_label])
    if args.allow_empty:
        cmd.append("--allow-empty")
    if args.no_insert:
        cmd.append("--no-insert")
    if args.dry_run:
        cmd.append("--dry-run")
    return cmd


def run_command_streaming(cmd: List[str]) -> tuple[int, str]:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 启动任务: {' '.join(cmd)}", flush=True)
    process = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output_lines: List[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        output_lines.append(line)
        print(line, end="", flush=True)
    returncode = process.wait()
    print(
        f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 任务结束: returncode={returncode}",
        flush=True,
    )
    return returncode, "".join(output_lines)


def extract_line(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def extract_output_files(text: str) -> List[str]:
    files: List[str] = []
    for label in ("本地 Klook 站点结果", "本地 Klook 地区结果", "本地 城市BM 结果"):
        value = extract_line(rf"^{re.escape(label)}:\s*(.+)$", text)
        if value:
            files.append(value)
    return files


def output_tail(text: str, max_lines: int = 8) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-max_lines:])


def load_feishu_notifier():
    src_dir = str(SRC_DIR)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    import feishu_notifier  # type: ignore

    return feishu_notifier


def build_at_text(notifier) -> str:
    at_user_id = notifier._读取配置(notifier.飞书通知提醒用户环境变量)
    at_user_name = notifier._读取配置(notifier.飞书通知提醒用户名环境变量) or "运营同学"
    return f'<at user_id="{at_user_id}">{at_user_name}</at> ' if at_user_id else ""


def send_feishu_text(text: str) -> bool:
    notifier = load_feishu_notifier()
    app_id = notifier._读取配置(notifier.飞书应用ID环境变量)
    app_secret = notifier._读取配置(notifier.飞书应用密钥环境变量)
    chat_id = notifier._读取配置(notifier.飞书通知群环境变量)
    webhook_url = notifier._读取配置(notifier.飞书Webhook环境变量)

    if app_id and app_secret and chat_id:
        token = notifier._获取租户访问令牌(app_id, app_secret)
        notifier._发送消息(token, chat_id, "text", {"text": text})
        return True

    if webhook_url:
        notifier._发送_webhook文本(webhook_url, text)
        return True

    return False


def build_success_message(output: str, dry_run: bool, notifier) -> str:
    week_range = extract_line(r"^统计周期:\s*(.+)$", output)
    week_label = extract_line(r"^周标签:\s*(.+)$", output)
    discovered = extract_line(r"^解析到的站点:\s*(.+)$", output)
    files = extract_output_files(output)
    warnings = re.findall(r"^WARNING .+$", output, re.MULTILINE)
    mode_text = "dry-run，本次未写入飞书" if dry_run else "已写入飞书"

    lines = [
        f"{build_at_text(notifier)}Klook BM 周均值汇总执行成功",
        f"执行时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"统计周期：{week_range or '-'}",
        f"周标签：{week_label or '-'}",
        f"写入状态：{mode_text}",
    ]
    if discovered:
        lines.append(f"解析站点：{discovered}")
    if files:
        lines.append("本地结果文件：")
        lines.extend(f"- {Path(path).name}" for path in files)
    if warnings:
        lines.append("注意事项：")
        lines.extend(warnings[:5])
        if len(warnings) > 5:
            lines.append(f"... 还有 {len(warnings) - 5} 条 WARNING")
    return "\n".join(lines)


def build_failure_message(output: str, returncode: int, notifier) -> str:
    return "\n".join(
        [
            "Klook BM 周均值汇总执行失败",
            f"执行时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
            f"退出码：{returncode}",
            "最后输出：",
            output_tail(output) or "-",
        ]
    )


def run_report_job(args: argparse.Namespace) -> int:
    cmd = build_report_command(args)
    returncode, output = run_command_streaming(cmd)

    if returncode == 0 and not args.no_notify:
        try:
            notifier = load_feishu_notifier()
            sent = send_feishu_text(build_success_message(output, args.dry_run, notifier))
            if sent:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 飞书执行结果通知已发送", flush=True)
            else:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 未配置飞书通知，已跳过发送", flush=True)
        except Exception as exc:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 飞书通知发送失败: {exc}", flush=True)
    elif returncode != 0 and args.notify_failure and not args.no_notify:
        try:
            notifier = load_feishu_notifier()
            sent = notifier.发送告警(build_failure_message(output, returncode, notifier))
            if sent:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 飞书失败通知已发送到告警群", flush=True)
            else:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 未配置飞书告警通知，已跳过发送", flush=True)
        except Exception as exc:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 飞书失败通知发送失败: {exc}", flush=True)

    return returncode


def run_scheduler(args: argparse.Namespace) -> int:
    schedule.every().friday.at(args.at).do(run_report_job, args)
    print("=" * 80, flush=True)
    print("Klook BM 周均值调度已启动", flush=True)
    print(f"每周五 {args.at} 执行 BM 周均值汇总并写入飞书", flush=True)
    print(f"工作目录: {PROJECT_ROOT}", flush=True)
    print("启动后只等待下一次定时，不会自动补跑已错过的时间点。", flush=True)
    print("=" * 80, flush=True)

    while True:
        schedule.run_pending()
        time.sleep(max(5, args.poll_seconds))


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Klook BM 周均值定时调度入口")
    parser.add_argument("--at", default="10:00", help="每周五执行时间，默认 10:00")
    parser.add_argument("--poll-seconds", type=int, default=60, help="调度检查间隔秒数，默认 60")
    parser.add_argument("--run-now", action="store_true", help="立即执行一次后退出，用于测试")
    parser.add_argument("--dry-run", action="store_true", help="只生成本地文件，不写飞书")
    parser.add_argument("--no-notify", action="store_true", help="不发送飞书群通知")
    parser.add_argument("--notify-failure", action="store_true", help="任务失败时也发送飞书群通知")
    parser.add_argument("--config", default="", help="透传给 bm_weekly_report.py 的配置文件路径")
    parser.add_argument("--run-date", default="", help="透传执行日期 YYYY-MM-DD；正式调度默认不传")
    parser.add_argument("--week-start", default="", help="透传统计开始日期 YYYY-MM-DD")
    parser.add_argument("--week-end", default="", help="透传统计结束日期 YYYY-MM-DD")
    parser.add_argument("--week-label", default="", help="透传周标签，例如 W25")
    parser.add_argument("--allow-empty", action="store_true", help="透传 allow-empty")
    parser.add_argument("--no-insert", action="store_true", help="透传 no-insert")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.run_now:
        return run_report_job(args)
    return run_scheduler(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已停止。")
        raise SystemExit(130)
