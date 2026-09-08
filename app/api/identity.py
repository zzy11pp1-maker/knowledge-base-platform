"""登录、用户、角色与部门 API；租户始终来自已验证 Token。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.core.auth import (
    DUMMY_HASH,
    Principal,
    current_user,
    enforce,
    hash_password,
    issue_token,
    load_principal,
    require,
    verify_password,
)
from app.db.models import Department, Permission, Role, User
from app.db.session import session_factory
from app.schemas.business import (
    DepartmentCreate,
    DepartmentOutput,
    DepartmentPatch,
    LoginInput,
    RoleCreate,
    RoleOutput,
    RolePatch,
    TokenOutput,
    UserCreate,
    UserOutput,
    UserPatch,
)
from app.services.access_control import scoped
from app.services.identity_service import (
    bind_permissions,
    bind_roles,
    check_parent,
    department_tree,
    ensure_not_acl_target,
    role_output,
    user_output,
)

router = APIRouter()


@router.post("/auth/login", response_model=TokenOutput)
def login(body: LoginInput):
    with session_factory()() as db:
        row = db.scalar(select(User).where(User.tenant_id == body.tenant_id, User.username == body.username))
        # 用户不存在时仍执行密码哈希核验，减少用户名枚举的时间差。
        valid = verify_password(body.password, row.password_hash if row else DUMMY_HASH)
        if not valid or row is None or not row.is_active:
            raise HTTPException(401, "用户名或密码错误")
        return {"access_token": issue_token(load_principal(row.id))}


@router.get("/auth/me")
def me(user: Principal = Depends(current_user)):
    with session_factory()() as db:
        return {**user_output(db, db.get(User, user.id)), "permissions": sorted(user.permissions)}


@router.post("/users", response_model=UserOutput, status_code=201)
def create_user(body: UserCreate, actor: Principal = Depends(require("user:manage"))):
    with session_factory()() as db:
        if body.department_id:
            scoped(db, Department, body.department_id, actor.tenant_id)
        if body.role_ids:
            enforce(actor, "role:manage")
        row = User(
            tenant_id=actor.tenant_id,
            username=body.username,
            password_hash=hash_password(body.password),
            department_id=body.department_id,
        )
        db.add(row)
        db.flush()
        bind_roles(db, actor.tenant_id, row.id, body.role_ids)
        db.commit()
        return user_output(db, row)


@router.get("/users", response_model=list[UserOutput])
def users(
    actor: Principal = Depends(require("user:manage")),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    with session_factory()() as db:
        return [
            user_output(db, x)
            for x in db.scalars(
                select(User)
                .where(User.tenant_id == actor.tenant_id)
                .order_by(User.id)
                .offset(offset)
                .limit(limit)
            )
        ]


@router.patch("/users/{user_id}", response_model=UserOutput)
def update_user(user_id: str, body: UserPatch, actor: Principal = Depends(require("user:manage"))):
    with session_factory()() as db:
        row = scoped(db, User, user_id, actor.tenant_id)
        if body.department_id:
            scoped(db, Department, body.department_id, actor.tenant_id)
        if "department_id" in body.model_fields_set:
            row.department_id = body.department_id
        if body.password is not None:
            row.password_hash = hash_password(body.password)
            row.auth_version += 1
        if body.role_ids is not None:
            enforce(actor, "role:manage")
            bind_roles(db, actor.tenant_id, row.id, body.role_ids)
        if body.is_active is not None:
            if row.id == actor.id and not body.is_active:
                raise HTTPException(409, "不能停用当前管理员")
            row.is_active = body.is_active
            row.auth_version += 1
        db.commit()
        return user_output(db, row)


@router.get("/permissions")
def permissions(actor: Principal = Depends(require("role:manage"))):
    with session_factory()() as db:
        return [
            {"code": x.code, "description": x.description}
            for x in db.scalars(select(Permission).order_by(Permission.code))
        ]


@router.post("/roles", response_model=RoleOutput, status_code=201)
def create_role(body: RoleCreate, actor: Principal = Depends(require("role:manage"))):
    with session_factory()() as db:
        row = Role(tenant_id=actor.tenant_id, name=body.name)
        db.add(row)
        db.flush()
        bind_permissions(db, row.id, body.permission_codes)
        db.commit()
        return role_output(db, row)


@router.get("/roles", response_model=list[RoleOutput])
def roles(actor: Principal = Depends(require("role:manage"))):
    with session_factory()() as db:
        return [
            role_output(db, x)
            for x in db.scalars(select(Role).where(Role.tenant_id == actor.tenant_id).order_by(Role.id))
        ]


@router.patch("/roles/{role_id}", response_model=RoleOutput)
def update_role(role_id: str, body: RolePatch, actor: Principal = Depends(require("role:manage"))):
    with session_factory()() as db:
        row = scoped(db, Role, role_id, actor.tenant_id)
        if row.is_system:
            raise HTTPException(409, "初始化管理员角色不可修改")
        if body.name is not None:
            row.name = body.name
        if body.permission_codes is not None:
            bind_permissions(db, row.id, body.permission_codes)
        db.commit()
        return role_output(db, row)


@router.delete("/roles/{role_id}", status_code=204)
def delete_role(role_id: str, actor: Principal = Depends(require("role:manage"))):
    with session_factory()() as db:
        row = scoped(db, Role, role_id, actor.tenant_id)
        if row.is_system:
            raise HTTPException(409, "初始化管理员角色不可删除")
        ensure_not_acl_target(db, "role", row.id)
        db.delete(row)
        db.commit()


@router.post("/departments", response_model=DepartmentOutput, status_code=201)
def create_department(body: DepartmentCreate, actor: Principal = Depends(require("department:manage"))):
    with session_factory()() as db:
        check_parent(db, actor.tenant_id, body.parent_id)
        row = Department(tenant_id=actor.tenant_id, **body.model_dump())
        db.add(row)
        db.commit()
        return {"id": row.id, "name": row.name, "parent_id": row.parent_id}


@router.get("/departments", response_model=list[DepartmentOutput])
def departments(actor: Principal = Depends(require("department:manage"))):
    with session_factory()() as db:
        return department_tree(
            list(
                db.scalars(
                    select(Department).where(Department.tenant_id == actor.tenant_id).order_by(Department.id)
                )
            )
        )


@router.patch("/departments/{department_id}", response_model=DepartmentOutput)
def update_department(
    department_id: str, body: DepartmentPatch, actor: Principal = Depends(require("department:manage"))
):
    with session_factory()() as db:
        row = scoped(db, Department, department_id, actor.tenant_id)
        if "parent_id" in body.model_fields_set:
            check_parent(db, actor.tenant_id, body.parent_id, row.id)
            row.parent_id = body.parent_id
        if body.name is not None:
            row.name = body.name
        db.commit()
        return {"id": row.id, "name": row.name, "parent_id": row.parent_id}


@router.delete("/departments/{department_id}", status_code=204)
def delete_department(department_id: str, actor: Principal = Depends(require("department:manage"))):
    with session_factory()() as db:
        row = scoped(db, Department, department_id, actor.tenant_id)
        ensure_not_acl_target(db, "department", row.id)
        # FK RESTRICT 同时保护子部门和仍在部门内的用户。
        db.delete(row)
        db.commit()
