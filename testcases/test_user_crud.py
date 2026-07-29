"""用户管理模块

覆盖：增删改查全链路、参数校验边界、唯一约束冲突、分页边界。
"""
from __future__ import annotations

import allure
import pytest

from common import data_faker
from common.assertions import Assert
from common.yaml_util import load_cases, to_params

INVALID_CASES = load_cases("user/create_invalid.yaml")
INVALID_VALUES, INVALID_IDS = to_params(INVALID_CASES, ["payload", "expect_status"])


@allure.epic("用户中心")
@allure.feature("用户管理")
class TestUserCrud:

    @allure.story("创建用户成功后可查询到")
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.smoke
    @pytest.mark.p0
    def test_create_then_query(self, user_api):
        payload = data_faker.new_user_payload()

        resp = user_api.create(payload)
        Assert.status(resp, 201)
        Assert.code(resp, 0)
        Assert.json_path(resp, "$.data.username", payload["username"])
        Assert.json_path(resp, "$.data.email", payload["email"])
        Assert.schema(resp, {"id": int, "username": str, "email": str,
                             "status": int, "created_at": int})
        Assert.no_sensitive_fields(resp, ["password_hash"])
        uid = Assert.json_path_value(resp, "$.data.id")

        with allure.step("回查详情，确认数据真的落库而不是接口只回显请求"):
            detail = user_api.detail(uid)
            Assert.status(detail, 200)
            Assert.json_path(detail, "$.data.username", payload["username"])
            Assert.json_path(detail, "$.data.age", payload["age"])

        user_api.delete(uid)

    @allure.story("创建用户参数校验")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.p1
    @pytest.mark.parametrize("payload,expect_status", INVALID_VALUES, ids=INVALID_IDS)
    def test_create_validation(self, user_api, payload, expect_status):
        """合法数据的用例会真的建出用户，因此给唯一字段加随机后缀并在断言后清理"""
        payload = dict(payload)
        if "username" in payload and expect_status == 201:
            payload["username"] = payload["username"][:24] + data_faker.unique_suffix()[-6:]
            payload["email"] = data_faker.rand_email()

        resp = user_api.create(payload)
        Assert.status(resp, expect_status)

        if resp.status_code == 201:
            user_api.delete(Assert.json_path_value(resp, "$.data.id"))

    @allure.story("用户名重复应被拒绝")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.p1
    def test_duplicate_username(self, user_api, created_user):
        payload = data_faker.new_user_payload(username=created_user["username"])
        resp = user_api.create(payload)
        Assert.status(resp, 409)
        Assert.code(resp, 2001)
        Assert.msg_contains(resp, "已存在")

    @allure.story("邮箱重复应被拒绝")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.p1
    def test_duplicate_email(self, user_api, created_user):
        payload = data_faker.new_user_payload(email=created_user["email"])
        resp = user_api.create(payload)
        Assert.status(resp, 409)
        Assert.code(resp, 2002)

    @allure.story("更新用户信息")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.p1
    def test_update_user(self, user_api, created_user):
        new_email = data_faker.rand_email("updated")

        resp = user_api.update(created_user["id"], {"email": new_email, "age": 30})
        Assert.status(resp, 200)
        Assert.json_path(resp, "$.data.email", new_email)
        Assert.json_path(resp, "$.data.age", 30)

        with allure.step("回查确认更新已持久化"):
            Assert.json_path(user_api.detail(created_user["id"]), "$.data.email", new_email)

    @allure.story("更新为已被占用的邮箱应被拒绝")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.p2
    def test_update_to_duplicate_email(self, user_api, created_user, settings):
        resp = user_api.update(created_user["id"], {"email": "admin@test.com"})
        Assert.status(resp, 409)
        Assert.code(resp, 2002)

    @allure.story("删除用户后再查应返回404")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.p1
    def test_delete_user(self, user_api):
        resp = user_api.create(data_faker.new_user_payload())
        uid = Assert.json_path_value(resp, "$.data.id")

        Assert.status(user_api.delete(uid), 200)

        with allure.step("已删除的用户再查应 404"):
            Assert.status(user_api.detail(uid), 404)

        with allure.step("重复删除应返回 404 而不是 500"):
            Assert.status(user_api.delete(uid), 404)

    @allure.story("查询不存在的用户")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.p2
    def test_get_nonexistent_user(self, user_api):
        resp = user_api.detail(99999999)
        Assert.status(resp, 404)
        Assert.code(resp, 2003)

    @allure.story("用户ID传非法类型")
    @allure.severity(allure.severity_level.MINOR)
    @pytest.mark.p2
    def test_get_user_with_invalid_id_type(self, client):
        resp = client.get("/users/abc")
        Assert.status(resp, 422)

    @allure.story("分页参数边界")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.p1
    @pytest.mark.parametrize("page,size,expect_status", [
        (1, 1, 200),
        (1, 100, 200),
        (0, 10, 422),
        (1, 0, 422),
        (1, 101, 422),
        (-1, 10, 422),
    ], ids=["首页1条", "单页上限100", "页码为0非法", "每页0条非法",
            "每页超上限非法", "页码为负非法"])
    def test_pagination_boundary(self, user_api, page, size, expect_status):
        resp = user_api.list(page=page, size=size)
        Assert.status(resp, expect_status)
        if expect_status == 200:
            Assert.json_path(resp, "$.data.page", page)
            Assert.json_path(resp, "$.data.size", size)
            items = Assert.json_path_value(resp, "$.data.items")
            assert len(items) <= size, f"返回条数 {len(items)} 超过每页上限 {size}"

    @allure.story("超出数据范围的页码返回空列表而非报错")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.p2
    def test_pagination_out_of_range(self, user_api):
        resp = user_api.list(page=99999, size=10)
        Assert.status(resp, 200)
        Assert.list_length(resp, "$.data.items", 0)
