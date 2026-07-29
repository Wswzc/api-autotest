"""测试数据加载

选 yaml 而不是 Excel 的原因：yaml 是文本，能进 Git 做 diff 和 code review，
测试数据的每一次改动都有记录；Excel 是二进制，多人协作必冲突、改了也追不到。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_cases(relative_path: str) -> list[dict[str, Any]]:
    path = DATA_DIR / relative_path
    if not path.exists():
        raise FileNotFoundError(f"测试数据文件不存在：{path}")
    with open(path, encoding="utf-8") as f:
        cases = yaml.safe_load(f)
    if not isinstance(cases, list):
        raise ValueError(f"{relative_path} 顶层必须是列表，实际是 {type(cases).__name__}")
    return cases


def to_params(cases: list[dict[str, Any]], keys: list[str]) -> tuple[list[tuple], list[str]]:
    """把 yaml 用例转成 pytest.mark.parametrize 需要的 (argvalues, ids)

    ids 取每条用例的 title，这样报告里看到的是中文用例名而不是 case0/case1。
    """
    values: list[tuple] = []
    ids: list[str] = []
    for index, case in enumerate(cases):
        missing = [k for k in keys if k not in case]
        if missing:
            raise KeyError(f"第 {index + 1} 条用例缺少字段 {missing}：{case}")
        values.append(tuple(case[k] for k in keys))
        ids.append(case.get("title", f"case_{index + 1}"))
    return values, ids
