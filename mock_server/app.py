"""被测服务（Mock Server）

用 FastAPI 实现的一个最小但"有真实缺陷面"的业务服务，作为自动化框架的被测对象。
之所以自带被测服务，而不是直接打公网接口，是为了让整套用例在离线环境和 CI 里都能稳定运行。

刻意保留了这些真实业务特征，便于设计有意义的测试用例：
  · Bearer Token 鉴权，区分 401（未认证）与 403（已认证但越权）
  · 登录失败累计锁定
  · 订单状态机（待支付 → 已支付 → 已完成 / 已取消），非法迁移必须被拒
  · 基于 Idempotency-Key 的幂等下单
  · 库存扣减，防止超卖
  · 密码加盐哈希存储，响应体不回传敏感字段
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

app = FastAPI(title="Mock Business API", version="1.0.0")

API = "/api/v1"
MAX_LOGIN_FAIL = 3
SALT = "mock-salt"

_lock = threading.Lock()


def hash_password(raw: str) -> str:
    return hashlib.sha256(f"{SALT}{raw}".encode()).hexdigest()


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    """对外输出时剔除敏感字段"""
    return {k: v for k, v in user.items() if k != "password_hash"}


def ok(data: Any = None, msg: str = "success") -> dict[str, Any]:
    return {"code": 0, "msg": msg, "data": data}


def fail(http_status: int, code: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=http_status, content={"code": code, "msg": msg, "data": None})


# --------------------------------------------------------------------------- #
# 存储（内存态，进程内共享）
# --------------------------------------------------------------------------- #
class Store:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.users: dict[int, dict[str, Any]] = {}
        self.tokens: dict[str, int] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, str] = {}
        self.login_fails: dict[str, int] = {}
        # SKU-001 备足库存：它是被大量用例共享的资源，量太小会因为累计消耗
        # 导致后执行的用例随机失败（这类失败极易被误判成产品缺陷）
        # SKU-002 固定为 0，专门用于验证库存不足的拒绝逻辑
        self.stock: dict[str, int] = {"SKU-001": 100000, "SKU-002": 0}
        self._uid = 0
        self._order_seq = 0
        # 预置两个账号，用于鉴权与越权测试
        self.create_user("admin", "123456", "admin@test.com", role="admin")
        self.create_user("user_b", "123456", "userb@test.com", role="user")

    def next_uid(self) -> int:
        self._uid += 1
        return self._uid

    def next_order_no(self) -> str:
        self._order_seq += 1
        return f"NO{int(time.time())}{self._order_seq:04d}"

    def create_user(self, username: str, password: str, email: str,
                    role: str = "user", age: int | None = None) -> dict[str, Any]:
        uid = self.next_uid()
        user = {
            "id": uid,
            "username": username,
            "email": email,
            "age": age,
            "role": role,
            "status": 1,
            "password_hash": hash_password(password),
            "created_at": int(time.time()),
        }
        self.users[uid] = user
        return user

    def find_by_username(self, username: str) -> dict[str, Any] | None:
        return next((u for u in self.users.values() if u["username"] == username), None)

    def find_by_email(self, email: str) -> dict[str, Any] | None:
        return next((u for u in self.users.values() if u["email"] == email), None)


store = Store()


# --------------------------------------------------------------------------- #
# 鉴权
# --------------------------------------------------------------------------- #
def current_user(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供有效的认证信息")
    token = authorization.removeprefix("Bearer ").strip()
    uid = store.tokens.get(token)
    if uid is None:
        raise HTTPException(status_code=401, detail="token 无效或已过期")
    user = store.users.get(uid)
    if user is None or user["status"] != 1:
        raise HTTPException(status_code=401, detail="账号状态异常")
    return user


CurrentUser = Annotated[dict, Depends(current_user)]


@app.exception_handler(HTTPException)
async def http_exc_handler(_, exc: HTTPException):
    """统一错误结构，避免测试端要处理两种响应格式"""
    return JSONResponse(status_code=exc.status_code,
                        content={"code": exc.status_code * 10, "msg": exc.detail, "data": None})


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #
class LoginReq(BaseModel):
    username: str
    password: str


class UserCreateReq(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=16)
    email: EmailStr
    age: int | None = Field(default=None, ge=0, le=150)


class UserUpdateReq(BaseModel):
    email: EmailStr | None = None
    age: int | None = Field(default=None, ge=0, le=150)


class OrderCreateReq(BaseModel):
    sku: str
    quantity: int = Field(ge=1, le=99)
    unit_price: Decimal = Field(gt=0)


# --------------------------------------------------------------------------- #
# 基础
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return ok({"status": "up"})


@app.post(f"{API}/_reset", include_in_schema=False)
def reset():
    """仅供测试调用：重置内存数据，保证用例可重复执行"""
    with _lock:
        store.reset()
    return ok({"reset": True})


# --------------------------------------------------------------------------- #
# 登录
# --------------------------------------------------------------------------- #
@app.post(f"{API}/login")
def login(req: LoginReq):
    if not req.username.strip():
        return fail(400, 1002, "用户名不能为空")
    if not req.password:
        return fail(400, 1003, "密码不能为空")

    with _lock:
        if store.login_fails.get(req.username, 0) >= MAX_LOGIN_FAIL:
            return fail(423, 1004, "账号已被锁定，请稍后再试")

        user = store.find_by_username(req.username)
        # 不区分"账号不存在"与"密码错误"，避免账号枚举
        if user is None or user["password_hash"] != hash_password(req.password):
            store.login_fails[req.username] = store.login_fails.get(req.username, 0) + 1
            return fail(401, 1001, "用户名或密码错误")

        store.login_fails.pop(req.username, None)
        token = secrets.token_hex(16)
        store.tokens[token] = user["id"]

    return ok({"token": token, "user": public_user(user)})


@app.post(f"{API}/logout")
def logout(authorization: Annotated[str | None, Header()] = None):
    if authorization and authorization.startswith("Bearer "):
        store.tokens.pop(authorization.removeprefix("Bearer ").strip(), None)
    return ok({"logout": True})


# --------------------------------------------------------------------------- #
# 用户 CRUD
# --------------------------------------------------------------------------- #
@app.post(f"{API}/users", status_code=201)
def create_user(req: UserCreateReq, _: CurrentUser):
    with _lock:
        if store.find_by_username(req.username):
            return fail(409, 2001, "用户名已存在")
        if store.find_by_email(req.email):
            return fail(409, 2002, "邮箱已被注册")
        user = store.create_user(req.username, req.password, req.email, age=req.age)
    return ok(public_user(user))


@app.get(f"{API}/users")
def list_users(_: CurrentUser,
               page: int = Query(default=1, ge=1),
               size: int = Query(default=10, ge=1, le=100)):
    items = sorted(store.users.values(), key=lambda u: u["id"])
    start = (page - 1) * size
    return ok({
        "total": len(items),
        "page": page,
        "size": size,
        "items": [public_user(u) for u in items[start:start + size]],
    })


@app.get(f"{API}/users/{{uid}}")
def get_user(uid: int, me: CurrentUser):
    user = store.users.get(uid)
    if user is None:
        return fail(404, 2003, "用户不存在")
    # 水平越权拦截：普通用户只能看自己
    if me["role"] != "admin" and me["id"] != uid:
        return fail(403, 2004, "无权访问该资源")
    return ok(public_user(user))


@app.put(f"{API}/users/{{uid}}")
def update_user(uid: int, req: UserUpdateReq, me: CurrentUser):
    user = store.users.get(uid)
    if user is None:
        return fail(404, 2003, "用户不存在")
    if me["role"] != "admin" and me["id"] != uid:
        return fail(403, 2004, "无权修改该资源")
    with _lock:
        if req.email and req.email != user["email"] and store.find_by_email(req.email):
            return fail(409, 2002, "邮箱已被注册")
        if req.email is not None:
            user["email"] = req.email
        if req.age is not None:
            user["age"] = req.age
    return ok(public_user(user))


@app.delete(f"{API}/users/{{uid}}")
def delete_user(uid: int, me: CurrentUser):
    if me["role"] != "admin":
        return fail(403, 2004, "仅管理员可删除用户")
    with _lock:
        if store.users.pop(uid, None) is None:
            return fail(404, 2003, "用户不存在")
    return ok({"deleted": uid})


# --------------------------------------------------------------------------- #
# 订单：状态机 + 幂等 + 库存
# --------------------------------------------------------------------------- #
def money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@app.post(f"{API}/orders", status_code=201)
def create_order(req: OrderCreateReq, me: CurrentUser,
                 idempotency_key: Annotated[str | None, Header()] = None):
    with _lock:
        # 幂等：同一个 key 重复提交只创建一次
        if idempotency_key and idempotency_key in store.idempotency:
            existing = store.orders[store.idempotency[idempotency_key]]
            return ok(existing, msg="重复请求，返回已有订单")

        if req.sku not in store.stock:
            return fail(404, 3001, "商品不存在")
        if store.stock[req.sku] < req.quantity:
            return fail(409, 3002, "库存不足")

        store.stock[req.sku] -= req.quantity
        order_no = store.next_order_no()
        order = {
            "order_no": order_no,
            "user_id": me["id"],
            "sku": req.sku,
            "quantity": req.quantity,
            "unit_price": money(req.unit_price),
            "amount": money(req.unit_price * req.quantity),
            "status": "pending",
            "created_at": int(time.time()),
        }
        store.orders[order_no] = order
        if idempotency_key:
            store.idempotency[idempotency_key] = order_no
    return ok(order)


@app.get(f"{API}/orders/{{order_no}}")
def get_order(order_no: str, me: CurrentUser):
    order = store.orders.get(order_no)
    if order is None:
        return fail(404, 3003, "订单不存在")
    if me["role"] != "admin" and order["user_id"] != me["id"]:
        return fail(403, 3004, "无权访问该订单")
    return ok(order)


TRANSITIONS = {
    "pay": {"from": "pending", "to": "paid"},
    "cancel": {"from": "pending", "to": "cancelled"},
    "finish": {"from": "paid", "to": "finished"},
}


def transit(order_no: str, action: str, me: dict) -> Any:
    order = store.orders.get(order_no)
    if order is None:
        return fail(404, 3003, "订单不存在")
    if me["role"] != "admin" and order["user_id"] != me["id"]:
        return fail(403, 3004, "无权操作该订单")

    rule = TRANSITIONS[action]
    with _lock:
        if order["status"] != rule["from"]:
            return fail(409, 3005,
                        f"订单状态为 {order['status']}，不允许执行 {action}")
        order["status"] = rule["to"]
        if action == "cancel":
            store.stock[order["sku"]] += order["quantity"]
    return ok(order)


@app.post(f"{API}/orders/{{order_no}}/pay")
def pay_order(order_no: str, me: CurrentUser):
    return transit(order_no, "pay", me)


@app.post(f"{API}/orders/{{order_no}}/cancel")
def cancel_order(order_no: str, me: CurrentUser):
    return transit(order_no, "cancel", me)


@app.post(f"{API}/orders/{{order_no}}/finish")
def finish_order(order_no: str, me: CurrentUser):
    return transit(order_no, "finish", me)


@app.get(f"{API}/stock/{{sku}}")
def get_stock(sku: str, _: CurrentUser):
    if sku not in store.stock:
        return fail(404, 3001, "商品不存在")
    return ok({"sku": sku, "stock": store.stock[sku]})


class StockUpsertReq(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    stock: int = Field(ge=0)


@app.post(f"{API}/stock")
def upsert_stock(req: StockUpsertReq, _: CurrentUser):
    """测试数据准备接口：登记或重置某个 SKU 的库存

    真实项目里也需要这类接口。库存是被大量用例共享的资源，只要共享就会在并行
    执行时互相干扰，让用例随机失败。有了它，需要精确断言库存变化的用例就可以
    申请一个自己专属的 SKU，做到资源隔离。
    """
    with _lock:
        store.stock[req.sku] = req.stock
    return ok({"sku": req.sku, "stock": req.stock})
