"""Argon2 密码哈希、严格 JWT 验证和实时 RBAC 身份快照。"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy import select

from app.core.config import get_settings
from app.core.exceptions import ConfigurationError
from app.db.models import Role, RolePermission, User, UserRole
from app.db.session import session_factory

PASSWORD_HASH = PasswordHash.recommended()
DUMMY_HASH = PASSWORD_HASH.hash("dummy-credential-for-timing-only")
BEARER = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    id: str
    tenant_id: str
    username: str
    department_id: str | None
    role_ids: frozenset[str]
    permissions: frozenset[str]
    auth_version: int


def hash_password(password: str) -> str:
    if len(password) < 12 or len(password) > 256:
        raise HTTPException(422, "密码长度必须为 12–256")
    return PASSWORD_HASH.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return PASSWORD_HASH.verify(password, hashed)
    except (ValueError, TypeError):
        return False


def jwt_key() -> str:
    secret = get_settings().jwt_secret
    if secret is None or len(secret.get_secret_value()) < 32:
        raise ConfigurationError("请配置至少 32 个字符的 JWT_SECRET")
    return secret.get_secret_value()


def load_principal(user_id: str) -> Principal:
    with session_factory()() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise HTTPException(401, "身份无效或已停用", headers={"WWW-Authenticate": "Bearer"})
        roles = frozenset(
            db.scalars(
                select(Role.id)
                .join(UserRole)
                .where(UserRole.user_id == user.id, Role.tenant_id == user.tenant_id)
            )
        )
        permissions = frozenset(
            db.scalars(select(RolePermission.permission_code).where(RolePermission.role_id.in_(roles)))
        )
        return Principal(
            user.id, user.tenant_id, user.username, user.department_id, roles, permissions, user.auth_version
        )


def issue_token(principal: Principal) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": principal.id,
            "tenant": principal.tenant_id,
            "ver": principal.auth_version,
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_minutes),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        jwt_key(),
        algorithm="HS256",
    )


def current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(BEARER)) -> Principal:
    if credentials is None:
        raise HTTPException(401, "需要登录", headers={"WWW-Authenticate": "Bearer"})
    settings = get_settings()
    try:
        data = jwt.decode(
            credentials.credentials,
            jwt_key(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "tenant", "ver", "iat", "exp", "iss", "aud"]},
        )
        principal = load_principal(data["sub"])
        if principal.tenant_id != data["tenant"] or principal.auth_version != data["ver"]:
            raise HTTPException(401, "Token 已失效")
        request.state.principal = principal
        return principal
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Token 无效或过期", headers={"WWW-Authenticate": "Bearer"}) from exc


def require(permission: str):
    def dependency(principal: Principal = Depends(current_user)) -> Principal:
        enforce(principal, permission)
        return principal

    return dependency


def enforce(principal: Principal, permission: str) -> None:
    if permission not in principal.permissions:
        raise HTTPException(403, "缺少功能权限")
