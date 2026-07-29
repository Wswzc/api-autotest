"""统一执行入口

把"选环境、选用例范围、并行度、生成报告"收敛成一条命令，
这样本地执行、Jenkins 任务和 GitHub Actions 用的是同一个入口，
避免出现"本地能过、CI 上参数写错跑了别的用例"这类问题。

示例：
    python run.py                              # 本地环境跑全量
    python run.py --env test --mark smoke      # 测试环境只跑冒烟
    python run.py -n 4 --report                # 并行 4 进程并生成 Allure 报告
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULT_DIR = ROOT / "allure-results"
REPORT_DIR = ROOT / "allure-report"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="接口自动化执行入口")
    parser.add_argument("--env", default="local", help="运行环境：local / test")
    parser.add_argument("--mark", default="", help="用例标记：smoke / p0 / security 等")
    parser.add_argument("-k", "--keyword", default="", help="按用例名关键字筛选")
    parser.add_argument("-n", "--parallel", default="0", help="并行进程数，auto 表示按 CPU 核数")
    parser.add_argument("--reruns", default="0", help="失败重试次数（仅用于网络抖动）")
    parser.add_argument("--report", action="store_true", help="执行后生成 Allure 静态报告")
    return parser.parse_args()


def build_command(args: argparse.Namespace) -> list[str]:
    cmd = [sys.executable, "-m", "pytest", f"--env={args.env}"]
    if args.mark:
        cmd += ["-m", args.mark]
    if args.keyword:
        cmd += ["-k", args.keyword]
    if args.parallel not in ("0", ""):
        cmd += ["-n", args.parallel]
    if args.reruns not in ("0", ""):
        cmd += ["--reruns", args.reruns, "--reruns-delay", "1"]
    return cmd


def generate_report() -> None:
    if shutil.which("allure") is None:
        print("\n[跳过报告生成] 未检测到 allure 命令行工具。"
              "\n  安装方式：scoop install allure / brew install allure"
              "\n  或直接查看原始结果目录：allure-results")
        return
    subprocess.run(["allure", "generate", str(RESULT_DIR),
                    "-o", str(REPORT_DIR), "--clean"], check=False, shell=True)
    print(f"\n报告已生成：{REPORT_DIR / 'index.html'}")
    print("本地在线查看：allure serve allure-results")


def main() -> int:
    args = parse_args()
    cmd = build_command(args)

    print("=" * 70)
    print(f"环境    : {args.env}")
    print(f"用例范围: {args.mark or '全量'}")
    print(f"并行度  : {args.parallel if args.parallel != '0' else '串行'}")
    print(f"命令    : {' '.join(cmd)}")
    print("=" * 70)

    exit_code = subprocess.call(cmd, cwd=ROOT)

    if args.report:
        generate_report()

    print(f"\n执行结束，退出码 {exit_code}"
          f"（0 表示全部通过，非 0 会让 CI 判定为失败并阻止合并）")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
