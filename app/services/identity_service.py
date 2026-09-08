"""组织与角色管理；所有关联对象必须属于当前租户。"""

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.auth import hash_password
from app.db.models import Department, DocumentPermission, Permission, Role, RolePermission, User, UserRole
from app.services.access_control import scoped

PERMISSIONS = {
    "knowledge:create": "创建知识库及文档",
    "knowledge:read": "读取授权知识",
    "knowledge:update": "维护知识及 ACL",
    "knowledge:delete": "删除知识",
    "user:manage": "管理用户",
    "role:manage": "管理角色及功能权限",
    "department:manage": "管理部门",
    "chat:use": "使用知识问答",
    "faq:manage": "审核和发布 FAQ",
    "gap:manage": "管理知识缺口",
    "dashboard:view": "查看运营看板",
    "audit:view": "查看问答鉴权审计记录",
}


def bind_roles(db: Session, tenant_id: str, user_id: str, ids: list[str]) -> None:
    for role_id in set(ids):
        scoped(db, Role, role_id, tenant_id)
    db.execute(delete(UserRole).where(UserRole.user_id == user_id))
    db.add_all(UserRole(user_id=user_id, role_id=role_id) for role_id in set(ids))


def bind_permissions(db: Session, role_id: str, codes: list[str]) -> None:
    if set(codes) - PERMISSIONS.keys():
        raise HTTPException(422, "未知功能权限")
    db.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
    db.add_all(RolePermission(role_id=role_id, permission_code=code) for code in set(codes))


def user_output(db: Session, user: User) -> dict:
    return {
        "id": user.id,
        "tenant_id": user.tenant_id,
        "username": user.username,
        "department_id": user.department_id,
        "is_active": user.is_active,
        "role_ids": list(db.scalars(select(UserRole.role_id).where(UserRole.user_id == user.id))),
    }


def role_output(db: Session, role: Role) -> dict:
    return {
        "id": role.id,
        "name": role.name,
        "is_system": role.is_system,
        "permission_codes": list(
            db.scalars(select(RolePermission.permission_code).where(RolePermission.role_id == role.id))
        ),
    }


def check_parent(
    db: Session, tenant_id: str, parent_id: str | None, department_id: str | None = None
) -> None:
    visited = {department_id} if department_id else set()
    while parent_id:
        if parent_id in visited:
            raise HTTPException(422, "部门树不能形成循环")
        visited.add(parent_id)
        parent_id = scoped(db, Department, parent_id, tenant_id).parent_id


def department_tree(rows: list[Department]) -> list[dict]:
    nodes = {x.id: {"id": x.id, "name": x.name, "parent_id": x.parent_id, "children": []} for x in rows}
    roots = []
    for row in rows:
        if row.parent_id in nodes:
            nodes[row.parent_id]["children"].append(nodes[row.id])
        else:
            roots.append(nodes[row.id])
    return roots


def ensure_not_acl_target(db: Session, kind: str, object_id: str) -> None:
    if db.scalar(
        select(DocumentPermission.id)
        .where(DocumentPermission.permission_type == kind, DocumentPermission.target_id == object_id)
        .limit(1)
    ):
        raise HTTPException(409, "对象仍被文档权限引用")


def bootstrap_admin(db: Session, tenant_id: str, username: str, password: str) -> User:
    """仅本地 CLI 初始化；存在用户时拒绝覆盖密码。"""
    if db.scalar(select(User.id).where(User.tenant_id == tenant_id).limit(1)):
        raise HTTPException(409, "租户已有用户，禁止重复初始化")
    for code, description in PERMISSIONS.items():
        if db.get(Permission, code) is None:
            db.add(Permission(code=code, description=description))
    admin_role = Role(tenant_id=tenant_id, name="admin", is_system=True)
    user = User(tenant_id=tenant_id, username=username, password_hash=hash_password(password))
    db.add_all([admin_role, user])
    db.flush()
    bind_permissions(db, admin_role.id, list(PERMISSIONS))
    db.add(UserRole(user_id=user.id, role_id=admin_role.id))
    return user
