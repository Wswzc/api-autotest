"""分层断言

框架第一版曾用"全量比对响应体"的方式断言，结果时间戳、自增 ID 这类天然变动的字段
造成了大量误报，报告一红没人看，自动化就失去了意义。

现在改成分层校验，每层只断言真正该稳定的东西：
  1. 传输层 —— HTTP 状态码
  2. 业务层 —— 响应体里的业务 code
  3. 数据层 —— 关键字段的值（JSONPath 精确定位）
  4. 结构层 —— 字段是否存在、类型是否正确（应对不稳定值）
  5. 安全层 —— 敏感字段绝不能出现在响应里
  6. 性能层 —— 单接口响应时延红线
"""
from __future__ import annotations

from typing import Any

import allure
from jsonpath_ng.ext import parse
from requests import Response

_MAX_MSG = 800


def _body(resp: Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        raise AssertionError(f"响应不是合法 JSON，原文：{resp.text[:_MAX_MSG]}") from None


def _ctx(resp: Response) -> str:
    return f"\n实际响应({resp.status_code})：{resp.text[:_MAX_MSG]}"


class Assert:
    """所有断言失败信息都带上完整响应，避免为了看一眼响应体去重跑用例"""

    # ------------------------------------------------------------ 传输层
    @staticmethod
    @allure.step("断言 HTTP 状态码 == {expected}")
    def status(resp: Response, expected: int = 200) -> None:
        assert resp.status_code == expected, (
            f"HTTP 状态码不符：期望 {expected}，实际 {resp.status_code}{_ctx(resp)}")

    @staticmethod
    @allure.step("断言 HTTP 状态码属于 {expected}")
    def status_in(resp: Response, expected: tuple[int, ...]) -> None:
        assert resp.status_code in expected, (
            f"HTTP 状态码不符：期望属于 {expected}，实际 {resp.status_code}{_ctx(resp)}")

    # ------------------------------------------------------------ 业务层
    @staticmethod
    @allure.step("断言业务码 {key} == {expected}")
    def code(resp: Response, expected: int = 0, key: str = "code") -> None:
        body = _body(resp)
        assert isinstance(body, dict) and key in body, (
            f"响应体缺少业务码字段 {key!r}{_ctx(resp)}")
        assert body[key] == expected, (
            f"业务码不符：期望 {expected}，实际 {body[key]}，msg={body.get('msg')!r}{_ctx(resp)}")

    @staticmethod
    @allure.step("断言 msg 包含 {keyword}")
    def msg_contains(resp: Response, keyword: str, key: str = "msg") -> None:
        msg = str(_body(resp).get(key, ""))
        assert keyword in msg, f"提示语不符：期望包含 {keyword!r}，实际 {msg!r}"

    # ------------------------------------------------------------ 数据层
    @staticmethod
    @allure.step("断言 {expr} == {expected}")
    def json_path(resp: Response, expr: str, expected: Any) -> None:
        matches = [m.value for m in parse(expr).find(_body(resp))]
        assert matches, f"JSONPath {expr!r} 未匹配到任何节点{_ctx(resp)}"
        assert matches[0] == expected, (
            f"{expr} 不符：期望 {expected!r}，实际 {matches[0]!r}{_ctx(resp)}")

    @staticmethod
    def json_path_value(resp: Response, expr: str) -> Any:
        """提取值，用于把上游接口的返回传给下游用例"""
        matches = [m.value for m in parse(expr).find(_body(resp))]
        assert matches, f"JSONPath {expr!r} 未匹配到任何节点{_ctx(resp)}"
        return matches[0]

    @staticmethod
    @allure.step("断言列表 {expr} 长度 == {expected}")
    def list_length(resp: Response, expr: str, expected: int) -> None:
        value = Assert.json_path_value(resp, expr)
        assert isinstance(value, list), f"{expr} 不是列表，实际类型 {type(value).__name__}"
        assert len(value) == expected, (
            f"{expr} 长度不符：期望 {expected}，实际 {len(value)}")

    # ------------------------------------------------------------ 结构层
    @staticmethod
    @allure.step("断言存在字段 {fields}")
    def has_fields(resp: Response, fields: list[str], node: str = "$.data") -> None:
        data = Assert.json_path_value(resp, node)
        assert isinstance(data, dict), f"{node} 不是对象，无法校验字段{_ctx(resp)}"
        missing = [f for f in fields if f not in data]
        assert not missing, f"缺少字段 {missing}，实际字段 {list(data)}{_ctx(resp)}"

    @staticmethod
    @allure.step("断言字段类型 {expr} 是 {expect_type}")
    def field_type(resp: Response, expr: str, expect_type: type | tuple[type, ...]) -> None:
        """时间戳、自增 ID 这类值会变的字段，只校验类型不校验具体值"""
        value = Assert.json_path_value(resp, expr)
        assert isinstance(value, expect_type), (
            f"{expr} 类型不符：期望 {expect_type}，实际 {type(value).__name__}（值 {value!r}）")

    @staticmethod
    @allure.step("按 schema 校验字段类型")
    def schema(resp: Response, schema: dict[str, type | tuple[type, ...]],
               node: str = "$.data") -> None:
        data = Assert.json_path_value(resp, node)
        errors: list[str] = []
        for field, expect_type in schema.items():
            if field not in data:
                errors.append(f"{field}: 缺失")
            elif not isinstance(data[field], expect_type):
                errors.append(f"{field}: 期望 {expect_type}，实际 {type(data[field]).__name__}")
        assert not errors, "结构校验失败 -> " + "; ".join(errors) + _ctx(resp)

    # ------------------------------------------------------------ 安全层
    @staticmethod
    @allure.step("断言响应中不含敏感字段 {fields}")
    def no_sensitive_fields(resp: Response, fields: list[str]) -> None:
        """密码、密钥这类字段一旦出现在响应里就是安全缺陷，做全文匹配比逐层遍历更保险"""
        text = resp.text.lower()
        leaked = [f for f in fields if f.lower() in text]
        assert not leaked, f"响应中泄露了敏感字段 {leaked}{_ctx(resp)}"

    # ------------------------------------------------------------ 性能层
    @staticmethod
    @allure.step("断言响应时延 < {max_ms}ms")
    def response_time(resp: Response, max_ms: int = 1000) -> None:
        actual = resp.elapsed.total_seconds() * 1000
        assert actual < max_ms, f"响应超时：{actual:.0f}ms 超过阈值 {max_ms}ms"
