"""可维护的非敏感模型参数；密钥绝不入库或返回。"""

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.models import ModelConfiguration
from app.db.session import session_factory

FIELDS = ("bge_base_url", "reranker_base_url", "llm_base_url", "llm_model", "embedding_model_id")


def runtime_settings():
    base = get_settings()
    try:
        with session_factory()() as db:
            row = db.get(ModelConfiguration, "default")
            overrides = {name: getattr(row, name) for name in FIELDS if row and getattr(row, name)}
        return base.model_copy(update=overrides)
    except SQLAlchemyError:
        # 数据库初始化之前健康检查仍可读取环境配置。
        return base


def public_config() -> dict:
    settings = runtime_settings()
    return {name: getattr(settings, name) for name in FIELDS} | {
        "llm_api_key_configured": bool(settings.llm_api_key),
        "bge_api_key_configured": bool(settings.bge_api_key),
        "reranker_api_key_configured": bool(settings.reranker_api_key),
    }
