"""会话隔离、来源留痕与撤权后的历史保护。"""

from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import Conversation, DocumentRecord, Message, MessageCitation
from app.schemas.business import Citation
from app.services.access_control import can_read_document, kb_access, scoped


def conversation_access(db, user, conversation_id):
    conversation = scoped(db, Conversation, conversation_id, user.tenant_id)
    if conversation.user_id != user.id:
        raise HTTPException(404, "会话不存在或不可访问")
    kb_access(db, user, conversation.kb_id)
    return conversation


def readable_messages(db, user, conversation_id):
    conversation_access(db, user, conversation_id)
    rows = list(
        db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at, Message.id)
        )
    )
    sources_by_message = {
        row.id: list(db.scalars(select(MessageCitation).where(MessageCitation.message_id == row.id)))
        for row in rows
    }
    invalid_turns = set()
    for row in rows:
        for source in sources_by_message[row.id]:
            doc = db.get(DocumentRecord, source.document_id)
            if doc is None or not can_read_document(db, user, doc) or doc.version != source.document_version:
                invalid_turns.add(row.turn_id)
                break
    result = []
    for row in rows:
        sources = sources_by_message[row.id]
        allowed = row.turn_id not in invalid_turns
        # 任何生成来源失效时隐藏整条回答，不能只删 citation。
        visible = row.status == "complete" and allowed
        result.append(
            {
                "id": row.id,
                "role": row.role,
                "content": row.content if visible else "",
                "status": row.status if allowed else "restricted",
                "turn_id": row.turn_id,
                "created_at": row.created_at,
                "citations": [Citation.model_validate(s) for s in sources if s.used] if visible else [],
            }
        )
    return result
