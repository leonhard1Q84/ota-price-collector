"""
主控脚本：先生成配置，再运行爬虫。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

ACTIVE_REQUIRED_KEYS = [
    "BKS",
    "HKT",
    "NGO",
    "OKA",
    "SMV",
    "CTS",
    "FUK",
    "GTZY",
    "GXCZ",
    "JDM",
    "KHH",
    "NRT",
    "QCQ",
    "TPE",
]


def parse_picks(value):
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = []
        for item in value:
            raw_items.extend(parse_picks(item))
    else:
        raw_items = str(value).replace("，", ",").replace(";", ",").split(",")

    picks = []
    seen = set()
    for item in raw_items:
        pick = str(item).strip().upper()
        if pick and pick not in seen:
            picks.append(pick)
            seen.add(pick)
    return picks


def parse_url_selectors(value):
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]

    selectors = []
    seen = set()
    for item in raw_items:
        selector = str(item or "").strip()
        if selector and selector not in seen:
            selectors.append(selector)
            seen.add(selector)
    return selectors


def get_config_path():
    """获取配置文件路径"""
    return os.path.join(os.path.dirname(__file__), "config.json")


def emit_text_block(title, content):
    if not content:
        return

    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        safe_text = content.encode(encoding, errors="replace").decode(encoding, errors="replace")
    except Exception:
        safe_text = content.encode("utf-8", errors="replace").decode("utf-8", errors="replace")

    stream.write(f"{title}\n")
    stream.write(safe_text)
    if not safe_text.endswith("\n"):
        stream.write("\n")
    stream.flush()


def run_config_generator(picks=None, date_pairs=None, replace_date_pairs=False):
    """运行配置生成程序"""
    print("=" * 60)
    print("步骤1: 生成配置")
    print("=" * 60)

    try:
        from generate_config import main as generate_config_main

        config_path = generate_config_main(
            picks=picks,
            extra_date_pairs=date_pairs,
            replace_date_pairs=replace_date_pairs,
        )
        if config_path:
            print(f"\n配置生成完成，保存到: {config_path}")
            return True
        print("\n配置生成失败")
        return False
    except ImportError as exc:
        print(f"无法导入配置生成模块: {exc}")
        print("请确认 generate_config.py 在同一目录中")
        return False
    except Exception as exc:
        print(f"配置生成失败: {exc}")
        return False


def check_config_file(picks=None):
    """检查配置文件是否存在且有效"""
    config_file = get_config_path()

    if not os.path.exists(config_file):
        print(f"配置文件不存在: {config_file}")
        print(f"当前目录: {os.getcwd()}")
        print(f"脚本位置: {__file__}")
        print(f"尝试查找的文件绝对路径: {os.path.abspath(config_file)}")
        return False

    try:
        with open(config_file, "r", encoding="utf-8") as file:
            config = json.load(file)

        required_keys = picks or ACTIVE_REQUIRED_KEYS
        for key in required_keys:
            if key not in config:
                print(f"配置文件中缺少 {key} 配置")
                return False

        print(f"配置文件校验通过: {os.path.abspath(config_file)}")
        print("\n配置摘要:")
        for key in required_keys:
            if key in config:
                date_pairs = config[key].get("date_pairs", [])
                source_countries = config[key].get("source_countries", [])
                print(f"  {key}: {len(date_pairs)} 组日期对, {len(source_countries)} 个客源地")
        return True
    except json.JSONDecodeError:
        print(f"配置文件格式错误: {config_file}")
        return False
    except Exception as exc:
        print(f"检查配置文件时出错: {exc}")
        return False


def run_spiderv3(picks=None, url_selectors=None):
    """运行爬虫主程序"""
    print("\n" + "=" * 60)
    print("步骤2: 运行爬虫主程序 spiderv3.py")
    print("=" * 60)

    try:
        spiderv3_path = os.path.join(os.path.dirname(__file__), "spiderv3.py")
        if not os.path.exists(spiderv3_path):
            print(f"找不到爬虫程序文件: {spiderv3_path}")
            print(f"当前目录: {os.getcwd()}")
            print(f"脚本位置: {__file__}")
            return False

        config_path = get_config_path()
        print(f"配置文件位置: {os.path.abspath(config_path)}")
        print(f"爬虫程序位置: {os.path.abspath(spiderv3_path)}")
        print("正在启动爬虫程序...")

        command = [sys.executable, spiderv3_path]
        if picks:
            command.extend(["--picks", ",".join(picks)])
        for selector in parse_url_selectors(url_selectors):
            command.extend(["--url-selector", selector])

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.path.dirname(__file__),
        )

        if result.stdout:
            emit_text_block(
                "爬虫程序输出:",
                result.stdout[:1000] + "..." if len(result.stdout) > 1000 else result.stdout,
            )

        if result.stderr:
            emit_text_block(
                "爬虫程序错误输出:",
                result.stderr[:1000] + "..." if len(result.stderr) > 1000 else result.stderr,
            )

        if result.returncode == 0:
            print("\n爬虫程序运行完成")
            return True

        print(f"\n爬虫程序运行失败，返回码: {result.returncode}")
        return False
    except FileNotFoundError:
        print("找不到爬虫程序文件: spiderv3.py")
        return False
    except Exception as exc:
        print(f"运行爬虫程序时出错: {exc}")
        import traceback

        traceback.print_exc()
        return False


def ensure_directories():
    """确保必要的目录存在"""
    print("\n检查目录结构...")
    current_dir = os.path.dirname(__file__) or os.getcwd()
    directories = [
        os.path.join(current_dir, "data"),
        os.path.join(current_dir, "logs"),
        os.path.join(current_dir, "price_data"),
    ]

    for directory in directories:
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
            print(f"创建目录: {directory}")
        else:
            print(f"目录已存在: {directory}")
    return True


def run_bm_weekly_report(extra_args=None):
    """运行 BM 周均值汇总后处理。"""
    print("\n" + "=" * 60)
    print("步骤3: 汇总 BM 周均值报表")
    print("=" * 60)

    script_path = os.path.join(os.path.dirname(__file__), "bm_weekly_report.py")
    if not os.path.exists(script_path):
        print(f"找不到 BM 周报脚本: {script_path}")
        return False

    command = [sys.executable, script_path]
    if extra_args:
        command.extend(extra_args)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=os.path.dirname(__file__),
    )
    if result.stdout:
        emit_text_block("BM 周报输出:", result.stdout)
    if result.stderr:
        emit_text_block("BM 周报错误输出:", result.stderr)
    if result.returncode == 0:
        print("BM 周报处理完成")
        return True
    print(f"BM 周报处理失败，返回码: {result.returncode}")
    return False


def run_sequential(
    picks=None,
    date_pairs=None,
    url_selectors=None,
    replace_date_pairs=False,
    bm_weekly_report_args=None,
):
    """顺序执行：先配置生成，再爬虫"""
    print("=" * 60)
    print("Klook 租车数据爬虫 - 自动化流程")
    print("=" * 60)

    start_time = datetime.now()
    print(f"开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    if picks:
        print(f"本次仅运行: {', '.join(picks)}")
    if date_pairs:
        print(f"本次指定日期对数量: {len(date_pairs)}")
    if url_selectors:
        print(f"本次指定 URL 数量: {len(parse_url_selectors(url_selectors))}")

    current_dir = os.path.dirname(__file__) or os.getcwd()
    print(f"当前目录: {current_dir}")
    print(f"脚本位置: {__file__}")

    ensure_directories()

    print(f"\n{'=' * 60}")
    if not run_config_generator(
        picks=picks,
        date_pairs=date_pairs,
        replace_date_pairs=replace_date_pairs,
    ):
        print("配置生成失败，程序退出")
        return False

    time.sleep(5)

    print(f"\n{'=' * 60}")
    print("校验配置文件...")
    if not check_config_file(picks=picks):
        print("配置文件校验失败，程序退出")
        return False

    print(f"\n{'=' * 60}")
    if not run_spiderv3(picks=picks, url_selectors=url_selectors):
        print("爬虫程序运行失败")
        return False

    if bm_weekly_report_args is not None:
        if not run_bm_weekly_report(extra_args=bm_weekly_report_args):
            return False

    end_time = datetime.now()
    duration = end_time - start_time
    print("\n" + "=" * 60)
    print("流程完成")
    print("=" * 60)
    print(f"开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"结束时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"总耗时: {duration}")

    config_path = get_config_path()
    if os.path.exists(config_path):
        print(f"配置文件: {os.path.abspath(config_path)}")

    data_dir = os.path.join(current_dir, "data")
    if os.path.exists(data_dir):
        data_files = [name for name in os.listdir(data_dir) if name.endswith(".csv")]
        if data_files:
            print("数据文件:")
            for name in data_files:
                print(f"  - {name}")
        else:
            print("数据文件: 无")

    return True


def main(argv=None):
    """主函数"""
    parser = argparse.ArgumentParser(description="Klook price data runner")
    parser.add_argument("--picks", default=None, help="只运行指定代码，例如 BKS 或 BKS,FUK")
    parser.add_argument(
        "--date-pair",
        dest="date_pairs",
        action="append",
        nargs=2,
        metavar=("P_DATE", "D_DATE"),
        default=None,
        help="Add a specific pickup/dropoff datetime pair",
    )
    parser.add_argument(
        "--date-pairs-only",
        dest="replace_date_pairs",
        action="store_true",
        help="Use only the provided --date-pair values",
    )
    parser.add_argument(
        "--url-selector",
        dest="url_selectors",
        action="append",
        default=None,
        help="Run only the URL entries whose normalized URL matches this selector",
    )
    parser.add_argument(
        "--bm-weekly-report",
        action="store_true",
        help="爬虫结束后生成 BM 周均值报表（默认 dry-run，除非额外传 --bm-write-feishu）",
    )
    parser.add_argument(
        "--bm-write-feishu",
        action="store_true",
        help="BM 周均值报表正式写入飞书",
    )
    parser.add_argument("--bm-run-date", default=None, help="BM 周报执行日期 YYYY-MM-DD")
    parser.add_argument("--bm-week-start", default=None, help="BM 周报统计开始日期 YYYY-MM-DD")
    parser.add_argument("--bm-week-end", default=None, help="BM 周报统计结束日期 YYYY-MM-DD")
    parser.add_argument("--bm-week-label", default=None, help="BM 周报周标签，例如 W25")
    args = parser.parse_args(argv)
    picks = parse_picks(args.picks)
    url_selectors = parse_url_selectors(args.url_selectors)
    bm_weekly_report_args = None
    if args.bm_weekly_report:
        bm_weekly_report_args = []
        if args.bm_write_feishu:
            bm_weekly_report_args.append("--write-feishu")
        else:
            bm_weekly_report_args.append("--dry-run")
        for option_name, option_value in (
            ("--run-date", args.bm_run_date),
            ("--week-start", args.bm_week_start),
            ("--week-end", args.bm_week_end),
            ("--week-label", args.bm_week_label),
        ):
            if option_value:
                bm_weekly_report_args.extend([option_name, option_value])

    try:
        success = run_sequential(
            picks=picks,
            date_pairs=args.date_pairs,
            url_selectors=url_selectors,
            replace_date_pairs=args.replace_date_pairs,
            bm_weekly_report_args=bm_weekly_report_args,
        )
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n程序被用户中断")
        sys.exit(1)
    except Exception as exc:
        print(f"\n程序运行出错: {exc}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
