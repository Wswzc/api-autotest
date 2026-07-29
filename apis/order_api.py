"""订单域接口对象"""
from __future__ import annotations

from typing import Any

import allure
from requests import Response

from common.request_client import RequestClient


class OrderApi:
    def __init__(self, client: RequestClient) -> None:
        self.client = client

    @allure.step("创建订单 sku={sku} quantity={quantity}")
    def create(self, sku: str, quantity: int, unit_price: str,
               idempotency_key: str | None = None) -> Response:
        payload: dict[str, Any] = {"sku": sku, "quantity": quantity, "unit_price": unit_price}
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self.client.post("/orders", json=payload, headers=headers)

    @allure.step("查询订单 {order_no}")
    def detail(self, order_no: str) -> Response:
        return self.client.get(f"/orders/{order_no}")

    @allure.step("支付订单 {order_no}")
    def pay(self, order_no: str) -> Response:
        return self.client.post(f"/orders/{order_no}/pay")

    @allure.step("取消订单 {order_no}")
    def cancel(self, order_no: str) -> Response:
        return self.client.post(f"/orders/{order_no}/cancel")

    @allure.step("完成订单 {order_no}")
    def finish(self, order_no: str) -> Response:
        return self.client.post(f"/orders/{order_no}/finish")

    @allure.step("查询库存 {sku}")
    def stock(self, sku: str) -> Response:
        return self.client.get(f"/stock/{sku}")

    @allure.step("登记库存 {sku}={stock}")
    def upsert_stock(self, sku: str, stock: int) -> Response:
        return self.client.post("/stock", json={"sku": sku, "stock": stock})
