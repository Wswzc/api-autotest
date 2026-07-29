"""登录模块

覆盖：正常登录、凭证错误、参数边界、注入类输入、账号锁定、响应结构与敏感字段。
"""
from __future__ import annotations

import allure
import pytest

from common import data_faker
from common.assertions import Assert
from common.yaml_util import load_cases, to_params

CASES = load_cases("user/login.yaml")
VALUES, IDS = to_params(CASES, ["username", "password", "expect_status", "expect_code"])


@allure.epic("用户中心")
@allure.feature("登录")
class TestLogin:

    @allure.story("登录接口数据驱动")
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.smoke
    @pytest.mark.p0
    @pytest.mark.parametrize("username,password,expect_status,expect_code", VALUES, ids=IDS)
    def test_login(self, anon_user_api, username, password, expect_status, expect_code):
        resp = anon_user_api.login(username, password)
        Assert.status(resp, expect_status)
        Assert.code(resp, expect_code)

    @allure.story("登录成功的响应结构")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.p0
    def test_login_response_schema(self, anon_user_api, settings):
        resp = anon_user_api.login(**settings.account)
        Assert.status(resp, 200)
        Assert.code(resp, 0)
        Assert.has_fields(resp, ["token", "user"])
        # token 每次登录都不同，只校验类型与非空，不校验具体值
        Assert.field_type(resp, "$.data.token", str)
        assert Assert.json_path_value(resp, "$.data.token"), "token 不应为空"
        Assert.schema(resp, {"id": int, "username": str, "email": str, "role": str},
                      node="$.data.user")
        Assert.response_time(resp, settings.max_response_ms)

    @allure.story("登录响应不得泄露密码相关字段")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.p0
    @pytest.mark.security
    def test_login_no_password_leak(self, anon_user_api, settings):
        resp = anon_user_api.login(**settings.account)
        Assert.status(resp, 200)
        Assert.no_sensitive_fields(resp, ["password", "password_hash", "salt"])

    @allure.story("连续登录失败达到阈值后账号锁定")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.p1
    def test_account_lock_after_repeated_failures(self, anon_user_api):
        """用随机用户名做锁定测试，避免锁掉公共账号影响其他用例

        锁定计数按用户名维度累计，不存在的用户名同样能验证这条规则，
        这样既覆盖了业务逻辑，又不会污染共享数据。
        """
        username = data_faker.rand_username("lock")

        for _ in range(3):
            resp = anon_user_api.login(username, "wrong_password")
            Assert.status(resp, 401)
            Assert.code(resp, 1001)

        with allure.step("第 4 次登录应返回账号锁定"):
            resp = anon_user_api.login(username, "wrong_password")
            Assert.status(resp, 423)
            Assert.code(resp, 1004)
            Assert.msg_contains(resp, "锁定")

    @allure.story("登出后原 token 立即失效")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.p1
    def test_token_invalid_after_logout(self, anon_client, anon_user_api, settings):
        resp = anon_user_api.login(**settings.account)
        token = Assert.json_path_value(resp, "$.data.token")

        anon_client.set_token(token)
        Assert.status(anon_user_api.list(), 200)

        anon_user_api.logout()
        with allure.step("登出后用旧 token 访问应返回 401"):
            Assert.status(anon_user_api.list(), 401)
