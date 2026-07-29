"""测试数据生成

自动化最常见的隐性缺陷是用例之间互相污染：A 用例注册了 user_test，
B 用例再注册就报"已存在"，串行跑没问题、并行跑就随机红。
所以凡是有唯一约束的字段，都用带随机后缀的方式生成，做到用例自建自清、互不干扰。
"""
from __future__ import annotations

import random
import string
import time
import uuid

# 常用边界与恶意输入，供参数化直接引用
BOUNDARY_STRINGS: list[str] = [
    "",                                  # 空字符串
    " ",                                 # 纯空格
    "a",                                 # 最短
    "a" * 255,                           # 超长
    "中文用户名",                          # 多字节
    "user name",                         # 含空格
    "<script>alert(1)</script>",         # XSS
    "' OR 1=1 --",                       # SQL 注入
    "../../etc/passwd",                  # 路径穿越
    "null",                              # 字符串型 null
]


def unique_suffix() -> str:
    """时间戳 + 随机串，保证并行执行下也不重复"""
    return f"{int(time.time() * 1000)}{random.randint(100, 999)}"


def rand_username(prefix: str = "auto") -> str:
    return f"{prefix}_{unique_suffix()}"


def rand_email(prefix: str = "auto") -> str:
    return f"{prefix}_{unique_suffix()}@example.com"


def rand_password(length: int = 12) -> str:
    pool = string.ascii_letters + string.digits
    return "Aa1" + "".join(random.choices(pool, k=max(length - 3, 5)))


def rand_phone() -> str:
    prefix = random.choice(["138", "139", "150", "186", "199"])
    return prefix + "".join(random.choices(string.digits, k=8))


def idempotency_key() -> str:
    return uuid.uuid4().hex


def new_user_payload(**overrides) -> dict:
    payload = {
        "username": rand_username(),
        "password": rand_password(),
        "email": rand_email(),
        "age": random.randint(18, 60),
    }
    payload.update(overrides)
    return payload
