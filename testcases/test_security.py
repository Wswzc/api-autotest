"""鉴权与安全专项

这是接口测试相比 UI 测试不可替代的价值所在：前端能做的校验都能被绕过，
只有直接打接口才能验证服务端是否真的做了鉴权与权限判定。

覆盖：认证缺失、凭证伪造、水平越权、垂直越权、注入类输入、敏感信息泄露。
"""
from __future__ import annotations

import allure
import pytest

from common import data_faker
from common.assertions import Assert
from common.data_faker import BOUNDARY_STRINGS


@allure.epic("安全")
@allure.feature("认证")
@pytest.mark.security
class TestAuthentication:

    @allure.story("未携带凭证访问受保护接口应返回401")
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.p0
    @pytest.mark.parametrize("method,path", [
        ("GET", "/users"),
        ("GET", "/users/1"),
        ("POST", "/users"),
        ("PUT", "/users/1"),
        ("DELETE", "/users/1"),
        ("POST", "/orders"),
        ("GET", "/orders/NO123"),
        ("GET", "/stock/SKU-001"),
    ], ids=lambda v: str(v))
    def test_protected_endpoints_require_auth(self, anon_client, method, path):
        resp = anon_client.request(method, path, json={})
        Assert.status(resp, 401)

    @allure.story("伪造或畸形的 token 应返回401")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.p0
    @pytest.mark.parametrize("header_value", [
        "Bearer forged_token_value",
        "Bearer ",
        "Basic YWRtaW46MTIzNDU2",
        "forged_token_without_scheme",
        "bearer lowercase_scheme",
        "",
    ], ids=["伪造token", "空token", "错误认证方案", "缺少方案前缀",
            "小写方案前缀", "空请求头"])
    def test_invalid_token_rejected(self, anon_client, header_value):
        resp = anon_client.get("/users", headers={"Authorization": header_value})
        Assert.status(resp, 401)


@allure.epic("安全")
@allure.feature("权限")
@pytest.mark.security
class TestAuthorization:

    @allure.story("水平越权：普通用户不能读取他人信息")
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.p0
    def test_horizontal_privilege_read(self, user_api_b):
        """用 B 账号的 token 访问 admin（uid=1）的资料，必须被拒绝

        这类缺陷在 UI 上通常发现不了，因为前端根本不会渲染这个入口。
        """
        resp = user_api_b.detail(1)
        Assert.status(resp, 403)
        Assert.code(resp, 2004)
        Assert.no_sensitive_fields(resp, ["password_hash", "admin@test.com"])

    @allure.story("水平越权：普通用户不能修改他人信息")
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.p0
    def test_horizontal_privilege_write(self, user_api_b):
        resp = user_api_b.update(1, {"age": 99})
        Assert.status(resp, 403)

    @allure.story("垂直越权：普通用户不能调用管理员接口")
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.p0
    def test_vertical_privilege(self, user_api, user_api_b):
        created = user_api.create(data_faker.new_user_payload())
        uid = Assert.json_path_value(created, "$.data.id")

        with allure.step("普通用户执行删除应返回 403"):
            Assert.status(user_api_b.delete(uid), 403)

        with allure.step("确认目标用户确实没有被删除"):
            Assert.status(user_api.detail(uid), 200)

        user_api.delete(uid)

    @allure.story("水平越权：用户不能查看他人订单")
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.p0
    def test_cannot_read_others_order(self, pending_order, order_api_b):
        resp = order_api_b.detail(pending_order["order_no"])
        Assert.status(resp, 403)
        Assert.code(resp, 3004)

    @allure.story("水平越权：用户不能操作他人订单")
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.p0
    def test_cannot_operate_others_order(self, pending_order, order_api_b, order_api):
        order_no = pending_order["order_no"]

        Assert.status(order_api_b.pay(order_no), 403)
        Assert.status(order_api_b.cancel(order_no), 403)

        with allure.step("确认订单状态未被他人改变"):
            Assert.json_path(order_api.detail(order_no), "$.data.status", "pending")


@allure.epic("安全")
@allure.feature("恶意输入")
@pytest.mark.security
class TestMaliciousInput:

    @allure.story("恶意字符串作为用户名不应造成服务异常")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.p1
    @pytest.mark.parametrize("payload_value", BOUNDARY_STRINGS,
                             ids=[f"输入_{i}" for i in range(len(BOUNDARY_STRINGS))])
    def test_malicious_username_no_500(self, user_api, payload_value):
        """核心断言是"绝不能返回 5xx"

        注入类输入的正确表现是被参数校验或唯一约束拦下（4xx），
        一旦出现 500 说明输入直达了底层，是明确的安全信号。
        """
        resp = user_api.create(data_faker.new_user_payload(username=payload_value))
        assert resp.status_code < 500, (
            f"恶意输入触发服务端异常 {resp.status_code}，输入={payload_value!r}，"
            f"响应={resp.text[:300]}")

        if resp.status_code == 201:
            uid = Assert.json_path_value(resp, "$.data.id")
            with allure.step("若被接受，需确认存储的是原文而未被当作代码执行"):
                Assert.json_path(user_api.detail(uid), "$.data.username", payload_value)
            user_api.delete(uid)

    @allure.story("路径参数注入不应造成服务异常")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.p2
    @pytest.mark.parametrize("raw", ["1 OR 1=1", "1;DROP TABLE users", "../../admin", "%00"],
                             ids=["SQL注入", "拼接删表", "路径穿越", "空字节"])
    def test_path_injection_no_500(self, client, raw):
        resp = client.get(f"/users/{raw}")
        assert resp.status_code < 500, f"路径注入触发 {resp.status_code}：{resp.text[:200]}"

    @allure.story("接口响应不得包含敏感字段")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.p1
    def test_no_sensitive_field_in_list(self, user_api):
        resp = user_api.list(page=1, size=100)
        Assert.status(resp, 200)
        Assert.no_sensitive_fields(resp, ["password_hash", "mock-salt"])
