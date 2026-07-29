"""订单模块

这个文件承载框架最有价值的几类校验：
  · 状态迁移法覆盖订单状态机的合法路径与非法路径
  · 幂等性：同一 Idempotency-Key 重复提交只能产生一笔订单
  · 库存一致性：用"下单前后的差值"断言，而不是断言绝对库存值，
    这样在并行执行或数据被其他用例改动时仍然稳定
  · 金额精度：支付类接口的高发缺陷区
"""
from __future__ import annotations

import allure
import pytest

from common import data_faker
from common.assertions import Assert
from common.yaml_util import load_cases, to_params

CASES = load_cases("order/create.yaml")
VALUES, IDS = to_params(CASES, ["sku", "quantity", "unit_price",
                                "expect_status", "expect_amount"])


@allure.epic("交易")
@allure.feature("订单")
class TestOrderCreate:

    @allure.story("下单接口数据驱动")
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.smoke
    @pytest.mark.p0
    @pytest.mark.parametrize("sku,quantity,unit_price,expect_status,expect_amount",
                             VALUES, ids=IDS)
    def test_create_order(self, order_api, sku, quantity, unit_price,
                          expect_status, expect_amount):
        resp = order_api.create(sku=sku, quantity=quantity, unit_price=unit_price)
        Assert.status(resp, expect_status)

        if expect_status == 201:
            Assert.code(resp, 0)
            Assert.json_path(resp, "$.data.amount", expect_amount)
            Assert.json_path(resp, "$.data.status", "pending")
            Assert.schema(resp, {"order_no": str, "sku": str, "quantity": int,
                                 "amount": float, "status": str, "created_at": int})
            # 用例自清：把库存还回去，避免后续用例受影响
            order_api.cancel(Assert.json_path_value(resp, "$.data.order_no"))

    @allure.story("下单需要鉴权")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.p0
    @pytest.mark.security
    def test_create_order_requires_auth(self, anon_order_api):
        resp = anon_order_api.create(sku="SKU-001", quantity=1, unit_price="19.99")
        Assert.status(resp, 401)

    @allure.story("下单成功后库存应准确扣减")
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.p0
    def test_stock_deducted_after_order(self, order_api, exclusive_sku):
        """用专属 SKU 而不是共享的 SKU-001

        库存是共享资源，用共享 SKU 做精确差值断言在并行执行时会被其他 worker
        的下单动作干扰，导致用例随机失败。资源隔离是这类用例唯一可靠的写法。
        """
        before = Assert.json_path_value(order_api.stock(exclusive_sku), "$.data.stock")

        resp = order_api.create(sku=exclusive_sku, quantity=2, unit_price="10.00")
        Assert.status(resp, 201)
        order_no = Assert.json_path_value(resp, "$.data.order_no")

        after = Assert.json_path_value(order_api.stock(exclusive_sku), "$.data.stock")
        assert after == before - 2, f"库存扣减不正确：下单前 {before}，下单后 {after}，应减少 2"

        with allure.step("取消订单后库存应还回"):
            Assert.status(order_api.cancel(order_no), 200)
            restored = Assert.json_path_value(order_api.stock(exclusive_sku), "$.data.stock")
            assert restored == before, f"取消后库存未还原：期望 {before}，实际 {restored}"

    @allure.story("库存恰好为1时下单成功且库存归零")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.p1
    def test_order_with_exact_stock(self, order_api, exclusive_sku):
        """库存边界：刚好够 → 成功；已归零 → 拒绝"""
        Assert.status(order_api.upsert_stock(exclusive_sku, 1), 200)

        resp = order_api.create(sku=exclusive_sku, quantity=1, unit_price="9.90")
        Assert.status(resp, 201)
        Assert.json_path(order_api.stock(exclusive_sku), "$.data.stock", 0)

        with allure.step("库存归零后再次下单应被拒绝"):
            again = order_api.create(sku=exclusive_sku, quantity=1, unit_price="9.90")
            Assert.status(again, 409)
            Assert.code(again, 3002)


@allure.epic("交易")
@allure.feature("订单状态机")
class TestOrderStateMachine:
    """用状态迁移法设计：先覆盖合法迁移路径，再逐条验证非法迁移必须被拒绝"""

    @allure.story("合法路径：待支付 → 已支付 → 已完成")
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.smoke
    @pytest.mark.p0
    def test_legal_transition_pay_then_finish(self, order_api, pending_order):
        order_no = pending_order["order_no"]

        Assert.json_path(order_api.pay(order_no), "$.data.status", "paid")
        Assert.json_path(order_api.finish(order_no), "$.data.status", "finished")

        with allure.step("回查确认最终状态已持久化"):
            Assert.json_path(order_api.detail(order_no), "$.data.status", "finished")

    @allure.story("合法路径：待支付 → 已取消")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.p0
    def test_legal_transition_cancel(self, order_api, pending_order):
        Assert.json_path(order_api.cancel(pending_order["order_no"]),
                         "$.data.status", "cancelled")

    @allure.story("非法迁移：已支付订单不能重复支付")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.p0
    def test_illegal_pay_twice(self, order_api, pending_order):
        order_no = pending_order["order_no"]
        Assert.status(order_api.pay(order_no), 200)

        resp = order_api.pay(order_no)
        Assert.status(resp, 409)
        Assert.code(resp, 3005)
        Assert.msg_contains(resp, "不允许")

    @allure.story("非法迁移：已支付订单不能取消")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.p1
    def test_illegal_cancel_after_paid(self, order_api, pending_order):
        order_no = pending_order["order_no"]
        order_api.pay(order_no)
        Assert.status(order_api.cancel(order_no), 409)

    @allure.story("非法迁移：已取消订单不能支付")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.p1
    def test_illegal_pay_after_cancelled(self, order_api, pending_order):
        order_no = pending_order["order_no"]
        order_api.cancel(order_no)
        Assert.status(order_api.pay(order_no), 409)

    @allure.story("非法迁移：待支付订单不能直接完成")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.p1
    def test_illegal_finish_without_pay(self, order_api, pending_order):
        Assert.status(order_api.finish(pending_order["order_no"]), 409)

    @allure.story("操作不存在的订单返回404")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.p2
    def test_operate_nonexistent_order(self, order_api):
        Assert.status(order_api.pay("NO_NOT_EXIST_123"), 404)
        Assert.status(order_api.detail("NO_NOT_EXIST_123"), 404)


@allure.epic("交易")
@allure.feature("幂等性")
class TestOrderIdempotency:

    @allure.story("相同幂等键重复下单只生成一笔订单")
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.p0
    def test_same_key_creates_single_order(self, order_api, exclusive_sku):
        """幂等的核心是"重复提交不产生副作用"，所以除了订单号一致，
        还必须验证库存只被扣减了一次——只断言订单号会漏掉重复扣减这类缺陷。
        """
        key = data_faker.idempotency_key()
        before = Assert.json_path_value(order_api.stock(exclusive_sku), "$.data.stock")

        first = order_api.create(exclusive_sku, 1, "19.99", idempotency_key=key)
        Assert.status(first, 201)
        first_no = Assert.json_path_value(first, "$.data.order_no")

        second = order_api.create(exclusive_sku, 1, "19.99", idempotency_key=key)
        Assert.status_in(second, (200, 201))
        second_no = Assert.json_path_value(second, "$.data.order_no")

        assert first_no == second_no, (
            f"幂等失效：两次请求生成了不同订单 {first_no} / {second_no}")

        after = Assert.json_path_value(order_api.stock(exclusive_sku), "$.data.stock")
        assert after == before - 1, (
            f"幂等失效：库存被重复扣减，下单前 {before}，两次请求后 {after}，应只减 1")

    @allure.story("不同幂等键应生成不同订单")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.p1
    def test_different_keys_create_different_orders(self, order_api, exclusive_sku):
        first = order_api.create(exclusive_sku, 1, "19.99",
                                 idempotency_key=data_faker.idempotency_key())
        second = order_api.create(exclusive_sku, 1, "19.99",
                                  idempotency_key=data_faker.idempotency_key())
        first_no = Assert.json_path_value(first, "$.data.order_no")
        second_no = Assert.json_path_value(second, "$.data.order_no")

        assert first_no != second_no, "不同幂等键不应复用同一笔订单"
