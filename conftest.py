"""全局 fixture

几个刻意的设计决定：

1. token 用 session 级 fixture 管理。整份用例集只登录一次，避免每条用例重复登录
   ——回归用例几百条时，这一项能省掉大量无意义请求和执行时间。

2. 用例依赖通过 fixture 链式传递（token → client → api → 业务数据），而不是
   靠执行顺序或全局变量。顺序依赖一旦开启并行执行就会崩，fixture 依赖不会。

3. 不做"全库清空再造数据"。本框架靠唯一后缀实现数据隔离，
   库存这类共享资源改用相对断言（下单前后的差值），因此支持 -n 并行安全执行。

4. 本地环境自动拉起被测服务，让新同学 clone 下来一条命令就能跑通，
   降低框架的接入成本。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import allure
import pytest
import requests

from apis.order_api import OrderApi
from apis.user_api import UserApi
from common import data_faker
from common.assertions import Assert
from common.logger import logger
from common.request_client import RequestClient
from config.settings import Settings

ROOT = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# 命令行参数
# --------------------------------------------------------------------------- #
def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--env", action="store", default=None,
                     help="运行环境，对应 config/config.yaml 中 envs 的 key，如 local / test")


# --------------------------------------------------------------------------- #
# 环境与被测服务
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def settings(request: pytest.FixtureRequest) -> Settings:
    Settings.reset()
    st = Settings(request.config.getoption("--env"))
    logger.info(f"===== 运行环境：{st} =====")
    return st


def _port_alive(host: str, port: int, timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _wait_health(base_url: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{base_url}/health", timeout=1).status_code == 200:
                return True
        except requests.RequestException:
            time.sleep(0.3)
    return False


def _kill_process_tree(process: subprocess.Popen) -> None:
    """终止整个进程树

    直接 terminate() 只会杀掉直接子进程。服务进程往下还会派生工作进程，
    残留的旧进程会继续占用端口，导致下一轮执行连到的是上一次的旧代码和旧数据
    ——表现为"改了代码却没生效"或"用例莫名其妙失败"，排查成本很高。
    """
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True, check=False)
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture(scope="session", autouse=True)
def mock_server(settings: Settings) -> None:
    """会话级前置校验：确认被测服务可用

    服务的启动与关闭统一由会话入口（pytest_configure / pytest_unconfigure）负责，
    不放在 fixture 里。原因是并行执行时每个 worker 都是独立进程、都会执行 session
    fixture，先跑完的 worker 会在 teardown 阶段把服务关掉，导致其他 worker 的用例
    大面积失败——这类失败看起来像产品问题，实际是框架自身的缺陷。
    """
    assert _wait_health(settings.base_url, timeout=20), (
        f"被测服务不可用：{settings.base_url}/health\n"
        f"local 环境下框架会自动拉起服务；其他环境请先确认服务已部署。")


# --------------------------------------------------------------------------- #
# 鉴权与客户端
# --------------------------------------------------------------------------- #
def _login_token(account: dict[str, str], role: str) -> str:
    resp = UserApi(RequestClient()).login(account["username"], account["password"])
    assert resp.status_code == 200, f"{role} 账号登录失败，后续用例无法执行：{resp.text}"
    token = Assert.json_path_value(resp, "$.data.token")
    logger.info(f"{role} 账号登录成功，token 已缓存至会话级")
    return token


@pytest.fixture(scope="session")
def token(settings: Settings) -> str:
    return _login_token(settings.account, "主")


@pytest.fixture(scope="session")
def token_b(settings: Settings) -> str:
    """第二个账号的 token，用于水平越权测试"""
    return _login_token(settings.account_b, "越权测试")


@pytest.fixture(scope="session")
def client(token: str) -> RequestClient:
    return RequestClient().set_token(token)


@pytest.fixture(scope="session")
def client_b(token_b: str) -> RequestClient:
    return RequestClient().set_token(token_b)


@pytest.fixture()
def anon_client() -> RequestClient:
    """未携带任何凭证的客户端，用于鉴权缺失场景与登录流程本身"""
    return RequestClient()


@pytest.fixture()
def anon_user_api(anon_client: RequestClient) -> UserApi:
    return UserApi(anon_client)


@pytest.fixture()
def anon_order_api(anon_client: RequestClient) -> OrderApi:
    return OrderApi(anon_client)


@pytest.fixture(scope="session")
def user_api(client: RequestClient) -> UserApi:
    return UserApi(client)


@pytest.fixture(scope="session")
def order_api(client: RequestClient) -> OrderApi:
    return OrderApi(client)


@pytest.fixture(scope="session")
def user_api_b(client_b: RequestClient) -> UserApi:
    return UserApi(client_b)


@pytest.fixture(scope="session")
def order_api_b(client_b: RequestClient) -> OrderApi:
    return OrderApi(client_b)


# --------------------------------------------------------------------------- #
# 业务数据（用例自建自清，避免相互污染）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def created_user(user_api: UserApi) -> dict:
    """创建一个临时用户，用例结束后删除"""
    payload = data_faker.new_user_payload()
    resp = user_api.create(payload)
    assert resp.status_code == 201, f"前置数据创建失败：{resp.text}"
    user = Assert.json_path_value(resp, "$.data")
    user["_password"] = payload["password"]

    yield user

    resp = user_api.delete(user["id"])
    if resp.status_code not in (200, 404):
        logger.warning(f"临时用户 {user['id']} 清理失败：{resp.status_code} {resp.text}")


@pytest.fixture()
def exclusive_sku(order_api: OrderApi) -> str:
    """申请一个本用例独享的 SKU

    需要精确断言"库存减少了几件"的用例不能使用共享 SKU：并行执行时其他 worker
    也在下单，两次查询之间的差值就不再等于本用例的操作量，用例会随机失败。
    """
    sku = f"SKU-AUTO-{data_faker.unique_suffix()}"
    resp = order_api.upsert_stock(sku, 50)
    assert resp.status_code == 200, f"专属 SKU 准备失败：{resp.text}"
    return sku


@pytest.fixture()
def pending_order(order_api: OrderApi) -> dict:
    """创建一个待支付订单，用例结束后若仍待支付则取消，把库存还回去"""
    resp = order_api.create(sku="SKU-001", quantity=1, unit_price="19.99")
    assert resp.status_code == 201, f"前置订单创建失败：{resp.text}"
    order = Assert.json_path_value(resp, "$.data")

    yield order

    detail = order_api.detail(order["order_no"])
    if detail.status_code == 200 and detail.json()["data"]["status"] == "pending":
        order_api.cancel(order["order_no"])


# --------------------------------------------------------------------------- #
# 执行观测
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _case_banner(request: pytest.FixtureRequest):
    logger.info(f"┌── 开始 {request.node.name}")
    yield
    logger.info(f"└── 结束 {request.node.name}")


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    """失败时把堆栈写进 Allure，报告里能直接看到失败原因而不用翻控制台"""
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        allure.attach(str(report.longrepr), name="失败详情",
                      attachment_type=allure.attachment_type.TEXT)


# --------------------------------------------------------------------------- #
# 会话入口：被测服务生命周期 + 报告环境信息
# --------------------------------------------------------------------------- #
_server_process: subprocess.Popen | None = None


def _is_xdist_worker(config: pytest.Config) -> bool:
    """xdist 的每个 worker 都是独立进程，也会走 configure，需要区分主进程"""
    return hasattr(config, "workerinput")


def _start_mock_server(st: Settings) -> subprocess.Popen | None:
    host = "127.0.0.1"
    port = int(st.base_url.rsplit(":", 1)[-1])
    if _port_alive(host, port):
        logger.info(f"{host}:{port} 已有服务在运行，直接复用")
        return None

    logger.info(f"启动被测服务 {host}:{port} ...")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_server.app:app",
         "--host", host, "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if not _wait_health(st.base_url):
        _kill_process_tree(process)
        raise RuntimeError(f"被测服务启动失败：{st.base_url}")
    logger.info("被测服务已就绪")
    return process


def _write_allure_environment(config: pytest.Config, st: Settings) -> None:
    """把环境信息写进报告，避免事后拿到一份报告却不知道它是在哪跑的"""
    alluredir = config.getoption("--alluredir", default=None)
    if not alluredir:
        return
    target = Path(alluredir)
    target.mkdir(parents=True, exist_ok=True)
    lines = [
        f"环境={st.env}",
        f"被测地址={st.api_base}",
        f"Python={sys.version.split()[0]}",
        f"执行机={socket.gethostname()}",
        f"执行方式={'CI' if os.getenv('GITHUB_ACTIONS') else '本地'}",
    ]
    (target / "environment.properties").write_text("\n".join(lines), encoding="utf-8")


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    global _server_process

    if _is_xdist_worker(config):
        # 并行时若每个 worker 都清一次报告目录，会互相删掉对方已写入的结果
        config.option.clean_alluredir = False
        return

    Settings.reset()
    st = Settings(config.getoption("--env"))
    _write_allure_environment(config, st)
    if st.auto_start_mock:
        _server_process = _start_mock_server(st)


def pytest_unconfigure(config: pytest.Config) -> None:
    global _server_process
    if _server_process is not None:
        logger.info("关闭被测服务")
        _kill_process_tree(_server_process)
        _server_process = None
