"""统一请求客户端

框架的心脏。把所有用例共用的横切逻辑收在这里，用例层只关心业务：
  · 相对路径自动拼接 base_url 与 api 前缀
  · 连接池复用 + 针对 5xx 的自动重试（只重试网络层抖动，不掩盖业务失败）
  · 统一超时，避免用例挂死拖垮整个回归
  · 请求响应双向落日志，并作为附件写进 Allure，失败时无需复现即可定位
"""
from __future__ import annotations

import json
import time
from typing import Any

import allure
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from common.logger import logger
from config.settings import Settings

_MAX_ATTACH_CHARS = 4000


class RequestClient:
    def __init__(self, base: str | None = None, timeout: int | None = None) -> None:
        st = Settings()
        self.base = (base or st.api_base).rstrip("/")
        self.timeout = timeout or st.timeout
        self.session = self._build_session()

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=2,
            backoff_factor=0.3,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT", "DELETE"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=20)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    # ---------------------------------------------------------------- 鉴权
    def set_token(self, token: str, scheme: str = "Bearer") -> "RequestClient":
        self.session.headers["Authorization"] = f"{scheme} {token}"
        return self

    def clear_token(self) -> "RequestClient":
        self.session.headers.pop("Authorization", None)
        return self

    # ---------------------------------------------------------------- 请求
    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = path if path.startswith("http") else f"{self.base}/{path.lstrip('/')}"
        kwargs.setdefault("timeout", self.timeout)

        logger.info(f"→ {method.upper()} {url}")
        if kwargs.get("params"):
            logger.debug(f"  params : {kwargs['params']}")
        if kwargs.get("json") is not None:
            logger.debug(f"  payload: {json.dumps(kwargs['json'], ensure_ascii=False)}")

        start = time.perf_counter()
        try:
            resp = self.session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            logger.error(f"✗ 请求异常 {method.upper()} {url}: {exc}")
            allure.attach(f"{method.upper()} {url}\n\n异常: {exc}",
                          name="HTTP 异常", attachment_type=allure.attachment_type.TEXT)
            raise
        cost_ms = (time.perf_counter() - start) * 1000

        logger.info(f"← {resp.status_code} {cost_ms:.0f}ms")
        logger.debug(f"  response: {self._pretty(resp)}")
        self._attach(method, url, kwargs, resp, cost_ms)
        return resp

    # ---------------------------------------------------------------- 报告
    @staticmethod
    def _pretty(resp: requests.Response) -> str:
        try:
            return json.dumps(resp.json(), ensure_ascii=False, indent=2)
        except ValueError:
            return resp.text[:_MAX_ATTACH_CHARS]

    def _attach(self, method: str, url: str, kwargs: dict[str, Any],
                resp: requests.Response, cost_ms: float) -> None:
        payload = kwargs.get("json") if kwargs.get("json") is not None else kwargs.get("data")
        safe_headers = {
            k: ("Bearer ***" if k.lower() == "authorization" else v)
            for k, v in self.session.headers.items()
        }
        detail = (
            f"{method.upper()} {url}\n"
            f"请求头: {json.dumps(safe_headers, ensure_ascii=False)}\n"
            f"查询参数: {kwargs.get('params')}\n"
            f"请求体: {json.dumps(payload, ensure_ascii=False, indent=2) if payload else '无'}\n"
            f"{'-' * 60}\n"
            f"状态码: {resp.status_code}    耗时: {cost_ms:.0f}ms\n"
            f"响应体:\n{self._pretty(resp)[:_MAX_ATTACH_CHARS]}"
        )
        allure.attach(detail, name=f"HTTP {method.upper()} {url.rsplit('/', 1)[-1]}",
                      attachment_type=allure.attachment_type.TEXT)

    # ---------------------------------------------------------------- 语法糖
    def get(self, path: str, **kw: Any) -> requests.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> requests.Response:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw: Any) -> requests.Response:
        return self.request("PUT", path, **kw)

    def delete(self, path: str, **kw: Any) -> requests.Response:
        return self.request("DELETE", path, **kw)
