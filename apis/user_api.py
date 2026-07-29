"""用户域接口对象

API Object 层的职责：把"接口怎么调"收敛在这里（路径、方法、参数结构），
用例层只表达"业务上做了什么"。接口路径变更时只改这一处，不用动几十条用例。
"""
from __future__ import annotations

from typing import Any

import allure
from requests import Response

from common.request_client import RequestClient


class UserApi:
    def __init__(self, client: RequestClient) -> None:
        self.client = client

    @allure.step("登录：username={username}")
    def login(self, username: str, password: str) -> Response:
        return self.client.post("/login", json={"username": username, "password": password})

    @allure.step("登出")
    def logout(self) -> Response:
        return self.client.post("/logout")

    @allure.step("创建用户")
    def create(self, payload: dict[str, Any]) -> Response:
        return self.client.post("/users", json=payload)

    @allure.step("查询用户列表 page={page} size={size}")
    def list(self, page: int = 1, size: int = 10) -> Response:
        return self.client.get("/users", params={"page": page, "size": size})

    @allure.step("查询用户详情 uid={uid}")
    def detail(self, uid: int) -> Response:
        return self.client.get(f"/users/{uid}")

    @allure.step("更新用户 uid={uid}")
    def update(self, uid: int, payload: dict[str, Any]) -> Response:
        return self.client.put(f"/users/{uid}", json=payload)

    @allure.step("删除用户 uid={uid}")
    def delete(self, uid: int) -> Response:
        return self.client.delete(f"/users/{uid}")
