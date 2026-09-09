"""鉴权服务层:密码哈希、用户校验、token 签发/校验/撤销、首次播种管理员。

设计约束(AGENTS.md):
- SQLite = 事实源,所有读写经 SessionStore(单写者);本模块不直连 sqlite。
- 密码哈希用标准库 hashlib.pbkdf2_hmac(sha256,盐 16B,迭代 100000),不引入第三方依赖。
- 校验用 hmac.compare_digest 做恒定时间比较,防时序侧信道。
- token = secrets.token_urlsafe(32);remember → 30 天,否则 8 小时。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import traceback
from dataclasses import dataclass
from datetime import timedelta

from app.session import events
from app.util.time import beijing_now, beijing_now_dt, parse_beijing

_PBKDF2_ROUNDS = 100_000
_REMEMBER_DAYS = 30
_SESSION_HOURS = 8


def _utcnow() -> str:
    """兼容旧调用:统一返回北京时间 YYYY-MM-DD HH:mm:ss。"""
    return beijing_now()


def _expiry(remember: bool) -> str:
    delta = timedelta(days=_REMEMBER_DAYS) if remember else timedelta(hours=_SESSION_HOURS)
    return (beijing_now_dt() + delta).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def hash_password(password: str) -> tuple[str, str]:
    """返回 (salt_hex, password_hash_hex)。"""
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return salt.hex(), h.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return hmac.compare_digest(actual, expected)


@dataclass
class LoginResult:
    token: str
    expires_at: str
    username: str
    display_name: str
    role: str = "agent"


def create_user(store, username: str, password: str, display_name: str = "", role: str = "agent") -> dict:
    salt_hex, hash_hex = hash_password(password)
    return store.create_user(username, hash_hex, salt_hex, display_name, role=role)


def authenticate(store, username: str, password: str) -> dict | None:
    """校验账号密码;成功返回用户 dict,失败返回 None(账号不存在/密码错/已禁用均返回 None,不泄露差异)。"""
    user = store.get_user(username)
    if user is None:
        # 仍做一次哈希运算,均衡时序,避免"账号不存在"可被枚举
        hash_password("probe")
        return None
    if user.get("disabled"):
        return None
    if not verify_password(password, user["salt"], user["password_hash"]):
        return None
    return user


def issue_token(store, username: str, remember: bool) -> LoginResult:
    token = secrets.token_urlsafe(32)
    created = _utcnow()
    expires = _expiry(remember)
    store.add_token(token, username, created, expires)
    user = store.get_user(username) or {}
    return LoginResult(token=token, expires_at=expires, username=username,
                       display_name=user.get("display_name", ""),
                       role=user.get("role", "agent"))


def _parse_expiry(s: str):
    """解析 token 过期时间,兼容两种存储格式:
    - 北京/裸格式 'YYYY-MM-DD HH:MM:SS[.ffffff]'
    - ISO 格式 'YYYY-MM-DDTHH:MM:SS[.ffffff][+HH:MM]'(auth_tokens 实际存的是这种)
    无时区的按北京时间补 tzinfo,统一返回 aware datetime;解析失败返回 None。
    """
    from datetime import datetime
    raw = (s or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=beijing_now_dt().tzinfo)
    return dt


def validate_token(store, token: str) -> str | None:
    """返回 username(有效且未过期)或 None。"""
    if not token:
        return None
    row = store.get_token(token)
    if row is None:
        return None
    exp = _parse_expiry(row["expires_at"])
    if exp is None:
        return None
    if exp <= beijing_now_dt():
        store.delete_token(token)
        return None
    return row["username"]


def revoke_token(store, token: str) -> bool:
    return store.delete_token(token) > 0


def seed_admin_if_empty(store, seed_user: str, seed_password: str) -> None:
    """users 表为空时播种一个管理员账号(来自 config.seed_admin_user/seed_admin_password)。
    仅当表为空才播种,绝不覆盖既有账号;**权限只认数据库 users.role,与此配置无关**。"""
    if store.count_users() > 0:
        return
    if not seed_user or not seed_password:
        print("[auth] users 表为空且无 seed_admin_user/seed_admin_password 配置,跳过管理员播种;请通过其它方式建账号。",
              flush=True)
        return
    create_user(store, seed_user, seed_password, display_name=seed_user, role="admin")
    warn = "默认弱口令" if seed_password in ("change-me", "admin", "password", "123456") else "已配置口令"
    print(f"[auth] 已播种管理员账号 '{seed_user}'({warn})。正式环境请尽快修改口令。", flush=True)
