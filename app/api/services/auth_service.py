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
from datetime import datetime, timedelta, timezone

from app.session import events

_PBKDF2_ROUNDS = 100_000
_REMEMBER_DAYS = 30
_SESSION_HOURS = 8


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _expiry(remember: bool) -> str:
    delta = timedelta(days=_REMEMBER_DAYS) if remember else timedelta(hours=_SESSION_HOURS)
    return (datetime.now(timezone.utc) + delta).isoformat(timespec="milliseconds")


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


def create_user(store, username: str, password: str, display_name: str = "") -> dict:
    salt_hex, hash_hex = hash_password(password)
    return store.create_user(username, hash_hex, salt_hex, display_name)


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
    return LoginResult(token=token, expires_at=expires, username=username, display_name=user.get("display_name", ""))


def validate_token(store, token: str) -> str | None:
    """返回 username(有效且未过期)或 None。"""
    if not token:
        return None
    row = store.get_token(token)
    if row is None:
        return None
    try:
        exp = datetime.fromisoformat(row["expires_at"])
    except ValueError:
        return None
    if exp <= datetime.now(timezone.utc):
        store.delete_token(token)
        return None
    return row["username"]


def revoke_token(store, token: str) -> bool:
    return store.delete_token(token) > 0


def seed_admin_if_empty(store, login_user: str, login_password: str) -> None:
    """users 表为空时播种一个管理员账号(来自 config.login_user/login_password)。
    仅当表为空才播种,绝不覆盖既有账号。"""
    if store.count_users() > 0:
        return
    if not login_user or not login_password:
        print("[auth] users 表为空且无 login_user/login_password 配置,跳过管理员播种;请通过其它方式建账号。",
              flush=True)
        return
    create_user(store, login_user, login_password, display_name=login_user)
    warn = "默认弱口令" if login_password in ("change-me", "admin", "password", "123456") else "已配置口令"
    print(f"[auth] 已播种管理员账号 '{login_user}'({warn})。正式环境请尽快修改口令。", flush=True)
