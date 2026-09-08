"""服务端授权：RBAC、知识库成员与四维 ACL 必须同时满足。"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Principal, enforce, load_principal
from app.db.models import (
    Department,
    DocumentPermission,
    DocumentRecord,
    KnowledgeBase,
    KnowledgeBaseMember,
    Role,
    User,
)

MEMBER_LEVEL = {"viewer": 1, "editor": 2, "owner": 3}


def scoped(db: Session, model, object_id: str, tenant_id: str):
    row = db.get(model, object_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "对象不存在或不可访问")
    return row


def kb_access(db: Session, user: Principal, kb_id: str, minimum: str = "viewer") -> KnowledgeBase:
    kb = scoped(db, KnowledgeBase, kb_id, user.tenant_id)
    member = db.get(KnowledgeBaseMember, (kb_id, user.id))
    if kb.is_deleted or member is None:
        raise HTTPException(404, "知识库不存在或不可访问")
    if MEMBER_LEVEL[member.role] < MEMBER_LEVEL[minimum]:
        raise HTTPException(403, "知识库成员权限不足")
    return kb


def acl_entries(db: Session, document_id: str) -> list[DocumentPermission]:
    return list(db.scalars(select(DocumentPermission).where(DocumentPermission.document_id == document_id)))


def acl_matches(user: Principal, entries: list[DocumentPermission]) -> bool:
    # global 的范围仍受租户和知识库成员限制；部门默认精确匹配，不隐式继承。
    return any(
        entry.permission_type == "global"
        or (entry.permission_type == "user" and entry.target_id == user.id)
        or (entry.permission_type == "department" and entry.target_id == user.department_id)
        or (entry.permission_type == "role" and entry.target_id in user.role_ids)
        for entry in entries
    )


def can_read_document(db: Session, user: Principal, doc: DocumentRecord) -> bool:
    if doc.tenant_id != user.tenant_id or doc.status != "ready" or not doc.is_enabled:
        return False
    kb = db.get(KnowledgeBase, doc.kb_id)
    if kb is None or kb.is_deleted or db.get(KnowledgeBaseMember, (doc.kb_id, user.id)) is None:
        return False
    return acl_matches(user, acl_entries(db, doc.id))


def validate_acl(db: Session, user: Principal, entries) -> None:
    seen = set()
    models = {"department": Department, "role": Role, "user": User}
    for entry in entries:
        key = (entry.permission_type, entry.target_id)
        if key in seen:
            raise HTTPException(422, "ACL 条目重复")
        seen.add(key)
        if entry.permission_type != "global":
            scoped(db, models[entry.permission_type], entry.target_id, user.tenant_id)


def acl_payload(db: Session, doc: DocumentRecord) -> dict:
    entries = acl_entries(db, doc.id)
    return {
        "acl_schema": 2,
        "acl_version": doc.acl_version,
        "is_global": any(x.permission_type == "global" for x in entries),
        "allowed_departments": [x.target_id for x in entries if x.permission_type == "department"],
        "allowed_roles": [x.target_id for x in entries if x.permission_type == "role"],
        "allowed_users": [x.target_id for x in entries if x.permission_type == "user"],
    }


def fresh_user(user: Principal, permission: str = "knowledge:read") -> Principal:
    current = load_principal(user.id)
    if current.auth_version != user.auth_version or current.tenant_id != user.tenant_id:
        raise HTTPException(401, "身份已变更，请重新登录")
    enforce(current, permission)
    return current
