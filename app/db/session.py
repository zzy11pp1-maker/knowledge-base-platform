"""每次业务操作使用独立 Session，SQLite 强制启用外键。"""

from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.models import Base


@lru_cache(maxsize=1)
def get_engine():
    url = make_url(get_settings().database_url)
    kwargs = {"pool_pre_ping": True}
    if url.get_backend_name() == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if url.database in (None, "", ":memory:"):
            kwargs["poolclass"] = StaticPool
        else:
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)
    if url.get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def session_factory():
    return sessionmaker(get_engine(), expire_on_commit=False)


def initialize_database() -> None:
    # 仅创建缺失表；既有表不会被删除或隐式改写。后续 schema 变更需版本迁移。
    Base.metadata.create_all(get_engine())
    if get_engine().dialect.name == "sqlite":
        # 已有 SQLite 可无损升级；只增加列，不删除、不重建用户数据。
        additions = {
            "documents": {
                "file_type": "ALTER TABLE documents ADD COLUMN file_type VARCHAR(16) NOT NULL DEFAULT 'text'",
                "category": "ALTER TABLE documents ADD COLUMN category VARCHAR(128) NOT NULL DEFAULT '未分类'",
                "is_enabled": "ALTER TABLE documents ADD COLUMN is_enabled BOOLEAN NOT NULL DEFAULT 1",
                "chunk_size": "ALTER TABLE documents ADD COLUMN chunk_size INTEGER NOT NULL DEFAULT 1000",
                "chunk_overlap": "ALTER TABLE documents ADD COLUMN chunk_overlap INTEGER NOT NULL DEFAULT 100",
                "error_message": "ALTER TABLE documents ADD COLUMN error_message VARCHAR(512)",
                "updated_at": "ALTER TABLE documents ADD COLUMN updated_at DATETIME",
            },
            "faq_candidates": {
                "confidence_score": "ALTER TABLE faq_candidates ADD COLUMN confidence_score FLOAT NOT NULL DEFAULT 0",
            },
            "knowledge_gaps": {
                "department_id": "ALTER TABLE knowledge_gaps ADD COLUMN department_id VARCHAR(128)",
                "highest_similarity_score": "ALTER TABLE knowledge_gaps ADD COLUMN highest_similarity_score FLOAT",
                "suggested_category": "ALTER TABLE knowledge_gaps ADD COLUMN suggested_category VARCHAR(128)",
            },
        }
        with get_engine().begin() as connection:
            for table, table_additions in additions.items():
                columns = {item["name"] for item in inspect(get_engine()).get_columns(table)}
                for name, statement in table_additions.items():
                    if name not in columns:
                        connection.execute(text(statement))
    # 系统管理员自动获得新增功能权限；普通角色必须由管理员显式授权。
    from app.db.models import Permission, Role, RolePermission
    from app.services.identity_service import PERMISSIONS

    with session_factory()() as db:
        for code, description in PERMISSIONS.items():
            if db.get(Permission, code) is None:
                db.add(Permission(code=code, description=description))
        db.flush()
        for role in db.scalars(select(Role).where(Role.is_system.is_(True))):
            existing = set(db.scalars(select(RolePermission.permission_code).where(RolePermission.role_id == role.id)))
            db.add_all(RolePermission(role_id=role.id, permission_code=code) for code in PERMISSIONS if code not in existing)
        db.commit()


def close_database() -> None:
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
