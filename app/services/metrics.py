"""真实业务埋点；只允许记录白名单元数据，禁止保存问题和知识正文。"""

import json
import logging

from app.db.models import MetricEvent
from app.db.session import session_factory

SAFE_METADATA = {"route", "status", "document_id", "faq_entry_id", "result_count", "usage_available"}
LOGGER = logging.getLogger(__name__)


def record_metric(event_type: str, *, tenant_id: str, kb_id=None, user_id=None, duration_ms=None,
                  prompt_tokens=0, completion_tokens=0, metadata=None) -> None:
    clean = {key: value for key, value in (metadata or {}).items() if key in SAFE_METADATA}
    try:
        with session_factory()() as db:
            db.add(MetricEvent(
                tenant_id=tenant_id, kb_id=kb_id, user_id=user_id, event_type=event_type,
                duration_ms=duration_ms, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                metadata_json=json.dumps(clean, ensure_ascii=False),
            ))
            db.commit()
    except Exception as exc:
        # 埋点故障不能中断问答；只记录异常类型，不记录业务正文。
        LOGGER.warning("指标写入失败 type=%s event_type=%s", type(exc).__name__, event_type)
